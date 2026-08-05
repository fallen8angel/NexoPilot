#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def read(path: str) -> str:
  source = (ROOT / path).read_text(encoding="utf-8")
  ast.parse(source, filename=path)
  return source


def main() -> None:
  daemon = read("selfdrive/monitoring/dmonitoringd.py")
  policy = read("selfdrive/monitoring/policy.py")
  dm_diag = read("system/nexo_web/nexo_driver_monitoring_diagnostics.py")
  web = read("system/nexo_web/web.py")

  for token in (
    "def driver_view_demo_mode",
    'get_bool("IsDriverViewEnabled")',
    'get_bool("IsOffroad")',
    'not params.get_bool("IsOnroad")',
    "actual_vehicle_state_valid",
    "not actual_vehicle_state_valid",
    "DM.run_step(sm, demo=False)",
  ):
    require(token in daemon, f"driver-view/onroad separation missing: {token}")
  require("DM.run_step(sm, demo=demo_mode)" not in daemon,
          "valid onroad state must never inherit driver-view demo mode")

  for token in (
    "enabled = sm['selfdriveState'].enabled and sm['carState'].cruiseState.enabled",
    "if wrong_gear or not op_engaged:",
    "self._reset_awareness()",
  ):
    require(token in policy, f"actual cruise driver-monitoring gate missing: {token}")

  for token in (
    "운전자 감시 크루즈 연동 확인",
    "actual_cruise",
    "warning_allowed",
    "driverMonitoringState",
    "7000 카메라",
  ):
    require(token in dm_diag, f"8-second driver-monitoring diagnostics missing: {token}")

  require("dm_diagnostics.prepend_driver_monitoring_report" in web,
          "driver-monitoring status is not included in the 8-second report")
  for token in ("StableThreadingHTTPServer", "allow_reuse_address = True", "request_queue_size = 32", "serve_forever"):
    require(token in web, f"port 7000 stability contract missing: {token}")

  print("NEXO cruise-only driver monitoring and port 7000 diagnostics PASS")


if __name__ == "__main__":
  main()
