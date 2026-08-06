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
  owner = read("opendbc_repo/opendbc/car/nexo_session_owner.py")
  takeover = read("opendbc_repo/opendbc/car/hyundai/nexo_takeover.py")
  card = read("selfdrive/car/card.py")
  guard = read("selfdrive/car/nexo_guard.py")
  web = read("system/nexo_web/nexo_diagnostics_v2.py")

  for path, source in (
    ("radar_tracks.py", radar),
    ("interface.py", interface),
    ("nexo_session_owner.py", owner),
    ("nexo_takeover.py", takeover),
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
    "restore_allowed_unlocked",
    "current_process_owns",
    "clear_owner_if_current_unlocked",
    "RESTORE SKIP",
    "DEINIT no active NEXO takeover; duplicate restore skipped",
  ):
    require(token in interface, f"owner-aware stock SCC restore contract missing: {token}")

  for token in (
    "NEXO_SCC_OWNER",
    "NEXO_SCC_OWNER_LOCK",
    "def current_owner_token",
    "def claim_owner",
    "def current_process_owns",
    "def restore_allowed_unlocked",
    "active takeover owned by",
    "def clear_owner_if_current_unlocked",
  ):
    require(token in owner, f"takeover process ownership contract missing: {token}")

  for token in (
    "from opendbc.car.nexo_session_owner import claim_owner",
    "owner = claim_owner()",
    "takeover owner claim failed",
    '"owner": owner',
  ):
    require(token in takeover, f"takeover owner claim/trace missing: {token}")

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
  print("NEXO owner-aware SCC crash recovery and no-auto-reboot policy PASS")


if __name__ == "__main__":
  main()
