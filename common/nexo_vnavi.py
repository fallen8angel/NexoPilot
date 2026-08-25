import math
import os
import time


VNAVI_STATE_PATH = "/dev/shm/nexopilot_vnavi"
VNAVI_STATE_MAX_AGE = 0.75
VNAVI_VIRTUAL_DISTANCE_FACTOR = 6.0
VNAVI_SAFE_TIME = 6.0
VNAVI_DECEL_RATE = 1.2


def write_vnavi_state(active: bool, speed_limit_kph: float, distance_m: float) -> None:
  """Publish stock-navigation state through tmpfs without touching persistent Params."""
  tmp_path = f"{VNAVI_STATE_PATH}.{os.getpid()}.tmp"
  try:
    with open(tmp_path, "w", encoding="utf-8") as f:
      f.write(f"{time.monotonic():.3f},{1 if active else 0},{float(speed_limit_kph):.1f},{max(0.0, float(distance_m)):.1f}\n")
    os.replace(tmp_path, VNAVI_STATE_PATH)
  except OSError:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass


def read_vnavi_state(max_age: float = VNAVI_STATE_MAX_AGE):
  """Return (active, speed_limit_kph, distance_m) or None when stale/unavailable."""
  try:
    with open(VNAVI_STATE_PATH, encoding="utf-8") as f:
      parts = f.read(128).strip().split(",")
    if len(parts) != 4:
      return None
    stamp = float(parts[0])
    age = time.monotonic() - stamp
    if age < 0.0 or age > max_age:
      return None
    active = int(parts[1]) != 0
    speed_limit_kph = float(parts[2])
    distance_m = max(0.0, float(parts[3]))
    return active, speed_limit_kph, distance_m
  except (OSError, ValueError):
    return None


def calculate_vnavi_target_speed(distance_m: float, speed_limit_kph: float,
                                  safe_time: float = VNAVI_SAFE_TIME,
                                  decel_rate: float = VNAVI_DECEL_RATE) -> float:
  """Carrot-style distance-based target speed for a stock-navigation camera."""
  if speed_limit_kph <= 0.0 or distance_m < 0.0:
    return 250.0
  safe_speed = speed_limit_kph / 3.6
  safe_dist = safe_speed * max(0.0, safe_time)
  decel_dist = max(0.0, distance_m - safe_dist)
  target_mps = math.sqrt(max(0.0, safe_speed * safe_speed + 2.0 * max(0.0, decel_rate) * decel_dist))
  return max(speed_limit_kph, min(250.0, target_mps * 3.6))
