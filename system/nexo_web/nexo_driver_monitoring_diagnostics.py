from __future__ import annotations

import time
from pathlib import Path

from cereal import car, messaging


SELFDRIVED_CRASH_LOG = Path("/data/nexopilot/selfdrived_crash.log")


def _param_bool(core, key: str) -> bool:
  try:
    return core.Params().get_bool(key)
  except Exception:
    return False


def _enum_numeric_value(value) -> int | None:
  """Convert pycapnp enums without assuming int(enum) is supported."""
  raw = getattr(value, "raw", None)
  if raw is not None:
    try:
      return int(raw)
    except (TypeError, ValueError):
      pass

  text = str(value).strip().lower()
  names = {"none": 0, "one": 1, "two": 2, "three": 3}
  if text in names:
    return names[text]
  try:
    return int(text)
  except (TypeError, ValueError):
    return None


def _selfdrived_process(core) -> tuple[bool, str]:
  try:
    code, output = core.run_command(["ps", "-eo", "pid,args"], timeout=3)
  except Exception as error:
    return False, f"ps 확인 실패: {error}"
  if code != 0:
    return False, output
  for line in output.splitlines():
    if "selfdrive.selfdrived.selfdrived" in line:
      return True, line.strip()
  return False, ""


def _selfdrived_crash_tail() -> str:
  try:
    lines = SELFDRIVED_CRASH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-30:])
  except OSError:
    return "저장된 selfdrived crash traceback이 없습니다."


def selfdrived_runtime_report(core) -> str:
  """Report the direct runtime source of an openpilot-unavailable screen."""
  running, process_line = _selfdrived_process(core)
  services = ("carState", "selfdriveState")
  sm = messaging.SubMaster(list(services))
  counts = dict.fromkeys(services, 0)
  deadline = time.monotonic() + 1.5
  while time.monotonic() < deadline:
    sm.update(100)
    for name in services:
      if sm.updated[name]:
        counts[name] += 1

  selfdrive_ok = bool(sm.seen["selfdriveState"]) and bool(sm.alive["selfdriveState"]) and bool(sm.valid["selfdriveState"])
  carstate_ok = bool(sm.seen["carState"]) and bool(sm.alive["carState"]) and bool(sm.valid["carState"])

  if running and selfdrive_ok:
    verdict = "[정상 후보] selfdrived와 selfdriveState가 정상적으로 관측됩니다."
  elif not running:
    verdict = "[실패] selfdrived 프로세스가 꺼져 있어 selfdriveState가 사라졌습니다. openpilot unavailable의 직접 원인입니다."
  else:
    verdict = "[실패] selfdrived는 실행 중이지만 selfdriveState를 발행하지 못하고 있습니다."

  lines = [
    "============================================================",
    "selfdrived · openpilot unavailable 확인",
    "============================================================",
    f"판정: {verdict}",
    f"selfdrived process={'ON' if running else 'OFF'}" + (f" | {process_line}" if process_line else ""),
    f"carState: seen={bool(sm.seen['carState'])} alive={bool(sm.alive['carState'])} valid={bool(sm.valid['carState'])} samples={counts['carState']}",
    f"selfdriveState: seen={bool(sm.seen['selfdriveState'])} alive={bool(sm.alive['selfdriveState'])} valid={bool(sm.valid['selfdriveState'])} samples={counts['selfdriveState']}",
    "자동 복구 조건: 0km/h + 크루즈 비활성 + Panda controlsAllowed=False + P단 또는 브레이크 유지",
    "※ 주행 중에는 selfdrived를 자동 재시작하지 않습니다.",
  ]
  if not running or not selfdrive_ok or not carstate_ok:
    lines.extend(["", "[최근 selfdrived crash traceback]", _selfdrived_crash_tail()])
  return "\n".join(lines)


def driver_monitoring_report(core) -> str:
  """Return a read-only snapshot proving whether DM alerts are cruise-gated."""
  driver_view = _param_bool(core, "IsDriverViewEnabled")
  is_onroad = _param_bool(core, "IsOnroad") and not _param_bool(core, "IsOffroad")
  is_offroad = _param_bool(core, "IsOffroad") and not _param_bool(core, "IsOnroad")
  always_on = _param_bool(core, "AlwaysOnDM")

  try:
    sm = messaging.SubMaster(["carState", "selfdriveState", "driverMonitoringState"])
    deadline = 1.5
    elapsed = 0.0
    while elapsed < deadline and not all(sm.seen[name] for name in ("carState", "selfdriveState", "driverMonitoringState")):
      sm.update(100)
      elapsed += 0.1

    cs = sm["carState"]
    ss = sm["selfdriveState"]
    dm = sm["driverMonitoringState"]

    actual_cruise = bool(ss.enabled) and bool(cs.cruiseState.enabled)
    wrong_gear = cs.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low)
    warning_allowed = actual_cruise and not wrong_gear
    alert_name = str(dm.alertLevel)
    alert_level = _enum_numeric_value(dm.alertLevel)
    alert_quiet = alert_level == 0 or alert_name.lower() == "none"

    if not warning_allowed and not alert_quiet:
      verdict = "[오류 후보] 크루즈 비활성 또는 D/L 이외 기어인데 운전자 감시 경고가 남아 있습니다."
    elif not warning_allowed:
      verdict = "[정상] 크루즈 비활성 또는 D/L 이외 기어라 운전자 감시 경고가 꺼져 있습니다."
    else:
      verdict = "[정상 후보] 실제 크루즈 활성 상태라 운전자 감시 경고 타이머 사용이 허용됩니다."

    return "\n".join([
      "============================================================",
      "운전자 감시 크루즈 연동 확인",
      "============================================================",
      f"판정: {verdict}",
      f"실제 크루즈 조건: selfdriveState.enabled={bool(ss.enabled)} + carState.cruiseState.enabled={bool(cs.cruiseState.enabled)} → {actual_cruise}",
      f"기어: {cs.gearShifter} | D/L 허용={not wrong_gear}",
      f"운전자 감시 경고 허용={warning_allowed} | 현재 alertLevel={alert_name}({alert_level if alert_level is not None else '숫자 변환 불가'})",
      f"메시지 수신: carState={sm.seen['carState']} selfdriveState={sm.seen['selfdriveState']} driverMonitoringState={sm.seen['driverMonitoringState']}",
      f"운전자 화면={driver_view} | IsOnroad={is_onroad} | IsOffroad={is_offroad} | AlwaysOnDM={always_on}",
      "※ 7000 카메라가 운전자 화면을 켜도 onroad에서는 실제 크루즈 조건을 대신하지 않습니다.",
      "※ 운전자 상태 영상 분석 프로세스는 계속 실행될 수 있지만 경고 타이머는 실제 크루즈 활성 때만 동작해야 합니다.",
    ])
  except Exception as error:
    return "\n".join([
      "============================================================",
      "운전자 감시 크루즈 연동 확인",
      "============================================================",
      f"[확인 실패] driverMonitoringState 수집 실패: {error}",
      f"운전자 화면={driver_view} | IsOnroad={is_onroad} | IsOffroad={is_offroad} | AlwaysOnDM={always_on}",
    ])


def prepend_driver_monitoring_report(core, report: str) -> str:
  return selfdrived_runtime_report(core) + "\n\n" + driver_monitoring_report(core) + "\n\n" + report
