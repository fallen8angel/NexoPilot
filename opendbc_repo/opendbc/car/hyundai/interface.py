import time
from pathlib import Path

from opendbc.car import Bus, get_safety_config, structs, uds
from opendbc.car.carlog import carlog
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import HyundaiFlags, CAR, DBC, HyundaiSafetyFlags
from opendbc.car.hyundai.radar_interface import RADAR_START_ADDR
from opendbc.car.hyundai.radar_tracks import enable_radar_tracks
from opendbc.car.hyundai.nexo_takeover import ensure_nexo_stock_scc_silent
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.radar_interface import RadarInterface

ButtonType = structs.CarState.ButtonEvent.Type
Ecu = structs.CarParams.Ecu
NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"
NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")
NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")


def _trace_nexo_long_init(message: str, reset: bool = False) -> None:
  try:
    with open(NEXO_LONG_INIT_LOG, "w" if reset else "a", encoding="utf-8") as trace:
      trace.write(f"{time.monotonic():.3f} {message}\n")
  except OSError:
    pass


def _trace_nexo_restore(message: str) -> None:
  try:
    with open(NEXO_SCC_RESTORE_LOG, "a", encoding="utf-8") as trace:
      trace.write(f"{time.time():.3f} {message}\n")
  except OSError:
    pass


def _set_nexo_takeover_marker(stage: str) -> None:
  try:
    temporary = NEXO_SCC_TAKEOVER_MARKER.with_suffix(".tmp")
    temporary.write_text(f"{time.time():.3f} {stage}\n", encoding="utf-8")
    temporary.replace(NEXO_SCC_TAKEOVER_MARKER)
  except OSError as error:
    _trace_nexo_restore(f"MARKER write failed stage={stage} detail={error}")


def nexo_stock_scc_restore_pending() -> bool:
  try:
    return NEXO_SCC_TAKEOVER_MARKER.exists()
  except OSError:
    return True


def _clear_nexo_takeover_marker() -> None:
  try:
    NEXO_SCC_TAKEOVER_MARKER.unlink(missing_ok=True)
  except OSError as error:
    _trace_nexo_restore(f"MARKER clear failed detail={error}")


def restore_nexo_stock_scc_communication(can_recv, can_send, *, bus: int = 0, addr: int = 0x7D0,
                                         reason: str = "", retries: int = 3) -> bool:
  """Best-effort, idempotent restoration of factory SCC communication.

  This never changes user settings and never requests a reboot. The persistent
  marker is cleared only after an acknowledged 0x28 0x80 0x01 response.
  """
  communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                 0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                 uds.MESSAGE_TYPE.NORMAL])
  for attempt in range(1, retries + 1):
    started = time.monotonic()
    try:
      restored = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)
      detail = ""
    except Exception as error:
      restored = False
      detail = f" detail={type(error).__name__}: {error}"

    message = (
      f"RESTORE reason={reason or 'unspecified'} attempt={attempt}/{retries} ecu=0x{addr:X} bus={bus} "
      f"acknowledged={restored} elapsed_ms={(time.monotonic() - started) * 1000:.1f}{detail}"
    )
    _trace_nexo_restore(message)
    _trace_nexo_long_init(message)
    if restored:
      _clear_nexo_takeover_marker()
      return True
    if attempt < retries:
      time.sleep(0.05)

  _set_nexo_takeover_marker("restore_pending")
  return False


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
      if is_nexo:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value
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
        _trace_nexo_long_init(f"START NEXOdriveAI long init bus={bus} addr=0x{addr:x}", reset=True)
        _trace_nexo_long_init("STEP 1 enter extended diagnostics and suppress stock SCC")
        disable_started = time.monotonic()
        _trace_nexo_long_init(f"UDS TX ecu=0x{addr:X} bus={bus} requests=10 03 then 28 83 01")
        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)
        _trace_nexo_long_init(
          f"UDS RESULT ecu=0x{addr:X} bus={bus} acknowledged={disabled} "
          f"elapsed_ms={(time.monotonic() - disable_started) * 1000:.1f}"
        )
        _trace_nexo_long_init(f"STEP 1 request completed={disabled}")
        if not disabled:
          _trace_nexo_long_init("FAIL stock SCC communication-control was not acknowledged")
          raise RuntimeError("NEXO stock SCC communication could not be disabled")

        # From this point onward a process crash can leave factory cruise muted.
        # Persist the takeover before doing any additional work.
        _set_nexo_takeover_marker("stock_scc_disabled")
        try:
          _trace_nexo_long_init("STEP 2 run NEXOdriveAI radar-track sequence")
          tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)
          _trace_nexo_long_init(f"STEP 2 radar-track request completed={tracks_enabled}")
          if not tracks_enabled:
            raise RuntimeError("NEXO radar track activation failed")

          _trace_nexo_long_init("STEP 3 re-suppress stock SCC after radar DID write and verify physical source0 silence")
          stock_scc_silent = ensure_nexo_stock_scc_silent(
            can_recv, can_send, bus=bus, addr=addr, communication_control=communication_control,
            trace=_trace_nexo_long_init, attempts=3,
          )
          _trace_nexo_long_init(f"STEP 3 verified source0 SCC silence={stock_scc_silent}")
          if not stock_scc_silent:
            raise RuntimeError("NEXO stock SCC remained active")
        except BaseException as error:
          restored = restore_nexo_stock_scc_communication(
            can_recv, can_send, bus=bus, addr=addr, reason=f"long init exception: {type(error).__name__}",
          )
          _trace_nexo_long_init(f"FAIL long init; stock SCC restore acknowledged={restored}")
          raise

        _set_nexo_takeover_marker("longitudinal_takeover_ready")
        _trace_nexo_long_init("DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card")
      else:
        disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)

    if CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      disable_ecu(can_recv, can_send, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                   0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                   uds.MESSAGE_TYPE.NORMAL])

    # NEXO restoration must only re-enable the factory SCC stream. Calling init()
    # here would run the radar programming sequence again while handling a fault.
    # A stale marker is honored even when the user has switched back to stock
    # cruise so the next card start can repair an interrupted prior takeover.
    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and (
        CP.openpilotLongitudinalControl or nexo_stock_scc_restore_pending()):
      restored = restore_nexo_stock_scc_communication(
        can_recv, can_send, bus=0, addr=0x7D0, reason="CarInterface.deinit",
      )
      _trace_nexo_long_init(f"DEINIT stock SCC communication restore acknowledged={restored}")
      return restored

    CarInterface.init(CP, can_recv, can_send, communication_control)
