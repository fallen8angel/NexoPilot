#!/usr/bin/env python3
"""Static NEXO integration preflight for the current openpilot tree."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def parse_python_files() -> None:
  paths = (
    "opendbc_repo/opendbc/car/hyundai/values.py",
    "opendbc_repo/opendbc/car/hyundai/carstate.py",
    "opendbc_repo/opendbc/car/hyundai/carcontroller.py",
    "opendbc_repo/opendbc/car/hyundai/hyundaican.py",
    "opendbc_repo/opendbc/car/hyundai/interface.py",
    "opendbc_repo/opendbc/car/hyundai/radar_interface.py",
    "opendbc_repo/opendbc/car/hyundai/radar_tracks.py",
    "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",
    "selfdrive/car/nexo_guard.py",
    "selfdrive/car/tests/test_nexo_guard.py",
    "selfdrive/car/card.py",
    "selfdrive/controls/controlsd.py",
    "selfdrive/controls/lib/longitudinal_planner.py",
    "system/nexo_web/web.py",
  )
  for path in paths:
    ast.parse(read(path), filename=path)
    print(f"syntax OK: {path}")


def validate_interface() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/interface.py")
  required = (
    "CAR.HYUNDAI_NEXO_1ST_GEN",
    "HyundaiFlags.FCEV",
    "HyundaiSafetyFlags.FCEV_GAS",
    "ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable",
    "disable_ecu(can_recv, can_send",
    "enable_radar_tracks(can_recv, can_send",
    "NEXOdriveAI long init",
  )
  for token in required:
    require(token in source, f"interface integration missing: {token}")


def validate_hyundaican() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/hyundaican.py")
  fn_start = source.index("def create_acc_commands")
  fn_end = source.index("def create_acc_opt", fn_start)
  fn_source = source[fn_start:fn_end]

  for token in (
    '"MainMode_ACC"',
    '"SCCInfoDisplay"',
    '"ACCFailInfo"',
    '"TakeOverReq"',
    '"CR_VSM_ChkSum"',
    '"CR_VSM_Alive"',
    '"JerkUpperLimit"',
    '"JerkLowerLimit"',
    '"ObjGap"',
  ):
    require(token in fn_source, f"SCC command field missing: {token}")

  require("if use_fca and not is_nexo" in fn_source,
          "NEXO must preserve the stock FCA stream")


def validate_controller() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/carcontroller.py")
  required = (
    "make_tester_present_msg",
    "self.CP.openpilotLongitudinalControl",
    "hyundaican.create_acc_commands",
    "hyundaican.create_acc_opt",
    "hyundaican.create_frt_radar_opt",
  )
  for token in required:
    require(token in source, f"controller integration missing: {token}")


def validate_recovery() -> None:
  source = read("selfdrive/car/card.py")
  required = (
    "self.nexo_long_init_failed",
    "self._handle_nexo_long_failure(error)",
    "record_nexo_card_crash",
    "record_nexo_long_success",
    "NexoCardHeartbeatMono",
    "NexoStockSccRuntimeGuard",
    "self.nexo_stock_scc_guard.arm()",
    "self.nexo_stock_scc_guard.disarm()",
    "self.nexo_stock_scc_guard.observe(can_list)",
  )
  for token in required:
    require(token in source, f"NEXO failure latch missing: {token}")

  require("self.CI.deinit(self.CP, *self.can_callbacks)" not in source,
          "card failure latch must not re-enter NEXO radar diagnostics")

  recovery = source[source.index("def recover_nexo_stock_cruise"):source.index("def can_comm_callbacks")]
  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):
    require(forbidden not in recovery, f"NEXO failure policy must not change settings or reboot: {forbidden}")


def validate_runtime_guard() -> None:
  source = read("selfdrive/car/nexo_guard.py")
  required = (
    "NEXO_STOCK_SCC_ADDRS",
    "NEXO_STOCK_SCC_SOURCE = 0",
    "class NexoStockSccRuntimeGuard",
    'getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE',
    "len(self._detections) < self.min_frames",
    "def disarm(self)",
  )
  for token in required:
    require(token in source, f"runtime SCC guard missing: {token}")


def validate_safety() -> None:
  source = read("opendbc_repo/opendbc/safety/modes/hyundai.h")
  require("HYUNDAI_LONG_COMMON_TX_MSGS" in source, "longitudinal TX allowlist missing")
  require("HYUNDAI_NEXO_LONG_COMMON_TX_MSGS" in source, "NEXO longitudinal TX allowlist missing")
  require("longitudinal_accel_checks" in source, "longitudinal acceleration safety check missing")
  require("hyundai_nexo_dynamic_scc_fwd" not in source,
          "obsolete bus-direction dynamic SCC forwarding must stay removed")

  # Generic Hyundai longitudinal forwarding must remain under the normal static
  # relay block. Only the explicit NEXO list may opt into dynamic blocking, while
  # retaining check_relay=true and the physical source-0 runtime guard.
  generic_macro = source.split("#define HYUNDAI_LONG_COMMON_TX_MSGS", 1)[1].split(
    "#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[0]
  nexo_macro = source.split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[1].split(
    "#define HYUNDAI_COMMON_RX_CHECKS", 1)[0]
  require("disable_static_blocking" not in generic_macro,
          "generic Hyundai SCC static relay blocking must remain enabled")
  require(nexo_macro.count(".disable_static_blocking = true") == 4,
          "NEXO dynamic ownership must be limited to SCC11/12/13/14")
  require(nexo_macro.count(".check_relay = true") >= 4,
          "NEXO SCC relay detection must remain enabled")
  require("hyundai_nexo_scc12_tx_seen" in source,
          "NEXO ownership must be armed by accepted SCC12")
  require("HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US = 400000U" in source,
          "NEXO ownership timeout must remain 400 ms")
  require(".fwd = hyundai_fwd_hook" in source,
          "NEXO dynamic SCC forwarding hook is not wired")

  for address in ("0x38D", "0x483", "0x7D0"):
    pattern = rf"\{{\s*{address}\s*,\s*0\s*,\s*8\s*,\s*\.check_relay\s*=\s*false\s*\}}"
    require(re.search(pattern, source) is not None, f"Panda safety allowlist missing: {address}")


def main() -> None:
  parse_python_files()
  validate_interface()
  validate_hyundaican()
  validate_controller()
  validate_recovery()
  validate_runtime_guard()
  validate_safety()
  print("NEXO integration preflight PASS")


if __name__ == "__main__":
  main()
