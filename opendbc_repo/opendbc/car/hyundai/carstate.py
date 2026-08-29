from collections import deque
import copy
import math
import os
import time

from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.nexo_med import NexoMedStateManager
from opendbc.car.hyundai.values import HyundaiFlags, CAR, DBC, Buttons, CarControllerParams
from opendbc.car.interfaces import CarStateBase
ButtonType = structs.CarState.ButtonEvent.Type

PREV_BUTTON_SAMPLES = 8
CLUSTER_SAMPLE_RATE = 20  # frames
STANDSTILL_THRESHOLD = 12 * 0.03125
VNAVI_STATE_PATH = "/dev/shm/nexopilot_vnavi"
VNAVI_VIRTUAL_DISTANCE_FACTOR = 6.0


def _write_vnavi_state(active: bool, speed_limit_kph: float, distance_m: float) -> None:
  """Publish vNAVI state without making opendbc depend on the openpilot Python package."""
  tmp_path = f"{VNAVI_STATE_PATH}.{os.getpid()}.tmp"
  try:
    with open(tmp_path, "w", encoding="utf-8") as f:
      f.write(f"{time.monotonic():.3f},{1 if active else 0},{float(speed_limit_kph):.1f},{max(0.0, float(distance_m)):.1f}\n")
    os.replace(tmp_path, VNAVI_STATE_PATH)
  except OSError:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass

# Cancel button can sometimes be ACC pause/resume button, main button can also enable on some cars
ENABLE_BUTTONS = (Buttons.RES_ACCEL, Buttons.SET_DECEL, Buttons.CANCEL)
BUTTONS_DICT = {Buttons.RES_ACCEL: ButtonType.accelCruise, Buttons.SET_DECEL: ButtonType.decelCruise,
                Buttons.GAP_DIST: ButtonType.gapAdjustCruise, Buttons.CANCEL: ButtonType.cancel}


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])

    self.cruise_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.main_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.lda_button = 0

    self.gear_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                          "GEAR_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS else \
                          "GEAR_ALT_2" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS_2 else \
                          "GEAR_SHIFTER"
    if CP.flags & HyundaiFlags.CANFD:
      self.shifter_values = can_define.dv[self.gear_msg_canfd]["GEAR"]
    elif CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      self.shifter_values = can_define.dv["ELECT_GEAR"]["Elect_Gear_Shifter"]
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      self.shifter_values = can_define.dv["CLU15"]["CF_Clu_Gear"]
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      self.shifter_values = can_define.dv["TCU12"]["CUR_GR"]
    elif CP.flags & HyundaiFlags.FCEV:
      self.shifter_values = can_define.dv["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
    else:
      self.shifter_values = can_define.dv["LVR12"]["CF_Lvr_Gear"]

    self.accelerator_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                                 "ACCELERATOR_ALT" if CP.flags & HyundaiFlags.HYBRID else \
                                 "ACCELERATOR_BRAKE_ALT"
    self.cruise_btns_msg_canfd = "CRUISE_BUTTONS_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else \
                                 "CRUISE_BUTTONS"
    self.is_metric = False
    self.buttons_counter = 0

    self.cruise_info = {}

    # On some cars, CLU15->CF_Clu_VehicleSpeed can oscillate faster than the dash updates. Sample at 5 Hz
    self.cluster_speed = 0
    self.cluster_speed_counter = CLUSTER_SAMPLE_RATE

    self.params = CarControllerParams(CP)
    # NEXO learned gear fallback: keep the last valid gear when the raw value is transient/unknown.
    self.gear_shifter = structs.CarState.GearShifter.park
    # Stock-cruise compatibility templates. NEXO openpilot longitudinal builds
    # complete SCC11/12/14 frames directly and must not register the silenced
    # stock SCC stream as a CANParser alive requirement.
    self.scc11 = {}
    self.scc12 = {}
    self.scc14 = {}

    # NEXO stock-navigation camera state (Navi_HU / CAN 0x544).
    # Exact event distance is not exposed on this generation, so use Carrot's
    # speed-based virtual-distance fallback until an exact NEXO distance CAN is validated.
    self.vnavi_total_distance = 0.0
    self.vnavi_target_distance = 0.0
    self.vnavi_active = False
    self.vnavi_speed = 0
    self.vnavi_publish_counter = 0

    # With stock SCC intentionally silent, NEXO cruise state must be owned by
    # the physical MODE/SET/RES/CANCEL buttons rather than TCS13.ACC_REQ.
    self.nexo_med = None
    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and CP.openpilotLongitudinalControl:
      self.nexo_med = NexoMedStateManager(
        ButtonType, BUTTONS_DICT, create_button_events, CV.KPH_TO_MS, CV.MPH_TO_KPH,
      )

  def _update_vnavi(self, cp, ret) -> None:
    self.vnavi_total_distance += max(0.0, ret.vEgo) * 0.01
    timestamp = max(cp.ts_nanos.get("Navi_HU", {}).values(), default=0)
    age = getattr(cp, "_last_update_nanos", timestamp) - timestamp
    fresh = timestamp > 0 and 0 <= age <= 1_000_000_000
    navi = cp.vl["Navi_HU"]
    speed_limit = int(navi["SpeedLim_Nav_Clu"])
    camera_active = fresh and int(navi["SpeedLim_Nav_Cam"]) == 1 and 0 < speed_limit < 255

    was_active = self.vnavi_active
    old_speed = self.vnavi_speed
    if camera_active:
      if not self.vnavi_active or speed_limit != self.vnavi_speed:
        self.vnavi_target_distance = self.vnavi_total_distance + speed_limit * VNAVI_VIRTUAL_DISTANCE_FACTOR
      self.vnavi_active = True
      self.vnavi_speed = speed_limit
      distance = max(0.0, self.vnavi_target_distance - self.vnavi_total_distance)
    else:
      self.vnavi_active = False
      self.vnavi_speed = 0
      self.vnavi_target_distance = self.vnavi_total_distance
      distance = 0.0

    # Publish at 10 Hz, plus immediately on activation/deactivation or limit changes.
    self.vnavi_publish_counter += 1
    state_changed = was_active != self.vnavi_active or old_speed != self.vnavi_speed
    if state_changed or self.vnavi_publish_counter >= 10:
      _write_vnavi_state(self.vnavi_active, self.vnavi_speed, distance)
      self.vnavi_publish_counter = 0

  def recent_button_interaction(self) -> bool:
    # On some newer model years, the CANCEL button acts as a pause/resume button based on the PCM state
    # To avoid re-engaging when openpilot cancels, check user engagement intention via buttons
    # Main button also can trigger an engagement on these cars
    return any(btn in ENABLE_BUTTONS for btn in self.cruise_buttons) or any(self.main_buttons)

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    if self.CP.flags & HyundaiFlags.CANFD:
      return self.update_canfd(can_parsers)

    ret = structs.CarState()
    cp_cruise = cp_cam if self.CP.flags & HyundaiFlags.CAMERA_SCC else cp
    self.is_metric = cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"] == 0
    speed_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    ret.doorOpen = any([cp.vl["CGW1"]["CF_Gway_DrvDrSw"], cp.vl["CGW1"]["CF_Gway_AstDrSw"],
                        cp.vl["CGW2"]["CF_Gway_RLDrSw"], cp.vl["CGW2"]["CF_Gway_RRDrSw"]])

    ret.seatbeltUnlatched = cp.vl["CGW1"]["CF_Gway_DrvSeatBeltSw"] == 0

    self.parse_wheel_speeds(ret,
      cp.vl["WHL_SPD11"]["WHL_SPD_FL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_FR"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RR"],
    )
    ret.standstill = cp.vl["WHL_SPD11"]["WHL_SPD_FL"] <= STANDSTILL_THRESHOLD and cp.vl["WHL_SPD11"]["WHL_SPD_RR"] <= STANDSTILL_THRESHOLD

    self.cluster_speed_counter += 1
    if self.cluster_speed_counter > CLUSTER_SAMPLE_RATE:
      self.cluster_speed = cp.vl["CLU15"]["CF_Clu_VehicleSpeed"]
      self.cluster_speed_counter = 0

      # Mimic how dash converts to imperial.
      # Sorento is the only platform where CF_Clu_VehicleSpeed is already imperial when not is_metric
      # TODO: CGW_USM1->CF_Gway_DrLockSoundRValue may describe this
      if not self.is_metric and self.CP.carFingerprint not in (CAR.KIA_SORENTO,):
        self.cluster_speed = math.floor(self.cluster_speed * CV.KPH_TO_MPH + CV.KPH_TO_MPH)

    ret.vEgoCluster = self.cluster_speed * speed_conv

    ret.steeringAngleDeg = cp.vl["SAS11"]["SAS_Angle"]
    ret.steeringRateDeg = cp.vl["SAS11"]["SAS_Speed"]
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(
      50, cp.vl["CGW1"]["CF_Gway_TurnSigLh"], cp.vl["CGW1"]["CF_Gway_TurnSigRh"])
    ret.steeringTorque = cp.vl["MDPS12"]["CR_Mdps_StrColTq"]
    ret.steeringTorqueEps = cp.vl["MDPS12"]["CR_Mdps_OutTq"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    ret.steerFaultTemporary = cp.vl["MDPS12"]["CF_Mdps_ToiUnavail"] != 0 or cp.vl["MDPS12"]["CF_Mdps_ToiFlt"] != 0

    # cruise state
    if self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
        # The NEXO MED manager applies the authoritative state after CLU11 is
        # decoded below. Do not feed the outgoing SCC request back into its own
        # activation gate through TCS13.ACC_REQ.
        ret.cruiseState.available = False
        ret.cruiseState.enabled = False
      else:
        ret.cruiseState.available = cp.vl["TCS13"]["ACCEnable"] == 0
        ret.cruiseState.enabled = cp.vl["TCS13"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
      ret.cruiseState.nonAdaptive = False
    else:
      ret.cruiseState.available = cp_cruise.vl["SCC11"]["MainMode_ACC"] == 1
      ret.cruiseState.enabled = cp_cruise.vl["SCC12"]["ACCMode"] != 0
      ret.cruiseState.standstill = cp_cruise.vl["SCC11"]["SCCInfoDisplay"] == 4.
      ret.cruiseState.nonAdaptive = cp_cruise.vl["SCC11"]["SCCInfoDisplay"] == 2.
      ret.cruiseState.speed = cp_cruise.vl["SCC11"]["VSetDis"] * speed_conv

    ret.brake = 0
    ret.brakePressed = cp.vl["TCS13"]["DriverOverride"] == 2
    ret.brakeHoldActive = cp.vl["TCS15"]["AVH_LAMP"] == 2
    ret.parkingBrake = cp.vl["TCS13"]["PBRAKE_ACT"] == 1
    ret.espDisabled = cp.vl["TCS11"]["TCS_PAS"] == 1
    ret.espActive = cp.vl["TCS11"]["ABS_ACT"] == 1
    acc_faulted = cp.vl["TCS13"]["ACCEnable"] != 0
    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and self.CP.openpilotLongitudinalControl:
      ret.accFaulted = False
    else:
      ret.accFaulted = acc_faulted

    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV | HyundaiFlags.FCEV):
      if self.CP.flags & HyundaiFlags.FCEV:
        ret.gasPressed = cp.vl["FCEV_ACCELERATOR"]["ACCELERATOR_PEDAL"] > 0
      elif self.CP.flags & HyundaiFlags.HYBRID:
        ret.gasPressed = cp.vl["E_EMS11"]["CR_Vcu_AccPedDep_Pos"] > 0
      else:
        ret.gasPressed = cp.vl["E_EMS11"]["Accel_Pedal_Pos"] > 0
    else:
      ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])

    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
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

    if not self.CP.openpilotLongitudinalControl or self.CP.flags & HyundaiFlags.CAMERA_SCC:
      aeb_src = "FCA11" if self.CP.flags & HyundaiFlags.USE_FCA.value else "SCC12"
      aeb_sig = "FCA_CmdAct" if self.CP.flags & HyundaiFlags.USE_FCA.value else "AEB_CmdAct"
      aeb_warning = cp_cruise.vl[aeb_src]["CF_VSM_Warn"] != 0
      scc_warning = cp_cruise.vl["SCC12"]["TakeOverReq"] == 1
      aeb_braking = cp_cruise.vl[aeb_src]["CF_VSM_DecCmdAct"] != 0 or cp_cruise.vl[aeb_src][aeb_sig] != 0
      ret.stockFcw = (aeb_warning or scc_warning) and not aeb_braking
      ret.stockAeb = aeb_warning and aeb_braking

    if self.CP.enableBsm:
      ret.leftBlindspot = cp.vl["LCA11"]["CF_Lca_IndLeft"] != 0
      ret.rightBlindspot = cp.vl["LCA11"]["CF_Lca_IndRight"] != 0

    self.lkas11 = copy.copy(cp_cam.vl["LKAS11"])
    self.clu11 = copy.copy(cp.vl["CLU11"])
    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and not self.CP.openpilotLongitudinalControl:
      if cp_cruise.vl["SCC11"]:
        self.scc11 = copy.copy(cp_cruise.vl["SCC11"])
      if cp_cruise.vl["SCC12"]:
        self.scc12 = copy.copy(cp_cruise.vl["SCC12"])
      if cp_cruise.vl["SCC14"]:
        self.scc14 = copy.copy(cp_cruise.vl["SCC14"])
    self.steer_state = cp.vl["MDPS12"]["CF_Mdps_ToiActive"]
    prev_cruise_buttons = self.cruise_buttons[-1]
    prev_main_buttons = self.main_buttons[-1]
    prev_lda_button = self.lda_button
    self.cruise_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"])
    self.main_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwMain"])
    if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
      self.lda_button = cp.vl["BCM_PO_11"]["LDA_BTN"]

    main_button_events = create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise})

    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *main_button_events,
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    if self.nexo_med is not None:
      driving_gear = ret.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low)
      ret.buttonEvents = self.nexo_med.update(
        ret,
        self.main_buttons[-1],
        self.cruise_buttons[-1],
        self.is_metric,
        ret.buttonEvents,
        driving_gear,
      )
      self.nexo_med.apply_to_car_state(ret)

    ret.blockPcmEnable = not self.recent_button_interaction()

    if ret.vEgo < (self.CP.minSteerSpeed + 2.) and self.CP.minSteerSpeed > 10.:
      self.low_speed_alert = True
    if ret.vEgo > (self.CP.minSteerSpeed + 4.):
      self.low_speed_alert = False
    ret.lowSpeedAlert = self.low_speed_alert

    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
      self._update_vnavi(cp, ret)
    return ret

  def update_button_enable(self, button_events):
    if self.nexo_med is not None:
      # MODE enters and enables the lateral MED session. SET/RES only changes
      # the manager's independent speed-control state.
      return self.nexo_med.consume_enable_pulse()
    return super().update_button_enable(button_events)

  def update_canfd(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    ret = structs.CarState()

    self.is_metric = cp.vl["CRUISE_BUTTONS_ALT"]["DISTANCE_UNIT"] != 1
    speed_factor = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    if self.CP.flags & (HyundaiFlags.EV | HyundaiFlags.HYBRID):
      ret.gasPressed = cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL"] > 1e-5
    else:
      ret.gasPressed = bool(cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL_PRESSED"])

    ret.brakePressed = cp.vl["TCS"]["DriverBraking"] == 1
    ret.doorOpen = cp.vl["DOORS_SEATBELTS"]["DRIVER_DOOR"] == 1
    ret.seatbeltUnlatched = cp.vl["DOORS_SEATBELTS"]["DRIVER_SEATBELT"] == 0

    gear = cp.vl[self.gear_msg_canfd]["GEAR"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdFLVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdFRVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdRLVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdRRVal"],
    )
    ret.standstill = cp.vl["WHEEL_SPEEDS"]["WHL_SpdFLVal"] <= STANDSTILL_THRESHOLD and cp.vl["WHEEL_SPEEDS"]["WHL_SpdFRVal"] <= STANDSTILL_THRESHOLD and \
                     cp.vl["WHEEL_SPEEDS"]["WHL_SpdRLVal"] <= STANDSTILL_THRESHOLD and cp.vl["WHEEL_SPEEDS"]["WHL_SpdRRVal"] <= STANDSTILL_THRESHOLD

    ret.steeringRateDeg = cp.vl["STEERING_SENSORS"]["STEERING_RATE"]
    ret.steeringAngleDeg = cp.vl["STEERING_SENSORS"]["STEERING_ANGLE"]
    ret.steeringTorque = cp.vl["MDPS"]["MDPS_StrTqSnsrVal"]
    ret.steeringTorqueEps = cp.vl["MDPS"]["MDPS_OutTqVal"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    ret.steerFaultTemporary = cp.vl["MDPS"]["MDPS_LkaFailSta"] != 0

    left_blinker_sig, right_blinker_sig = "LEFT_LAMP", "RIGHT_LAMP"
    if self.CP.carFingerprint == CAR.HYUNDAI_KONA_EV_2ND_GEN:
      left_blinker_sig, right_blinker_sig = "LEFT_LAMP_ALT", "RIGHT_LAMP_ALT"
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["BLINKERS"][left_blinker_sig],
                                                                      cp.vl["BLINKERS"][right_blinker_sig])
    if self.CP.enableBsm:
      ret.leftBlindspot = bool(cp.vl["ADAS_CMD_50_50ms"]["BCW_LtIndSta"])
      ret.rightBlindspot = bool(cp.vl["ADAS_CMD_50_50ms"]["BCW_RtIndSta"])

    ret.cruiseState.available = cp.vl["TCS"]["ACCEnable"] == 0
    if self.CP.openpilotLongitudinalControl:
      ret.cruiseState.enabled = cp.vl["TCS"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
    else:
      cp_cruise_info = cp_cam if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC else cp
      ret.cruiseState.enabled = cp_cruise_info.vl["SCC_CONTROL"]["ACCMode"] in (1, 2)
      ret.cruiseState.standstill = cp_cruise_info.vl["SCC_CONTROL"]["CRUISE_STANDSTILL"] == 1
      ret.cruiseState.speed = cp_cruise_info.vl["SCC_CONTROL"]["VSetDis"] * speed_factor
      self.cruise_info = copy.copy(cp_cruise_info.vl["SCC_CONTROL"])

    if self.CP.flags & HyundaiFlags.EV:
      ret.cruiseState.nonAdaptive = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["MSLA_ENABLED"] == 1

    prev_cruise_buttons = self.cruise_buttons[-1]
    prev_main_buttons = self.main_buttons[-1]
    prev_lda_button = self.lda_button
    self.cruise_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["CRUISE_BUTTONS"])
    self.main_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["ADAPTIVE_CRUISE_MAIN_BTN"])
    self.lda_button = cp.vl[self.cruise_btns_msg_canfd]["LDA_BTN"]
    self.buttons_counter = cp.vl[self.cruise_btns_msg_canfd]["COUNTER"]
    ret.accFaulted = cp.vl["TCS"]["ACCEnable"] != 0

    if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      self.lfa_block_msg = copy.copy(cp_cam.vl["CAM_0x362"] if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT
                                          else cp_cam.vl["CAM_0x2a4"])

    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise}),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]
    ret.blockPcmEnable = not self.recent_button_interaction()
    return ret

  def get_can_parsers_canfd(self, CP):
    msgs = []
    if not (CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS):
      msgs += [("CRUISE_BUTTONS", 1)]
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], msgs, CanBus(CP).ECAN),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus(CP).CAM),
    }

  def get_can_parsers(self, CP):
    if CP.flags & HyundaiFlags.CANFD:
      return self.get_can_parsers_canfd(CP)

    pt_msgs = [("Navi_HU", math.nan)] if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN else []
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_msgs, 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
