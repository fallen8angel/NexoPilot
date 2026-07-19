#!/usr/bin/env python3
"""Apply NEXO-specific configuration carried over from the user's working forks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALUES_PATH = ROOT / "opendbc_repo/opendbc/car/hyundai/values.py"
CARSTATE_PATH = ROOT / "opendbc_repo/opendbc/car/hyundai/carstate.py"

OLD_BLOCK = '''  HYUNDAI_NEXO_1ST_GEN = HyundaiPlatformConfig(
    [HyundaiCarDocs("Hyundai Nexo 2021", "All", car_parts=CarParts.common([CarHarness.hyundai_h]))],
    CarSpecs(mass=3990 * CV.LB_TO_KG, wheelbase=2.79, steerRatio=14.19),  # https://www.hyundainews.com/assets/documents/original/42768-2021NEXOProductGuideSpecs.pdf
    flags=HyundaiFlags.FCEV,
  )'''

PREVIOUS_BLOCK = '''  HYUNDAI_NEXO_1ST_GEN = HyundaiPlatformConfig(
    [HyundaiCarDocs("HYUNDAI NEXO", "All", car_parts=CarParts.common([CarHarness.hyundai_h]))],
    CarSpecs(mass=1885, wheelbase=2.79, steerRatio=14.19, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.FCEV | HyundaiFlags.MANDO_RADAR,
  )'''

NEW_BLOCK = '''  HYUNDAI_NEXO_1ST_GEN = HyundaiPlatformConfig(
    [HyundaiCarDocs("HYUNDAI NEXO", "All", car_parts=CarParts.common([CarHarness.hyundai_h]))],
    CarSpecs(mass=1885, wheelbase=2.79, steerRatio=14.19, tireStiffnessFactor=0.385,
             minEnableSpeed=10 * CV.KPH_TO_MS),
    flags=HyundaiFlags.FCEV | HyundaiFlags.MANDO_RADAR,
  )'''

GEAR_INIT_OLD = '''    self.params = CarControllerParams(CP)
'''

GEAR_INIT_NEW = '''    self.params = CarControllerParams(CP)
    # NEXO learned gear fallback: keep the last valid gear when the raw value is transient/unknown.
    self.gear_shifter = structs.CarState.GearShifter.park
    # NEXO legacy cruise-state manager compatibility.
    self.nexo_cruise_enabled = False
    self.nexo_cruise_available = True
'''

GEAR_PARSE_OLD = '''    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
    elif self.CP.flags & HyundaiFlags.FCEV:
      gear = cp.vl["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      gear = cp.vl["CLU15"]["CF_Clu_Gear"]
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      gear = cp.vl["TCU12"]["CUR_GR"]
    else:
      gear = cp.vl["LVR12"]["CF_Lvr_Gear"]

    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))
'''

GEAR_PARSE_NEW = '''    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
      # Learned NEXO EMS20.HYDROGEN_GEAR_SHIFTER raw values:
      # P=0, D=5, N=6, R=7. Unknown values retain the last valid gear.
      gear = cp.vl["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
      gear_map = {
        0: structs.CarState.GearShifter.park,
        5: structs.CarState.GearShifter.drive,
        6: structs.CarState.GearShifter.neutral,
        7: structs.CarState.GearShifter.reverse,
      }
      if gear in gear_map:
        self.gear_shifter = gear_map[gear]
      ret.gearShifter = self.gear_shifter
    else:
      if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
        gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
      elif self.CP.flags & HyundaiFlags.FCEV:
        gear = cp.vl["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
      elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
        gear = cp.vl["CLU15"]["CF_Clu_Gear"]
      elif self.CP.flags & HyundaiFlags.TCU_GEARS:
        gear = cp.vl["TCU12"]["CUR_GR"]
      else:
        gear = cp.vl["LVR12"]["CF_Lvr_Gear"]

      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))
'''

BUTTONS_OLD = '''    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise}),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    ret.blockPcmEnable = not self.recent_button_interaction()
'''

BUTTONS_NEW = '''    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise}),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
      enable_pressed = any(be.pressed and be.type in (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.mainCruise)
                           for be in ret.buttonEvents)
      cancel_pressed = any(be.pressed and be.type == ButtonType.cancel for be in ret.buttonEvents)

      if enable_pressed:
        self.nexo_cruise_available = True
        self.nexo_cruise_enabled = True

      if cancel_pressed:
        # First CANCEL exits enabled control. A second CANCEL while already
        # disabled clears availability, matching the older MED-mode flow.
        if not self.nexo_cruise_enabled:
          self.nexo_cruise_available = False
        self.nexo_cruise_enabled = False

      ret.cruiseState.available = ret.cruiseState.available and self.nexo_cruise_available
      if self.nexo_cruise_enabled:
        ret.cruiseState.enabled = True

    ret.blockPcmEnable = not self.recent_button_interaction()
'''


def patch_values() -> None:
  text = VALUES_PATH.read_text(encoding="utf-8")
  if NEW_BLOCK not in text:
    for block in (PREVIOUS_BLOCK, OLD_BLOCK):
      if block in text:
        VALUES_PATH.write_text(text.replace(block, NEW_BLOCK, 1), encoding="utf-8")
        return
    raise RuntimeError(f"NEXO platform block was not found in {VALUES_PATH}")


def patch_carstate() -> None:
  text = CARSTATE_PATH.read_text(encoding="utf-8")

  if GEAR_INIT_NEW not in text:
    if GEAR_INIT_OLD not in text:
      raise RuntimeError(f"NEXO state initialization location was not found in {CARSTATE_PATH}")
    text = text.replace(GEAR_INIT_OLD, GEAR_INIT_NEW, 1)

  if GEAR_PARSE_NEW not in text:
    if GEAR_PARSE_OLD not in text:
      raise RuntimeError(f"NEXO gear parsing block was not found in {CARSTATE_PATH}")
    text = text.replace(GEAR_PARSE_OLD, GEAR_PARSE_NEW, 1)

  if BUTTONS_NEW not in text:
    if BUTTONS_OLD not in text:
      raise RuntimeError(f"NEXO cruise button block was not found in {CARSTATE_PATH}")
    text = text.replace(BUTTONS_OLD, BUTTONS_NEW, 1)

  CARSTATE_PATH.write_text(text, encoding="utf-8")


def main() -> None:
  patch_values()
  patch_carstate()


if __name__ == "__main__":
  main()
