#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


web = text("system/nexo_web/web.py")
panda_diag = text("system/nexo_web/nexo_panda_fault_diagnostics.py")
writer = text("selfdrive/car/nexo_diagnostics.py")

for path, source in (
  ("system/nexo_web/web.py", web),
  ("system/nexo_web/nexo_panda_fault_diagnostics.py", panda_diag),
  ("selfdrive/car/nexo_diagnostics.py", writer),
):
  ast.parse(source, filename=path)

for token in (
  "Panda fault·CAN IRQ·펌웨어 진단",
  "interruptRateCan2",
  "currentFaults",
  "observedFaults",
  "faultStatus",
  "interruptLoad",
  "irq0CallRate",
  "irq1CallRate",
  "sampleDeltas",
  "Panda 펌웨어 출처",
  "trackedRepoFirmwareVersion",
  "prepend_panda_fault_report",
  "Panda fault 기계 판독 JSON",
  "이 검사는 읽기 전용",
):
  if token not in panda_diag:
    raise SystemExit(f"missing Panda fault diagnostic token: {token}")

for token in (
  "nexo_panda_fault_diagnostics",
  "prepend_panda_fault_report",
  "Panda fault 이름",
):
  if token not in web:
    raise SystemExit(f"Panda fault diagnostics not wired into web: {token}")

for token in (
  "_panda_snapshot",
  "fault_status",
  '"faults"',
  '"panda_faults"',
  'payload["pandas"]',
):
  if token not in writer:
    raise SystemExit(f"persistent Panda fault capture missing: {token}")

for forbidden in ("pub_sock(", "disable_ecu", "put_bool(", "schedule_reboot", "sendcan"):
  if forbidden in panda_diag:
    raise SystemExit(f"Panda fault diagnostics must remain read-only: {forbidden}")

print("NEXO Panda fault/IRQ/firmware diagnostics PASS")
