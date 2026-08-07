#!/usr/bin/env python3
import ast
from pathlib import Path

fingerprint_path = Path("opendbc_repo/opendbc/car/hyundai/fingerprints.py")
fingerprint_source = fingerprint_path.read_text(encoding="utf-8")
fingerprint_tree = ast.parse(fingerprint_source, filename=str(fingerprint_path))

helper_path = Path("opendbc_repo/opendbc/car/car_helpers.py")
helper_source = helper_path.read_text(encoding="utf-8")
ast.parse(helper_source, filename=str(helper_path))

if "NEXOdriveAI-proven CAN fallback fingerprint" not in fingerprint_source:
  raise AssertionError("NEXO CAN fallback marker missing")
if "CAR.HYUNDAI_NEXO_1ST_GEN" not in fingerprint_source:
  raise AssertionError("NEXO platform missing from FINGERPRINTS")

assignment = next((n for n in fingerprint_tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FINGERPRINTS" for t in n.targets)), None)
if assignment is None or not isinstance(assignment.value, ast.Dict):
  raise AssertionError("Hyundai FINGERPRINTS dictionary missing")

# The source uses decimal keys copied exactly from the proven AI fingerprint.
required_decimal = ("512: 6", "832: 8", "905: 8", "909: 8", "1056: 8", "1057: 8", "1155: 8", "1265: 4", "1290: 8")
for token in required_decimal:
  if token not in fingerprint_source:
    raise AssertionError(f"NEXO fallback fingerprint missing {token}")

# Runtime recognition contract: the 7000 manual NEXO selection must actually
# feed the live fingerprint path, and automatic fallback may only run after
# normal matching fails with the strong proven NEXO CAN signature present.
for token in (
  'NEXO_FINGERPRINT = "HYUNDAI_NEXO_1ST_GEN"',
  'NEXO_FORCE_FILE = Path("/data/nexopilot/force_nexo")',
  "def _force_nexo_selected()",
  "def _matches_nexo_signature(finger",
  "if not fixed_fingerprint and _force_nexo_selected():",
  "fixed_fingerprint = NEXO_FINGERPRINT",
  "elif car_fingerprint is None and _matches_nexo_signature(finger):",
  "car_fingerprint = NEXO_FINGERPRINT",
  "exact_match = False",
):
  if token not in helper_source:
    raise AssertionError(f"NEXO runtime identification contract missing: {token}")

for token in ("0x200: 6", "0x340: 8", "0x389: 8", "0x38D: 8", "0x420: 8", "0x421: 8", "0x483: 8", "0x4F1: 4", "0x50A: 8"):
  if token not in helper_source:
    raise AssertionError(f"NEXO runtime signature missing {token}")

print("NEXO vehicle identification checks passed")
