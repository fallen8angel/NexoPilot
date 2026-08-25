from pathlib import Path


def replace_once(path, old, new):
  p = Path(path)
  text = p.read_text()
  if old not in text:
    raise SystemExit(f"anchor not found in {path}: {old[:120]!r}")
  if text.count(old) != 1:
    raise SystemExit(f"anchor count != 1 in {path}: {text.count(old)}")
  p.write_text(text.replace(old, new, 1))


carstate = "opendbc_repo/opendbc/car/hyundai/carstate.py"
replace_once(
  carstate,
  "import copy\nimport math\n\nfrom opendbc.can",
  "import copy\nimport math\nimport os\nimport time\n\nfrom opendbc.can",
)
replace_once(
  carstate,
  "from opendbc.car.interfaces import CarStateBase\nfrom openpilot.common.nexo_vnavi import VNAVI_VIRTUAL_DISTANCE_FACTOR, write_vnavi_state\nButtonType = structs.CarState.ButtonEvent.Type\n\nPREV_BUTTON_SAMPLES = 8\nCLUSTER_SAMPLE_RATE = 20  # frames\nSTANDSTILL_THRESHOLD = 12 * 0.03125\n",
  "from opendbc.car.interfaces import CarStateBase\nButtonType = structs.CarState.ButtonEvent.Type\n\nPREV_BUTTON_SAMPLES = 8\nCLUSTER_SAMPLE_RATE = 20  # frames\nSTANDSTILL_THRESHOLD = 12 * 0.03125\nVNAVI_STATE_PATH = \"/dev/shm/nexopilot_vnavi\"\nVNAVI_VIRTUAL_DISTANCE_FACTOR = 6.0\n\n\ndef _write_vnavi_state(active: bool, speed_limit_kph: float, distance_m: float) -> None:\n  \"\"\"Publish vNAVI state without making opendbc depend on the openpilot Python package.\"\"\"\n  tmp_path = f\"{VNAVI_STATE_PATH}.{os.getpid()}.tmp\"\n  try:\n    with open(tmp_path, \"w\", encoding=\"utf-8\") as f:\n      f.write(f\"{time.monotonic():.3f},{1 if active else 0},{float(speed_limit_kph):.1f},{max(0.0, float(distance_m)):.1f}\\n\")\n    os.replace(tmp_path, VNAVI_STATE_PATH)\n  except OSError:\n    try:\n      os.unlink(tmp_path)\n    except OSError:\n      pass\n",
)
replace_once(carstate, "      write_vnavi_state(self.vnavi_active, self.vnavi_speed, distance)\n",
             "      _write_vnavi_state(self.vnavi_active, self.vnavi_speed, distance)\n")

checker = Path("tools/check_nexo_vnavi.py")
checker.write_text('''#!/usr/bin/env python3
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
  require('(\"Navi_HU\", math.nan)' in carstate, "Navi_HU must remain optional and not cause CAN timeout")
  require('age <= 1_000_000_000' in carstate, "stale Navi_HU data must fail closed within one second")
  require('VNAVI_VIRTUAL_DISTANCE_FACTOR = 6.0' in carstate, "Carrot-style NEXO virtual-distance fallback changed")
  require('from openpilot.' not in carstate, "opendbc CarState must not depend on the openpilot package")
  require('self.CP.openpilotLongitudinalControl' in planner and 'calculate_vnavi_target_speed' in planner,
          "vNAVI target must be gated to openpilot longitudinal control")
  require('v_cruise = min(v_cruise, vnavi_target_kph * CV.KPH_TO_MS)' in planner,
          "vNAVI must only cap the existing cruise target")
  require('VNAVI_STATE_MAX_AGE = 0.75' in helper, "planner/UI vNAVI state must expire quickly")
  require('text = f\"vNAVI {speed} · {distance}m\"' in hud, "vNAVI HUD label/limit/distance display missing")

  print("NEXO stock-navigation vNAVI contract PASS")


if __name__ == "__main__":
  main()
''')

workflow = ".github/workflows/nexo-validation.yml"
replace_once(
  workflow,
  "      - name: Check XPlus MED and onroad HUD contract\n        run: python tools/check_nexo_xplus_med_ui.py\n\n      - name: Check NEXO UDS and failure policy\n",
  "      - name: Check XPlus MED and onroad HUD contract\n        run: python tools/check_nexo_xplus_med_ui.py\n\n      - name: Check NEXO stock-navigation vNAVI\n        run: python tools/check_nexo_vnavi.py\n\n      - name: Check NEXO UDS and failure policy\n",
)
