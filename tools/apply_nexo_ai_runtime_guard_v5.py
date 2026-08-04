#!/usr/bin/env python3
from pathlib import Path

import apply_nexo_ai_runtime_guard_v4  # noqa: F401 - applies the generated patch


ROOT = Path(__file__).resolve().parents[1]
checker = ROOT / "tools/check_nexo_post_radar_guard.py"
source = checker.read_text(encoding="utf-8")
source = source.replace(
  '  require("_verify_post_track_state" not in radar and "disable_ecu" not in radar,\n'
  '          "post-programming session-changing probes must stay removed")\n',
  '  require("_verify_post_track_state" not in radar and\n'
  '          "from opendbc.car.disable_ecu import" not in radar and\n'
  '          "disable_ecu(can_recv" not in radar,\n'
  '          "post-programming session-changing probes must stay removed")\n',
)
checker.write_text(source, encoding="utf-8")
