from __future__ import annotations

import time
from pathlib import Path
from typing import Any


PROC_STAT_PATH = Path("/proc/stat")
PROC_MEMINFO_PATH = Path("/proc/meminfo")


class SystemStatsSampler:
  def __init__(self, refresh_interval_s: float = 1.0) -> None:
    self.refresh_interval_s = max(0.1, float(refresh_interval_s))
    self.next_sample_time = 0.0
    self.previous_cpu_times: tuple[tuple[int, int], ...] | None = None
    self.last_stats: dict[str, Any] = {"available": False, "cores": []}

  def sample(self) -> dict[str, Any]:
    now = time.monotonic()
    if now < self.next_sample_time:
      return self.last_stats
    stats = self._sample_linux()
    if stats is not None:
      self.last_stats = stats
    self.next_sample_time = now + self.refresh_interval_s
    return self.last_stats

  def _sample_linux(self) -> dict[str, Any] | None:
    if not PROC_STAT_PATH.exists() and not PROC_MEMINFO_PATH.exists():
      return None

    total, used, memory_percent = self._read_memory()
    cpu_times = self._read_cpu_times()
    cores: list[float | None] = []
    if cpu_times is not None:
      cores = self._cpu_percents(cpu_times)
      self.previous_cpu_times = cpu_times

    return {
      "available": True,
      "generatedAtMs": int(time.time() * 1000),
      "memoryTotalBytes": total,
      "memoryUsedBytes": used,
      "memoryUsedPercent": memory_percent,
      "cores": cores,
    }

  def _read_memory(self) -> tuple[int | None, int | None, float | None]:
    try:
      values: dict[str, int] = {}
      for line in PROC_MEMINFO_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 2:
          continue
        try:
          values[parts[0].rstrip(":")] = int(parts[1]) * 1024
        except ValueError:
          continue
    except OSError:
      return None, None, None

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None:
      available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    if total is None or total <= 0:
      return total, None, None
    used = max(0, min(total, total - available))
    return total, used, used / total * 100.0

  def _read_cpu_times(self) -> tuple[tuple[int, int], ...] | None:
    try:
      lines = PROC_STAT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
      return None
    cpu_times: list[tuple[int, int]] = []
    for line in lines:
      parts = line.split()
      if not parts or not parts[0].startswith("cpu") or not parts[0][3:].isdigit():
        continue
      try:
        fields = [int(value) for value in parts[1:]]
      except ValueError:
        continue
      if len(fields) < 4:
        continue
      idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
      cpu_times.append((sum(fields), idle))
    return tuple(cpu_times)

  def _cpu_percents(self, cpu_times: tuple[tuple[int, int], ...]) -> list[float | None]:
    previous = self.previous_cpu_times
    if previous is None or len(previous) != len(cpu_times):
      return [None for _ in cpu_times]

    percents: list[float | None] = []
    for (total, idle), (previous_total, previous_idle) in zip(cpu_times, previous, strict=True):
      delta_total = total - previous_total
      delta_idle = idle - previous_idle
      if delta_total <= 0:
        percents.append(None)
        continue
      busy = max(0, min(delta_total, delta_total - delta_idle))
      percents.append(busy / delta_total * 100.0)
    return percents
