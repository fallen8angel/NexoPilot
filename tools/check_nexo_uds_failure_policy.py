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
  interface = read("opendbc_repo/opendbc/car/hyundai/interface.py")
  card = read("selfdrive/car/card.py")
  guard = read("selfdrive/car/nexo_guard.py")
  web = read("system/nexo_web/nexo_diagnostics_v2.py")

  for path, source in (
    ("radar_tracks.py", radar),
    ("interface.py", interface),
    ("card.py", card),
    ("nexo_guard.py", guard),
    ("nexo_diagnostics_v2.py", web),
  ):
    ast.parse(source, filename=path)

  for token in ("def _format_isotp_address", "def _render_isotp_result", "_render_isotp_result(result)"):
    require(token in radar, f"tuple-safe UDS logging missing: {token}")
  require('f"0x{address:X}' not in radar, "raw AddrType tuple formatting must not remain")

  for token in (
    "NEXO_SCC_TAKEOVER_MARKER",
    "def nexo_stock_scc_restore_pending",
    "def restore_nexo_stock_scc_communication",
    '_set_nexo_takeover_marker("stock_scc_disabled")',
    'except BaseException as error:',
    'reason=f"long init exception: {type(error).__name__}"',
    'CP.openpilotLongitudinalControl or nexo_stock_scc_restore_pending()',
  ):
    require(token in interface, f"stock SCC restore contract missing: {token}")

  recovery = card[card.index("def recover_nexo_stock_cruise"):card.index("def can_comm_callbacks")]
  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):
    require(forbidden not in recovery, f"automatic setting/reboot mutation remains: {forbidden}")

  for token in (
    '_safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)',
    '_safe_nexo_param_remove(self.params, "NexoLongitudinalFailure")',
    "self.nexo_long_init_failed",
    "self._handle_nexo_long_failure(error)",
    "self._restore_nexo_stock_scc_if_pending",
    "card startup stale takeover",
    "card thread exit",
    "uncaught card exception",
    "record_nexo_card_crash",
    "record_nexo_long_success",
    "NexoCardHeartbeatMono",
  ):
    require(token in card, f"current-session recovery missing: {token}")

  failure_handler = card[card.index("def _handle_nexo_long_failure"):card.index("def state_update")]
  require("self.CI.init(" not in failure_handler, "failure handler must never re-run radar initialization")
  require("self.CI.deinit(self.CP, *self.can_callbacks)" in card, "restore-only deinit path missing")
  require("def disarm(self)" in guard, "runtime guard disarm support missing")
  require("순정 SCC 복구 대기 마커" in web, "restore marker is not visible in port 7000")
  require("NEXO_SCC_RESTORE_LOG" in web, "restore attempts are not visible in port 7000")
  require("자동 재부팅 없이 저장됩니다" in web, "no-auto-reboot policy is not visible in diagnostics")
  print("NEXO stock SCC crash recovery and no-auto-reboot policy PASS")


if __name__ == "__main__":
  main()
