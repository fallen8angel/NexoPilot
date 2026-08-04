#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def main() -> None:
  radar = read("opendbc_repo/opendbc/car/hyundai/radar_tracks.py")
  card = read("selfdrive/car/card.py")
  guard = read("selfdrive/car/nexo_guard.py")
  web = read("system/nexo_web/nexo_diagnostics_v2.py")

  for path, source in (
    ("radar_tracks.py", radar),
    ("card.py", card),
    ("nexo_guard.py", guard),
    ("nexo_diagnostics_v2.py", web),
  ):
    ast.parse(source, filename=path)

  for token in ("def _format_isotp_address", "def _render_isotp_result", "_render_isotp_result(result)"):
    require(token in radar, f"tuple-safe UDS logging missing: {token}")
  require('f"0x{address:X}' not in radar, "raw AddrType tuple formatting must not remain")

  recovery = card[card.index("def recover_nexo_stock_cruise"):card.index("def can_comm_callbacks")]
  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):
    require(forbidden not in recovery, f"automatic setting/reboot mutation remains: {forbidden}")
  for token in (
    'params.put("NexoLongitudinalFailure", reason, block=True)',
    "self.nexo_long_init_failed",
    "self._handle_nexo_long_failure(error)",
    "record_nexo_card_crash",
    "record_nexo_long_success",
    "NexoCardHeartbeatMono",
    "if self.nexo_long_init_failed:",
    "self._update_nexo_heartbeat()",
  ):
    require(token in card, f"current-session failure latch missing: {token}")

  require("self.CI.deinit(self.CP, *self.can_callbacks)" not in card,
          "card failure handler must not re-enter the radar UDS sequence")
  require("def disarm(self)" in guard, "runtime guard disarm support missing")
  require("마지막 롱컨 실패 기록" in web, "diagnostics wording still claims automatic recovery")
  require("자동 재부팅 없이 저장됩니다" in web, "no-auto-reboot policy is not visible in diagnostics")
  print("NEXO tuple-safe UDS logging and no-auto-reboot policy PASS")


if __name__ == "__main__":
  main()
