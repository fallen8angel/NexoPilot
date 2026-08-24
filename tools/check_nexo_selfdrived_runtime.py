#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import time

import cereal.messaging as messaging


SERVICES = ("carState", "selfdriveState", "driverMonitoringState", "longitudinalPlan", "carControl", "pandaStates")


def process_running(pattern: str) -> tuple[bool, str]:
  result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=3, check=False)
  if result.returncode != 0:
    return False, result.stderr.strip() or result.stdout.strip()
  for line in result.stdout.splitlines():
    if pattern in line and "check_nexo_selfdrived_runtime.py" not in line:
      return True, line.strip()
  return False, ""


def main() -> None:
  running, process_line = process_running("selfdrive.selfdrived.selfdrived")
  sm = messaging.SubMaster(list(SERVICES))
  counts = dict.fromkeys(SERVICES, 0)
  deadline = time.monotonic() + 2.0
  while time.monotonic() < deadline:
    sm.update(100)
    for service in SERVICES:
      if sm.updated[service]:
        counts[service] += 1

  print("NEXO selfdrived 런타임 점검")
  print(f"selfdrived process={'ON' if running else 'OFF'}")
  if process_line:
    print(f"process={process_line}")

  failed = False
  for service in SERVICES:
    seen = bool(sm.seen[service])
    alive = bool(sm.alive[service])
    valid = bool(sm.valid[service])
    freq_ok = bool(sm.freq_ok[service])
    print(f"{service}: seen={seen} alive={alive} valid={valid} freqOk={freq_ok} samples={counts[service]}")
    if service in ("carState", "selfdriveState") and not (seen and alive and valid):
      failed = True

  if not running:
    failed = True
    print("판정: [실패] selfdrived 프로세스가 실행 중이 아닙니다. selfdriveState 미수신의 직접 원인 후보입니다.")
  elif not bool(sm.seen["selfdriveState"]):
    failed = True
    print("판정: [실패] selfdrived는 실행 중이지만 selfdriveState가 수신되지 않습니다. 프로세스 crash/초기화 로그를 확인해야 합니다.")
  elif failed:
    print("판정: [주의] 핵심 런타임 서비스 중 유효하지 않은 항목이 있습니다.")
  else:
    print("판정: [정상 후보] selfdrived와 핵심 상태 메시지가 정상적으로 관측됩니다.")

  raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
  main()
