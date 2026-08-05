#!/usr/bin/env python3
import os
import time
import threading
import traceback

import cereal.messaging as messaging

from cereal import car, log

from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog, ForwardingHandler

from opendbc.car import DT_CTRL, structs
from opendbc.car.can_definitions import CanData, CanRecvCallable, CanSendCallable
from opendbc.car.carlog import carlog
from opendbc.car.fw_versions import ObdCallback
from opendbc.car.car_helpers import get_car, interfaces
from opendbc.car.interfaces import CarInterfaceBase, RadarInterfaceBase
from openpilot.selfdrive.pandad import can_capnp_to_list, can_list_to_can_capnp
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.selfdrive.car.nexo_diagnostics import record_nexo_fault_snapshot
from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard
from openpilot.selfdrive.car.nexo_runtime_diagnostics import (
  record_nexo_card_crash, record_nexo_long_success, set_nexo_runtime_state,
)

REPLAY = "REPLAY" in os.environ

EventName = log.OnroadEvent.EventName
NEXO_LONGITUDINAL_INIT_FAILURES = (
  "NEXO radar track activation failed",
  "NEXO stock SCC communication could not be disabled",
  "NEXO stock SCC remained active",
  "NEXO stock SCC returned during longitudinal control",
)

# forward
carlog.addHandler(ForwardingHandler(cloudlog))


def obd_callback(params: Params) -> ObdCallback:
  def set_obd_multiplexing(obd_multiplexing: bool):
    if params.get_bool("ObdMultiplexingEnabled") != obd_multiplexing:
      cloudlog.warning(f"Setting OBD multiplexing to {obd_multiplexing}")
      params.remove("ObdMultiplexingChanged")
      params.put_bool("ObdMultiplexingEnabled", obd_multiplexing, block=True)
      params.get_bool("ObdMultiplexingChanged", block=True)
      cloudlog.warning("OBD multiplexing set successfully")
  return set_obd_multiplexing


def _safe_nexo_param_put(params: Params, key: str, value: str, *, block: bool = False) -> bool:
  try:
    params.put(key, value, block=block)
    return True
  except Exception as error:
    cloudlog.warning(f"NEXO diagnostic Params put ignored key={key}: {error}")
    return False


def _safe_nexo_param_remove(params: Params, key: str) -> bool:
  try:
    params.remove(key)
    return True
  except Exception as error:
    cloudlog.warning(f"NEXO diagnostic Params remove ignored key={key}: {error}")
    return False


def recover_nexo_stock_cruise(params: Params, car_fingerprint: str, error: Exception) -> bool:
  """Record a NEXO longitudinal failure without changing settings or rebooting.

  Diagnostic bookkeeping is deliberately non-fatal. A stale compiled Params
  registry must never prevent the factory SCC restoration path from running.
  """
  if car_fingerprint != "HYUNDAI_NEXO_1ST_GEN":
    return False

  reason = str(error)
  if not any(message in reason for message in NEXO_LONGITUDINAL_INIT_FAILURES):
    return False

  _safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)
  cloudlog.error(f"NEXO longitudinal setup failed; controls latched off for this session, settings preserved: {reason}")
  return True


def can_comm_callbacks(logcan: messaging.SubSocket, sendcan: messaging.PubSocket) -> tuple[CanRecvCallable, CanSendCallable]:
  def can_recv(wait_for_one: bool = False) -> list[list[CanData]]:
    """
    wait_for_one: wait the normal logcan socket timeout for a CAN packet, may return empty list if nothing comes

    Returns: CAN packets comprised of CanData objects for easy access
    """
    ret = []
    for can in messaging.drain_sock(logcan, wait_for_one=wait_for_one):
      ret.append([CanData(msg.address, msg.dat, msg.src) for msg in can.can])
    return ret

  def can_send(msgs: list[CanData]) -> None:
    sendcan.send(can_list_to_can_capnp(msgs, msgtype='sendcan'))

  return can_recv, can_send


class Car:
  CI: CarInterfaceBase
  RI: RadarInterfaceBase
  CP: car.CarParams

  def __init__(self, CI=None, RI=None) -> None:
    self.can_sock = messaging.sub_sock('can', timeout=20)
    self.sm = messaging.SubMaster(['pandaStates', 'carControl', 'onroadEvents', 'selfdriveState', 'radarState'])
    self.pm = messaging.PubMaster(['sendcan', 'carState', 'carParams', 'carOutput', 'liveTracks'])

    self.can_rcv_cum_timeout_counter = 0

    self.CC_prev = car.CarControl.new_message()
    self.CS_prev = car.CarState.new_message()
    self.initialized_prev = False

    self.last_actuators_output = structs.CarControl.Actuators()

    self.params = Params()

    self.can_callbacks = can_comm_callbacks(self.can_sock, self.pm.sock['sendcan'])

    is_release = self.params.get_bool("IsReleaseBranch")

    if CI is None:
      # wait for one pandaState and one CAN packet
      print("Waiting for CAN messages...")
      while True:
        can = messaging.recv_one_retry(self.can_sock)
        if len(can.can) > 0:
          break

      alpha_long_allowed = self.params.get_bool("AlphaLongitudinalEnabled")

      cached_params = None
      cached_params_raw = self.params.get("CarParamsCache")
      if cached_params_raw is not None:
        with car.CarParams.from_bytes(cached_params_raw) as _cached_params:
          cached_params = _cached_params

      self.CI = get_car(*self.can_callbacks, obd_callback(self.params), alpha_long_allowed, is_release, cached_params)
      self.RI = interfaces[self.CI.CP.carFingerprint].RadarInterface(self.CI.CP)
      self.CP = self.CI.CP

      # continue onto next fingerprinting step in pandad
      self.params.put_bool("FirmwareQueryDone", True, block=True)
    else:
      self.CI, self.CP = CI, CI.CP
      self.RI = RI

    self.CP.alternativeExperience = 0
    openpilot_enabled_toggle = self.params.get_bool("OpenpilotEnabledToggle")
    controller_available = self.CI.CC is not None and openpilot_enabled_toggle and not self.CP.dashcamOnly
    self.CP.passive = not controller_available or self.CP.dashcamOnly
    if self.CP.passive:
      safety_config = structs.CarParams.SafetyConfig()
      safety_config.safetyModel = structs.CarParams.SafetyModel.noOutput
      self.CP.safetyConfigs = [safety_config]

    self.nexo_stock_scc_guard = NexoStockSccRuntimeGuard(
      not REPLAY and self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN" and self.CP.openpilotLongitudinalControl
    )
    self.nexo_long_init_failed = False
    self.nexo_stage = "constructed"
    self.nexo_session_state = "waiting_for_long_init" if self.nexo_stock_scc_guard.enabled else "stock_cruise"
    self.nexo_last_heartbeat = 0.0
    self.nexo_restore_attempted = False
    if self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN":
      # Repair an interrupted prior takeover before this process can attempt a
      # new one. With no marker this is inert, including in normal stock cruise.
      self._restore_nexo_stock_scc_if_pending("card startup stale takeover")
      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)

    if self.CP.secOcRequired:
      # Copy user key if available
      try:
        with open("/cache/params/SecOCKey") as f:
          user_key = f.readline().strip()
          if len(user_key) == 32:
            self.params.put("SecOCKey", user_key, block=True)
      except Exception:
        pass

      secoc_key = self.params.get("SecOCKey")
      if secoc_key is not None:
        saved_secoc_key = bytes.fromhex(secoc_key.strip())
        if len(saved_secoc_key) == 16:
          self.CP.secOcKeyAvailable = True
          self.CI.CS.secoc_key = saved_secoc_key
          if controller_available:
            self.CI.CC.secoc_key = saved_secoc_key
        else:
          cloudlog.warning("Saved SecOC key is invalid")

    # Write previous route's CarParams
    prev_cp = self.params.get("CarParamsPersistent")
    if prev_cp is not None:
      self.params.put("CarParamsPrevRoute", prev_cp, block=True)

    # Write CarParams for controls and radard
    cp_bytes = self.CP.to_bytes()
    self.params.put("CarParams", cp_bytes, block=True)
    self.params.put("CarParamsCache", cp_bytes)
    self.params.put("CarParamsPersistent", cp_bytes)

    self.v_cruise_helper = VCruiseHelper(self.CP)

    self.is_metric = self.params.get_bool("IsMetric")
    self.experimental_mode = self.params.get_bool("ExperimentalMode")

    # card is driven by can recv, expected at 100Hz
    self.rk = Ratekeeper(100, print_delay_threshold=None)

  def _restore_nexo_stock_scc_if_pending(self, reason: str) -> bool:
    if self.CP.carFingerprint != "HYUNDAI_NEXO_1ST_GEN":
      return True

    try:
      from opendbc.car.hyundai.interface import nexo_stock_scc_restore_pending
      pending = nexo_stock_scc_restore_pending()
    except Exception as error:
      cloudlog.warning(f"NEXO restore marker read failed: {error}")
      pending = True

    if not pending:
      return True

    self.nexo_restore_attempted = True
    previous_stage = getattr(self, "nexo_stage", "unknown")
    self.nexo_stage = "stock_scc_restoring"
    try:
      result = self.CI.deinit(self.CP, *self.can_callbacks)
    except Exception as error:
      cloudlog.exception(f"NEXO stock SCC restore failed reason={reason}: {error}")
      self.nexo_stage = previous_stage
      return False

    try:
      pending_after = nexo_stock_scc_restore_pending()
    except Exception:
      pending_after = not bool(result)
    restored = bool(result) or not pending_after
    cloudlog.warning(f"NEXO stock SCC restore reason={reason} restored={restored}")
    self.nexo_stage = previous_stage
    return restored

  def _update_nexo_heartbeat(self, force: bool = False) -> None:
    if self.CP.carFingerprint != "HYUNDAI_NEXO_1ST_GEN":
      return
    now = time.monotonic()
    if not force and now - self.nexo_last_heartbeat < 1.0:
      return
    self.nexo_last_heartbeat = now
    try:
      self.params.put("NexoCardHeartbeatMono", f"{now:.3f}")
      self.params.put("NexoCardStage", self.nexo_stage)
      self.params.put("NexoCardSessionState", self.nexo_session_state)
    except Exception as error:
      cloudlog.warning(f"NEXO card heartbeat publish failed: {error}")

  def _handle_nexo_long_failure(self, error: Exception) -> bool:
    self.sm.update(0)
    record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)
    if not recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error):
      return False

    # This calls the dedicated restore-only path, never CarInterface.init(), so
    # it cannot re-run radar programming. Keep the marker when no ECU ack arrives.
    restored = self._restore_nexo_stock_scc_if_pending(f"longitudinal failure: {error}")
    self.nexo_stock_scc_guard.disarm()
    self.nexo_long_init_failed = True
    self.nexo_session_state = "failed_latched_stock_restored" if restored else "failed_latched_restore_pending"
    self.nexo_stage = "longitudinal_failed_latched"
    self.last_actuators_output = structs.CarControl.Actuators()
    set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage, str(error))
    self._update_nexo_heartbeat(force=True)
    return True

  def state_update(self) -> tuple[car.CarState, structs.RadarDataT | None]:
    """carState update loop, driven by can"""

    self.nexo_stage = "state_update"
    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)
    can_list = can_capnp_to_list(can_strs)
    self.sm.update(0)

    if self.nexo_stock_scc_guard.observe(can_list):
      error = RuntimeError("NEXO stock SCC returned during longitudinal control")
      if not self._handle_nexo_long_failure(error):
        raise error

    # Update carState from CAN
    CS = self.CI.update(can_list)

    # Update radar tracks from CAN
    RD: structs.RadarDataT | None = self.RI.update(can_list)

    can_rcv_valid = len(can_strs) > 0

    # Check for CAN timeout
    if not can_rcv_valid:
      self.can_rcv_cum_timeout_counter += 1

    if can_rcv_valid and REPLAY:
      self.can_log_mono_time = messaging.log_from_bytes(can_strs[0]).logMonoTime

    self.v_cruise_helper.update_v_cruise(CS, self.sm['carControl'].enabled, self.is_metric)
    if self.sm['carControl'].enabled and not self.CC_prev.enabled:
      # Use CarState w/ buttons from the step selfdrived enables on
      self.v_cruise_helper.initialize_v_cruise(self.CS_prev, self.experimental_mode)

    # TODO: mirror the carState.cruiseState struct?
    CS.vCruise = float(self.v_cruise_helper.v_cruise_kph)
    CS.vCruiseCluster = float(self.v_cruise_helper.v_cruise_cluster_kph)

    return CS, RD

  def state_publish(self, CS: car.CarState, RD: structs.RadarDataT | None):
    """carState and carParams publish loop"""

    # carParams - logged every 50 seconds (> 1 per segment)
    if self.sm.frame % int(50. / DT_CTRL) == 0:
      cp_send = messaging.new_message('carParams')
      cp_send.valid = True
      cp_send.carParams = self.CP
      self.pm.send('carParams', cp_send)

    # publish new carOutput
    co_send = messaging.new_message('carOutput')
    co_send.valid = self.sm.all_checks(['carControl'])
    co_send.carOutput.actuatorsOutput = self.last_actuators_output
    self.pm.send('carOutput', co_send)

    # kick off controlsd step while we actuate the latest carControl packet
    cs_send = messaging.new_message('carState')
    cs_send.valid = CS.canValid
    cs_send.carState = CS
    cs_send.carState.canErrorCounter = self.can_rcv_cum_timeout_counter
    cs_send.carState.cumLagMs = -self.rk.remaining * 1000.
    self.pm.send('carState', cs_send)

    if RD is not None:
      tracks_msg = messaging.new_message('liveTracks')
      tracks_msg.valid = not any(RD.errors.to_dict().values())
      tracks_msg.liveTracks = RD
      self.pm.send('liveTracks', tracks_msg)

  def controls_update(self, CS: car.CarState, CC: car.CarControl):
    """control update loop, driven by carControl"""

    if self.nexo_long_init_failed:
      self.nexo_stage = "longitudinal_failed_latched"
      self._update_nexo_heartbeat()
      return

    if not self.initialized_prev:
      self.nexo_stage = "longitudinal_initializing"
      self.nexo_session_state = "initializing"
      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)

      # Initialize CarInterface, once controls are ready
      # TODO: this can make us miss at least a few cycles when doing an ECU knockout
      try:
        self.CI.init(self.CP, *self.can_callbacks)
      except RuntimeError as error:
        if self._handle_nexo_long_failure(error):
          return
        raise
      _safe_nexo_param_remove(self.params, "NexoLongitudinalFailure")
      self.nexo_session_state = "active"
      self.nexo_stage = "longitudinal_active"
      record_nexo_long_success(self.params)
      # Arm the raw-CAN guard only after the diagnostic takeover completed.
      self.nexo_stock_scc_guard.arm()
      # signal pandad to switch to car safety mode
      self.params.put_bool("ControlsReady", True)

    if self.sm.all_alive(['carControl']):
      self.nexo_stage = "carcontroller_apply"
      # send car controls over can
      now_nanos = self.can_log_mono_time if REPLAY else int(time.monotonic() * 1e9)
      self.last_actuators_output, can_sends = self.CI.apply(CC, now_nanos)
      self.pm.send('sendcan', can_list_to_can_capnp(can_sends, msgtype='sendcan', valid=CS.canValid))

      self.CC_prev = CC

  def step(self):
    CS, RD = self.state_update()

    self.nexo_stage = "state_publish"
    self.state_publish(CS, RD)

    initialized = (not any(e.name == EventName.selfdriveInitializing for e in self.sm['onroadEvents']) and
                   self.sm.seen['onroadEvents'])
    if not self.CP.passive and initialized:
      self.controls_update(CS, self.sm['carControl'])

    self.initialized_prev = initialized
    self.CS_prev = CS
    self.nexo_stage = "idle" if not self.nexo_long_init_failed else "longitudinal_failed_latched"
    self._update_nexo_heartbeat()

  def params_thread(self, evt):
    while not evt.is_set():
      self.is_metric = self.params.get_bool("IsMetric")
      self.experimental_mode = self.params.get_bool("ExperimentalMode") and self.CP.openpilotLongitudinalControl
      time.sleep(0.1)

  def card_thread(self):
    e = threading.Event()
    t = threading.Thread(target=self.params_thread, args=(e, ))
    try:
      t.start()
      while True:
        self.step()
        self.rk.monitor_time()
    finally:
      # Any card exit means NexoPilot SCC output is ending. Restore factory SCC
      # before the process disappears; a failed ack leaves the persistent marker
      # for the next card start.
      try:
        self._restore_nexo_stock_scc_if_pending("card thread exit")
      finally:
        e.set()
        t.join()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  params = Params()
  card_process = None
  try:
    card_process = Car()
    card_process.card_thread()
  except Exception as error:
    stage = getattr(card_process, "nexo_stage", "card_constructor")
    if card_process is not None:
      # card_thread finally normally restores first. This is a second, idempotent
      # fallback for constructor/cleanup paths that did not complete.
      try:
        card_process._restore_nexo_stock_scc_if_pending("uncaught card exception")
      except Exception as restore_error:
        cloudlog.exception(f"NEXO final stock SCC restore attempt failed: {restore_error}")
    record_nexo_card_crash(params, stage, error, traceback.format_exc())
    raise


if __name__ == "__main__":
  main()
