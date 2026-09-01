from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


CONFIG_PATH = Path("/data/nexopilot/experimental_speed_switch.json")
DEFAULT_SWITCH_SPEED_KPH = 20
MIN_SWITCH_SPEED_KPH = 10
MAX_SWITCH_SPEED_KPH = 100
SWITCH_SPEED_STEP_KPH = 5
HYSTERESIS_KPH = 2.0
NEXO_FINGERPRINT = "HYUNDAI_NEXO_1ST_GEN"


@dataclass(frozen=True)
class NexoExperimentalSpeedSettings:
  enabled: bool = False
  speed_kph: int = DEFAULT_SWITCH_SPEED_KPH


def is_nexo_fingerprint(fingerprint: object) -> bool:
  """Accept both capnp enum values and their string representation."""
  return getattr(fingerprint, "name", str(fingerprint)) == NEXO_FINGERPRINT


def nexo_med_phase(is_nexo: bool, cruise_available: bool, speed_control_active: bool) -> str:
  """Return the short driver-facing MED phase used by both onroad UIs."""
  if not is_nexo or not cruise_available:
    return ""
  return "SPEED" if speed_control_active else "MED"


def nexo_experimental_icon_visible(is_nexo: bool, speed_control_active: bool,
                                   actual_experimental: bool) -> bool:
  """Mici follows XPlus: its EXP icon describes active MED speed control only."""
  if is_nexo:
    return bool(speed_control_active and actual_experimental)
  return bool(actual_experimental)


def normalize_speed_kph(value: object) -> int:
  try:
    speed = int(float(value))
  except (TypeError, ValueError):
    speed = DEFAULT_SWITCH_SPEED_KPH
  speed = max(MIN_SWITCH_SPEED_KPH, min(MAX_SWITCH_SPEED_KPH, speed))
  return ((speed + SWITCH_SPEED_STEP_KPH // 2) // SWITCH_SPEED_STEP_KPH) * SWITCH_SPEED_STEP_KPH


def load_settings() -> NexoExperimentalSpeedSettings:
  try:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
      raise ValueError("설정 형식 오류")
    return NexoExperimentalSpeedSettings(
      enabled=bool(raw.get("enabled", False)),
      speed_kph=normalize_speed_kph(raw.get("speedKph", DEFAULT_SWITCH_SPEED_KPH)),
    )
  except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
    return NexoExperimentalSpeedSettings()


def save_settings(enabled: bool, speed_kph: int) -> NexoExperimentalSpeedSettings:
  settings = NexoExperimentalSpeedSettings(bool(enabled), normalize_speed_kph(speed_kph))
  CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
  temporary = CONFIG_PATH.with_suffix(".tmp")
  temporary.write_text(json.dumps({
    "enabled": settings.enabled,
    "speedKph": settings.speed_kph,
  }, ensure_ascii=False), encoding="utf-8")
  temporary.replace(CONFIG_PATH)
  return settings


class NexoExperimentalModeController:
  """XPlus-style speed-gated Experimental Mode with a configurable threshold."""

  def __init__(self) -> None:
    self.speed_control_active = False
    self.experimental = False

  def update(self, enabled: bool, speed_control_active: bool, cruise_available: bool,
             vehicle_speed_kph: float, manual_mode: bool, switch_speed_kph: int) -> bool:
    if not enabled:
      self.speed_control_active = False
      self.experimental = bool(manual_mode)
      return self.experimental

    if not speed_control_active:
      self.speed_control_active = False
      # XPlus keeps Experimental Mode ready in MED wait before SET/RES.
      self.experimental = bool(cruise_available) or bool(manual_mode)
      return self.experimental

    speed_kph = max(0.0, float(vehicle_speed_kph))
    switch_kph = float(normalize_speed_kph(switch_speed_kph))
    experimental_below = max(0.0, switch_kph - HYSTERESIS_KPH)
    normal_above = switch_kph + HYSTERESIS_KPH

    if not self.speed_control_active:
      self.experimental = speed_kph <= switch_kph
    elif self.experimental and speed_kph >= normal_above:
      self.experimental = False
    elif not self.experimental and speed_kph <= experimental_below:
      self.experimental = True

    self.speed_control_active = True
    return self.experimental
