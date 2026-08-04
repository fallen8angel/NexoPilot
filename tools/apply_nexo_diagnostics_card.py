#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, replacements: list[tuple[str, str]]) -> None:
  target = ROOT / path
  source = target.read_text(encoding="utf-8")
  for old, new in replacements:
    if new in source:
      continue
    if old not in source:
      raise RuntimeError(f"missing patch anchor in {path}: {old[:80]!r}")
    source = source.replace(old, new, 1)
  target.write_text(source, encoding="utf-8")


patch("selfdrive/car/card.py", [
  (
    "from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard\n",
    "from openpilot.selfdrive.car.nexo_diagnostics import record_nexo_fault_snapshot\n"
    "from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard\n",
  ),
  (
    "self.sm = messaging.SubMaster(['pandaStates', 'carControl', 'onroadEvents'])",
    "self.sm = messaging.SubMaster(['pandaStates', 'carControl', 'onroadEvents', 'selfdriveState', 'radarState'])",
  ),
  (
    "    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)\n"
    "    can_list = can_capnp_to_list(can_strs)\n\n"
    "    if self.nexo_stock_scc_guard.observe(can_list):\n"
    "      error = RuntimeError(\"NEXO stock SCC returned during longitudinal control\")\n"
    "      recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n"
    "      raise error\n",
    "    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)\n"
    "    can_list = can_capnp_to_list(can_strs)\n"
    "    self.sm.update(0)\n\n"
    "    if self.nexo_stock_scc_guard.observe(can_list):\n"
    "      error = RuntimeError(\"NEXO stock SCC returned during longitudinal control\")\n"
    "      record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)\n"
    "      recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n"
    "      raise error\n",
  ),
  (
    "    self.sm.update(0)\n\n"
    "    can_rcv_valid = len(can_strs) > 0\n",
    "    can_rcv_valid = len(can_strs) > 0\n",
  ),
  (
    "      except RuntimeError as error:\n"
    "        recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n"
    "        raise\n",
    "      except RuntimeError as error:\n"
    "        self.sm.update(0)\n"
    "        record_nexo_fault_snapshot(self.params, self.nexo_stock_scc_guard, self.sm, error)\n"
    "        recover_nexo_stock_cruise(self.params, self.CP.carFingerprint, error)\n"
    "        raise\n",
  ),
])

patch("opendbc_repo/opendbc/car/hyundai/interface.py", [
  (
    'NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"\n'
    'ENABLE_BUTTONS =',
    'NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"\n\n\n'
    'def _trace_nexo_long_init(message: str, reset: bool = False) -> None:\n'
    '  try:\n'
    '    with open(NEXO_LONG_INIT_LOG, "w" if reset else "a", encoding="utf-8") as trace:\n'
    '      trace.write(f"{time.monotonic():.3f} {message}\\n")\n'
    '  except OSError:\n'
    '    pass\n\n\n'
    'ENABLE_BUTTONS =',
  ),
  (
    "        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n"
    "        _trace_nexo_long_init(f\"STEP 1 request completed={disabled}\")\n",
    "        disable_started = time.monotonic()\n"
    "        _trace_nexo_long_init(f\"UDS TX ecu=0x{addr:X} bus={bus} requests=10 03 then 28 83 01\")\n"
    "        disabled = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)\n"
    "        _trace_nexo_long_init(\n"
    "          f\"UDS RESULT ecu=0x{addr:X} bus={bus} acknowledged={disabled} \"\n"
    "          f\"elapsed_ms={(time.monotonic() - disable_started) * 1000:.1f}\"\n"
    "        )\n"
    "        _trace_nexo_long_init(f\"STEP 1 request completed={disabled}\")\n",
  ),
])

print("Applied NEXO diagnostics card/interface patch")
