#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
  target = ROOT / path
  source = target.read_text(encoding="utf-8")
  if old not in source:
    raise AssertionError(f"expected block not found in {path}: {old[:120]!r}")
  target.write_text(source.replace(old, new, 1), encoding="utf-8")


def write(path: str, content: str) -> None:
  (ROOT / path).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Hyundai interface: persistent takeover marker and idempotent stock SCC restore
# ---------------------------------------------------------------------------
replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "import time\n",
  "import time\nfrom pathlib import Path\n",
)

replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  'NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"\n',
  'NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"\n'
  'NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")\n'
  'NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")\n',
)

replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  '''def _trace_nexo_long_init(message: str, reset: bool = False) -> None:\n  try:\n    with open(NEXO_LONG_INIT_LOG, "w" if reset else "a", encoding="utf-8") as trace:\n      trace.write(f"{time.monotonic():.3f} {message}\\n")\n  except OSError:\n    pass\n\n\nENABLE_BUTTONS''',
  '''def _trace_nexo_long_init(message: str, reset: bool = False) -> None:\n  try:\n    with open(NEXO_LONG_INIT_LOG, "w" if reset else "a", encoding="utf-8") as trace:\n      trace.write(f"{time.monotonic():.3f} {message}\\n")\n  except OSError:\n    pass\n\n\ndef _trace_nexo_restore(message: str) -> None:\n  try:\n    with open(NEXO_SCC_RESTORE_LOG, "a", encoding="utf-8") as trace:\n      trace.write(f"{time.time():.3f} {message}\\n")\n  except OSError:\n    pass\n\n\ndef _set_nexo_takeover_marker(stage: str) -> None:\n  try:\n    temporary = NEXO_SCC_TAKEOVER_MARKER.with_suffix(".tmp")\n    temporary.write_text(f"{time.time():.3f} {stage}\\n", encoding="utf-8")\n    temporary.replace(NEXO_SCC_TAKEOVER_MARKER)\n  except OSError as error:\n    _trace_nexo_restore(f"MARKER write failed stage={stage} detail={error}")\n\n\ndef nexo_stock_scc_restore_pending() -> bool:\n  try:\n    return NEXO_SCC_TAKEOVER_MARKER.exists()\n  except OSError:\n    return True\n\n\ndef _clear_nexo_takeover_marker() -> None:\n  try:\n    NEXO_SCC_TAKEOVER_MARKER.unlink(missing_ok=True)\n  except OSError as error:\n    _trace_nexo_restore(f"MARKER clear failed detail={error}")\n\n\ndef restore_nexo_stock_scc_communication(can_recv, can_send, *, bus: int = 0, addr: int = 0x7D0,\n                                         reason: str = "", retries: int = 3) -> bool:\n  \"\"\"Best-effort, idempotent restoration of factory SCC communication.\n\n  This never changes user settings and never requests a reboot. The persistent\n  marker is cleared only after an acknowledged 0x28 0x80 0x01 response.\n  \"\"\"\n  communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,\n                                 0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,\n                                 uds.MESSAGE_TYPE.NORMAL])\n  for attempt in range(1, retries + 1):\n    started = time.monotonic()\n    try:\n      restored = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n      detail = ""\n    except Exception as error:\n      restored = False\n      detail = f" detail={type(error).__name__}: {error}"\n\n    message = (\n      f"RESTORE reason={reason or 'unspecified'} attempt={attempt}/{retries} ecu=0x{addr:X} bus={bus} "\n      f"acknowledged={restored} elapsed_ms={(time.monotonic() - started) * 1000:.1f}{detail}"\n    )\n    _trace_nexo_restore(message)\n    _trace_nexo_long_init(message)\n    if restored:\n      _clear_nexo_takeover_marker()\n      return True\n    if attempt < retries:\n      time.sleep(0.05)\n\n  _set_nexo_takeover_marker("restore_pending")\n  return False\n\n\nENABLE_BUTTONS''',
)

old_nexo_init = '''      if is_nexo and disabling_normal_comms:\n        _trace_nexo_long_init(f"START NEXOdriveAI long init bus={bus} addr=0x{addr:x}", reset=True)\n        _trace_nexo_long_init("STEP 1 enter extended diagnostics and suppress stock SCC")\n        disable_started = time.monotonic()\n        _trace_nexo_long_init(f"UDS TX ecu=0x{addr:X} bus={bus} requests=10 03 then 28 83 01")\n        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n        _trace_nexo_long_init(\n          f"UDS RESULT ecu=0x{addr:X} bus={bus} acknowledged={disabled} "\n          f"elapsed_ms={(time.monotonic() - disable_started) * 1000:.1f}"\n        )\n        _trace_nexo_long_init(f"STEP 1 request completed={disabled}")\n        if not disabled:\n          _trace_nexo_long_init("FAIL stock SCC communication-control was not acknowledged")\n          raise RuntimeError("NEXO stock SCC communication could not be disabled")\n\n        _trace_nexo_long_init("STEP 2 run NEXOdriveAI radar-track sequence")\n        tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)\n        _trace_nexo_long_init(f"STEP 2 radar-track request completed={tracks_enabled}")\n        if not tracks_enabled:\n          enable_communication = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,\n                                        0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,\n                                        uds.MESSAGE_TYPE.NORMAL])\n          disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=enable_communication)\n          _trace_nexo_long_init("FAIL radar tracks; requested stock communication restore")\n          raise RuntimeError("NEXO radar track activation failed")\n\n        _trace_nexo_long_init("DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card")\n      else:\n'''
new_nexo_init = '''      if is_nexo and disabling_normal_comms:\n        _trace_nexo_long_init(f"START NEXOdriveAI long init bus={bus} addr=0x{addr:x}", reset=True)\n        _trace_nexo_long_init("STEP 1 enter extended diagnostics and suppress stock SCC")\n        disable_started = time.monotonic()\n        _trace_nexo_long_init(f"UDS TX ecu=0x{addr:X} bus={bus} requests=10 03 then 28 83 01")\n        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n        _trace_nexo_long_init(\n          f"UDS RESULT ecu=0x{addr:X} bus={bus} acknowledged={disabled} "\n          f"elapsed_ms={(time.monotonic() - disable_started) * 1000:.1f}"\n        )\n        _trace_nexo_long_init(f"STEP 1 request completed={disabled}")\n        if not disabled:\n          _trace_nexo_long_init("FAIL stock SCC communication-control was not acknowledged")\n          raise RuntimeError("NEXO stock SCC communication could not be disabled")\n\n        # From this point onward a process crash can leave factory cruise muted.\n        # Persist the takeover before doing any additional work.\n        _set_nexo_takeover_marker("stock_scc_disabled")\n        try:\n          _trace_nexo_long_init("STEP 2 run NEXOdriveAI radar-track sequence")\n          tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)\n          _trace_nexo_long_init(f"STEP 2 radar-track request completed={tracks_enabled}")\n          if not tracks_enabled:\n            raise RuntimeError("NEXO radar track activation failed")\n        except BaseException as error:\n          restored = restore_nexo_stock_scc_communication(\n            can_recv, can_send, bus=bus, addr=addr, reason=f"long init exception: {type(error).__name__}",\n          )\n          _trace_nexo_long_init(f"FAIL long init; stock SCC restore acknowledged={restored}")\n          raise\n\n        _set_nexo_takeover_marker("longitudinal_takeover_ready")\n        _trace_nexo_long_init("DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card")\n      else:\n'''
replace_once("opendbc_repo/opendbc/car/hyundai/interface.py", old_nexo_init, new_nexo_init)

replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  '''    # NEXO restoration must only re-enable the factory SCC stream. Calling init()\n    # here would run the radar programming sequence again while handling a fault.\n    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and CP.openpilotLongitudinalControl:\n      addr, bus = 0x7D0, 0\n      restored = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n      _trace_nexo_long_init(f"DEINIT stock SCC communication restore acknowledged={restored}")\n      return\n\n    CarInterface.init(CP, can_recv, can_send, communication_control)''',
  '''    # NEXO restoration must only re-enable the factory SCC stream. Calling init()\n    # here would run the radar programming sequence again while handling a fault.\n    # A stale marker is honored even when the user has switched back to stock\n    # cruise so the next card start can repair an interrupted prior takeover.\n    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and (\n        CP.openpilotLongitudinalControl or nexo_stock_scc_restore_pending()):\n      restored = restore_nexo_stock_scc_communication(\n        can_recv, can_send, bus=0, addr=0x7D0, reason="CarInterface.deinit",\n      )\n      _trace_nexo_long_init(f"DEINIT stock SCC communication restore acknowledged={restored}")\n      return restored\n\n    CarInterface.init(CP, can_recv, can_send, communication_control)''',
)

# ---------------------------------------------------------------------------
# card: diagnostics can never kill control; restore on startup/failure/exit/crash
# ---------------------------------------------------------------------------
replace_once(
  "selfdrive/car/card.py",
  '''def recover_nexo_stock_cruise(params: Params, car_fingerprint: str, error: Exception) -> bool:\n  \"\"\"Record a NEXO longitudinal failure without changing settings or rebooting.\n\n  The caller must stop longitudinal CAN output for the current process and make\n  a best-effort request to restore factory SCC communication. User selections\n  remain untouched so a manual vehicle restart can retry the same configuration.\n  \"\"\"\n  if car_fingerprint != "HYUNDAI_NEXO_1ST_GEN":\n    return False\n\n  reason = str(error)\n  if not any(message in reason for message in NEXO_LONGITUDINAL_INIT_FAILURES):\n    return False\n\n  params.put("NexoLongitudinalFailure", reason, block=True)\n  cloudlog.error(f"NEXO longitudinal setup failed; controls latched off for this session, settings preserved: {reason}")\n  return True\n''',
  '''def _safe_nexo_param_put(params: Params, key: str, value: str, *, block: bool = False) -> bool:\n  try:\n    params.put(key, value, block=block)\n    return True\n  except Exception as error:\n    cloudlog.warning(f"NEXO diagnostic Params put ignored key={key}: {error}")\n    return False\n\n\ndef _safe_nexo_param_remove(params: Params, key: str) -> bool:\n  try:\n    params.remove(key)\n    return True\n  except Exception as error:\n    cloudlog.warning(f"NEXO diagnostic Params remove ignored key={key}: {error}")\n    return False\n\n\ndef recover_nexo_stock_cruise(params: Params, car_fingerprint: str, error: Exception) -> bool:\n  \"\"\"Record a NEXO longitudinal failure without changing settings or rebooting.\n\n  Diagnostic bookkeeping is deliberately non-fatal. A stale compiled Params\n  registry must never prevent the factory SCC restoration path from running.\n  \"\"\"\n  if car_fingerprint != "HYUNDAI_NEXO_1ST_GEN":\n    return False\n\n  reason = str(error)\n  if not any(message in reason for message in NEXO_LONGITUDINAL_INIT_FAILURES):\n    return False\n\n  _safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)\n  cloudlog.error(f"NEXO longitudinal setup failed; controls latched off for this session, settings preserved: {reason}")\n  return True\n''',
)

replace_once(
  "selfdrive/car/card.py",
  '''    self.nexo_last_heartbeat = 0.0\n    if self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN":\n      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)\n''',
  '''    self.nexo_last_heartbeat = 0.0\n    self.nexo_restore_attempted = False\n    if self.CP.carFingerprint == "HYUNDAI_NEXO_1ST_GEN":\n      # Repair an interrupted prior takeover before this process can attempt a\n      # new one. With no marker this is inert, including in normal stock cruise.\n      self._restore_nexo_stock_scc_if_pending("card startup stale takeover")\n      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)\n''',
)

replace_once(
  "selfdrive/car/card.py",
  '''  def _update_nexo_heartbeat(self, force: bool = False) -> None:\n''',
  '''  def _restore_nexo_stock_scc_if_pending(self, reason: str) -> bool:\n    if self.CP.carFingerprint != "HYUNDAI_NEXO_1ST_GEN":\n      return True\n\n    try:\n      from opendbc.car.hyundai.interface import nexo_stock_scc_restore_pending\n      pending = nexo_stock_scc_restore_pending()\n    except Exception as error:\n      cloudlog.warning(f"NEXO restore marker read failed: {error}")\n      pending = True\n\n    if not pending:\n      return True\n\n    self.nexo_restore_attempted = True\n    previous_stage = getattr(self, "nexo_stage", "unknown")\n    self.nexo_stage = "stock_scc_restoring"\n    try:\n      result = self.CI.deinit(self.CP, *self.can_callbacks)\n    except Exception as error:\n      cloudlog.exception(f"NEXO stock SCC restore failed reason={reason}: {error}")\n      self.nexo_stage = previous_stage\n      return False\n\n    try:\n      pending_after = nexo_stock_scc_restore_pending()\n    except Exception:\n      pending_after = not bool(result)\n    restored = bool(result) or not pending_after\n    cloudlog.warning(f"NEXO stock SCC restore reason={reason} restored={restored}")\n    self.nexo_stage = previous_stage\n    return restored\n\n  def _update_nexo_heartbeat(self, force: bool = False) -> None:\n''',
)

replace_once(
  "selfdrive/car/card.py",
  '''    # Do not issue a second diagnostic sequence here. Initial radar failure paths\n    # restore stock communication inside CarInterface.init(), and a runtime guard\n    # trip already proves that the factory SCC stream is present. Re-entering UDS\n    # from the card loop can terminate the process or create a new cluster fault.\n    self.nexo_stock_scc_guard.disarm()\n    self.nexo_long_init_failed = True\n    self.nexo_session_state = "failed_latched"\n''',
  '''    # This calls the dedicated restore-only path, never CarInterface.init(), so\n    # it cannot re-run radar programming. Keep the marker when no ECU ack arrives.\n    restored = self._restore_nexo_stock_scc_if_pending(f"longitudinal failure: {error}")\n    self.nexo_stock_scc_guard.disarm()\n    self.nexo_long_init_failed = True\n    self.nexo_session_state = "failed_latched_stock_restored" if restored else "failed_latched_restore_pending"\n''',
)

replace_once(
  "selfdrive/car/card.py",
  '      self.params.remove("NexoLongitudinalFailure")\n',
  '      _safe_nexo_param_remove(self.params, "NexoLongitudinalFailure")\n',
)

replace_once(
  "selfdrive/car/card.py",
  '''    finally:\n      e.set()\n      t.join()\n''',
  '''    finally:\n      # Any card exit means NexoPilot SCC output is ending. Restore factory SCC\n      # before the process disappears; a failed ack leaves the persistent marker\n      # for the next card start.\n      try:\n        self._restore_nexo_stock_scc_if_pending("card thread exit")\n      finally:\n        e.set()\n        t.join()\n''',
)

replace_once(
  "selfdrive/car/card.py",
  '''  except Exception as error:\n    stage = getattr(card_process, "nexo_stage", "card_constructor")\n    record_nexo_card_crash(params, stage, error, traceback.format_exc())\n    raise\n''',
  '''  except Exception as error:\n    stage = getattr(card_process, "nexo_stage", "card_constructor")\n    if card_process is not None:\n      # card_thread finally normally restores first. This is a second, idempotent\n      # fallback for constructor/cleanup paths that did not complete.\n      try:\n        card_process._restore_nexo_stock_scc_if_pending("uncaught card exception")\n      except Exception as restore_error:\n        cloudlog.exception(f"NEXO final stock SCC restore attempt failed: {restore_error}")\n    record_nexo_card_crash(params, stage, error, traceback.format_exc())\n    raise\n''',
)

# ---------------------------------------------------------------------------
# Port 7000: make pending takeover and restore history visible
# ---------------------------------------------------------------------------
replace_once(
  "system/nexo_web/nexo_diagnostics_v2.py",
  'NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")\n',
  'NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")\n'
  'NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")\n'
  'NEXO_SCC_RESTORE_LOG = Path("/data/nexo_scc_restore.log")\n',
)

replace_once(
  "system/nexo_web/nexo_diagnostics_v2.py",
  '''  success = _json_log(NEXO_LONG_SUCCESS_LOG)\n  lines = [\n''',
  '''  success = _json_log(NEXO_LONG_SUCCESS_LOG)\n  takeover_pending = NEXO_SCC_TAKEOVER_MARKER.exists()\n  try:\n    restore_log = NEXO_SCC_RESTORE_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]\n  except OSError:\n    restore_log = "복구 시도 기록 없음"\n  lines = [\n''',
)

replace_once(
  "system/nexo_web/nexo_diagnostics_v2.py",
  '''    f"현재 실패 이유: {value('NexoCardSessionReason') or value('NexoLongitudinalFailure') or '없음'}",\n    f"마지막 성공 기록: {json.dumps(success, ensure_ascii=False) if success else '없음'}",\n  ]\n''',
  '''    f"현재 실패 이유: {value('NexoCardSessionReason') or value('NexoLongitudinalFailure') or '없음'}",\n    f"순정 SCC 복구 대기 마커: {'있음 - 일반 크루즈 복구 필요' if takeover_pending else '없음'}",\n    f"마지막 성공 기록: {json.dumps(success, ensure_ascii=False) if success else '없음'}",\n    "",\n    "[순정 SCC 복구 시도]",\n    restore_log,\n  ]\n''',
)

# ---------------------------------------------------------------------------
# Validation contracts and executable tests
# ---------------------------------------------------------------------------
replace_once(
  "tools/check_nexo_integration.py",
  '''    'params.put("NexoLongitudinalFailure", reason, block=True)',\n''',
  '''    '_safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)',\n''',
)
replace_once(
  "tools/check_nexo_integration.py",
  '''    "self._handle_nexo_long_failure(error)",\n''',
  '''    "self._handle_nexo_long_failure(error)",\n    "self._restore_nexo_stock_scc_if_pending",\n    "card startup stale takeover",\n    "card thread exit",\n    "uncaught card exception",\n''',
)
replace_once(
  "tools/check_nexo_integration.py",
  '''  require("self.CI.deinit(self.CP, *self.can_callbacks)" not in source,\n          "card failure latch must not re-enter NEXO radar diagnostics")\n''',
  '''  require("self.CI.init(self.CP, *self.can_callbacks)" not in source[source.index("def _handle_nexo_long_failure"):source.index("def state_update")],\n          "failure latch must never re-enter radar initialization")\n  require("self.CI.deinit(self.CP, *self.can_callbacks)" in source,\n          "dedicated stock SCC restore path is not connected")\n''',
)

write(
  "tools/check_nexo_uds_failure_policy.py",
  '''#!/usr/bin/env python3\nfrom __future__ import annotations\n\nimport ast\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef read(path: str) -> str:\n  return (ROOT / path).read_text(encoding="utf-8")\n\n\ndef require(condition: bool, message: str) -> None:\n  if not condition:\n    raise AssertionError(message)\n\n\ndef main() -> None:\n  radar = read("opendbc_repo/opendbc/car/hyundai/radar_tracks.py")\n  interface = read("opendbc_repo/opendbc/car/hyundai/interface.py")\n  card = read("selfdrive/car/card.py")\n  guard = read("selfdrive/car/nexo_guard.py")\n  web = read("system/nexo_web/nexo_diagnostics_v2.py")\n\n  for path, source in (\n    ("radar_tracks.py", radar),\n    ("interface.py", interface),\n    ("card.py", card),\n    ("nexo_guard.py", guard),\n    ("nexo_diagnostics_v2.py", web),\n  ):\n    ast.parse(source, filename=path)\n\n  for token in ("def _format_isotp_address", "def _render_isotp_result", "_render_isotp_result(result)"):\n    require(token in radar, f"tuple-safe UDS logging missing: {token}")\n  require('f"0x{address:X}' not in radar, "raw AddrType tuple formatting must not remain")\n\n  for token in (\n    "NEXO_SCC_TAKEOVER_MARKER",\n    "def nexo_stock_scc_restore_pending",\n    "def restore_nexo_stock_scc_communication",\n    '_set_nexo_takeover_marker("stock_scc_disabled")',\n    'except BaseException as error:',\n    'reason=f"long init exception: {type(error).__name__}"',\n    'CP.openpilotLongitudinalControl or nexo_stock_scc_restore_pending()',\n  ):\n    require(token in interface, f"stock SCC restore contract missing: {token}")\n\n  recovery = card[card.index("def recover_nexo_stock_cruise"):card.index("def can_comm_callbacks")]\n  for forbidden in ("AlphaLongitudinalEnabled", "ExperimentalMode", "DoReboot", "CarParamsCache"):\n    require(forbidden not in recovery, f"automatic setting/reboot mutation remains: {forbidden}")\n\n  for token in (\n    '_safe_nexo_param_put(params, "NexoLongitudinalFailure", reason, block=True)',\n    '_safe_nexo_param_remove(self.params, "NexoLongitudinalFailure")',\n    "self.nexo_long_init_failed",\n    "self._handle_nexo_long_failure(error)",\n    "self._restore_nexo_stock_scc_if_pending",\n    "card startup stale takeover",\n    "card thread exit",\n    "uncaught card exception",\n    "record_nexo_card_crash",\n    "record_nexo_long_success",\n    "NexoCardHeartbeatMono",\n  ):\n    require(token in card, f"current-session recovery missing: {token}")\n\n  failure_handler = card[card.index("def _handle_nexo_long_failure"):card.index("def state_update")]\n  require("self.CI.init(" not in failure_handler, "failure handler must never re-run radar initialization")\n  require("self.CI.deinit(self.CP, *self.can_callbacks)" in card, "restore-only deinit path missing")\n  require("def disarm(self)" in guard, "runtime guard disarm support missing")\n  require("순정 SCC 복구 대기 마커" in web, "restore marker is not visible in port 7000")\n  require("NEXO_SCC_RESTORE_LOG" in web, "restore attempts are not visible in port 7000")\n  require("자동 재부팅 없이 저장됩니다" in web, "no-auto-reboot policy is not visible in diagnostics")\n  print("NEXO stock SCC crash recovery and no-auto-reboot policy PASS")\n\n\nif __name__ == "__main__":\n  main()\n''',
)

write(
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",
  '''import tempfile\nimport unittest\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom unittest.mock import patch\n\nfrom opendbc.car.hyundai import interface\nfrom opendbc.car.hyundai.interface import CarInterface\nfrom opendbc.car.hyundai.values import CAR\n\n\nclass TestNexoLongitudinalInit(unittest.TestCase):\n  def setUp(self):\n    self.CP = SimpleNamespace(\n      openpilotLongitudinalControl=True,\n      flags=0,\n      carFingerprint=CAR.HYUNDAI_NEXO_1ST_GEN,\n    )\n    self.can_recv = lambda wait_for_one=False: []\n    self.can_send = object()\n    self.temporary = tempfile.TemporaryDirectory()\n    root = Path(self.temporary.name)\n    self.marker = root / "takeover"\n    self.restore_log = root / "restore.log"\n    self.long_log = root / "long.log"\n    self.patchers = (\n      patch.object(interface, "NEXO_SCC_TAKEOVER_MARKER", self.marker),\n      patch.object(interface, "NEXO_SCC_RESTORE_LOG", self.restore_log),\n      patch.object(interface, "NEXO_LONG_INIT_LOG", str(self.long_log)),\n    )\n    for patcher in self.patchers:\n      patcher.start()\n\n  def tearDown(self):\n    for patcher in reversed(self.patchers):\n      patcher.stop()\n    self.temporary.cleanup()\n\n  def test_successful_takeover_leaves_recovery_marker(self):\n    calls = []\n\n    def disable(*args, **kwargs):\n      calls.append(kwargs["com_cont_req"])\n      return True\n\n    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", return_value=True) as radar_enable:\n      CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual([b"\\x28\\x83\\x01"], calls)\n    self.assertEqual(40, radar_enable.call_args.kwargs["retries"])\n    self.assertTrue(interface.nexo_stock_scc_restore_pending())\n    self.assertIn("longitudinal_takeover_ready", self.marker.read_text())\n\n  def test_radar_failure_restores_stock_before_raising(self):\n    calls = []\n\n    def disable(*args, **kwargs):\n      calls.append(kwargs["com_cont_req"])\n      return True\n\n    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", return_value=False):\n      with self.assertRaisesRegex(RuntimeError, "radar track activation"):\n        CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual(b"\\x28\\x83\\x01", calls[0])\n    self.assertEqual(b"\\x28\\x80\\x01", calls[1])\n    self.assertFalse(interface.nexo_stock_scc_restore_pending())\n\n  def test_unexpected_init_exception_also_restores(self):\n    calls = []\n\n    def disable(*args, **kwargs):\n      calls.append(kwargs["com_cont_req"])\n      return True\n\n    with patch.object(interface, "disable_ecu", side_effect=disable), \\\n         patch.object(interface, "enable_radar_tracks", side_effect=ValueError("boom")):\n      with self.assertRaisesRegex(ValueError, "boom"):\n        CarInterface.init(self.CP, self.can_recv, self.can_send)\n\n    self.assertEqual([b"\\x28\\x83\\x01", b"\\x28\\x80\\x01"], calls)\n    self.assertFalse(interface.nexo_stock_scc_restore_pending())\n\n  def test_restore_retries_and_only_then_clears_marker(self):\n    self.marker.write_text("pending")\n    with patch.object(interface, "disable_ecu", side_effect=[False, True]) as disable:\n      restored = interface.restore_nexo_stock_scc_communication(\n        self.can_recv, self.can_send, reason="test", retries=3,\n      )\n    self.assertTrue(restored)\n    self.assertEqual(2, disable.call_count)\n    self.assertFalse(self.marker.exists())\n\n  def test_failed_restore_keeps_marker_for_next_start(self):\n    self.marker.write_text("pending")\n    with patch.object(interface, "disable_ecu", return_value=False):\n      restored = interface.restore_nexo_stock_scc_communication(\n        self.can_recv, self.can_send, reason="test", retries=2,\n      )\n    self.assertFalse(restored)\n    self.assertTrue(self.marker.exists())\n    self.assertIn("restore_pending", self.marker.read_text())\n\n  def test_stock_cruise_without_marker_does_not_touch_uds(self):\n    self.CP.openpilotLongitudinalControl = False\n    with patch.object(interface, "disable_ecu") as disable, \\\n         patch.object(interface, "enable_radar_tracks") as enable:\n      CarInterface.init(self.CP, self.can_recv, self.can_send)\n    disable.assert_not_called()\n    enable.assert_not_called()\n\n  def test_stock_mode_deinit_repairs_stale_takeover_marker(self):\n    self.CP.openpilotLongitudinalControl = False\n    self.marker.write_text("pending")\n    with patch.object(interface, "disable_ecu", return_value=True) as disable:\n      restored = CarInterface.deinit(self.CP, self.can_recv, self.can_send)\n    self.assertTrue(restored)\n    self.assertEqual(b"\\x28\\x80\\x01", disable.call_args.kwargs["com_cont_req"])\n    self.assertFalse(self.marker.exists())\n\n\nif __name__ == "__main__":\n  unittest.main()\n''',
)

replace_once(
  ".github/workflows/nexo-validation.yml",
  '''      - name: Test NEXO CAN fingerprint fallback\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.car.hyundai.tests.test_nexo_fingerprint\n''',
  '''      - name: Test NEXO CAN fingerprint fallback\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.car.hyundai.tests.test_nexo_fingerprint\n\n      - name: Test NEXO stock SCC crash recovery\n        working-directory: opendbc_repo\n        run: python -m unittest opendbc.car.hyundai.tests.test_nexo_init\n''',
)

print("Applied NEXO stock SCC crash recovery patch")
