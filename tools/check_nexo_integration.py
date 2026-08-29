#!/usr/bin/env python3
"""Stable, dependency-free validation for the NEXO longitudinal integration.

The checks intentionally inspect Python syntax trees instead of exact source
formatting so harmless whitespace, comments, and line wrapping cannot break CI.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PYTHON_FILES = (
  "opendbc_repo/opendbc/car/hyundai/values.py",
  "opendbc_repo/opendbc/car/hyundai/carstate.py",
  "opendbc_repo/opendbc/car/hyundai/carcontroller.py",
  "opendbc_repo/opendbc/car/hyundai/hyundaican.py",
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "opendbc_repo/opendbc/car/hyundai/nexo_acc_fault.py",
  "opendbc_repo/opendbc/car/hyundai/radar_interface.py",
  "opendbc_repo/opendbc/car/hyundai/radar_tracks.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_acc_fault.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",
  "selfdrive/car/nexo_guard.py",
  "selfdrive/car/tests/test_nexo_guard.py",
  "selfdrive/car/card.py",
  "selfdrive/controls/controlsd.py",
  "selfdrive/controls/lib/longitudinal_planner.py",
  "system/nexo_web/web.py",
)


def read(relative: str) -> str:
  path = ROOT / relative
  if not path.is_file():
    raise AssertionError(f"missing required file: {relative}")
  return path.read_text(encoding="utf-8", errors="strict")


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def parse(relative: str) -> ast.Module:
  return ast.parse(read(relative), filename=relative)


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
  function = next(
    (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name),
    None,
  )
  require(function is not None, f"missing function: {name}")
  return function


def assignments(function: ast.FunctionDef) -> dict[str, ast.AST]:
  result: dict[str, ast.AST] = {}
  for node in ast.walk(function):
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name):
          result[target.id] = node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
      result[node.target.id] = node.value
  return result


def dict_keys(node: ast.AST | None) -> set[str]:
  if not isinstance(node, ast.Dict):
    return set()
  return {
    key.value
    for key in node.keys
    if isinstance(key, ast.Constant) and isinstance(key.value, str)
  }


def is_name(node: ast.AST | None, name: str) -> bool:
  return isinstance(node, ast.Name) and node.id == name


def parse_python_files() -> None:
  for relative in PYTHON_FILES:
    parse(relative)
    print(f"syntax OK: {relative}")


def validate_interface() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/interface.py")
  required = (
    'raise RuntimeError("NEXO radar track activation failed")',
    'raise RuntimeError("NEXO stock SCC remained active")',
    "START NEXOdriveAI long init",
    "UDS PLAN radar first",
    "physical src0 silence is the success criterion",
    "DONE NEXO radar-then-disable sequence; physical src0 silence verified; runtime SCC guard armed by card",
    "NexoAccFaultQualifier",
    "ret.accFaulted = decision.qualified_fault",
  )
  for token in required:
    require(token in source, f"interface contract missing: {token}")

  radar_pos = source.find("tracks_enabled = enable_radar_tracks")
  marker_pos = source.find('_set_nexo_takeover_marker("stock_scc_disabled")', radar_pos)
  suppress_pos = source.find("stock_scc_silent = ensure_nexo_stock_scc_silent", marker_pos)
  require(radar_pos >= 0 and marker_pos > radar_pos and suppress_pos > marker_pos,
          "NEXO init order must be radar-track programming -> recovery marker -> final physical SCC suppression")
  require("_nexo_stock_scc_active" not in source,
          "startup-only SCC silence check must not replace the runtime raw-CAN guard")

  stock_guard = (
    'if self.CP.carFingerprint != CAR.HYUNDAI_NEXO_1ST_GEN or not self.CP.openpilotLongitudinalControl:'
  )
  require(stock_guard in source,
          "ACC fault qualification must stay NEXO-long-only so stock cruise keeps base behavior")


def validate_hyundaican() -> None:
  relative = "opendbc_repo/opendbc/car/hyundai/hyundaican.py"
  source = read(relative)
  tree = parse(relative)
  create_acc_commands = find_function(tree, "create_acc_commands")
  create_acc_opt = find_function(tree, "create_acc_opt")
  values = assignments(create_acc_commands)

  # NEXO cluster CRUISE main must follow the driver's MED main selection, not
  # CC.longActive. Longitudinal actuation remains independently gated by enabled.
  main_mode_acc = values.get("main_mode_acc")
  require(isinstance(main_mode_acc, ast.IfExp), "main_mode_acc must be a conditional expression")
  require(is_name(main_mode_acc.test, "is_nexo") and ast.unparse(main_mode_acc.body) == "hud_control.lanesVisible" and
          is_name(main_mode_acc.orelse, "cruise_available"),
          "NEXO main mode must follow MED main selection while non-NEXO follows cruise_available")

  acc_enabled = values.get("acc_enabled")
  require(isinstance(acc_enabled, ast.IfExp), "acc_enabled must be a conditional expression")
  require(is_name(acc_enabled.test, "is_nexo") and is_name(acc_enabled.body, "enabled"),
          "NEXO acc_enabled must follow enabled")
  require(isinstance(acc_enabled.orelse, ast.BoolOp) and isinstance(acc_enabled.orelse.op, ast.And),
          "non-NEXO acc_enabled must require enabled and main_mode_acc")
  require([node.id for node in acc_enabled.orelse.values if isinstance(node, ast.Name)] == ["enabled", "main_mode_acc"],
          "non-NEXO acc_enabled operands changed")

  required_dict_keys = {
    "scc11_values": {
      "MainMode_ACC", "TauGapSet", "VSetDis", "AliveCounterACC",
      "ObjValid", "ACC_ObjStatus", "ACC_ObjRelSpd", "ACC_ObjDist",
    },
    "scc12_values": {
      "ACCMode", "StopReq", "aReqRaw", "aReqValue",
      "ACCFailInfo", "CR_VSM_ChkSum", "CR_VSM_Alive",
    },
    "scc14_values": {
      "ComfortBandUpper", "ComfortBandLower", "JerkUpperLimit",
      "JerkLowerLimit", "ACCMode", "ObjGap",
    },
  }
  for variable, expected in required_dict_keys.items():
    actual = dict_keys(values.get(variable))
    require(expected <= actual, f"{variable} missing fields: {sorted(expected - actual)}")

  # Stock templates must not return to the direct-generation NEXO path.
  require("copy.copy(stock_scc11)" not in source, "stock SCC11 template copy must stay removed")
  require("copy.copy(stock_scc12)" not in source, "stock SCC12 template copy must stay removed")
  require("copy.copy(stock_scc14)" not in source, "stock SCC14 template copy must stay removed")

  # XPlus keeps the NEXO FCA heartbeat at FCA_Status=0/FCA_USM=1. The status
  # stream prevents a missing-message warning while all FCA actuation fields
  # stay zero and remain independently blocked by Panda safety.
  require("nexo_fca" not in values and "nexo_fca" not in source,
          "legacy NEXO FCA force variable must stay removed")

  command_conditions = [ast.unparse(node.test) for node in ast.walk(create_acc_commands) if isinstance(node, ast.If)]
  require(any("use_fca or is_nexo" in condition and "CAMERA_SCC" in condition
              for condition in command_conditions),
          "NEXO FCA heartbeat condition missing")
  require(any("not use_fca" in condition and "not is_nexo" in condition
              for condition in command_conditions),
          "SCC12 non-FCA fallback must exclude NEXO")
  require('"CF_VSM_ConfMode"' in source and '"AEB_Status"' in source,
          "SCC12 fallback status fields missing")
  require('"FCA_Status": 0 if is_nexo else 1' in source,
          "NEXO FCA heartbeat must use the XPlus status")
  require('"FCA_USM": 1' in source,
          "NEXO FCA12 must match the XPlus user-setting state")

  opt_conditions = [ast.unparse(node.test) for node in ast.walk(create_acc_opt) if isinstance(node, ast.If)]
  require(any("CAMERA_SCC" in condition and "not" in condition for condition in opt_conditions),
          "FCA12 production must exclude CAMERA_SCC")


def validate_controller() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/carcontroller.py")
  compact = " ".join(source.split())
  required = (
    "self.frame % 2 == 0 and self.CP.openpilotLongitudinalControl",
    "hyundaican.create_acc_commands",
    "self.frame % 20 == 0 and self.CP.openpilotLongitudinalControl",
    "hyundaican.create_acc_opt",
    "self.frame % 50 == 0 and self.CP.openpilotLongitudinalControl",
    "hyundaican.create_frt_radar_opt",
  )
  for token in required:
    require(token in compact, f"controller cadence missing: {token}")

  require("if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN: longitudinal_enabled = CC.longActive and CS.out.cruiseState.enabled else: longitudinal_enabled = CC.enabled" in compact,
          "NEXO longitudinal SCC must require CC.longActive and stock cruise enabled for immediate disengage")


def validate_controlsd() -> None:
  source = read("selfdrive/controls/controlsd.py")
  compact = " ".join(source.split())

  required = (
    "self.nexo_med_lateral = False",
    "self.nexo_med_speed = False",
    "self.nexo_med_rearm_required = False",
    "if self.nexo_med_lateral and self.nexo_med_rearm_required",
    "speed_pressed and driving_gear and not disable_events and not self.nexo_med_rearm_required",
    "self.nexo_med_rearm_required = True",
    "not self.nexo_med_rearm_required and driving_gear and not disable_events",
    "hudControl.lanesVisible = self.nexo_med_lateral if self.nexo_med else CC.enabled",
  )
  for token in required:
    require(token in compact, f"NEXO MED re-arm contract missing: {token}")

  non_gear_start = source.index("if non_gear_disable:")
  non_gear_end = source.index("# A remembered MED selection", non_gear_start)
  non_gear_block = source[non_gear_start:non_gear_end]
  require("self.nexo_med_lateral = False" not in non_gear_block,
          "real disable must preserve the MED main selection while latching actuation off")
  require("self.nexo_med_speed = False" in non_gear_block and "self.nexo_med_rearm_required = True" in non_gear_block,
          "real disable must drop speed control and set the re-arm latch")


def validate_recovery() -> None:
  source = read("selfdrive/car/card.py")
  required = (
    '"NEXO radar track activation failed"',
    '"NEXO stock SCC communication could not be disabled"',
    '"NEXO stock SCC returned during longitudinal control"',
    '_safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)',
    "self.nexo_long_init_failed",
    "self._handle_nexo_long_failure(error)",
    "self._restore_nexo_stock_scc_if_pending",
    "card startup stale takeover",
    "card thread exit",
    "uncaught card exception",
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

  require("self.CI.init(self.CP, *self.can_callbacks)" not in source[source.index("def _handle_nexo_long_failure"):source.index("def state_update")],
          "failure latch must never re-enter radar initialization")
  require("self.CI.deinit(self.CP, *self.can_callbacks)" in source,
          "dedicated stock SCC restore path is not connected")

  recovery = source[source.index("def recover_nexo_stock_cruise"):source.index("def can_comm_callbacks")]
  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):
    require(forbidden not in recovery, f"NEXO failure policy must not change settings or reboot: {forbidden}")


def validate_runtime_guard() -> None:
  source = read("selfdrive/car/nexo_guard.py")
  required = (
    "NEXO_STOCK_SCC_ADDRS",
    "NEXO_STOCK_SCC_SOURCE = 0",
    "class NexoStockSccRuntimeGuard",
    "def _iter_can_messages",
    "for msg in _iter_can_messages(can_messages)",
    'source = int(getattr(msg, "src", -1))',
    "source == NEXO_STOCK_SCC_SOURCE",
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

  # Generic Hyundai longitudinal forwarding stays under normal static relay
  # blocking. Only the explicit NEXO list opts into dynamic SCC11/12/13/14
  # blocking, while keeping check_relay enabled and the source-0 runtime guard.
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
  validate_controlsd()
  validate_recovery()
  validate_runtime_guard()
  validate_safety()
  print("NEXO integration preflight PASS")


if __name__ == "__main__":
  main()
