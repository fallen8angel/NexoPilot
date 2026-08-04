#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARAM_KEYS = ROOT / "common/params_keys.h"
NEXO_SOURCES = (
  ROOT / "selfdrive/car/card.py",
  ROOT / "selfdrive/car/nexo_runtime_diagnostics.py",
  ROOT / "system/nexo_web/nexo_diagnostics_v2.py",
)


def registered_keys(source: str) -> set[str]:
  return set(re.findall(r'\{\"([^\"]+)\",\s*\{', source))


def used_nexo_keys() -> set[str]:
  found: set[str] = set()
  for path in NEXO_SOURCES:
    source = path.read_text(encoding="utf-8")
    found.update(re.findall(r'\"(Nexo[A-Za-z0-9_]+)\"', source))
  return found


def main() -> None:
  source = PARAM_KEYS.read_text(encoding="utf-8")
  registered = registered_keys(source)
  used = used_nexo_keys()
  missing = sorted(used - registered)
  if missing:
    raise AssertionError(f"unregistered NEXO Params keys: {missing}")

  required_flags = {
    "NexoCardHeartbeatMono": "CLEAR_ON_MANAGER_START | DONT_LOG, FLOAT",
    "NexoCardLastCrash": "CLEAR_ON_MANAGER_START | DONT_LOG, STRING",
    "NexoCardSessionReason": "CLEAR_ON_MANAGER_START | DONT_LOG, STRING",
    "NexoCardSessionState": "CLEAR_ON_MANAGER_START | DONT_LOG, STRING",
    "NexoCardStage": "CLEAR_ON_MANAGER_START | DONT_LOG, STRING",
    "NexoLongitudinalFailure": "CLEAR_ON_MANAGER_START | DONT_LOG, STRING",
  }
  for key, flags in required_flags.items():
    token = f'{{"{key}", {{{flags}}}}}'
    if token not in source:
      raise AssertionError(f"NEXO Params key has wrong type/flags: {key}")

  print(f"NEXO Params registry PASS ({len(used)} used keys registered)")


if __name__ == "__main__":
  main()
