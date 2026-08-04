#!/usr/bin/env python3
from pathlib import Path

import apply_nexo_card_crash_diagnostics_v3_fix3  # noqa: F401 - applies previous generators


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tools/check_nexo_uds_failure_policy.py"
source = path.read_text(encoding="utf-8")
source = source.replace(
  '    "if self.nexo_long_init_failed:\\n      return",\n',
  '    "if self.nexo_long_init_failed:",\n'
  '    "self._update_nexo_heartbeat()",\n',
)
path.write_text(source, encoding="utf-8")
