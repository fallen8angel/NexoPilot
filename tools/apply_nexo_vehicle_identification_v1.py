#!/usr/bin/env python3
from pathlib import Path


FINGERPRINTS_PATH = Path("opendbc_repo/opendbc/car/hyundai/fingerprints.py")
TEST_PATH = Path("opendbc_repo/opendbc/car/hyundai/tests/test_nexo_fingerprint.py")
CHECK_PATH = Path("tools/check_nexo_vehicle_identification.py")

NEXO_FINGERPRINT = {
  127: 8, 145: 8, 146: 8, 304: 8, 320: 8, 339: 8, 352: 8, 356: 4,
  512: 6, 544: 8, 593: 8, 688: 5, 832: 8, 881: 8, 882: 8, 897: 8,
  902: 8, 903: 8, 905: 8, 908: 8, 909: 8, 912: 7, 916: 8, 1056: 8,
  1057: 8, 1078: 4, 1136: 8, 1151: 8, 1155: 8, 1156: 8, 1157: 4,
  1162: 8, 1164: 8, 1168: 7, 1173: 8, 1174: 8, 1180: 8, 1183: 8,
  1186: 2, 1191: 2, 1192: 8, 1193: 8, 1210: 8, 1219: 8, 1220: 8,
  1222: 6, 1223: 8, 1224: 8, 1227: 8, 1230: 6, 1231: 6, 1265: 4,
  1268: 8, 1280: 1, 1287: 4, 1290: 8, 1291: 8, 1292: 8, 1294: 8,
  1297: 8, 1298: 8, 1305: 8, 1312: 8, 1315: 8, 1316: 8, 1322: 8,
  1324: 8, 1342: 6, 1345: 8, 1348: 8, 1355: 8, 1363: 8, 1369: 8,
  1371: 8, 1407: 8, 1419: 8, 1427: 6, 1429: 8, 1430: 8, 1437: 8,
  1456: 4, 1460: 8, 1470: 8, 1484: 8, 1507: 8, 1520: 8, 1535: 8,
}


def render_fingerprint() -> str:
  entries = []
  items = list(NEXO_FINGERPRINT.items())
  for i in range(0, len(items), 8):
    row = ", ".join(f"{addr}: {length}" for addr, length in items[i:i + 8])
    entries.append(f"    {row},")
  return "\n".join(entries)


def patch_fingerprints() -> None:
  text = FINGERPRINTS_PATH.read_text(encoding="utf-8")
  marker = "# NEXOdriveAI-proven CAN fallback fingerprint."
  if marker in text:
    return
  block = f'''\n\n# NEXOdriveAI-proven CAN fallback fingerprint.\n# Modern Hyundai identification first uses firmware matching. This exact CAN\n# fingerprint is a fallback for NEXO firmware variants that are not yet listed\n# in FW_VERSIONS. It is intentionally scoped to NEXO only.\nFINGERPRINTS = {{\n  CAR.HYUNDAI_NEXO_1ST_GEN: [{{\n{render_fingerprint()}\n  }}],\n}}\n'''
  FINGERPRINTS_PATH.write_text(text.rstrip() + block, encoding="utf-8")


def write_test() -> None:
  TEST_PATH.write_text('''#!/usr/bin/env python3\nimport unittest\nfrom types import SimpleNamespace\n\nfrom opendbc.car.fingerprints import eliminate_incompatible_cars\nfrom opendbc.car.hyundai.fingerprints import FINGERPRINTS\nfrom opendbc.car.hyundai.values import CAR\n\n\nclass TestNexoFingerprint(unittest.TestCase):\n  def test_nexo_registered_for_can_fallback(self):\n    self.assertIn(CAR.HYUNDAI_NEXO_1ST_GEN, FINGERPRINTS)\n    self.assertEqual(len(FINGERPRINTS[CAR.HYUNDAI_NEXO_1ST_GEN]), 1)\n\n  def test_proven_nexo_scc_and_fcev_messages_present(self):\n    fp = FINGERPRINTS[CAR.HYUNDAI_NEXO_1ST_GEN][0]\n    expected = {\n      0x200: 6,   # FCEV powertrain status\n      0x340: 8,   # LKAS11\n      0x389: 8,   # SCC14\n      0x38D: 8,   # FCA11\n      0x420: 8,   # SCC11\n      0x421: 8,   # SCC12\n      0x483: 8,   # FCA12\n      0x4F1: 4,   # CLU11 buttons\n      0x50A: 8,   # SCC13\n    }\n    for addr, length in expected.items():\n      self.assertEqual(fp.get(addr), length, hex(addr))\n\n  def test_known_nexo_message_keeps_candidate(self):\n    msg = SimpleNamespace(address=0x421, dat=b"\\x00" * 8)\n    self.assertEqual(\n      eliminate_incompatible_cars(msg, [CAR.HYUNDAI_NEXO_1ST_GEN]),\n      [CAR.HYUNDAI_NEXO_1ST_GEN],\n    )\n\n  def test_unknown_message_rejects_candidate(self):\n    msg = SimpleNamespace(address=0x123, dat=b"\\x00" * 8)\n    self.assertEqual(eliminate_incompatible_cars(msg, [CAR.HYUNDAI_NEXO_1ST_GEN]), [])\n\n\nif __name__ == "__main__":\n  unittest.main()\n''', encoding="utf-8")


def write_checker() -> None:
  CHECK_PATH.write_text('''#!/usr/bin/env python3\nimport ast\nfrom pathlib import Path\n\npath = Path("opendbc_repo/opendbc/car/hyundai/fingerprints.py")\nsource = path.read_text(encoding="utf-8")\ntree = ast.parse(source, filename=str(path))\n\nif "NEXOdriveAI-proven CAN fallback fingerprint" not in source:\n  raise AssertionError("NEXO CAN fallback marker missing")\nif "CAR.HYUNDAI_NEXO_1ST_GEN" not in source:\n  raise AssertionError("NEXO platform missing from FINGERPRINTS")\n\nassignment = next((n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FINGERPRINTS" for t in n.targets)), None)\nif assignment is None or not isinstance(assignment.value, ast.Dict):\n  raise AssertionError("Hyundai FINGERPRINTS dictionary missing")\n\nrequired_tokens = ("0x200", "0x340", "0x389", "0x38D", "0x420", "0x421", "0x483", "0x4F1", "0x50A")\n# The source uses decimal keys copied exactly from the proven AI fingerprint.\nrequired_decimal = ("512: 6", "832: 8", "905: 8", "909: 8", "1056: 8", "1057: 8", "1155: 8", "1265: 4", "1290: 8")\nfor token in required_decimal:\n  if token not in source:\n    raise AssertionError(f"NEXO fallback fingerprint missing {token}")\n\nprint("NEXO vehicle identification checks passed")\n''', encoding="utf-8")


if __name__ == "__main__":
  patch_fingerprints()
  write_test()
  write_checker()
  print("Applied NEXO vehicle identification fallback")
