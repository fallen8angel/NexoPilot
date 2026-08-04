#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import apply_nexo_ai_runtime_guard as patch


ROOT = Path(__file__).resolve().parents[1]
_original_subn = re.subn


def literal_subn(pattern, replacement, string, count=0, flags=0):
  if isinstance(replacement, str):
    return _original_subn(pattern, lambda _match: replacement, string, count=count, flags=flags)
  return _original_subn(pattern, replacement, string, count=count, flags=flags)


patch.re.subn = literal_subn
patch.restore_from_commit("opendbc_repo/opendbc/safety/modes/hyundai.h")
patch.restore_from_commit("opendbc_repo/opendbc/safety/tests/test_hyundai.py")
patch.patch_radar_tracks()
patch.patch_interface()
patch.add_runtime_guard()
patch.patch_card()
patch.patch_integration_check()

checker = ROOT / "tools/check_nexo_integration.py"
source = checker.read_text(encoding="utf-8")
source = source.replace(
  '    "getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE",\n',
  '    \'getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE\',\n',
)
checker.write_text(source, encoding="utf-8")

(ROOT / "tools/check_nexo_post_radar_guard.py").write_text('''#!/usr/bin/env python3
"""Dependency-free contract check for the NEXOdriveAI sequence and runtime guard."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def main() -> None:
  radar = (ROOT / "opendbc_repo/opendbc/car/hyundai/radar_tracks.py").read_text(encoding="utf-8")
  interface = (ROOT / "opendbc_repo/opendbc/car/hyundai/interface.py").read_text(encoding="utf-8")
  guard = (ROOT / "selfdrive/car/nexo_guard.py").read_text(encoding="utf-8")
  card = (ROOT / "selfdrive/car/card.py").read_text(encoding="utf-8")

  require('b"\\\\x10\\\\x07"' in radar and 'b"\\\\x50\\\\x07"' in radar,
          "NEXOdriveAI radar diagnostic session is missing")
  require('b"\\\\x2e" + RADAR_TRACK_CONFIG_DID + RADAR_TRACK_CONFIG' in radar,
          "NEXOdriveAI DID 0x0142 write is missing")
  require("_verify_post_track_state" not in radar and "disable_ecu" not in radar,
          "post-programming session-changing probes must stay removed")
  require("START NEXOdriveAI long init" in interface,
          "NEXOdriveAI init trace is missing")
  require("_nexo_stock_scc_active" not in interface,
          "startup-only SCC check must stay removed")
  require("NEXO_STOCK_SCC_SOURCE = 0" in guard,
          "runtime guard must watch the physical vehicle source")
  for address in ("0x389", "0x420", "0x421", "0x50A"):
    require(address in guard, f"runtime guard missing SCC address {address}")
  require("self.nexo_stock_scc_guard.observe(can_list)" in card,
          "card raw-CAN runtime guard is not connected")
  require("NEXO stock SCC returned during longitudinal control" in card,
          "runtime stock-cruise fallback reason is missing")
  print("NEXOdriveAI sequence and runtime SCC guard PASS")


if __name__ == "__main__":
  main()
''', encoding="utf-8")

print("Generated NEXOdriveAI sequence and runtime stock-SCC fail-closed guard")
