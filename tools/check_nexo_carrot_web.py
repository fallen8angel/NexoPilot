#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "system/nexo_web/web.py"
UI = ROOT / "system/nexo_web/web_carrot_ui.py"
HUD = ROOT / "system/nexo_web/web_hud_ui.py"
REMOTE = ROOT / "system/nexo_web/web_remote_ui.py"
PARAMS = ROOT / "common/params_keys.h"

web = WEB.read_text(encoding="utf-8")
ui = UI.read_text(encoding="utf-8")
hud = HUD.read_text(encoding="utf-8")
remote = REMOTE.read_text(encoding="utf-8")
params = PARAMS.read_text(encoding="utf-8")

ast.parse(web, filename=str(WEB))
ast.parse(ui, filename=str(UI))
ast.parse(hud, filename=str(HUD))
ast.parse(remote, filename=str(REMOTE))

required_ui = (
  "CARROT-STYLE SETTINGS",
  "OpenpilotEnabledToggle",
  "AlphaLongitudinalEnabled",
  "ExperimentalMode",
  "DisengageOnAccelerator",
  "IsLdwEnabled",
  "AlwaysOnDM",
  "RecordFront",
  "IsMetric",
  "ShowDebugInfo",
  "LongitudinalPersonality",
  "주행 금지",
  "Panda Safety",
  "Panda 펌웨어",
  "stationary_gate",
  "parkingBrake",
  "cruiseState.enabled",
  "selfdriveState",
  "/settings",
  "/diagnostics",
  "/system",
)
for token in required_ui:
  if token not in ui:
    raise SystemExit(f"Carrot-style web UI missing token: {token}")

required_hud = (
  'HUD_PARAM = "NexoHudEnabled"',
  "carState",
  "selfdriveState",
  "radarState",
  "pandaStates",
  "model_snapshot_json",
  "leadOne",
  "hud_status_json",
  "NEXO HUD",
  "HUD 활성화",
  "CAN 송신·Panda 설정·차량 제어는 하지 않습니다.",
  "전체화면",
)
for token in required_hud:
  if token not in hud:
    raise SystemExit(f"HUD web UI missing token: {token}")

# The HUD is display-only. It may read vehicle state and store its own enable
# preference, but it must never write Panda/CAN or vehicle-control state.
for forbidden_hud in (
  "sendcan",
  "can_send",
  "set_safety_model",
  "controlsAllowed =",
  "AlphaLongitudinalEnabled",
  "ExperimentalMode",
  "EnableRadarTracks",
  "DoReboot",
):
  if forbidden_hud in hud:
    raise SystemExit(f"HUD unexpectedly contains a vehicle-control action: {forbidden_hud}")

required_remote = (
  '("hud", "/hud", "HUD")',
  '("remote", "/remote", "원격")',
  "grid-template-columns:repeat(7,1fr)",
  "grid-template-rows:repeat(7,minmax(0,1fr))",
  "미지원 · 업데이트 예정",
  "원격 제어",
  "원격 주차·이동",
  "원격 카메라·센서 연동",
  "현재 이 화면에는 차량을 움직이거나 Panda/CAN 명령을 보내는 기능이 없습니다.",
)
for token in required_remote:
  if token not in remote:
    raise SystemExit(f"Remote/HUD navigation UI missing token: {token}")

# The remote placeholder must remain display-only until a separately reviewed
# remote control implementation exists. No POST forms, Panda writes or CAN sending.
for forbidden_remote in (
  '<form',
  'method="post"',
  'sendcan',
  'can_send',
  'set_safety_model',
  'controlsAllowed =',
  'Params().put',
):
  if forbidden_remote in remote:
    raise SystemExit(f"Remote placeholder unexpectedly contains a control action: {forbidden_remote}")

required_web = (
  "web_carrot_ui",
  "web_hud_ui",
  "web_remote_ui",
  "remote_ui.install(carrot_ui)",
  "CarrotStyleHandler",
  "/api/status",
  'parsed.path == "/api/hud"',
  "hud_ui.hud_status_json(core)",
  'parsed.path == "/hud"',
  "hud_ui.hud_page(core, message)",
  'parsed.path == "/hud/toggle"',
  "hud_ui.HUD_PARAM",
  "/personality",
  'parsed.path == "/remote"',
  "remote_ui.remote_page(core)",
  "stationary_gate",
  "core.TOGGLES = list(carrot_ui.TOGGLES)",
  'server_version = "NexoPilotWeb/7.7"',
)
for token in required_web:
  if token not in web:
    raise SystemExit(f"Carrot-style web wiring missing token: {token}")

# Every writable boolean item exposed by the web UI must be a registered Param.
for key in (
  "OpenpilotEnabledToggle", "AlphaLongitudinalEnabled", "ExperimentalMode",
  "DisengageOnAccelerator", "IsLdwEnabled", "AlwaysOnDM", "RecordFront",
  "IsMetric", "ShowDebugInfo", "LongitudinalPersonality", "NexoHudEnabled",
):
  if f'{{"{key}",' not in params:
    raise SystemExit(f"web setting is not registered in Params: {key}")

# These are deliberately automatic or safety-owned on NEXO and must never be
# presented as user-writable web toggles.
for forbidden_toggle in (
  '("EnableRadarTracks",',
  '("NEXO_DYNAMIC_SCC",',
  '("safetyParam",',
  '("controlsAllowed",',
):
  if forbidden_toggle in ui:
    raise SystemExit(f"unsafe/automatic setting exposed as web toggle: {forbidden_toggle}")

# Port 7000 must not weaken the Panda interrupt-rate protection or clear faults.
for forbidden_action in (
  "interruptRateCan2",  # dashboard/HUD may display faults dynamically; do not special-case this name
  "FAULT_INTERRUPT_RATE_CAN_2",
  "CAN_INTERRUPT_RATE =",
  "set_safety_model",
  "fault_recovered",
  "fault_occurred",
):
  if forbidden_action in ui or forbidden_action in web or forbidden_action in remote or forbidden_action in hud:
    raise SystemExit(f"web UI must not modify/special-case Panda safety: {forbidden_action}")

print("NEXO Carrot-style port 7000 UI PASS (read-only HUD + Remote placeholder included)")