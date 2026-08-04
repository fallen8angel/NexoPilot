#!/usr/bin/env python3
from pathlib import Path

import apply_nexo_card_crash_diagnostics_v3_fix2  # noqa: F401 - applies previous generators


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "system/nexo_web/nexo_diagnostics_v2.py"
source = path.read_text(encoding="utf-8")
source = source.replace('return "\n".join(lines)', 'return chr(10).join(lines)')
path.write_text(source, encoding="utf-8")
