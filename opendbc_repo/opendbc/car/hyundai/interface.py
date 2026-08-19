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
from opendbc.car.hyundai.nexo_acc_fault import NexoAccFaultQualifier
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.hyundai.carcontroller import CarController as HyundaiCarController
from opendbc.car.hyundai.carstate import CarState as HyundaiCarState
from opendbc.car.hyundai.radar_interface import RadarInterface
from opendbc.car.nexo_session_owner import (
  clear_owner_if_current_unlocked,
  current_owner_token,
  current_process_owns,
  owner_lock,
  read_owner_unlocked,
  restore_allowed_unlocked,
)

ButtonType = structs.CarState.ButtonEvent.Type
Ecu = structs.CarParams.Ecu
NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"
NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")
NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")
NEXO_ACC_FAULT_STARTUP_GRACE_S = 2.0


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
  """Best-effort restoration without letting an old card process undo a newer takeover.

  The owner lock serializes restore against the final NEXO re-suppress/verify
  step. An older process that exits after a newer card process has claimed SCC
  ownership returns without sending 0x28 0x80 0x01 and without clearing the
  newer process recovery marker.
  """
  caller_token = current_owner_token()
  with owner_lock():
    allowed, owner_detail = restore_allowed_unlocked(caller_token)
    if not allowed:
      message = (
        f"RESTORE SKIP reason={reason or 'unspecified'} caller={caller_token} "
        f"detail={owner_detail}"
      )
      _trace_nexo_restore(message)
      _trace_nexo_long_init(message)
      return True

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
        f"owner={caller_token} ownerCheck={owner_detail} acknowledged={restored} "
        f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}{detail}"
      )
      _trace_nexo_restore(message)
      _trace_nexo_long_init(message)
      if restored:
        _clear_nexo_takeover_marker()
        clear_owner_if_current_unlocked(caller_token)
        return True
      if attempt < retries:
        time.sleep(0.05)

    _set_nexo_takeover_marker("restore_pending")
    return False


class NexoQualifiedCarState(HyundaiCarState):
  """NEXO-only final ACC fault qualification while leaving stock cruise untouched."""

  def __init__(self, CP):
    super().__init__(CP)
    self._nexo_acc_fault = NexoAccFaultQualifier(NEXO_ACC_FAULT_STARTUP_GRACE_S)
    self._nexo_acc_last_trace = None

  def update(self, can_parsers):
    ret = super().update(can_parsers)
    if self.CP.carFingerprint != CAR.HYUNDAI_NEXO_1ST_GEN or not self.CP.openpilotLongitudinalControl:
      return ret

    raw_acc_enable = int(can_parsers[Bus.pt].vl["TCS13"]["ACCEnable"])
    decision = self._nexo_acc_fault.update(raw_acc_enable != 0, time.monotonic())
    ret.accFaulted = decision.qualified_fault

    trace_state = (raw_acc_enable, decision.qualified_fault, decision.reason, decision.healthy_seen)
    if trace_state != self._nexo_acc_last_trace:
      _trace_nexo_long_init(
        "ACCFAULT source=TCS13.ACCEnable code=NexoQualifiedCarState.update "
        f"raw={raw_acc_enable} rawFault={decision.raw_fault} qualified={decision.qualified_fault} "
        f"reason={decision.reason} rawDuration={decision.raw_fault_duration_s:.3f}s "
        f"healthySeen={decision.healthy_seen} grace={NEXO_ACC_FAULT_STARTUP_GRACE_S:.1f}s"
      )
      self._nexo_acc_last_trace = trace_state
    return ret


class NexoTracingCarController(HyundaiCarController):
  """Read-only NEXO longitudinal state trace; actuation remains in the base controller."""

  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self._nexo_control_last = None

  def update(self, CC, CS, now_nanos):
    if self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and self.CP.openpilotLongitudinalControl:
      if CC.longActive:
        mode = "SPEED_CONTROL"
      elif CC.latActive:
        mode = "MED_WAIT"
      else:
        mode = "OFF"

      button_events = []
      try:
        for event in CS.out.buttonEvents:
          button_events.append(f"{event.type}:{'down' if event.pressed else 'up'}")
      except Exception:
        pass

      blockers = []
      if not CC.enabled:
        blockers.append("CC.enabled=False")
      if not CC.longActive:
        blockers.append("CC.longActive=False")
      if not self.CP.openpilotLongitudinalControl:
        blockers.append("openpilotLongitudinalControl=False")

      snapshot = (
        mode, bool(CC.enabled), bool(CC.latActive), bool(CC.longActive),
        bool(CS.out.cruiseState.available), bool(CS.out.cruiseState.enabled), tuple(button_events),
      )
      if snapshot != self._nexo_control_last or button_events:
        _trace_nexo_long_init(
          f"CONTROL mode={mode} enabled={bool(CC.enabled)} latActive={bool(CC.latActive)} "
          f"longActive={bool(CC.longActive)} cruiseAvailable={bool(CS.out.cruiseState.available)} "
          f"cruiseEnabled={bool(CS.out.cruiseState.enabled)} "
          f"buttons={','.join(button_events) if button_events else 'none'} "
          f"longBlockers={','.join(blockers) if blockers else 'none'}"
        )
        self._nexo_control_last = snapshot

    return super().update(CC, CS, now_nanos)


ENABLE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.cancel, ButtonType.mainCruise)


class CarInterface(CarInterfaceBase):
  CarState = NexoQualifiedCarState
  CarController = NexoTracingCarController
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
      ret.vEgoStarting = 0.1
      ret.vEgoStopping = 0.3
      ret.startAccel = 1.0
      ret.longitudinalActuatorDelay = 0.5

    # NEXOdriveAI succeeds with the normal Hyundai LONG safety path. Keep the
    # NEXO FCEV pedal signal, but do not enable the experimental dynamic SCC
    # forwarding mode that blocked SCC/Tester Present and overloaded CAN2.
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
        _trace_nexo_long_init(f"START NEXOdriveAI long init bus={bus} addr=0x{addr:x}", reset=True)
        safety_text = ",".join(
          f"{cfg.safetyModel}:{int(cfg.safetyParam)}" for cfg in getattr(CP, "safetyConfigs", ())
        ) or "unavailable"
        _trace_nexo_long_init(
          f"CARPARAMS fingerprint={CP.carFingerprint} openpilotLong={bool(CP.openpilotLongitudinalControl)} "
          f"pcmCruise={getattr(CP, 'pcmCruise', 'unavailable')} "
          f"radarUnavailable={getattr(CP, 'radarUnavailable', 'unavailable')} "
          f"flags={int(CP.flags)} safety={safety_text}"
        )
        _trace_nexo_long_init(
          "UDS PLAN radar first: 10 07 then 2E 01 42; final SCC suppression: 10 03 then 28 83 01 "
          "on ecu=0x7D0 bus=0 no-subaddress. 28 83 01 suppresses positive response; physical src0 silence is the success criterion."
        )

        # Newer working XPlus programs the radar DID before the final
        # CommunicationControl. A diagnostic-session change can otherwise wake
        # the stock SCC stream after an earlier disable.
        _trace_nexo_long_init("STEP 1 run radar-track DID sequence while stock SCC remains untouched")
        tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)
        _trace_nexo_long_init(f"STEP 1 radar-track request completed={tracks_enabled}")
        if not tracks_enabled:
          _trace_nexo_long_init("FAIL radar-track activation; stock SCC was not disabled")
          raise RuntimeError("NEXO radar track activation failed")

        # From this point onward the next operation can mute factory cruise, so
        # persist recovery intent before issuing CommunicationControl.
        _set_nexo_takeover_marker("stock_scc_disabled")
        try:
          _trace_nexo_long_init("STEP 2 final SCC suppression after radar DID; verify physical source0 SCC disappearance")
          stock_scc_silent = ensure_nexo_stock_scc_silent(
            can_recv, can_send, bus=bus, addr=addr, communication_control=communication_control,
            trace=_trace_nexo_long_init, attempts=3,
          )
          _trace_nexo_long_init(f"STEP 2 physical source0 SCC silence={stock_scc_silent}")
          if not stock_scc_silent:
            raise RuntimeError("NEXO stock SCC remained active")
        except BaseException as error:
          restored = restore_nexo_stock_scc_communication(
            can_recv, can_send, bus=bus, addr=addr, reason=f"long init exception: {type(error).__name__}",
          )
          _trace_nexo_long_init(f"FAIL long init; stock SCC restore acknowledged={restored}")
          raise

        _set_nexo_takeover_marker("longitudinal_takeover_ready")
        _trace_nexo_long_init("DONE NEXO radar-then-disable sequence; physical source0 silence verified; runtime SCC guard armed by card")
      else:
        disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)

    if CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      disable_ecu(can_recv, can_send, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                   0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                   uds.MESSAGE_TYPE.NORMAL])

    # NEXO deinit must not let an older card process restore SCC after a newer
    # process has completed takeover. The ownership guard handles that race and
    # stock mode also repairs stale owner/marker state from a dead process.
    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN:
      owner_exists = bool(read_owner_unlocked())
      if nexo_stock_scc_restore_pending() or owner_exists or current_process_owns():
        restored = restore_nexo_stock_scc_communication(
          can_recv, can_send, bus=0, addr=0x7D0, reason="CarInterface.deinit",
        )
        _trace_nexo_long_init(f"DEINIT stock SCC communication restore acknowledged={restored}")
        return restored

      _trace_nexo_long_init("DEINIT no active NEXO takeover; duplicate restore skipped")
      return True

    CarInterface.init(CP, can_recv, can_send, communication_control)
