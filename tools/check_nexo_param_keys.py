#!/usr/bin/env python3
"""Validate that every NEXO runtime diagnostic Params key is registered.

An unregistered key raises common.params_pyx.UnknownKeyName at runtime. This
checker scans the card/runtime/web diagnostic sources and fails CI before a
vehicle build can reach that path.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARAM_KEYS = ROOT / "common/params_keys.h"
SOURCES = (
  ROOT / "selfdrive/car/card.py",
  ROOT / "selfdrive/car/nexo_runtime_diagnostics.py",
  ROOT / "system/nexo_web/nexo_diagnostics_v2.py",
)
EXPECTED = {
  "NexoCardHeartbeatMono": "FLOAT",
  "NexoCardLastCrash": "STRING",
  "NexoCardSessionReason": "STRING",
  "NexoCardSessionState": "STRING",
  "NexoCardStage": "STRING",
  "NexoLongitudinalFailure": "STRING",
}


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def used_nexo_keys() -> set[str]:
  keys: set[str] = set()
  for path in SOURCES:
    require(path.is_file(), f"missing NEXO Params source: {path.relative_to(ROOT)}")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
      if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("Nexo"):
        if re.fullmatch(r"Nexo[A-Za-z0-9_]+", node.value):
          keys.add(node.value)
  return keys


def registered_nexo_keys(header: str) -> dict[str, tuple[str, str]]:
  entries: dict[str, tuple[str, str]] = {}
  pattern = re.compile(r'\{"(Nexo[A-Za-z0-9_]+)",\s*\{([^,}]+),\s*([A-Z_]+)(?:,\s*[^}]*)?\}\}')
  for key, flags, value_type in pattern.findall(header):
    entries[key] = (flags.strip(), value_type.strip())
  return entries


def main() -> None:
  header = PARAM_KEYS.read_text(encoding="utf-8")
  used = used_nexo_keys()
  registered = registered_nexo_keys(header)

  missing = sorted(used - registered.keys())
  require(not missing, f"unregistered NEXO Params keys would raise UnknownKeyName: {missing}")

  missing_expected = sorted(EXPECTED.keys() - registered.keys())
  require(not missing_expected, f"required NEXO runtime Params keys missing: {missing_expected}")

  for key, expected_type in EXPECTED.items():
    flags, actual_type = registered[key]
    require(actual_type == expected_type, f"{key} type must be {expected_type}, got {actual_type}")
    require("CLEAR_ON_MANAGER_START" in flags, f"{key} must clear on manager start to avoid stale session state")

  card_source = (ROOT / "selfdrive/car/card.py").read_text(encoding="utf-8")
  require('self.params.remove("NexoLongitudinalFailure")' in card_source,
          "card success path no longer clears the previous session failure marker")

  print("NEXO Params registration PASS")
  print("registered:", ", ".join(sorted(registered)))


if __name__ == "__main__":
  main()
