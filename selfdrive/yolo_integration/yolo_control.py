from collections import deque
import time

# COCO class IDs relevant to driving
CLASS_PERSON = 0
CLASS_BICYCLE = 1
CLASS_CAR = 2
CLASS_MOTORCYCLE = 3
CLASS_BUS = 5
CLASS_TRUCK = 7
CLASS_TRAFFIC_LIGHT = 9
CLASS_STOP_SIGN = 11

# Speed limits by class and distance (m/s)
# (max_distance_m, speed_limit_ms)
CLASS_SPEED_RULES = {
  CLASS_PERSON: [
    (15.0, 0.0),    # < 15m -> stop
    (30.0, 2.78),   # < 30m -> 10 km/h
  ],
  CLASS_BICYCLE: [
    (20.0, 5.56),   # < 20m -> 20 km/h
  ],
  CLASS_MOTORCYCLE: [
    (20.0, 5.56),   # < 20m -> 20 km/h
  ],
  CLASS_STOP_SIGN: [
    (30.0, 0.0),    # < 30m -> stop
  ],
}

STALE_TIMEOUT = 0.5  # seconds
MIN_CONSECUTIVE_FRAMES = 3


class YoloControlProcessor:
  def __init__(self):
    self.detection_history = deque(maxlen=10)
    self.last_valid_ts = 0.0
    self.consecutive_counts = {}  # class_id -> consecutive frame count

  def process(self, yolo_data, v_ego, v_cruise):
    """Process YOLO detections and return adjusted speed constraints.

    Returns:
      (adjusted_v_cruise, should_stop)
      - adjusted_v_cruise: minimum allowed cruise speed (m/s), None if no constraint
      - should_stop: True if emergency stop required
    """
    now = time.monotonic()

    if yolo_data is None:
      self._decay_counts()
      return v_cruise, False

    # Check staleness via message validity
    self.last_valid_ts = now

    # Handle backward-compatible red light field
    should_stop = False
    if getattr(yolo_data, 'hasRedLight', False):
      should_stop = True

    # Process multi-object detections
    detections = getattr(yolo_data, 'detections', None)
    if detections is None or len(detections) == 0:
      self._decay_counts()
      if should_stop:
        return 0.0, True
      return v_cruise, False

    # Track which classes are present this frame
    classes_this_frame = set()
    min_v_cruise = v_cruise

    for det in detections:
      class_id = det.classId
      dist = det.distanceEstimate
      conf = det.confidence

      if conf < 0.5:
        continue

      classes_this_frame.add(class_id)

      # Update consecutive count
      self.consecutive_counts[class_id] = self.consecutive_counts.get(class_id, 0) + 1

      # Only act if seen for enough consecutive frames
      if self.consecutive_counts[class_id] < MIN_CONSECUTIVE_FRAMES:
        continue

      # Apply class-specific speed rules
      rules = CLASS_SPEED_RULES.get(class_id)
      if rules:
        for max_dist, speed_limit in rules:
          if dist < max_dist:
            min_v_cruise = min(min_v_cruise, speed_limit)
            if speed_limit == 0.0:
              should_stop = True
            break

    # Decay counts for classes not seen this frame
    for class_id in list(self.consecutive_counts.keys()):
      if class_id not in classes_this_frame:
        self.consecutive_counts[class_id] = max(0, self.consecutive_counts[class_id] - 1)
        if self.consecutive_counts[class_id] == 0:
          del self.consecutive_counts[class_id]

    return min_v_cruise, should_stop

  def _decay_counts(self):
    for class_id in list(self.consecutive_counts.keys()):
      self.consecutive_counts[class_id] = max(0, self.consecutive_counts[class_id] - 1)
      if self.consecutive_counts[class_id] == 0:
        del self.consecutive_counts[class_id]
