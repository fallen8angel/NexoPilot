#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRE_DYNAMIC_SAFETY_COMMIT = "aa10fd5566c40c6f5fac209cc97aafaaabbd74cd"


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
  target = ROOT / path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
  source = read(path)
  count = source.count(old)
  if count != 1:
    raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:80]!r}")
  write(path, source.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
  source = read(path)
  updated, count = re.subn(pattern, replacement, source, count=1, flags=re.DOTALL)
  if count != 1:
    raise RuntimeError(f"{path}: regex replacement count={count}: {pattern[:80]!r}")
  write(path, updated)


def restore_from_commit(path: str) -> None:
  content = subprocess.check_output(
    ["git", "show", f"{PRE_DYNAMIC_SAFETY_COMMIT}:{path}"], cwd=ROOT, text=True,
  )
  write(path, content)


def patch_radar_tracks() -> None:
  write("opendbc_repo/opendbc/car/hyundai/radar_tracks.py", '''import time

from opendbc.car.carlog import carlog
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\\x01\\x42"
RADAR_TRACK_CONFIG = b"\\x00\\x00\\x00\\x01\\x00\\x01"
RADAR_QUERY_TIMEOUT = 0.1
RADAR_QUERY_TOTAL_TIMEOUT = 0.35


def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  return query.get_data(timeout, total_timeout=max(timeout * 3, RADAR_QUERY_TOTAL_TIMEOUT))


def enable_radar_tracks(can_recv, can_send, bus, retries=40) -> bool:
  """Enable NEXO MANDO radar tracks using the proven NEXOdriveAI order.

  disable_ecu() has already entered extended diagnostics (0x10 03) and sent
  communication control (0x28 83 01). Match NEXOdriveAI by entering the radar
  configuration session (0x10 07) and writing DID 0x0142 immediately after it.
  Do not issue a read-back or a second communication-control request here since
  either can change the session state on older NEXO radar firmware.
  """
  for attempt in range(1, retries + 1):
    try:
      session = _query(can_recv, can_send, bus, b"\\x10\\x07", b"\\x50\\x07")
      if not session:
        raise RuntimeError("no diagnostic-session response")

      write_result = _query(
        can_recv, can_send, bus,
        b"\\x2e" + RADAR_TRACK_CONFIG_DID + RADAR_TRACK_CONFIG,
        b"\\x6e" + RADAR_TRACK_CONFIG_DID,
      )
      if not write_result:
        raise RuntimeError("no write-data response")

      carlog.info(f"NEXOdriveAI radar-track sequence completed on bus {bus}, attempt {attempt}")
      return True
    except Exception as error:
      carlog.warning(f"NEXO radar track activation attempt {attempt}/{retries} failed on bus {bus}: {error}")
      time.sleep(0.05)

  carlog.error(f"NEXO radar tracks could not be enabled on bus {bus}")
  return False
''')


def patch_interface() -> None:
  path = "opendbc_repo/opendbc/car/hyundai/interface.py"
  source = read(path)
  source, count = re.subn(
    r'NEXO_STOCK_SCC_ADDRS = .*?\n\n\nENABLE_BUTTONS =',
    'ENABLE_BUTTONS =', source, count=1, flags=re.DOTALL,
  )
  if count != 1:
    raise RuntimeError("interface.py: failed to remove obsolete startup-only SCC verifier")

  pattern = r'''      if is_nexo and disabling_normal_comms:\n.*?      else:\n        disable_ecu\(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control\)'''
  replacement = '''      if is_nexo and disabling_normal_comms:
        _trace_nexo_long_init(f"START NEXOdriveAI long init bus={bus} addr=0x{addr:x}", reset=True)
        _trace_nexo_long_init("STEP 1 enter extended diagnostics and suppress stock SCC")
        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)
        _trace_nexo_long_init(f"STEP 1 request completed={disabled}")
        if not disabled:
          _trace_nexo_long_init("FAIL stock SCC communication-control was not acknowledged")
          raise RuntimeError("NEXO stock SCC communication could not be disabled")

        _trace_nexo_long_init("STEP 2 run NEXOdriveAI radar-track sequence")
        tracks_enabled = enable_radar_tracks(can_recv, can_send, bus, retries=40)
        _trace_nexo_long_init(f"STEP 2 radar-track request completed={tracks_enabled}")
        if not tracks_enabled:
          enable_communication = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                        0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                        uds.MESSAGE_TYPE.NORMAL])
          disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=enable_communication)
          _trace_nexo_long_init("FAIL radar tracks; requested stock communication restore")
          raise RuntimeError("NEXO radar track activation failed")

        _trace_nexo_long_init("DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card")
      else:
        disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)'''
  source, count = re.subn(pattern, replacement, source, count=1, flags=re.DOTALL)
  if count != 1:
    raise RuntimeError("interface.py: failed to replace NEXO initialization block")
  write(path, source)


def add_runtime_guard() -> None:
  write("selfdrive/car/nexo_guard.py", '''from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_STOCK_SCC_SOURCE = 0
NEXO_RUNTIME_GUARD_GRACE_S = 0.30
NEXO_RUNTIME_GUARD_WINDOW_S = 0.25
NEXO_RUNTIME_GUARD_MIN_FRAMES = 3


class NexoStockSccRuntimeGuard:
  """Fail closed when the factory SCC stream returns after radar takeover.

  Outgoing Panda acknowledgements use sources >=128, so source 0 is the actual
  vehicle-side stock SCC stream observed in NEXO logs. The guard is armed only
  after CarInterface.init() succeeds and is disabled entirely for stock cruise.
  """

  def __init__(self, enabled: bool, *, grace_s: float = NEXO_RUNTIME_GUARD_GRACE_S,
               window_s: float = NEXO_RUNTIME_GUARD_WINDOW_S,
               min_frames: int = NEXO_RUNTIME_GUARD_MIN_FRAMES) -> None:
    self.enabled = enabled
    self.grace_s = grace_s
    self.window_s = window_s
    self.min_frames = min_frames
    self.armed = False
    self.armed_at = 0.0
    self._timestamps: deque[float] = deque()

  def arm(self, now: float | None = None) -> None:
    self.armed = self.enabled
    self.armed_at = time.monotonic() if now is None else now
    self._timestamps.clear()

  def observe(self, can_messages: Iterable[object], now: float | None = None) -> bool:
    if not self.armed:
      return False

    timestamp = time.monotonic() if now is None else now
    if timestamp - self.armed_at < self.grace_s:
      self._timestamps.clear()
      return False

    for msg in can_messages:
      if getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE and \
         getattr(msg, "address", -1) in NEXO_STOCK_SCC_ADDRS:
        self._timestamps.append(timestamp)

    cutoff = timestamp - self.window_s
    while self._timestamps and self._timestamps[0] < cutoff:
      self._timestamps.popleft()

    return len(self._timestamps) >= self.min_frames
''')

  write("selfdrive/car/tests/test_nexo_guard.py", '''import unittest
from types import SimpleNamespace

from selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard


def frame(address: int, src: int = 0):
  return SimpleNamespace(address=address, src=src)


class TestNexoStockSccRuntimeGuard(unittest.TestCase):
  def test_not_armed_is_inert(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0)
    self.assertFalse(guard.observe([frame(0x420)] * 4, now=1.0))

  def test_ignores_startup_buffer_during_grace(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.3)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420)] * 4, now=1.2))
    self.assertFalse(guard.observe([], now=1.31))

  def test_detects_sustained_vehicle_side_scc(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0, window_s=0.25, min_frames=3)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420)], now=1.00))
    self.assertFalse(guard.observe([frame(0x421)], now=1.05))
    self.assertTrue(guard.observe([frame(0x389)], now=1.10))

  def test_ignores_outgoing_and_blocked_sources(self):
    guard = NexoStockSccRuntimeGuard(True, grace_s=0.0, min_frames=1)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420, 128), frame(0x421, 192)], now=1.1))

  def test_disabled_for_stock_cruise(self):
    guard = NexoStockSccRuntimeGuard(False, grace_s=0.0, min_frames=1)
    guard.arm(now=1.0)
    self.assertFalse(guard.observe([frame(0x420)], now=1.1))


if __name__ == "__main__":
  unittest.main()
''')


def patch_card() -> None:
  path = "selfdrive/car/card.py"
  replace_once(
    path,
    "from openpilot.selfdrive.car.cruise import VCruiseHelper\n",
    "from openpilot.selfdrive.car.cruise import VCruiseHelper\nfrom openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard\n",
  )
  replace_once(
    path,
    '  "NEXO stock SCC remained active",\n)',
    '  "NEXO stock SCC remained active",\n  "NEXO stock SCC returned during longitudinal control",\n)',
  )
  replace_once(
    path,
    "      self.CP.safetyConfigs = [safety_config]\n\n    if self.CP.secOcRequired:",
    "      self.CP.safetyConfigs = [safety_config]\n\n"
    "    self.nexo_stock_scc_guard = NexoStockSccRuntimeGuard(\n"
    "      not REPLAY and self.CP.carFingerprint == \"HYUNDAI_NEXO_1ST_GEN\" and self.CP.openpilotLongitudinalControl\n"
    "    )\n\n"
    "    if self.CP.secOcRequired:",
  )
  replace_once(
    path,
    "    can_list = can_capnp_to_list(can_strs)\n\n    # Update carState from CAN",
    "    can_list = can_capnp_to_list(can_strs)\n\n"
    "    if self.nexo_stock_scc_guard.observe(can_list):\n"
    "      error = RuntimeError(\"NEXO stock SCC returned during longitudinal control\")\n"
    "      recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n"
    "      raise error\n\n"
    "    # Update carState from CAN",
  )
  replace_once(
    path,
    "      # signal pandad to switch to car safety mode\n      self.params.put_bool(\"ControlsReady\", True)",
    "      # Arm the raw-CAN guard only after the diagnostic takeover completed.\n"
    "      self.nexo_stock_scc_guard.arm()\n"
    "      # signal pandad to switch to car safety mode\n"
    "      self.params.put_bool(\"ControlsReady\", True)",
  )


def patch_integration_check() -> None:
  path = "tools/check_nexo_integration.py"
  source = read(path)
  source = source.replace(
    '  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",\n  "selfdrive/car/card.py",',
    '  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_init.py",\n  "selfdrive/car/nexo_guard.py",\n  "selfdrive/car/tests/test_nexo_guard.py",\n  "selfdrive/car/card.py",',
  )

  source, count = re.subn(
    r'def validate_interface\(\) -> None:\n.*?\n\ndef validate_hyundaican',
    '''def validate_interface() -> None:
  source = read("opendbc_repo/opendbc/car/hyundai/interface.py")
  required = (
    'raise RuntimeError("NEXO stock SCC communication could not be disabled")',
    'raise RuntimeError("NEXO radar track activation failed")',
    "START NEXOdriveAI long init",
    "DONE NEXOdriveAI disable-then-radar sequence; runtime SCC guard armed by card",
  )
  for token in required:
    require(token in source, f"interface contract missing: {token}")

  disable_pos = source.find("disabled = disable_ecu")
  radar_pos = source.find("tracks_enabled = enable_radar_tracks", disable_pos)
  require(disable_pos >= 0 and radar_pos > disable_pos,
          "NEXO init order must be extended-diagnostic disable -> radar-track programming")
  require("_nexo_stock_scc_active" not in source,
          "startup-only SCC silence check must not replace the runtime raw-CAN guard")


def validate_hyundaican''',
    source, count=1, flags=re.DOTALL,
  )
  if count != 1:
    raise RuntimeError("check_nexo_integration.py: validate_interface replacement failed")

  source, count = re.subn(
    r'def validate_recovery\(\) -> None:\n.*?\n\ndef validate_safety',
    '''def validate_recovery() -> None:
  source = read("selfdrive/car/card.py")
  required = (
    '"NEXO radar track activation failed"',
    '"NEXO stock SCC communication could not be disabled"',
    '"NEXO stock SCC returned during longitudinal control"',
    'params.put_bool("AlphaLongitudinalEnabled", False, block=True)',
    'params.put_bool("ExperimentalMode", False, block=True)',
    'params.put_bool("DoReboot", True, block=True)',
    "NexoStockSccRuntimeGuard",
    "self.nexo_stock_scc_guard.arm()",
    "self.nexo_stock_scc_guard.observe(can_list)",
  )
  for token in required:
    require(token in source, f"stock-cruise recovery missing: {token}")


def validate_runtime_guard() -> None:
  source = read("selfdrive/car/nexo_guard.py")
  required = (
    "NEXO_STOCK_SCC_ADDRS",
    "NEXO_STOCK_SCC_SOURCE = 0",
    "class NexoStockSccRuntimeGuard",
    "getattr(msg, \"src\", -1) == NEXO_STOCK_SCC_SOURCE",
    "len(self._timestamps) >= self.min_frames",
  )
  for token in required:
    require(token in source, f"runtime SCC guard missing: {token}")


def validate_safety''',
    source, count=1, flags=re.DOTALL,
  )
  if count != 1:
    raise RuntimeError("check_nexo_integration.py: validate_recovery replacement failed")

  source, count = re.subn(
    r'def validate_safety\(\) -> None:\n.*?\n\ndef main',
    '''def validate_safety() -> None:
  source = read("opendbc_repo/opendbc/safety/modes/hyundai.h")
  require("HYUNDAI_LONG_COMMON_TX_MSGS" in source, "longitudinal TX allowlist missing")
  require("longitudinal_accel_checks" in source, "longitudinal acceleration safety check missing")
  require("hyundai_nexo_dynamic_scc_fwd" not in source,
          "incorrect bus-direction dynamic SCC forwarding must stay removed")
  require(".disable_static_blocking = true" not in source,
          "SCC static relay blocking must remain enabled")

  for address in ("0x38D", "0x483", "0x7D0"):
    pattern = rf"\\{{\\s*{address}\\s*,\\s*0\\s*,\\s*8\\s*,\\s*\\.check_relay\\s*=\\s*false\\s*\\}}"
    require(re.search(pattern, source) is not None, f"Panda safety allowlist missing: {address}")


def main''',
    source, count=1, flags=re.DOTALL,
  )
  if count != 1:
    raise RuntimeError("check_nexo_integration.py: validate_safety replacement failed")

  source = source.replace(
    "  validate_recovery()\n  validate_safety()",
    "  validate_recovery()\n  validate_runtime_guard()\n  validate_safety()",
  )
  write(path, source)


def patch_workflow() -> None:
  write(".github/workflows/nexo-validation.yml", '''name: NEXO validation

on:
  push:
    branches:
      - NEXO
      - "agent/**"
  pull_request:
    branches:
      - NEXO
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: nexo-validation-${{ github.ref }}
  cancel-in-progress: true

jobs:
  validate-nexo-integration:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Check NEXO integration
        run: python tools/check_nexo_integration.py

      - name: Test NEXO runtime SCC guard
        run: PYTHONPATH=. python -m unittest selfdrive.car.tests.test_nexo_guard

      - name: Check NEXO DBC contract
        run: python tools/check_nexo_dbc_contract.py
''')


def main() -> None:
  restore_from_commit("opendbc_repo/opendbc/safety/modes/hyundai.h")
  restore_from_commit("opendbc_repo/opendbc/safety/tests/test_hyundai.py")
  patch_radar_tracks()
  patch_interface()
  add_runtime_guard()
  patch_card()
  patch_integration_check()
  patch_workflow()
  obsolete = ROOT / "tools/check_nexo_post_radar_guard.py"
  if obsolete.exists():
    obsolete.unlink()
  print("Applied NEXOdriveAI sequence and runtime stock-SCC fail-closed guard")


if __name__ == "__main__":
  main()
