#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "system/nexo_web/web.py"
UI = ROOT / "system/nexo_web/web_carrot_ui.py"
REMOTE = ROOT / "system/nexo_web/web_remote_ui.py"
PARAMS = ROOT / "common/params_keys.h"

web = WEB.read_text(encoding="utf-8")
ui = UI.read_text(encoding="utf-8")
remote = REMOTE.read_text(encoding="utf-8")
params = PARAMS.read_text(encoding="utf-8")

ast.parse(web, filename=str(WEB))
ast.parse(ui, filename=str(UI))
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

required_remote = (
  '("remote", "/remote", "원격")',
  "grid-template-columns:repeat(6,1fr)",
  "미지원 · 업데이트 예정",
  "원격 제어",
  "원격 주차·이동",
  "원격 카메라·센서 연동",
  "현재 이 화면에는 차량을 움직이거나 Panda/CAN 명령을 보내는 기능이 없습니다.",
)
for token in required_remote:
  if token not in remote:
    raise SystemExit(f"Remote placeholder UI missing token: {token}")

# The placeholder must remain display-only until a separately reviewed remote
# control implementation exists. No POST forms, Panda writes or CAN sending.
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
  "web_remote_ui",
  "remote_ui.install(carrot_ui)",
  "CarrotStyleHandler",
  "/api/status",
  "/personality",
  'parsed.path == "/remote"',
  "remote_ui.remote_page(core)",
  "stationary_gate",
  "core.TOGGLES = list(carrot_ui.TOGGLES)",
  'server_version = "NexoPilotWeb/7.8"',
)
for token in required_web:
  if token not in web:
    raise SystemExit(f"Carrot-style web wiring missing token: {token}")

# Every writable boolean item exposed by the web UI must be a registered Param.
for key in (
  "OpenpilotEnabledToggle", "AlphaLongitudinalEnabled", "ExperimentalMode",
  "DisengageOnAccelerator", "IsLdwEnabled", "AlwaysOnDM", "RecordFront",
  "IsMetric", "ShowDebugInfo", "LongitudinalPersonality",
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
  "interruptRateCan2",  # dashboard may display faults dynamically; do not special-case this name
  "FAULT_INTERRUPT_RATE_CAN_2",
  "CAN_INTERRUPT_RATE =",
  "set_safety_model",
  "fault_recovered",
  "fault_occurred",
):
  if forbidden_action in ui or forbidden_action in web or forbidden_action in remote:
    raise SystemExit(f"web UI must not modify/special-case Panda safety: {forbidden_action}")

print("NEXO Carrot-style port 7000 UI PASS (Remote placeholder included)")
