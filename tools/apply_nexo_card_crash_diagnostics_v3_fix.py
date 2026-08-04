#!/usr/bin/env python3
from pathlib import Path

import apply_nexo_card_crash_diagnostics_v3 as base


ROOT = Path(__file__).resolve().parents[1]
base.main()

path = ROOT / "tools/check_nexo_integration.py"
source = path.read_text(encoding="utf-8")
source = source.replace(
  '    "self.CI.deinit(self.CP, *self.can_callbacks)",\n',
  '    "record_nexo_card_crash",\n'
  '    "record_nexo_long_success",\n'
  '    "NexoCardHeartbeatMono",\n',
)
needle = '''  for token in required:
    require(token in source, f"NEXO failure latch missing: {token}")

  recovery = source[source.index("def recover_nexo_stock_cruise"):source.index("def can_comm_callbacks")]
'''
replacement = '''  for token in required:
    require(token in source, f"NEXO failure latch missing: {token}")

  require("self.CI.deinit(self.CP, *self.can_callbacks)" not in source,
          "card failure latch must not re-enter NEXO radar diagnostics")

  recovery = source[source.index("def recover_nexo_stock_cruise"):source.index("def can_comm_callbacks")]
'''
if needle not in source:
  raise RuntimeError("check_nexo_integration.py recovery contract block not found")
source = source.replace(needle, replacement, 1)
path.write_text(source, encoding="utf-8")
