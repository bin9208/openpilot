#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np


LABELS = [
  "white_dashed",
  "white_solid",
  "yellow_solid",
  "yellow_double_solid",
  "yellow_double_dashed",
  "road_edge_or_barrier",
  "unknown",
]
EXCLUDE = "__exclude__"


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lane Label Review</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101418;
      --panel: #171d23;
      --line: #2c3540;
      --text: #e7edf3;
      --muted: #8ea0b2;
      --good: #30c48d;
      --bad: #f05d5e;
      --warn: #f0c94a;
      --blue: #55a7ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, sans-serif;
      font-size: 14px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 12px 16px;
      background: rgba(16, 20, 24, 0.96);
      border-bottom: 1px solid var(--line);
    }
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    select, input, button {
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0f1419;
      color: var(--text);
      padding: 0 10px;
      font: inherit;
    }
    button {
      cursor: pointer;
      background: #1d2630;
    }
    button:hover { border-color: #526271; }
    button.primary { background: #17466f; border-color: #236ba8; }
    button.good { background: #123b2c; border-color: #1a8f63; }
    button.bad { background: #451c21; border-color: #b5464e; }
    button.warn { background: #443719; border-color: #a88726; }
    main {
      display: grid;
      grid-template-columns: 280px 1fr;
      min-height: calc(100vh - 57px);
    }
    aside {
      border-right: 1px solid var(--line);
      padding: 14px;
      background: var(--panel);
    }
    .summary {
      display: grid;
      gap: 8px;
    }
    .metric {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid #202832;
      padding-bottom: 6px;
    }
    .metric span:last-child { color: var(--muted); }
    .content {
      padding: 14px;
      min-width: 0;
    }
    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 12px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--panel);
    }
    .card.changed { border-color: var(--blue); }
    .card.excluded { opacity: 0.55; border-color: var(--bad); }
    .card.kept { border-color: var(--good); }
    .image-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      background: #000;
    }
    .image-row img {
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
      background: #020304;
      display: block;
    }
    .meta {
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .meta-top {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }
    .label {
      font-weight: 700;
      color: var(--warn);
    }
    .small {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      word-break: break-all;
    }
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }
    .actions select {
      grid-column: 1 / 3;
      width: 100%;
    }
    .pager {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .kbd {
      margin-top: 14px;
      color: var(--muted);
      line-height: 1.7;
    }
    @media (max-width: 920px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      header { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="filters">
      <label>Label
        <select id="labelFilter"></select>
      </label>
      <label>Side
        <select id="sideFilter">
          <option value="">all</option>
          <option value="left">left</option>
          <option value="right">right</option>
        </select>
      </label>
      <label>Status
        <select id="statusFilter">
          <option value="all">all</option>
          <option value="unreviewed">unreviewed</option>
          <option value="kept">kept</option>
          <option value="changed">changed</option>
          <option value="excluded">excluded</option>
        </select>
      </label>
      <label>Min conf
        <input id="minConf" type="number" min="0" max="1" step="0.05" value="0.75">
      </label>
      <label>Page
        <input id="pageSize" type="number" min="12" max="240" step="12" value="48">
      </label>
      <button id="reloadBtn">Reload</button>
    </div>
    <div class="filters">
      <button id="saveBtn" class="primary">Save corrected_labels.csv</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="summary" id="summary"></div>
      <div class="kbd">
        <b>Shortcuts</b><br>
        k: keep selected<br>
        x: exclude selected<br>
        1-5: relabel selected<br>
        ← / →: page<br>
        Click a card to select it.
      </div>
    </aside>
    <section class="content">
      <div class="toolbar">
        <div id="pageInfo" class="small"></div>
        <div class="pager">
          <button id="prevBtn">Prev</button>
          <button id="nextBtn">Next</button>
        </div>
      </div>
      <div class="grid" id="grid"></div>
    </section>
  </main>
  <script>
    const LABELS = ["white_dashed", "white_solid", "yellow_solid", "yellow_double_solid", "yellow_double_dashed", "road_edge_or_barrier", "unknown"];
    let offset = 0;
    let selectedId = null;
    let currentItems = [];

    const el = (id) => document.getElementById(id);

    function initFilters() {
      const sel = el("labelFilter");
      sel.innerHTML = `<option value="">all</option>` + LABELS.map(x => `<option value="${x}">${x}</option>`).join("");
    }

    function params() {
      return new URLSearchParams({
        label: el("labelFilter").value,
        side: el("sideFilter").value,
        status: el("statusFilter").value,
        min_conf: el("minConf").value || "0",
        offset: offset,
        limit: el("pageSize").value || "48",
      });
    }

    async function loadSummary() {
      const res = await fetch("/api/summary");
      const data = await res.json();
      const rows = [
        ["Rows", data.total_rows],
        ["Kept", data.status_counts.kept || 0],
        ["Changed", data.status_counts.changed || 0],
        ["Excluded", data.status_counts.excluded || 0],
        ["Unreviewed", data.status_counts.unreviewed || 0],
        ["Output", data.output_path],
      ];
      el("summary").innerHTML = rows.map(([k, v]) => `<div class="metric"><span>${k}</span><span>${v}</span></div>`).join("")
        + `<div class="small">${Object.entries(data.corrected_label_counts).map(([k,v]) => `${k}: ${v}`).join("<br>")}</div>`;
    }

    async function loadItems() {
      const res = await fetch("/api/items?" + params().toString());
      const data = await res.json();
      currentItems = data.items;
      el("pageInfo").textContent = `${data.offset + 1}-${Math.min(data.offset + data.items.length, data.total)} of ${data.total}`;
      renderItems(data.items);
      await loadSummary();
    }

    function statusClass(item) {
      if (item.review_status === "excluded") return "excluded";
      if (item.review_status === "changed") return "changed";
      if (item.review_status === "kept") return "kept";
      return "";
    }

    function renderItems(items) {
      const grid = el("grid");
      grid.innerHTML = items.map(item => {
        const select = `<select data-id="${item.row_id}" class="relabel">${LABELS.map(l => `<option value="${l}" ${item.corrected_label === l ? "selected" : ""}>${l}</option>`).join("")}</select>`;
        return `<article class="card ${statusClass(item)}" data-id="${item.row_id}">
          <div class="image-row">
            <img loading="lazy" src="/image/${item.row_id}?kind=crop&v=${item.image_version}" alt="crop">
            <img loading="lazy" src="/image/${item.row_id}?kind=frame&v=${item.image_version}" alt="frame">
          </div>
          <div class="meta">
            <div class="meta-top">
              <div class="label">${item.corrected_label}</div>
              <div class="small">${item.review_status}</div>
            </div>
            <div class="small">
              original=${item.label} side=${item.side} conf=${item.confidence}<br>
              v=${item.v_ego || "-"} model=${item.model_lane_prob || "-"} frame=${item.frame_idx}<br>
              ${item.segment}<br>${item.reason}
            </div>
            <div class="actions">
              ${select}
              <button class="good keep" data-id="${item.row_id}">Keep</button>
              <button class="bad exclude" data-id="${item.row_id}">Exclude</button>
            </div>
          </div>
        </article>`;
      }).join("");

      for (const card of document.querySelectorAll(".card")) {
        card.addEventListener("click", () => {
          selectedId = Number(card.dataset.id);
          document.querySelectorAll(".card").forEach(c => c.style.outline = "");
          card.style.outline = "2px solid var(--blue)";
        });
      }
      for (const btn of document.querySelectorAll(".keep")) btn.addEventListener("click", (e) => correct(Number(e.target.dataset.id), "keep"));
      for (const btn of document.querySelectorAll(".exclude")) btn.addEventListener("click", (e) => correct(Number(e.target.dataset.id), "exclude"));
      for (const sel of document.querySelectorAll(".relabel")) sel.addEventListener("change", (e) => correct(Number(e.target.dataset.id), "keep", e.target.value));
    }

    async function correct(rowId, action, label = null) {
      if (label === null) {
        const item = currentItems.find(x => x.row_id === rowId);
        label = item ? item.corrected_label : "unknown";
      }
      await fetch("/api/correct", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({row_id: rowId, action, label}),
      });
      await loadItems();
    }

    async function save() {
      const res = await fetch("/api/save", {method: "POST"});
      const data = await res.json();
      alert(`saved ${data.kept_rows} rows\n${data.output_path}`);
      await loadSummary();
    }

    function nextPage(delta) {
      const size = Number(el("pageSize").value || 48);
      offset = Math.max(0, offset + delta * size);
      loadItems();
    }

    for (const id of ["labelFilter", "sideFilter", "statusFilter", "minConf", "pageSize"]) {
      window.addEventListener("load", () => el(id).addEventListener("change", () => { offset = 0; loadItems(); }));
    }
    window.addEventListener("load", () => {
      initFilters();
      el("reloadBtn").addEventListener("click", () => loadItems());
      el("saveBtn").addEventListener("click", save);
      el("prevBtn").addEventListener("click", () => nextPage(-1));
      el("nextBtn").addEventListener("click", () => nextPage(1));
      document.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight") nextPage(1);
        if (e.key === "ArrowLeft") nextPage(-1);
        if (selectedId !== null && e.key === "k") correct(selectedId, "keep");
        if (selectedId !== null && e.key === "x") correct(selectedId, "exclude");
        if (selectedId !== null && /^[1-7]$/.test(e.key)) correct(selectedId, "keep", LABELS[Number(e.key) - 1]);
      });
      loadItems();
    });
  </script>
</body>
</html>
"""


@dataclass
class ReviewState:
  labels_path: Path
  output_path: Path
  corrections_path: Path
  rows: list[dict[str, str]]
  fieldnames: list[str]
  corrections: dict[int, dict[str, Any]]
  image_version: int = 1


def read_labels(path: Path) -> tuple[list[dict[str, str]], list[str]]:
  with path.open("r", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    fieldnames = list(reader.fieldnames or [])
  for idx, row in enumerate(rows):
    row["row_id"] = str(idx)
  return rows, fieldnames


def load_corrections(path: Path) -> dict[int, dict[str, Any]]:
  if not path.exists():
    return {}
  with path.open("r", encoding="utf-8") as f:
    raw = json.load(f)
  return {int(k): v for k, v in raw.items()}


def save_corrections(state: ReviewState) -> None:
  state.corrections_path.parent.mkdir(parents=True, exist_ok=True)
  with state.corrections_path.open("w", encoding="utf-8") as f:
    json.dump({str(k): v for k, v in state.corrections.items()}, f, indent=2, ensure_ascii=False)


def corrected_label(state: ReviewState, row_id: int) -> str:
  corr = state.corrections.get(row_id)
  if corr is None:
    return state.rows[row_id]["label"]
  if corr.get("action") == "exclude":
    return EXCLUDE
  return str(corr.get("label") or state.rows[row_id]["label"])


def review_status(state: ReviewState, row_id: int) -> str:
  corr = state.corrections.get(row_id)
  if corr is None:
    return "unreviewed"
  if corr.get("action") == "exclude":
    return "excluded"
  return "changed" if corr.get("label") != state.rows[row_id]["label"] else "kept"


def parse_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
  try:
    return float(row.get(key, default) or default)
  except ValueError:
    return default


def parse_int(row: dict[str, str], key: str, default: int = 0) -> int:
  try:
    return int(float(row.get(key, default) or default))
  except ValueError:
    return default


def image_color(label: str) -> tuple[int, int, int]:
  if label in ("yellow_solid", "yellow_double_solid", "yellow_double_dashed"):
    return (0, 230, 255)
  if label == "white_dashed":
    return (255, 170, 0)
  if label == "road_edge_or_barrier":
    return (220, 220, 220)
  if label == EXCLUDE:
    return (60, 60, 220)
  return (255, 255, 255)


def read_frame(row: dict[str, str]) -> np.ndarray | None:
  cap = cv2.VideoCapture(row["qcamera"])
  if not cap.isOpened():
    return None
  cap.set(cv2.CAP_PROP_POS_FRAMES, parse_int(row, "frame_idx"))
  ok, frame = cap.read()
  cap.release()
  return frame if ok else None


def draw_overlay(frame: np.ndarray, row: dict[str, str], label: str) -> np.ndarray:
  out = frame.copy()
  h, w = out.shape[:2]
  slope = parse_float(row, "slope")
  x_eval = parse_float(row, "x_eval")
  y_eval = int(h * 0.86)
  intercept = x_eval - slope * y_eval
  color = image_color(label)
  if abs(slope) > 1e-4 and x_eval > 0:
    y1, y2 = int(h * 0.42), int(h * 0.96)
    x1, x2 = int(slope * y1 + intercept), int(slope * y2 + intercept)
    cv2.line(out, (x1, y1), (x2, y2), color, 3)
  text = f"{row.get('side')} {label} conf={row.get('confidence')}"
  cv2.putText(out, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)
  cv2.putText(out, row.get("segment", ""), (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
  return out


def crop_lane(frame: np.ndarray, row: dict[str, str], label: str) -> np.ndarray:
  h, w = frame.shape[:2]
  slope = parse_float(row, "slope")
  x_eval = parse_float(row, "x_eval")
  y_eval = int(h * 0.86)
  intercept = x_eval - slope * y_eval
  y1, y2 = int(h * 0.38), int(h * 0.98)
  if abs(slope) < 1e-4 or x_eval <= 0:
    if row.get("side") == "left":
      x1, x2 = 0, int(w * 0.58)
    else:
      x1, x2 = int(w * 0.42), w
  else:
    xs = [int(slope * y + intercept) for y in (y1, y2)]
    pad = max(70, int(w * 0.13))
    x1, x2 = min(xs) - pad, max(xs) + pad
  x1, x2 = max(0, x1), min(w, x2)
  y1, y2 = max(0, y1), min(h, y2)
  crop = frame[y1:y2, x1:x2].copy()
  if crop.size == 0:
    crop = frame.copy()
    x1, y1 = 0, 0
  over = draw_overlay(frame, row, label)
  crop_over = over[y1:y2, x1:x2].copy()
  if crop_over.size == 0:
    crop_over = over
  return crop_over


def encode_jpeg(image: np.ndarray) -> bytes:
  ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
  if not ok:
    raise RuntimeError("failed to encode jpeg")
  return bytes(buf)


def row_payload(state: ReviewState, row_id: int) -> dict[str, Any]:
  row = state.rows[row_id]
  label = corrected_label(state, row_id)
  return {
    **row,
    "row_id": row_id,
    "original_label": row["label"],
    "corrected_label": row["label"] if label == EXCLUDE else label,
    "review_status": review_status(state, row_id),
    "image_version": state.image_version,
  }


def filtered_items(state: ReviewState, query: dict[str, list[str]]) -> tuple[list[int], int, int]:
  label_filter = query.get("label", [""])[0]
  side_filter = query.get("side", [""])[0]
  status_filter = query.get("status", ["all"])[0]
  min_conf = float(query.get("min_conf", ["0"])[0] or 0)
  offset = max(0, int(query.get("offset", ["0"])[0] or 0))
  limit = max(1, min(500, int(query.get("limit", ["48"])[0] or 48)))

  ids: list[int] = []
  for idx, row in enumerate(state.rows):
    status = review_status(state, idx)
    corr_label = corrected_label(state, idx)
    visible_label = row["label"] if corr_label == EXCLUDE else corr_label
    if label_filter and visible_label != label_filter and row["label"] != label_filter:
      continue
    if side_filter and row.get("side") != side_filter:
      continue
    if status_filter != "all" and status != status_filter:
      continue
    if parse_float(row, "confidence") < min_conf:
      continue
    ids.append(idx)
  return ids[offset:offset + limit], len(ids), offset


def write_corrected_csv(state: ReviewState) -> dict[str, Any]:
  output_fields = ["row_id", "original_label", "corrected_label", "review_status"] + state.fieldnames
  kept_rows: list[dict[str, str]] = []
  excluded_rows: list[dict[str, str]] = []

  for idx, row in enumerate(state.rows):
    status = review_status(state, idx)
    label = corrected_label(state, idx)
    out = dict(row)
    out.pop("row_id", None)
    original = out["label"]
    if label == EXCLUDE:
      out_row = {"row_id": str(idx), "original_label": original, "corrected_label": "", "review_status": status, **out}
      excluded_rows.append(out_row)
      continue
    out["label"] = label
    out_row = {"row_id": str(idx), "original_label": original, "corrected_label": label, "review_status": status, **out}
    kept_rows.append(out_row)

  state.output_path.parent.mkdir(parents=True, exist_ok=True)
  with state.output_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    writer.writerows(kept_rows)

  excluded_path = state.output_path.with_name("excluded_labels.csv")
  with excluded_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    writer.writerows(excluded_rows)

  save_corrections(state)
  return {
    "output_path": str(state.output_path),
    "excluded_path": str(excluded_path),
    "kept_rows": len(kept_rows),
    "excluded_rows": len(excluded_rows),
  }


class ReviewHandler(BaseHTTPRequestHandler):
  state: ReviewState

  def log_message(self, fmt: str, *args: Any) -> None:
    print(f"{self.client_address[0]} - {fmt % args}")

  def send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def send_json(self, obj: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
    self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

  def read_json(self) -> Any:
    length = int(self.headers.get("Content-Length", "0"))
    raw = self.rfile.read(length)
    return json.loads(raw.decode("utf-8")) if raw else {}

  def do_GET(self) -> None:
    parsed = urllib.parse.urlparse(self.path)
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)

    if path == "/":
      self.send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
      return

    if path == "/api/summary":
      self.send_json(self.summary_payload())
      return

    if path == "/api/items":
      ids, total, offset = filtered_items(self.state, query)
      self.send_json({"items": [row_payload(self.state, idx) for idx in ids], "total": total, "offset": offset})
      return

    if path.startswith("/image/"):
      try:
        row_id = int(path.split("/", 2)[2])
        kind = query.get("kind", ["crop"])[0]
        self.send_image(row_id, kind)
      except Exception as e:
        self.send_json({"error": str(e)}, HTTPStatus.NOT_FOUND)
      return

    self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

  def do_POST(self) -> None:
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path == "/api/correct":
      data = self.read_json()
      row_id = int(data["row_id"])
      action = data.get("action", "keep")
      label = data.get("label") or self.state.rows[row_id]["label"]
      if action not in ("keep", "exclude"):
        self.send_json({"error": "invalid action"}, HTTPStatus.BAD_REQUEST)
        return
      if action == "keep" and label not in LABELS:
        self.send_json({"error": "invalid label"}, HTTPStatus.BAD_REQUEST)
        return
      self.state.corrections[row_id] = {
        "action": action,
        "label": label,
        "updated_at": time.time(),
      }
      self.state.image_version += 1
      save_corrections(self.state)
      self.send_json({"ok": True, "item": row_payload(self.state, row_id)})
      return

    if parsed.path == "/api/save":
      self.send_json(write_corrected_csv(self.state))
      return

    self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

  def summary_payload(self) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    for idx, row in enumerate(self.state.rows):
      status = review_status(self.state, idx)
      label = corrected_label(self.state, idx)
      if label == EXCLUDE:
        label = "excluded"
      status_counts[status] = status_counts.get(status, 0) + 1
      label_counts[label] = label_counts.get(label, 0) + 1
    return {
      "total_rows": len(self.state.rows),
      "labels_path": str(self.state.labels_path),
      "output_path": str(self.state.output_path),
      "corrections_path": str(self.state.corrections_path),
      "status_counts": status_counts,
      "corrected_label_counts": label_counts,
    }

  def send_image(self, row_id: int, kind: str) -> None:
    if row_id < 0 or row_id >= len(self.state.rows):
      raise ValueError("row_id out of range")
    row = self.state.rows[row_id]
    label = corrected_label(self.state, row_id)
    frame = read_frame(row)
    if frame is None:
      image = np.zeros((240, 320, 3), dtype=np.uint8)
      cv2.putText(image, "frame unavailable", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
    elif kind == "frame":
      image = draw_overlay(frame, row, label)
    else:
      image = crop_lane(frame, row, label)
    self.send_bytes(encode_jpeg(image), "image/jpeg")


def build_arg_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Review lane-marking pseudo-labels and save corrected_labels.csv.")
  parser.add_argument("--labels", default=r"C:\tmp\lane_marking_labels_full\labels_trusted.csv", help="Input labels_trusted.csv path.")
  parser.add_argument("--out", default="", help="Output corrected_labels.csv path. Defaults next to input CSV.")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=8765)
  parser.add_argument("--open", action="store_true", help="Open the review page in the default browser.")
  parser.add_argument("--summary", action="store_true", help="Print summary and exit without starting the server.")
  return parser


def main() -> int:
  args = build_arg_parser().parse_args()
  labels_path = Path(args.labels)
  if not labels_path.exists():
    raise FileNotFoundError(labels_path)
  output_path = Path(args.out) if args.out else labels_path.with_name("corrected_labels.csv")
  corrections_path = output_path.with_name("corrections.json")
  rows, fieldnames = read_labels(labels_path)
  state = ReviewState(
    labels_path=labels_path,
    output_path=output_path,
    corrections_path=corrections_path,
    rows=rows,
    fieldnames=fieldnames,
    corrections=load_corrections(corrections_path),
  )

  if args.summary:
    status_counts: dict[str, int] = {}
    for idx in range(len(rows)):
      status = review_status(state, idx)
      status_counts[status] = status_counts.get(status, 0) + 1
    print(json.dumps({
      "labels": str(labels_path),
      "output": str(output_path),
      "rows": len(rows),
      "status_counts": status_counts,
    }, indent=2, ensure_ascii=False))
    return 0

  ReviewHandler.state = state
  server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
  url = f"http://{args.host}:{args.port}/"
  print(f"Serving lane label reviewer at {url}")
  print(f"Input:  {labels_path}")
  print(f"Output: {output_path}")
  if args.open:
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nStopping reviewer")
  finally:
    server.server_close()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
