import time

from opendbc.car import Bus, get_safety_config, structs, uds
from opendbc.car.carlog import carlog
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import HyundaiFlags, CAR, DBC, HyundaiSafetyFlags
from opendbc.car.hyundai.radar_interface import RADAR_START_ADDR
from opendbc.car.hyundai.radar_tracks import enable_radar_tracks
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.radar_interface import RadarInterface

ButtonType = structs.CarState.ButtonEvent.Type
Ecu = structs.CarParams.Ecu
NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"


def _trace_nexo_long_init(message: str, reset: bool = False) -> None:
  try:
    with open(NEXO_LONG_INIT_LOG, "w" if reset else "a", encoding="utf-8") as trace:
      trace.write(f"{time.monotonic():.3f} {message}\n")
  except OSError:
    pass


ENABLE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.cancel, ButtonType.mainCruise)


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  DRIVABLE_GEARS = (structs.CarState.GearShifter.sport, structs.CarState.GearShifter.manumatic)

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "hyundai"

    if ret.flags & HyundaiFlags.CANFD:
      cam_can = CanBus(None, fingerprint).CAM
      lka_steering = 0x50 in fingerprint[cam_can] or 0x110 in fingerprint[cam_can]
      CAN = CanBus(None, fingerprint, lka_steering)

      ret.alphaLongitudinalAvailable = not (ret.flags & HyundaiFlags.CANFD_NO_RADAR_DISABLE)
      if lka_steering and Ecu.adas not in [fw.ecu for fw in car_fw]:
        ret.alphaLongitudinalAvailable = False

      ret.enableBsm = 0x1ba in fingerprint[CAN.ECAN]

      if 0xFA in fingerprint[CAN.ECAN]:
        ret.flags |= HyundaiFlags.HYBRID.value

      if lka_steering:
        ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG.value
        if 0x110 in fingerprint[CAN.CAM]:
          ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
      else:
        if 0x1cf not in fingerprint[CAN.ECAN]:
          ret.flags |= HyundaiFlags.CANFD_ALT_BUTTONS.value
        if not ret.flags & HyundaiFlags.CANFD_RADAR_SCC:
          ret.flags |= HyundaiFlags.CANFD_CAMERA_SCC.value

      if 0x130 not in fingerprint[CAN.ECAN]:
        if 0x40 not in fingerprint[CAN.ECAN]:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS_2.value
        else:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS.value

      cfgs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiCanfd), ]
      if CAN.ECAN >= 4:
        cfgs.insert(0, get_safety_config(structs.CarParams.SafetyModel.noOutput))
      ret.safetyConfigs = cfgs

      if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG.value
        if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT:
          ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG_ALT.value
      if ret.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_ALT_BUTTONS.value
      if ret.flags & HyundaiFlags.CANFD_CAMERA_SCC:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value

    else:
      ret.alphaLongitudinalAvailable = not (ret.flags & (HyundaiFlags.LEGACY | HyundaiFlags.UNSUPPORTED_LONGITUDINAL))
      ret.enableBsm = 0x58b in fingerprint[0]

      if 0x485 in fingerprint[2]:
        ret.flags |= HyundaiFlags.SEND_LFA.value

      if 0x38d in fingerprint[0] or 0x38d in fingerprint[2]:
        ret.flags |= HyundaiFlags.USE_FCA.value

      if ret.flags & HyundaiFlags.LEGACY:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiLegacy)]
      else:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundai, 0)]

      if ret.flags & HyundaiFlags.CAMERA_SCC:
        ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value

      if 0x391 in fingerprint[0]:
        ret.flags |= HyundaiFlags.HAS_LDA_BUTTON.value

    ret.centerToFront = ret.wheelbase * 0.4
    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4
    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if ret.flags & HyundaiFlags.ALT_LIMITS:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS.value

    if ret.flags & HyundaiFlags.ALT_LIMITS_2:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS_2.value
      ret.dashcamOnly = True

    radar_dbc_available = Bus.radar in DBC[ret.carFingerprint]
    if candidate == CAR.HYUNDAI_NEXO_1ST_GEN:
      ret.radarUnavailable = not radar_dbc_available
    else:
      ret.radarUnavailable = RADAR_START_ADDR not in fingerprint[1] or not radar_dbc_available

    is_nexo = candidate == CAR.HYUNDAI_NEXO_1ST_GEN
    ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable
    ret.pcmCruise = not ret.openpilotLongitudinalControl
    ret.startingState = True
    ret.vEgoStarting = 0.1
    ret.startAccel = 1.0
    ret.longitudinalActuatorDelay = 0.5

    if is_nexo:
      ret.longitudinalTuning.kpBP = [0., 5. * CV.KPH_TO_MS, 10. * CV.KPH_TO_MS,
                                    30. * CV.KPH_TO_MS, 130. * CV.KPH_TO_MS]
      ret.longitudinalTuning.kpV = [1.2, 1.05, 1.0, 0.92, 0.55]
      ret.longitudinalTuning.kiBP = [0., 130. * CV.KPH_TO_MS]
      ret.longitudinalTuning.kiV = [0.2, 0.1]
      ret.stoppingDecelRate = 0.3
      ret.stopAccel = -2.0
      ret.vEgoStarting = 0.3
      ret.vEgoStopping = 0.3
      ret.startAccel = 1.0
      ret.longitudinalActuatorDelay = 0.5

    if ret.openpilotLongitudinalControl:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value
    if ret.flags & HyundaiFlags.HYBRID:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.HYBRID_GAS.value
    elif ret.flags & HyundaiFlags.EV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.EV_GAS.value
    elif ret.flags & HyundaiFlags.FCEV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.FCEV_GAS.value

    if candidate == CAR.KIA_OPTIMA_G4_FL:
      ret.steerActuatorDelay = 0.2

    if candidate in (CAR.KIA_OPTIMA_H,):
      ret.dashcamOnly = True

    return ret

  @staticmethod
  def init(CP, can_recv, can_send, communication_control=None):
    if communication_control is None:
      communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                     0x80 | uds.CONTROL_TYPE.DISABLE_RX_DISABLE_TX,
                                     uds.MESSAGE_TYPE.NORMAL])

    if CP.openpilotLongitudinalControl and not (CP.flags & (HyundaiFlags.CANFD_CAMERA_SCC | HyundaiFlags.CAMERA_SCC)):
      addr, bus = 0x7d0, CanBus(CP).ECAN if CP.flags & HyundaiFlags.CANFD else 0
      if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value:
        addr, bus = 0x730, CanBus(CP).ECAN

      disabling_normal_comms = communication_control[1] == (0x80 | uds.CONTROL_TYPE.DISABLE_RX_DISABLE_TX)
      is_nexo = CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN

      if is_nexo and disabling_normal_comms:
        _trace_nexo_long_init(f"START Carrot-style long init bus={bus} addr=0x{addr:x}", reset=True)
        _trace_nexo_long_init("STEP 1 request stock SCC communication suppression")
        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)
        _trace_nexo_long_init(f"STEP 1 request completed={disabled}")
        if not disabled:
          carlog.warning(f"NEXO stock SCC communication-control was not acknowledged on bus {bus}")

        _trace_nexo_long_init("STEP 2 request radar-track activation")
        tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)
        _trace_nexo_long_init(f"STEP 2 radar-track request completed={tracks_enabled}")
        if not tracks_enabled:
          enable_communication = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                        0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                        uds.MESSAGE_TYPE.NORMAL])
          disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=enable_communication)
          _trace_nexo_long_init("FAIL radar tracks; requested stock communication restore")
          raise RuntimeError("NEXO radar track activation failed")

        # Match the working Carrot sequence exactly: do not issue a second
        # communication-control request after writing radar DID 0x0142. On NEXO
        # that second request can drop the cluster-facing FCA status and light
        # the forward-collision warning even though radar tracks are healthy.
        _trace_nexo_long_init("DONE Carrot-style disable-then-radar sequence")
      else:
        disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)

    if CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      disable_ecu(can_recv, can_send, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                   0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                   uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)
