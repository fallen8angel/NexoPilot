#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(value: bool, message: str) -> None:
  if not value:
    raise AssertionError(message)


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
  guard = text("selfdrive/car/nexo_guard.py")
  card = text("selfdrive/car/card.py")
  writer = text("selfdrive/car/nexo_diagnostics.py")
  web = text("system/nexo_web/nexo_diagnostics_v2.py")
  entry = text("system/nexo_web/web.py")
  radar = text("opendbc_repo/opendbc/car/hyundai/radar_tracks.py")
  interface = text("opendbc_repo/opendbc/car/hyundai/interface.py")

  for token in ("NEXO_FCA_ADDRS", "NEXO_CAN_HISTORY_S = 5.0", "fault_snapshot", "recent_can"):
    require(token in guard, f"runtime diagnostic guard missing: {token}")
  for token in ("NEXO_LAST_FAULT_LOG", "record_nexo_fault_snapshot", "safety_rx_checks_invalid", "radar_errors"):
    require(token in writer, f"persistent fault capture missing: {token}")
  for token in ("record_nexo_fault_snapshot", "selfdriveState", "radarState"):
    require(token in card, f"card diagnostic connection missing: {token}")
  for token in ("[SCC/FCA 분리 자동 판정]", "[sendcan 요청 → Panda 결과]",
                "[주요 SCC/FCA 신호 DBC 해석]", "last_fault_output",
                "순정 FCA11/FCA12 수신은 정상"):
    require(token in web, f"web diagnostics missing: {token}")
  require("diagnostics_v2.longitudinal_blackbox_output" in entry, "web v2 blackbox not wired")
  require("diagnostics_v2.enhance_diagnostic_page" in entry, "last fault card not wired")
  for token in ("UDS TX", "UDS RX", "UDS ERROR", "request.hex(' ')"):
    require(token in radar, f"radar UDS diagnostics missing: {token}")
  require("def _trace_nexo_long_init" in interface, "NEXO init trace helper missing")
  require("elapsed_ms" in interface, "disable ECU timing trace missing")
  print("NEXO diagnostics v2 PASS")


if __name__ == "__main__":
  main()
