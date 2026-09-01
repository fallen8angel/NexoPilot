#!/usr/bin/env python3
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


controlsd = text("selfdrive/controls/controlsd.py")
selfdrived = text("selfdrive/selfdrived/selfdrived.py")
nexo_experimental = text("selfdrive/selfdrived/nexo_experimental_mode.py")
nexo_med = text("opendbc_repo/opendbc/car/hyundai/nexo_med.py")
main_ui = text("selfdrive/ui/layouts/main.py")
hud = text("selfdrive/ui/onroad/hud_renderer.py")
onroad_init = text("selfdrive/ui/onroad/__init__.py")
exp_button = text("selfdrive/ui/onroad/exp_button.py")
mici_view = text("selfdrive/ui/mici/onroad/augmented_road_view.py")
mici_hud = text("selfdrive/ui/mici/onroad/hud_renderer.py")
web_ui = text("system/nexo_web/web_carrot_ui.py")

for path, source in (
  ("selfdrive/controls/controlsd.py", controlsd),
  ("selfdrive/selfdrived/selfdrived.py", selfdrived),
  ("selfdrive/selfdrived/nexo_experimental_mode.py", nexo_experimental),
  ("opendbc_repo/opendbc/car/hyundai/nexo_med.py", nexo_med),
  ("selfdrive/ui/layouts/main.py", main_ui),
  ("selfdrive/ui/onroad/hud_renderer.py", hud),
  ("selfdrive/ui/onroad/__init__.py", onroad_init),
  ("selfdrive/ui/onroad/exp_button.py", exp_button),
  ("selfdrive/ui/mici/onroad/augmented_road_view.py", mici_view),
  ("selfdrive/ui/mici/onroad/hud_renderer.py", mici_hud),
  ("system/nexo_web/web_carrot_ui.py", web_ui),
):
  ast.parse(source, filename=path)

# NEXO MED state is owned once from raw CLU11 buttons. It must start OFF, enter
# MED on MODE, add speed control on SET/RES, retain selection across brake/P/N/R,
# require fresh SET/RES after reverse, and implement two-stage CANCEL without
# leaking the first cancel to selfdrived.
for token in (
  "self.available = False",
  "self.enabled = False",
  "main_pressed = self.main_armed and raw_main != 0 and self.prev_raw_main == 0",
  "self.enable_pulse = driving_gear",
  "if not driving_gear:",
  "self.enabled = False",
  "self.reverse_reengage_required = True",
  "driving_gear and not self.prev_driving_gear and not self.reverse_reengage_required",
  "self.reverse_reengage_required = False",
  "self.suppress_cancel_until_release = True",
  "car_state.cruiseState.enabled = bool(self.available and self.enabled)",
):
  if token not in nexo_med:
    raise SystemExit(f"NEXO XPlus MED contract missing: {token}")

for token in (
  "med_selected = self.nexo_med and CS.cruiseState.available",
  "med_speed_control = med_selected and CS.cruiseState.enabled",
  '_onroad_event_name(e) != "wrongGear"',
  "med_selected and selfdrive_enabled and not self.nexo_med_rearm_required",
  "longitudinal_requested = med_actuation_allowed and med_speed_control",
):
  if token not in controlsd:
    raise SystemExit(f"NEXO MED actuation gate missing: {token}")

# The web speed switch must drive the published runtime mode, not merely save a
# setting file that no onroad process reads.
for token in (
  "NexoExperimentalModeController",
  "load_nexo_experimental_speed_settings()",
  "self.update_experimental_mode(CS)",
  "speed_control_active=bool(CS.cruiseState.enabled)",
  "cruise_available=bool(CS.cruiseState.available)",
):
  if token not in selfdrived:
    raise SystemExit(f"NEXO runtime Experimental Mode switch missing: {token}")

for token in (
  'return "SPEED" if speed_control_active else "MED"',
  "return bool(speed_control_active and actual_experimental)",
):
  if token not in nexo_experimental:
    raise SystemExit(f"NEXO MED UI state helper missing: {token}")

for token in (
  "gear_box_width=30",
  "gear_box_height=36",
  "gear=28",
  "cruise_gap=24",
):
  if token not in onroad_init:
    raise SystemExit(f"NEXO compact gear HUD contract missing: {token}")

for token in (
  'self._draw_current_speed(rect)',
):
  if token not in hud:
    raise SystemExit(f"NEXO persistent speed HUD contract missing: {token}")

for token in (
  "car_state.steeringAngleDeg",
  "selfdrive_state.enabled or selfdrive_state.active or car_state.cruiseState.enabled",
  "self._active_green if self._cruise_active else self._white_color",
  "rl.draw_texture_pro",
  "-self._steering_angle_deg",
):
  if token not in exp_button:
    raise SystemExit(f"NEXO rotating cruise wheel contract missing: {token}")

for token in (
  "def driver_monitoring_position",
  "wheel_bounds = self._hud_renderer.steering_wheel_bounds(self._content_rect)",
  "self._driver_state_renderer.BASE_SIZE",
):
  if token not in mici_view:
    raise SystemExit(f"NEXO Mici DMoji layout contract missing: {token}")

for token in (
  "def steering_wheel_bounds",
  "wheel_bounds = self.steering_wheel_bounds(rect)",
):
  if token not in mici_hud:
    raise SystemExit(f"NEXO Mici wheel layout contract missing: {token}")

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
  "self._draw_med_badge(button_x, button_y)",
):
  if token not in hud:
    raise SystemExit(f"NEXO XPlus HUD contract missing: {token}")

for token in (
  "nexo_experimental_icon_visible",
  "self._draw_med_phase(rect)",
):
  if token not in mici_hud:
    raise SystemExit(f"NEXO Mici MED/EXP HUD contract missing: {token}")

for token in (
  "MED 모드 사용 방법",
  "브레이크 / CANCEL",
  "후진 안전 경계",
  "MED 속도 제어",
):
  if token not in web_ui:
    raise SystemExit(f"NEXO MED web guide/status missing: {token}")

print("NEXO MED/runtime/startup/Home/turn/EXP/gear/web UI contract PASS")
