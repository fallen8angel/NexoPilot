#!/usr/bin/env python3
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


controlsd = text("selfdrive/controls/controlsd.py")
main_ui = text("selfdrive/ui/layouts/main.py")
hud = text("selfdrive/ui/onroad/hud_renderer.py")

for path, source in (
  ("selfdrive/controls/controlsd.py", controlsd),
  ("selfdrive/ui/layouts/main.py", main_ui),
  ("selfdrive/ui/onroad/hud_renderer.py", hud),
):
  ast.parse(source, filename=path)

# XPlus-style NEXO MED must start ready, remember MAIN/lateral selection through
# P/N/R, drop speed control outside D/L, and never bypass disable/gear actuation gates.
for token in (
  "self.nexo_med_lateral = self.nexo_med",
  "if not driving_gear:",
  "self.nexo_med_speed = False",
  '_onroad_event_name(e) != "wrongGear"',
  "med_actuation_allowed = self.nexo_med and self.nexo_med_lateral and driving_gear and not disable_events",
  "longitudinal_requested = longitudinal_requested and self.nexo_med_speed and driving_gear and not disable_events",
  "Two-stage CANCEL: SPEED -> MED, then MED -> OFF.",
):
  if token not in controlsd:
    raise SystemExit(f"NEXO XPlus MED contract missing: {token}")

# Startup must visibly begin on Home before normal ignition/onroad routing.
for token in (
  "STARTUP_HOME_SECONDS = 1.5",
  "self._current_mode = MainState.HOME",
  "self._startup_home_until = time.monotonic() + STARTUP_HOME_SECONDS",
  "if time.monotonic() < self._startup_home_until:",
):
  if token not in main_ui:
    raise SystemExit(f"NEXO startup Home contract missing: {token}")

# XPlus-style onroad HUD: persistent turn arrows, active EXP mark, and P/R/N/D gear.
for token in (
  "self.left_blinker = bool(car_state.leftBlinker)",
  "self.right_blinker = bool(car_state.rightBlinker)",
  "self._draw_turn_indicators(rect)",
  "self._draw_experimental_badge(button_x, button_y)",
  'text = "EXP"',
  "self.gear_text = self._gear_label(car_state.gearShifter)",
  "self._draw_gear(rect)",
):
  if token not in hud:
    raise SystemExit(f"NEXO XPlus HUD contract missing: {token}")

print("NEXO XPlus MED/startup/Home/turn/EXP/gear UI contract PASS")
