#!/usr/bin/env python3
import ast
from pathlib import Path

path = Path("opendbc_repo/opendbc/car/hyundai/fingerprints.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(path))

if "NEXOdriveAI-proven CAN fallback fingerprint" not in source:
  raise AssertionError("NEXO CAN fallback marker missing")
if "CAR.HYUNDAI_NEXO_1ST_GEN" not in source:
  raise AssertionError("NEXO platform missing from FINGERPRINTS")

assignment = next((n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FINGERPRINTS" for t in n.targets)), None)
if assignment is None or not isinstance(assignment.value, ast.Dict):
  raise AssertionError("Hyundai FINGERPRINTS dictionary missing")

required_tokens = ("0x200", "0x340", "0x389", "0x38D", "0x420", "0x421", "0x483", "0x4F1", "0x50A")
# The source uses decimal keys copied exactly from the proven AI fingerprint.
required_decimal = ("512: 6", "832: 8", "905: 8", "909: 8", "1056: 8", "1057: 8", "1155: 8", "1265: 4", "1290: 8")
for token in required_decimal:
  if token not in source:
    raise AssertionError(f"NEXO fallback fingerprint missing {token}")

print("NEXO vehicle identification checks passed")
