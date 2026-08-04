#!/usr/bin/env python3
"""Dependency-free contract check for the NEXO post-radar safety guard."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RADAR_TRACKS = ROOT / "opendbc_repo/opendbc/car/hyundai/radar_tracks.py"


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
  for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return node
  raise AssertionError(f"missing function: {name}")


def constant_value(tree: ast.Module, name: str):
  for node in tree.body:
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name) and target.id == name:
          return ast.literal_eval(node.value)
  raise AssertionError(f"missing constant: {name}")


def call_positions(function: ast.FunctionDef, name: str) -> list[int]:
  positions = []
  for node in ast.walk(function):
    if not isinstance(node, ast.Call):
      continue
    func = node.func
    called_name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if called_name == name:
      positions.append(node.lineno)
  return sorted(positions)


def main() -> None:
  source = RADAR_TRACKS.read_text(encoding="utf-8")
  tree = ast.parse(source, filename=str(RADAR_TRACKS))

  stock_addrs = set(constant_value(tree, "NEXO_STOCK_SCC_ADDRS"))
  require(stock_addrs == {0x389, 0x420, 0x421, 0x50A},
          f"unexpected stock SCC address set: {sorted(stock_addrs)}")
  require(constant_value(tree, "NEXO_POST_TRACK_VERIFY_TIMEOUT") >= 1.0,
          "post-track verification window must be at least one second")
  require(constant_value(tree, "NEXO_STOCK_SCC_MIN_FRAMES") >= 2,
          "stock SCC detection must require repeated fresh frames")
  require(constant_value(tree, "NEXO_RADAR_TRACK_MIN_IDS") >= 4,
          "radar track survival check is too weak")

  enable = find_function(tree, "enable_radar_tracks")
  disable_calls = call_positions(enable, "disable_ecu")
  verify_calls = call_positions(enable, "_verify_post_track_state")
  query_calls = call_positions(enable, "_query")

  require(query_calls, "radar programming query missing")
  require(disable_calls, "post-track SCC re-suppression missing")
  require(verify_calls, "post-track SCC/radar verification missing")
  require(max(query_calls) < disable_calls[0] < verify_calls[0],
          "required order is radar programming -> SCC re-suppression -> final verification")

  verify = find_function(tree, "_verify_post_track_state")
  verify_source = ast.get_source_segment(source, verify) or ""
  for token in ("msg.src == scc_bus", "NEXO_STOCK_SCC_ADDRS", "NEXO_RADAR_TRACK_ADDRS", "msg.src < 128"):
    require(token in verify_source, f"post-track verification contract missing: {token}")

  require("return False" in ast.get_source_segment(source, enable),
          "failed final verification must fall back instead of enabling long control")
  print("NEXO post-radar SCC guard PASS")


if __name__ == "__main__":
  main()
