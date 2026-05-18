import time


STALE_TIMEOUT = 0.7  # seconds
MAX_ACCEPTED_RTT_MS = 450
MIN_CONSECUTIVE_FRAMES = 3
MIN_RED_CONFIDENCE = 0.45


def _signal_state(name):
  text = str(name or "").lower().replace("-", "_").replace(" ", "_")
  if any(word in text for word in ("green", "go", "left_green", "arrow_green")):
    return "green"
  if any(word in text for word in ("yellow", "amber", "orange")):
    return "yellow"
  if any(word in text for word in ("red", "stop")):
    return "red"
  return ""


class YoloControlProcessor:
  def __init__(self):
    self.last_frame_id = None
    self.last_update_ts = 0.0
    self.red_count = 0
    self.green_count = 0
    self.red_active = False

  def process(self, yolo_data, v_ego, v_cruise):
    """Process traffic-light YOLO detections.

    The phone model is expected to be traffic-light-specific, so class ids are
    intentionally not treated as COCO ids here. Only explicit red/green/yellow
    state from the class name or hasRedLight/hasGreenLight summary is used.
    """
    now = time.monotonic()
    if yolo_data is None:
      self._decay_counts()
      return v_cruise, False

    frame_id = getattr(yolo_data, "frameId", 0)
    if frame_id == self.last_frame_id and now - self.last_update_ts <= STALE_TIMEOUT:
      return (0.0, True) if self.red_active else (v_cruise, False)

    if now - self.last_update_ts > STALE_TIMEOUT:
      self._decay_counts()

    self.last_frame_id = frame_id
    self.last_update_ts = now

    rtt_ms = int(getattr(yolo_data, "roundTripMs", 0) or 0)
    if rtt_ms > MAX_ACCEPTED_RTT_MS:
      self._decay_counts()
      return (0.0, True) if self.red_active else (v_cruise, False)

    red_seen = bool(getattr(yolo_data, "hasRedLight", False))
    green_seen = bool(getattr(yolo_data, "hasGreenLight", False))

    detections = getattr(yolo_data, "detections", None)
    if detections is not None:
      for det in detections:
        conf = float(getattr(det, "confidence", 0.0))
        if conf < MIN_RED_CONFIDENCE:
          continue
        state = _signal_state(getattr(det, "className", ""))
        red_seen = red_seen or state == "red"
        green_seen = green_seen or state == "green"

    if red_seen:
      self.red_count += 1
      self.green_count = 0
    elif green_seen:
      self.green_count += 1
      self.red_count = max(0, self.red_count - 2)
    else:
      self._decay_counts()

    self.red_active = self.red_count >= MIN_CONSECUTIVE_FRAMES
    if self.green_count >= 2:
      self.red_active = False
      self.red_count = 0

    return (0.0, True) if self.red_active else (v_cruise, False)

  def _decay_counts(self):
    self.red_count = max(0, self.red_count - 1)
    self.green_count = max(0, self.green_count - 1)
    if self.red_count == 0:
      self.red_active = False
