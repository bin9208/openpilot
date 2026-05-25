#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterable

from tools.galaxy_hud_bridge.canfd_radar import CanFdRadarParser
from tools.galaxy_hud_bridge.system_stats import SystemStatsSampler


DISCOVERY_MAGIC = b"galaxy-hud-discover-v1"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 28888
DEFAULT_DISCOVERY_PORT = 28889
DEFAULT_FPS = 20.0
DEFAULT_OPTIONAL_SERVICES = (
  "radarState",
  "carrotMan",
  "gpsLocationExternal",
  "lateralPlan",
  "navInstructionCarrot",
  "peripheralState",
  "wideRoadCameraState",
  "carControl",
)


def _read_process_rss_mb() -> float | None:
  try:
    with open("/proc/self/status", encoding="utf-8") as handle:
      for line in handle:
        if line.startswith("VmRSS:"):
          fields = line.split()
          if len(fields) >= 2:
            return int(fields[1]) / 1024.0
  except Exception:
    return None
  return None


def _load_realtime_broker() -> Any:
  try:
    from openpilot.selfdrive.carrot.server.live_runtime.broker import RealtimeBroker
  except ModuleNotFoundError:
    from selfdrive.carrot.server.live_runtime.broker import RealtimeBroker
  return RealtimeBroker


def _json_line(payload: dict) -> bytes:
  return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class BridgeProfiler:
  def __init__(self, interval_s: float) -> None:
    self.interval_s = max(0.0, interval_s)
    self.enabled = self.interval_s > 0.0
    self.samples: dict[str, list[float]] = {}
    self.payload_bytes: list[int] = []
    self.frames = 0
    self.last_report_time = time.perf_counter()
    self.last_process_time = time.process_time()

  def add_elapsed(self, name: str, start_time: float) -> None:
    if self.enabled:
      self.samples.setdefault(name, []).append((time.perf_counter() - start_time) * 1000.0)

  def add_payload(self, size: int) -> None:
    if self.enabled:
      self.payload_bytes.append(size)

  def frame_done(self) -> None:
    if self.enabled:
      self.frames += 1

  def maybe_report(self, *, clients: int, logger: logging.Logger) -> None:
    if not self.enabled:
      return
    now = time.perf_counter()
    elapsed = now - self.last_report_time
    if elapsed < self.interval_s:
      return

    process_time = time.process_time()
    process_cpu = max(0.0, process_time - self.last_process_time) / max(0.001, elapsed) * 100.0
    self.last_process_time = process_time
    fps = self.frames / max(0.001, elapsed)
    avg_payload = sum(self.payload_bytes) / max(1, len(self.payload_bytes))
    parts = [f"profile frames={self.frames} fps={fps:.1f} clients={clients} proc_cpu={process_cpu:.1f}% payload={avg_payload:.0f}B"]
    rss_mb = _read_process_rss_mb()
    if rss_mb is not None:
      parts.append(f"rss={rss_mb:.1f}MB")
    for name, values in sorted(self.samples.items()):
      if not values:
        continue
      parts.append(f"{name}=avg{sum(values) / len(values):.2f}ms/max{max(values):.2f}ms")
    logger.info(" ".join(parts))

    self.samples.clear()
    self.payload_bytes.clear()
    self.frames = 0
    self.last_report_time = now


@dataclass(eq=False)
class HudClient:
  writer: asyncio.StreamWriter
  queue: asyncio.Queue[bytes]

  @property
  def peer(self) -> str:
    peername = self.writer.get_extra_info("peername")
    return str(peername) if peername is not None else "unknown"

  def send_latest(self, payload: bytes) -> None:
    if self.queue.full():
      with contextlib.suppress(asyncio.QueueEmpty):
        self.queue.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
      self.queue.put_nowait(payload)

  async def write_loop(self) -> None:
    try:
      while True:
        payload = await self.queue.get()
        self.writer.write(payload)
        await self.writer.drain()
    finally:
      self.writer.close()
      with contextlib.suppress(Exception):
        await self.writer.wait_closed()


class DiscoveryProtocol(asyncio.DatagramProtocol):
  def __init__(self, *, tcp_port: int, logger: logging.Logger) -> None:
    self.tcp_port = tcp_port
    self.logger = logger
    self.transport: asyncio.DatagramTransport | None = None

  def connection_made(self, transport: asyncio.BaseTransport) -> None:
    self.transport = transport  # type: ignore[assignment]

  def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
    if data.strip() != DISCOVERY_MAGIC or self.transport is None:
      return
    payload = _json_line({
      "type": "galaxy-hud-announce",
      "transport": "tcp-json-lines",
      "port": self.tcp_port,
    })
    self.transport.sendto(payload, addr)
    self.logger.debug("discovery reply sent to %s", addr)


class HudBridge:
  def __init__(
    self,
    *,
    host: str,
    port: int,
    discovery_port: int,
    fps: float,
    profile_interval: float,
    can_fd_radar: bool,
    include_optional: Iterable[str] | None,
    exclude_services: Iterable[str] | None,
    repo_flavor: str,
  ) -> None:
    self.host = host
    self.port = port
    self.discovery_port = discovery_port
    self.fps = max(1.0, min(fps, 60.0))
    self.logger = logging.getLogger("galaxy_hud_bridge")
    self.clients: set[HudClient] = set()
    self.profiler = BridgeProfiler(profile_interval)
    self.system_stats = SystemStatsSampler()
    self.can_fd_radar = CanFdRadarParser() if can_fd_radar else None
    include_optional = self._optional_services(include_optional, can_fd_radar)
    realtime_broker = _load_realtime_broker()
    self.broker = realtime_broker(
      repo_flavor=repo_flavor,
      include_optional=include_optional,
      exclude_services=exclude_services,
    )
    self.last_payload = b""

  @staticmethod
  def _optional_services(include_optional: Iterable[str] | None, can_fd_radar: bool) -> list[str] | None:
    services = list(include_optional) if include_optional is not None else list(DEFAULT_OPTIONAL_SERVICES)
    if can_fd_radar:
      for service in ("can", "liveTracks"):
        if service not in services:
          services.append(service)
    return services

  async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    del reader
    client = HudClient(writer=writer, queue=asyncio.Queue(maxsize=2))
    self.clients.add(client)
    self.logger.info("client connected: %s", client.peer)

    hello = self.broker.hello_meta()
    hello["transport"] = "tcp-json-lines"
    hello["discoveryMagic"] = DISCOVERY_MAGIC.decode("ascii")
    writer.write(_json_line(hello))
    if self.last_payload:
      writer.write(self.last_payload)
    await writer.drain()

    task = asyncio.create_task(client.write_loop())
    try:
      await task
    except Exception:
      self.logger.debug("client write loop ended: %s", client.peer, exc_info=True)
    finally:
      self.clients.discard(client)
      if not task.done():
        task.cancel()
      self.logger.info("client disconnected: %s", client.peer)

  async def publish_loop(self) -> None:
    interval = 1.0 / self.fps
    while True:
      if not self.clients:
        await asyncio.sleep(1.0)
        continue

      profile_stage = time.perf_counter()
      snapshot = self.broker.poll(0)
      self.profiler.add_elapsed("broker.poll", profile_stage)
      if self.can_fd_radar is not None:
        profile_stage = time.perf_counter()
        self.can_fd_radar.update(self.broker.sm)
        snapshot.setdefault("services", {})["canFdRadar"] = self.can_fd_radar.snapshot()
        self.profiler.add_elapsed("canfd.parse", profile_stage)
      snapshot.setdefault("meta", {})["transport"] = "tcp-json-lines"
      runtime = snapshot.setdefault("runtime", {})
      if isinstance(runtime, dict):
        runtime["bridgeClients"] = len(self.clients)
        runtime["bridgeTargetFps"] = self.fps
        runtime["systemStats"] = self.system_stats.sample()
      profile_stage = time.perf_counter()
      self.last_payload = _json_line(snapshot)
      self.profiler.add_elapsed("json.encode", profile_stage)
      self.profiler.add_payload(len(self.last_payload))
      for client in tuple(self.clients):
        client.send_latest(self.last_payload)
      self.profiler.frame_done()
      self.profiler.maybe_report(clients=len(self.clients), logger=self.logger)
      await asyncio.sleep(interval)

  async def run(self) -> None:
    server = await asyncio.start_server(
      self.handle_client,
      host=self.host,
      port=self.port,
      family=socket.AF_INET,
      reuse_address=True,
    )
    loop = asyncio.get_running_loop()
    discovery_transport, _ = await loop.create_datagram_endpoint(
      lambda: DiscoveryProtocol(tcp_port=self.port, logger=self.logger),
      local_addr=(self.host, self.discovery_port),
      allow_broadcast=True,
    )

    sockets = ", ".join(str(sock.getsockname()) for sock in (server.sockets or ()))
    self.logger.info("Galaxy HUD bridge listening on %s", sockets)
    self.logger.info("UDP discovery listening on %s:%d", self.host, self.discovery_port)

    publisher = asyncio.create_task(self.publish_loop())
    try:
      async with server:
        await server.serve_forever()
    finally:
      publisher.cancel()
      discovery_transport.close()
      with contextlib.suppress(asyncio.CancelledError):
        await publisher


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Stream openpilot HUD telemetry to a Galaxy Tab app over TCP JSON lines.")
  parser.add_argument("--host", default=DEFAULT_HOST, help=f"TCP/UDP bind host, default {DEFAULT_HOST}")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP telemetry port, default {DEFAULT_PORT}")
  parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT, help=f"UDP discovery port, default {DEFAULT_DISCOVERY_PORT}")
  parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help=f"telemetry publish rate, default {DEFAULT_FPS:g}")
  parser.add_argument("--profile-interval", type=float, default=0.0, help="log bridge timing every N seconds; 0 disables profiling")
  parser.add_argument("--no-can-fd-radar", action="store_true", help="disable Hyundai CAN-FD radar parsing")
  parser.add_argument("--repo-flavor", default="openpilot", help="repo flavor tag included in hello/meta")
  parser.add_argument("--include-optional", nargs="*", default=None, help="optional cereal services to include")
  parser.add_argument("--exclude-service", action="append", default=None, help="service to exclude; may be repeated")
  parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
  return parser.parse_args()


async def async_main() -> None:
  args = parse_args()
  logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
  bridge = HudBridge(
    host=args.host,
    port=args.port,
    discovery_port=args.discovery_port,
    fps=args.fps,
    profile_interval=args.profile_interval,
    can_fd_radar=not args.no_can_fd_radar,
    include_optional=args.include_optional,
    exclude_services=args.exclude_service,
    repo_flavor=args.repo_flavor,
  )
  await bridge.run()


def main() -> None:
  asyncio.run(async_main())


if __name__ == "__main__":
  main()
