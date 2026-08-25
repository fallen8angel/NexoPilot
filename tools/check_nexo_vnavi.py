#!/usr/bin/env python3
"""Dependency-free contract checks for first-generation NEXO stock-navigation vNAVI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
  path = ROOT / relative
  if not path.is_file():
    raise AssertionError(f"missing required file: {relative}")
  return path.read_text(encoding="utf-8", errors="strict")


def require(condition: bool, message: str) -> None:
  if not condition:
    raise AssertionError(message)


def main() -> None:
  carstate = read("opendbc_repo/opendbc/car/hyundai/carstate.py")
  planner = read("selfdrive/controls/lib/longitudinal_planner.py")
  hud = read("selfdrive/ui/onroad/hud_renderer.py")
  helper = read("common/nexo_vnavi.py")
  dbc = read("opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_can.dbc")

  for relative in (
    "opendbc_repo/opendbc/car/hyundai/carstate.py",
    "selfdrive/controls/lib/longitudinal_planner.py",
    "selfdrive/ui/onroad/hud_renderer.py",
    "common/nexo_vnavi.py",
  ):
    ast.parse(read(relative), filename=relative)

  require("BO_ 1348 Navi_HU" in dbc, "NEXO DBC must expose stock-navigation CAN 0x544")
  require("SpeedLim_Nav_Clu" in dbc and "SpeedLim_Nav_Cam" in dbc, "Navi_HU speed/camera signals missing")
  require('("Navi_HU", math.nan)' in carstate, "Navi_HU must remain optional and not cause CAN timeout")
  require('age <= 1_000_000_000' in carstate, "stale Navi_HU data must fail closed within one second")
  require('VNAVI_VIRTUAL_DISTANCE_FACTOR = 6.0' in carstate, "Carrot-style NEXO virtual-distance fallback changed")
  require('from openpilot.' not in carstate, "opendbc CarState must not depend on the openpilot package")
  require('self.CP.openpilotLongitudinalControl' in planner and 'calculate_vnavi_target_speed' in planner,
          "vNAVI target must be gated to openpilot longitudinal control")
  require('v_cruise = min(v_cruise, vnavi_target_kph * CV.KPH_TO_MS)' in planner,
          "vNAVI must only cap the existing cruise target")
  require('VNAVI_STATE_MAX_AGE = 0.75' in helper, "planner/UI vNAVI state must expire quickly")
  require('text = f"vNAVI {speed} · {distance}m"' in hud, "vNAVI HUD label/limit/distance display missing")

  print("NEXO stock-navigation vNAVI contract PASS")


if __name__ == "__main__":
  main()
