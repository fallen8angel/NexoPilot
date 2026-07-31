#!/usr/bin/env python3
"""Check that NEXO longitudinal CAN fields exist in the Hyundai DBC."""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HYUNDAICAN = ROOT / "opendbc_repo/opendbc/car/hyundai/hyundaican.py"
HYUNDAI_DBC = ROOT / "opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_can.dbc"
VARIABLE_TO_MESSAGE = {
  "scc11_values": "SCC11",
  "scc12_values": "SCC12",
  "scc14_values": "SCC14",
  "fca11_values": "FCA11",
}


def dbc_signals(path: Path) -> dict[str, set[str]]:
  messages: dict[str, set[str]] = {}
  current: str | None = None
  for line in path.read_text(encoding="utf-8").splitlines():
    message = re.match(r"BO_\s+\d+\s+(\w+):", line)
    if message:
      current = message.group(1)
      messages[current] = set()
      continue
    signal = re.match(r"\s*SG_\s+(\w+)", line)
    if current and signal:
      messages[current].add(signal.group(1))
  return messages


def literal_dict_keys(node: ast.AST) -> set[str]:
  if not isinstance(node, ast.Dict):
    return set()
  return {
    key.value for key in node.keys
    if isinstance(key, ast.Constant) and isinstance(key.value, str)
  }


def longitudinal_fields(path: Path) -> dict[str, set[str]]:
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  function = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "create_acc_commands"
  )
  fields = {variable: set() for variable in VARIABLE_TO_MESSAGE}

  for node in ast.walk(function):
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name) and target.id in fields:
          fields[target.id].update(literal_dict_keys(node.value))
        elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
              and target.value.id in fields and isinstance(target.slice, ast.Constant)
              and isinstance(target.slice.value, str)):
          fields[target.value.id].add(target.slice.value)
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "update" and isinstance(node.func.value, ast.Name)
          and node.func.value.id in fields and node.args):
      fields[node.func.value.id].update(literal_dict_keys(node.args[0]))

  return fields


def main() -> None:
  dbc = dbc_signals(HYUNDAI_DBC)
  fields = longitudinal_fields(HYUNDAICAN)
  failures: list[str] = []

  for variable, message in VARIABLE_TO_MESSAGE.items():
    unknown = sorted(fields[variable] - dbc.get(message, set()))
    if unknown:
      failures.append(f"{message}: unsupported fields {', '.join(unknown)}")
    else:
      print(f"{message}: {len(fields[variable])} transmitted fields match DBC")

  if failures:
    raise SystemExit("NEXO DBC contract FAIL\n" + "\n".join(failures))
  print("NEXO DBC contract PASS")


if __name__ == "__main__":
  main()
