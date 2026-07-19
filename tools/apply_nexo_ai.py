#!/usr/bin/env python3
"""Apply the NEXO-specific vehicle configuration carried over from NEXOdriveAI.

This patch is intentionally narrow: it keeps the current openpilot/opendbc codebase
and updates only the HYUNDAI NEXO platform configuration to avoid copying old-version
files over the newer tree.
"""

from pathlib import Path


VALUES_PATH = Path(__file__).resolve().parents[1] / "opendbc_repo/opendbc/car/hyundai/values.py"

OLD_BLOCK = '''  HYUNDAI_NEXO_1ST_GEN = HyundaiPlatformConfig(
    [HyundaiCarDocs("Hyundai Nexo 2021", "All", car_parts=CarParts.common([CarHarness.hyundai_h]))],
    CarSpecs(mass=3990 * CV.LB_TO_KG, wheelbase=2.79, steerRatio=14.19),  # https://www.hyundainews.com/assets/documents/original/42768-2021NEXOProductGuideSpecs.pdf
    flags=HyundaiFlags.FCEV,
  )'''

NEW_BLOCK = '''  HYUNDAI_NEXO_1ST_GEN = HyundaiPlatformConfig(
    [HyundaiCarDocs("HYUNDAI NEXO", "All", car_parts=CarParts.common([CarHarness.hyundai_h]))],
    CarSpecs(mass=1885, wheelbase=2.79, steerRatio=14.19, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.FCEV | HyundaiFlags.MANDO_RADAR,
  )'''


def main() -> None:
  text = VALUES_PATH.read_text(encoding="utf-8")

  if NEW_BLOCK in text:
    return

  if OLD_BLOCK not in text:
    raise RuntimeError(f"NEXO platform block was not found in {VALUES_PATH}")

  VALUES_PATH.write_text(text.replace(OLD_BLOCK, NEW_BLOCK, 1), encoding="utf-8")


if __name__ == "__main__":
  main()
