#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "selfdrive/car/card.py"
source = path.read_text(encoding="utf-8")
old = "    # Update radar tracks from CAN\n    RD: structs.RadarDataT | None = self.RI.update(can_list)\n\n    self.sm.update(0)\n\n    can_rcv_valid = len(can_strs) > 0\n"
new = "    # Update radar tracks from CAN\n    RD: structs.RadarDataT | None = self.RI.update(can_list)\n\n    can_rcv_valid = len(can_strs) > 0\n"
if old in source:
  source = source.replace(old, new, 1)
elif new not in source:
  raise RuntimeError("missing duplicate SubMaster update anchor")
path.write_text(source, encoding="utf-8")
print("Removed duplicate card SubMaster update")
