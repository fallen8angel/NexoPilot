#!/usr/bin/env python3
"""Stable, dependency-free validation for the NEXO longitudinal integration."""

from __future__ import annotations

import ast
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


def parse_python_files() -> None:
  for relative in PYTHON_FILES:
    source = read(relative)
    ast.parse(source, filename=relative)
    print(f"syntax OK: {relative}")


def validate_interface() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/interface.py")

  required = (
    "NEXO_STOCK_SCC_ADDRS",
    "def _nexo_stock_scc_active",
    'raise RuntimeError("NEXO stock SCC communication could not be disabled")',
    'raise RuntimeError("NEXO stock SCC remained active")',
    'raise RuntimeError("NEXO radar track activation failed")',
    "STEP 1B verify stock SCC traffic is silent",
    "DONE verified disable-then-radar sequence",
  )
  for token in required:
    require(token in source, f"interface contract missing: {token}")

  disable_pos = source.find("disabled = disable_ecu")
  verify_pos = source.find("stock_scc_active = _nexo_stock_scc_active", disable_pos)
  radar_pos = source.find("tracks_enabled = enable_radar_tracks", verify_pos)
  require(disable_pos >= 0 and verify_pos > disable_pos and radar_pos > verify_pos,
          "NEXO init order must be disable -> verify SCC silence -> enable radar tracks")


def validate_hyundaican() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/hyundaican.py")
  tree = ast.parse(source, filename="hyundaican.py")
  functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
  require("create_acc_commands" in functions, "create_acc_commands missing")
  require("create_acc_opt" in functions, "create_acc_opt missing")

  required = (
    "del stock_scc11, stock_scc12, stock_scc14",
    "scc11_values = {",
    "scc12_values = {",
    "scc14_values = {",
    "main_mode_acc = (enabled or vehicle_cruise_enabled) if is_nexo else cruise_available",
    "acc_enabled = enabled if is_nexo else enabled and main_mode_acc",
    '"ACCMode": 2 if acc_enabled and long_override else 1 if acc_enabled else 0',
    '"ACCMode": 2 if scc14_enabled and long_override else 1 if scc14_enabled else 4',
    "if use_fca and not is_nexo and not (CP.flags & HyundaiFlags.CAMERA_SCC):",
    "if not is_nexo and not (CP.flags & HyundaiFlags.CAMERA_SCC):",
  )
  for token in required:
    require(token in source, f"hyundaican contract missing: {token}")

  require("copy.copy(stock_scc11)" not in source, "stock SCC11 template copy must stay removed")
  require("copy.copy(stock_scc12)" not in source, "stock SCC12 template copy must stay removed")
  require("copy.copy(stock_scc14)" not in source, "stock SCC14 template copy must stay removed")


def validate_controller() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/carcontroller.py")
  required = (
    "if self.frame % 2 == 0 and self.CP.openpilotLongitudinalControl:",
    "hyundaican.create_acc_commands",
    "if self.frame % 20 == 0 and self.CP.openpilotLongitudinalControl:",
    "hyundaican.create_acc_opt",
    "if self.frame % 50 == 0 and self.CP.openpilotLongitudinalControl:",
    "hyundaican.create_frt_radar_opt",
  )
  for token in required:
    require(token in source, f"controller cadence missing: {token}")


def validate_recovery() -> None:
  source = read("selfdrive/car/card.py")
  required = (
    '"NEXO radar track activation failed"',
    '"NEXO stock SCC communication could not be disabled"',
    '"NEXO stock SCC remained active"',
    'params.put_bool("AlphaLongitudinalEnabled", False, block=True)',
    'params.put_bool("ExperimentalMode", False, block=True)',
    'params.put_bool("DoReboot", True, block=True)',
    "recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)",
  )
  for token in required:
    require(token in source, f"stock-cruise recovery missing: {token}")


def validate_safety() -> None:
  source = read("opendbc_repo/opendbc/safety/modes/hyundai.h")
  required = (
    "HYUNDAI_LONG_COMMON_TX_MSGS",
    "{0x38D, 0, 8, .check_relay = false}",
    "{0x483, 0, 8, .check_relay = false}",
    "{0x7D0, 0, 8, .check_relay = false}",
    "longitudinal_accel_checks",
  )
  for token in required:
    require(token in source, f"Panda safety contract missing: {token}")


def main() -> None:
  parse_python_files()
  validate_interface()
  validate_hyundaican()
  validate_controller()
  validate_recovery()
  validate_safety()
  print("NEXO integration preflight PASS")


if __name__ == "__main__":
  main()
