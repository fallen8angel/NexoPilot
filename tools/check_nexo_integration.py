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
    'raise RuntimeError("NEXO stock SCC communication could not be disabled")',
    'raise RuntimeError("NEXO radar track activation failed")',
    "START NEXOdriveAI long init",
    "DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card",
  )
  for token in required:
    require(token in source, f"interface contract missing: {token}")

  disable_pos = source.find("disabled = disable_ecu")
  radar_pos = source.find("tracks_enabled = enable_radar_tracks", disable_pos)
  require(disable_pos >= 0 and radar_pos > disable_pos,
          "NEXO init order must be extended-diagnostic disable -> radar-track programming")
  require("_nexo_stock_scc_active" not in source,
          "startup-only SCC silence check must not replace the runtime raw-CAN guard")


def validate_hyundaican() -> None:
  relative = "opendbc_repo/opendbc/car/hyundai/hyundaican.py"
  source = read(relative)
  tree = parse(relative)
  create_acc_commands = find_function(tree, "create_acc_commands")
  create_acc_opt = find_function(tree, "create_acc_opt")
  values = assignments(create_acc_commands)

  # Current NEXO contract: after stock SCC takeover, SCC11 continues to
  # advertise cruise availability while actual acceleration still requires
  # openpilot enablement. Check the expression tree rather than source spacing.
  require(is_name(values.get("main_mode_acc"), "cruise_available"),
          "NEXO main mode must follow cruise_available")

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

  command_conditions = [ast.unparse(node.test) for node in ast.walk(create_acc_commands) if isinstance(node, ast.If)]
  require(any("use_fca" in condition and "not is_nexo" in condition and "CAMERA_SCC" in condition
              for condition in command_conditions),
          "NEXO FCA11 suppression condition missing")

  opt_conditions = [ast.unparse(node.test) for node in ast.walk(create_acc_opt) if isinstance(node, ast.If)]
  require(any("not is_nexo" in condition and "CAMERA_SCC" in condition for condition in opt_conditions),
          "NEXO FCA12 suppression condition missing")


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
  validate_recovery()
  validate_runtime_guard()
  validate_safety()
  print("NEXO integration preflight PASS")


if __name__ == "__main__":
  main()
