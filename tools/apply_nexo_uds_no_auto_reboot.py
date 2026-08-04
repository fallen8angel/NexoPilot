#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
  (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
  source = read(path)
  count = source.count(old)
  if count != 1:
    raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:100]!r}")
  write(path, source.replace(old, new, 1))


def patch_radar_tracks() -> None:
  path = "opendbc_repo/opendbc/car/hyundai/radar_tracks.py"
  replace_once(
    path,
    '''def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):\n''',
    '''def _format_isotp_address(address) -> str:\n  """Render AddrType values without allowing diagnostics to affect UDS control flow."""\n  try:\n    if isinstance(address, tuple):\n      tx_addr, sub_addr = address\n      rendered = f"0x{int(tx_addr):X}"\n      return rendered if sub_addr is None else f"{rendered}:sub=0x{int(sub_addr):X}"\n    return f"0x{int(address):X}"\n  except Exception:\n    return repr(address)\n\n\ndef _render_isotp_result(result) -> str:\n  try:\n    items = []\n    for address, payload in result.items():\n      try:\n        payload_text = bytes(payload).hex(" ")\n      except Exception:\n        payload_text = repr(payload)\n      items.append(f"{_format_isotp_address(address)}:{payload_text}")\n    return ", ".join(items) or "none"\n  except Exception as error:\n    return f"unavailable({type(error).__name__}: {error})"\n\n\ndef _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):\n''',
  )
  replace_once(
    path,
    '''    rendered = ", ".join(f"0x{address:X}:{bytes(payload).hex(' ')}" for address, payload in result.items())\n''',
    '''    rendered = _render_isotp_result(result)\n''',
  )
  replace_once(path, "f\"payloads={rendered or 'none'}\"", "f\"payloads={rendered}\"")


def patch_guard() -> None:
  path = "selfdrive/car/nexo_guard.py"
  replace_once(
    path,
    '''  def _prune(self, timestamp: float) -> None:\n''',
    '''  def disarm(self) -> None:\n    """Stop runtime detection while retaining the captured fault history."""\n    self.armed = False\n    self._detections.clear()\n\n  def _prune(self, timestamp: float) -> None:\n''',
  )


def patch_card() -> None:
  path = "selfdrive/car/card.py"
  replace_once(
    path,
    '''def recover_nexo_stock_cruise(params: Params, car_fingerprint: str, error: Exception) -> bool:\n  """Fall back to stock SCC after a verified NEXO longitudinal initialization failure."""\n  if car_fingerprint != "HYUNDAI_NEXO_1ST_GEN":\n    return False\n\n  reason = str(error)\n  if not any(message in reason for message in NEXO_LONGITUDINAL_INIT_FAILURES):\n    return False\n\n  params.put_bool("AlphaLongitudinalEnabled", False, block=True)\n  params.put_bool("ExperimentalMode", False, block=True)\n  # Do not reuse CarParams that were built with longitudinal control enabled.\n  # A full manager reboot makes pandad and card both restart in stock SCC mode.\n  for key in ("CarParams", "CarParamsCache", "CarParamsPersistent"):\n    params.remove(key)\n  params.put_bool("DoReboot", True, block=True)\n  cloudlog.error(f"NEXO longitudinal setup failed; rebooting into stock cruise: {reason}")\n  return True\n''',
    '''def recover_nexo_stock_cruise(params: Params, car_fingerprint: str, error: Exception) -> bool:\n  """Record a NEXO longitudinal failure without changing settings or rebooting.\n\n  The caller must stop longitudinal CAN output for the current process and make\n  a best-effort request to restore factory SCC communication. User selections\n  remain untouched so a manual vehicle restart can retry the same configuration.\n  """\n  if car_fingerprint != "HYUNDAI_NEXO_1ST_GEN":\n    return False\n\n  reason = str(error)\n  if not any(message in reason for message in NEXO_LONGITUDINAL_INIT_FAILURES):\n    return False\n\n  params.put("NexoLongitudinalFailure", reason, block=True)\n  cloudlog.error(f"NEXO longitudinal setup failed; controls latched off for this session, settings preserved: {reason}")\n  return True\n''',
  )
  replace_once(
    path,
    '''    self.nexo_stock_scc_guard = NexoStockSccRuntimeGuard(\n      not REPLAY and self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN" and self.CP.openpilotLongitudinalControl\n    )\n\n    if self.CP.secOcRequired:\n''',
    '''    self.nexo_stock_scc_guard = NexoStockSccRuntimeGuard(\n      not REPLAY and self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN" and self.CP.openpilotLongitudinalControl\n    )\n    self.nexo_long_init_failed = False\n\n    if self.CP.secOcRequired:\n''',
  )
  replace_once(
    path,
    '''  def state_update(self) -> tuple[car.CarState, structs.RadarDataT | None]:\n''',
    '''  def _handle_nexo_long_failure(self, error: Exception) -> bool:\n    self.sm.update(0)\n    record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)\n    if not recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error):\n      return False\n\n    # Restore the factory ECU stream when possible, but never change the user's\n    # longitudinal/experimental settings and never request an automatic reboot.\n    try:\n      self.CI.deinit(self.CP, *self.can_callbacks)\n    except Exception as restore_error:\n      cloudlog.exception(f"NEXO stock SCC restore request failed: {restore_error}")\n\n    self.nexo_stock_scc_guard.disarm()\n    self.nexo_long_init_failed = True\n    self.last_actuators_output = structs.CarControl.Actuators()\n    return True\n\n  def state_update(self) -> tuple[car.CarState, structs.RadarDataT | None]:\n''',
  )
  replace_once(
    path,
    '''    if self.nexo_stock_scc_guard.observe(can_list):\n      error = RuntimeError("NEXO stock SCC returned during longitudinal control")\n      record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)\n      recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n      raise error\n''',
    '''    if self.nexo_stock_scc_guard.observe(can_list):\n      error = RuntimeError("NEXO stock SCC returned during longitudinal control")\n      if not self._handle_nexo_long_failure(error):\n        raise error\n''',
  )
  replace_once(
    path,
    '''  def controls_update(self, CS: car.CarState, CC: car.CarControl):\n    """control update loop, driven by carControl"""\n\n    if not self.initialized_prev:\n''',
    '''  def controls_update(self, CS: car.CarState, CC: car.CarControl):\n    """control update loop, driven by carControl"""\n\n    if self.nexo_long_init_failed:\n      return\n\n    if not self.initialized_prev:\n''',
  )
  replace_once(
    path,
    '''      except RuntimeError as error:\n        self.sm.update(0)\n        record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)\n        recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n        raise\n      # Arm the raw-CAN guard only after the diagnostic takeover completed.\n      self.nexo_stock_scc_guard.arm()\n''',
    '''      except RuntimeError as error:\n        if self._handle_nexo_long_failure(error):\n          return\n        raise\n      self.params.remove("NexoLongitudinalFailure")\n      # Arm the raw-CAN guard only after the diagnostic takeover completed.\n      self.nexo_stock_scc_guard.arm()\n''',
  )


def patch_web_text() -> None:
  path = "system/nexo_web/nexo_diagnostics_v2.py"
  source = read(path)
  source = source.replace("[마지막 자동 복구 기록]", "[마지막 롱컨 실패 기록]")
  source = source.replace("<h2>마지막 자동 복구 기록</h2>", "<h2>마지막 롱컨 실패 기록</h2>")
  source = source.replace(
    "순정 SCC 재등장 또는 초기화 실패 직전 상태와 최근 5초 CAN 기록입니다. 재부팅 후에도 유지됩니다.",
    "순정 SCC 재등장 또는 초기화 실패 직전 상태와 최근 5초 CAN 기록입니다. 설정 자동해제나 자동 재부팅 없이 저장됩니다.",
  )
  write(path, source)


def patch_integration_validator() -> None:
  path = "tools/check_nexo_integration.py"
  replace_once(
    path,
    '''    'params.put_bool("AlphaLongitudinalEnabled", False, block=True)',\n    'params.put_bool("ExperimentalMode", False, block=True)',\n    'params.put_bool("DoReboot", True, block=True)',\n    "NexoStockSccRuntimeGuard",\n    "self.nexo_stock_scc_guard.arm()",\n    "self.nexo_stock_scc_guard.observe(can_list)",\n''',
    '''    'params.put("NexoLongitudinalFailure", reason, block=True)',\n    "self.nexo_long_init_failed",\n    "self._handle_nexo_long_failure(error)",\n    "self.CI.deinit(self.CP, *self.can_callbacks)",\n    "NexoStockSccRuntimeGuard",\n    "self.nexo_stock_scc_guard.arm()",\n    "self.nexo_stock_scc_guard.disarm()",\n    "self.nexo_stock_scc_guard.observe(can_list)",\n''',
  )
  replace_once(
    path,
    '''  for token in required:\n    require(token in source, f"stock-cruise recovery missing: {token}")\n\n\ndef validate_runtime_guard() -> None:\n''',
    '''  for token in required:\n    require(token in source, f"NEXO failure latch missing: {token}")\n\n  recovery = source[source.index("def recover_nexo_stock_cruise"):source.index("def can_comm_callbacks")]
  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):
    require(forbidden not in recovery, f"NEXO failure policy must not change settings or reboot: {forbidden}")\n\n\ndef validate_runtime_guard() -> None:\n''',
  )
  replace_once(
    path,
    '''    "len(self._detections) < self.min_frames",\n''',
    '''    "len(self._detections) < self.min_frames",\n    "def disarm(self)",\n''',
  )


def main() -> None:
  patch_radar_tracks()
  patch_guard()
  patch_card()
  patch_web_text()
  patch_integration_validator()
  print("Applied NEXO UDS logging fix and no-auto-reboot failure policy")


if __name__ == "__main__":
  main()
