#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tools/check_nexo_integration.py"
source = path.read_text(encoding="utf-8")
old = '    "len(self._timestamps) >= self.min_frames",\n'
new = '    "len(self._detections) < self.min_frames",\n'
if old not in source and new not in source:
  raise RuntimeError("missing runtime guard validation anchor")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
print("Updated runtime guard validator for rolling detection history")
