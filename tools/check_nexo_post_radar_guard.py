#!/usr/bin/env python3
"""Dependency-free contract check for the NEXOdriveAI sequence and runtime guard."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def main() -> None:
  radar = (ROOT / "opendbc_repo/opendbc/car/hyundai/radar_tracks.py").read_text(encoding="utf-8")
  interface = (ROOT / "opendbc_repo/opendbc/car/hyundai/interface.py").read_text(encoding="utf-8")
  takeover = (ROOT / "opendbc_repo/opendbc/car/hyundai/nexo_takeover.py").read_text(encoding="utf-8")
  guard = (ROOT / "selfdrive/car/nexo_guard.py").read_text(encoding="utf-8")
  card = (ROOT / "selfdrive/car/card.py").read_text(encoding="utf-8")

  require('b"\\x10\\x07"' in radar and 'b"\\x50\\x07"' in radar,
          "NEXOdriveAI radar diagnostic session is missing")
  require('b"\\x2e" + RADAR_TRACK_CONFIG_DID + RADAR_TRACK_CONFIG' in radar,
          "NEXOdriveAI DID 0x0142 write is missing")
  require("_verify_post_track_state" not in radar and
          "from opendbc.car.disable_ecu import" not in radar and
          "disable_ecu(can_recv" not in radar,
          "post-programming session-changing probes must stay removed")
  require("START NEXOdriveAI long init" in interface,
          "NEXOdriveAI init trace is missing")
  require("_nexo_stock_scc_active" not in interface,
          "startup-only SCC check must stay removed")
  for token in (
    "def _wait_for_exclusive_card_process",
    "other card processes remained during takeover",
    "stability_reassertions",
    "make_tester_present_msg",
    "STEP 3 stability relapse",
  ):
    require(token in takeover, f"takeover stabilization contract missing: {token}")
  require("NEXO_STOCK_SCC_SOURCE = 0" in guard,
          "runtime guard must watch the physical vehicle source")
  require("def _iter_can_messages" in guard and
          "for msg in _iter_can_messages(can_messages)" in guard,
          "runtime guard must flatten card CAN batches")
  for address in ("0x389", "0x420", "0x421", "0x50A"):
    require(address in guard, f"runtime guard missing SCC address {address}")
  require("self.nexo_stock_scc_guard.observe(can_list)" in card,
          "card raw-CAN runtime guard is not connected")
  require("NEXO stock SCC returned during longitudinal control" in card,
          "runtime stock-cruise fallback reason is missing")
  print("NEXOdriveAI sequence, process quarantine, stability verification, and nested-CAN runtime guard PASS")


if __name__ == "__main__":
  main()
