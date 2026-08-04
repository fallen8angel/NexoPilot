#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
  file_path = Path(path)
  text = file_path.read_text(encoding="utf-8")
  if new in text:
    return
  if old not in text:
    raise RuntimeError(f"patch marker not found in {path}: {old[:120]!r}")
  file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Explicit NEXO-only safety flag. Do not infer this behavior for every future FCEV.
replace_once(
  "opendbc_repo/opendbc/car/hyundai/values.py",
  "  FCEV_GAS = 256\n  ALT_LIMITS_2 = 512\n",
  "  FCEV_GAS = 256\n  ALT_LIMITS_2 = 512\n  NEXO_DYNAMIC_SCC = 1024\n",
)

# Pass the NEXO-only ownership flag to Panda only while openpilot longitudinal is selected.
replace_once(
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "    if ret.openpilotLongitudinalControl:\n      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value\n",
  "    if ret.openpilotLongitudinalControl:\n      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value\n      if is_nexo:\n        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value\n",
)

# Decode and reset the NEXO-only flag in the common Hyundai safety state.
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai_common.h",
  "extern bool hyundai_alt_limits_2;\nbool hyundai_alt_limits_2 = false;\n",
  "extern bool hyundai_alt_limits_2;\nbool hyundai_alt_limits_2 = false;\n\nextern bool hyundai_nexo_dynamic_scc;\nbool hyundai_nexo_dynamic_scc = false;\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai_common.h",
  "  const uint16_t HYUNDAI_PARAM_FCEV_GAS = 256;\n  const uint16_t HYUNDAI_PARAM_ALT_LIMITS_2 = 512;\n",
  "  const uint16_t HYUNDAI_PARAM_FCEV_GAS = 256;\n  const uint16_t HYUNDAI_PARAM_ALT_LIMITS_2 = 512;\n  const uint16_t HYUNDAI_PARAM_NEXO_DYNAMIC_SCC = 1024;\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai_common.h",
  "  hyundai_fcev_gas_signal = GET_FLAG(param, HYUNDAI_PARAM_FCEV_GAS);\n  hyundai_alt_limits_2 = GET_FLAG(param, HYUNDAI_PARAM_ALT_LIMITS_2);\n",
  "  hyundai_fcev_gas_signal = GET_FLAG(param, HYUNDAI_PARAM_FCEV_GAS);\n  hyundai_alt_limits_2 = GET_FLAG(param, HYUNDAI_PARAM_ALT_LIMITS_2);\n  hyundai_nexo_dynamic_scc = GET_FLAG(param, HYUNDAI_PARAM_NEXO_DYNAMIC_SCC);\n",
)

# Keep the normal Hyundai longitudinal allowlist unchanged. Only the NEXO list
# opts out of static SCC forwarding blocks so its dedicated timeout hook can own them.
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "#define HYUNDAI_COMMON_RX_CHECKS(legacy)",
  "#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS(scc_bus) \\\n  HYUNDAI_COMMON_TX_MSGS(scc_bus) \\\n  {0x420, 0,       8, .check_relay = true, .disable_static_blocking = true},  /* SCC11 Bus 0 */ \\\n  {0x421, 0,       8, .check_relay = true, .disable_static_blocking = true},  /* SCC12 Bus 0 */ \\\n  {0x50A, 0,       8, .check_relay = true, .disable_static_blocking = true},  /* SCC13 Bus 0 */ \\\n  {0x389, 0,       8, .check_relay = true, .disable_static_blocking = true},  /* SCC14 Bus 0 */ \\\n  {0x4A2, 0,       2, .check_relay = false},                                 /* FRT_RADAR11 Bus 0 */ \\\n\n#define HYUNDAI_COMMON_RX_CHECKS(legacy)",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "static bool hyundai_legacy = false;\n",
  "static bool hyundai_legacy = false;\n\n// NEXO SCC ownership is armed only by an SCC12 frame that passed every Panda TX\n// payload and longitudinal limit check. If accepted SCC12 stops for 400 ms,\n// factory camera-side SCC forwarding is restored automatically. Stock FCA is\n// never claimed here and remains forwarded.\nstatic bool hyundai_nexo_scc12_tx_seen = false;\nstatic uint32_t hyundai_nexo_scc12_last_tx = 0U;\nstatic const uint32_t HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US = 400000U;\n\nstatic bool hyundai_nexo_is_scc_addr(const int addr) {\n  return (addr == 0x389) || (addr == 0x420) || (addr == 0x421) || (addr == 0x50A);\n}\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "  return tx;\n}\n\nstatic safety_config hyundai_init(uint16_t param) {",
  "  if (tx && hyundai_nexo_dynamic_scc && (msg->bus == 0U) && (msg->addr == 0x421U)) {\n    hyundai_nexo_scc12_tx_seen = true;\n    hyundai_nexo_scc12_last_tx = microsecond_timer_get();\n  }\n\n  return tx;\n}\n\nstatic safety_config hyundai_init(uint16_t param) {",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "  static const CanMsg HYUNDAI_CAMERA_SCC_TX_MSGS[] = {",
  "  static const CanMsg HYUNDAI_NEXO_LONG_TX_MSGS[] = {\n    HYUNDAI_NEXO_LONG_COMMON_TX_MSGS(0)\n    {0x38D, 0, 8, .check_relay = false}, // FCA11 status only; actuation bits remain blocked in tx hook\n    {0x483, 0, 8, .check_relay = false}, // FCA12 status only\n    {0x7D0, 0, 8, .check_relay = false}, // radar tester present\n  };\n\n  static const CanMsg HYUNDAI_CAMERA_SCC_TX_MSGS[] = {",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "  hyundai_common_init(param);\n  hyundai_legacy = false;\n",
  "  hyundai_common_init(param);\n  hyundai_legacy = false;\n  hyundai_nexo_scc12_tx_seen = false;\n  hyundai_nexo_scc12_last_tx = 0U;\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "    if (hyundai_camera_scc) {\n      SET_TX_MSGS(HYUNDAI_CAMERA_SCC_LONG_TX_MSGS, ret);\n    } else {\n      SET_TX_MSGS(HYUNDAI_LONG_TX_MSGS, ret);\n    }\n",
  "    if (hyundai_camera_scc) {\n      SET_TX_MSGS(HYUNDAI_CAMERA_SCC_LONG_TX_MSGS, ret);\n    } else if (hyundai_nexo_dynamic_scc) {\n      SET_TX_MSGS(HYUNDAI_NEXO_LONG_TX_MSGS, ret);\n    } else {\n      SET_TX_MSGS(HYUNDAI_LONG_TX_MSGS, ret);\n    }\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "static safety_config hyundai_legacy_init(uint16_t param) {",
  "static bool hyundai_fwd_hook(int bus_num, int addr) {\n  // The NEXO harness uses the standard camera-side bus 2 -> vehicle-side bus 0\n  // forwarding path. Source-0 SCC is not hidden here; the runtime guard remains\n  // responsible for stopping openpilot longitudinal if the physical radar SCC\n  // stream returns.\n  if (hyundai_nexo_dynamic_scc && (bus_num == 2) && hyundai_nexo_is_scc_addr(addr)) {\n    const uint32_t now = microsecond_timer_get();\n    return hyundai_nexo_scc12_tx_seen &&\n           (safety_get_ts_elapsed(now, hyundai_nexo_scc12_last_tx) < HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US);\n  }\n\n  // NEXO does not synthesize FCA11/FCA12. Factory FCA must remain available.\n  return false;\n}\n\nstatic safety_config hyundai_legacy_init(uint16_t param) {",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "  hyundai_camera_scc = false;\n  return BUILD_SAFETY_CFG(hyundai_legacy_rx_checks, HYUNDAI_TX_MSGS);\n",
  "  hyundai_camera_scc = false;\n  hyundai_nexo_dynamic_scc = false;\n  hyundai_nexo_scc12_tx_seen = false;\n  hyundai_nexo_scc12_last_tx = 0U;\n  return BUILD_SAFETY_CFG(hyundai_legacy_rx_checks, HYUNDAI_TX_MSGS);\n",
)
replace_once(
  "opendbc_repo/opendbc/safety/modes/hyundai.h",
  "  .tx = hyundai_tx_hook,\n  .get_counter = hyundai_get_counter,",
  "  .tx = hyundai_tx_hook,\n  .fwd = hyundai_fwd_hook,\n  .get_counter = hyundai_get_counter,",
)

# Match the proven AI NEXO cluster-facing SCC fields without changing generic Hyundai behavior.
replace_once(
  "opendbc_repo/opendbc/car/hyundai/hyundaican.py",
  '    "ObjValid": 1 if lead_visible else 0,\n    "ACC_ObjStatus": 1 if lead_visible else 0,\n',
  '    "ObjValid": 1 if is_nexo else 1 if lead_visible else 0,\n    "ACC_ObjStatus": 1 if is_nexo else 1 if lead_visible else 0,\n',
)
replace_once(
  "opendbc_repo/opendbc/car/hyundai/hyundaican.py",
  '    "JerkLowerLimit": upper_jerk,\n',
  '    "JerkLowerLimit": 5.0 if is_nexo else upper_jerk,\n',
)

# Dedicated safety tests for NEXO ownership. Non-NEXO longitudinal keeps the
# existing static forwarding block and all longitudinal payload tests still run.
replace_once(
  "opendbc_repo/opendbc/safety/tests/test_hyundai.py",
  "\n\nif __name__ == \"__main__\":\n  unittest.main()\n",
  '''\n\nclass TestHyundaiNexoDynamicSCCOwnership(unittest.TestCase):\n  SCC_ADDRS = (0x389, 0x420, 0x421, 0x50A)\n  FCA_ADDRS = (0x38D, 0x483)\n  TIMEOUT_US = 400_000\n\n  def setUp(self):\n    self.packer = CANPackerSafety("hyundai_can_generated")\n    self.safety = libsafety_py.libsafety\n    param = HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG | HyundaiSafetyFlags.NEXO_DYNAMIC_SCC\n    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, param)\n    self.safety.init_tests()\n    self.safety.set_timer(1_000_000)\n\n  def _accel_msg(self, accel):\n    values = {"aReqRaw": accel, "aReqValue": accel, "AEB_CmdAct": 0, "CR_VSM_DecCmd": 0}\n    return self.packer.make_can_msg_safety("SCC12", 0, values)\n\n  def test_factory_scc_forwarded_before_openpilot_ownership(self):\n    for addr in self.SCC_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)\n\n  def test_accepted_scc12_arms_ownership_and_timeout_restores(self):\n    self.safety.set_controls_allowed(True)\n    self.assertTrue(self.safety.safety_tx_hook(self._accel_msg(0.0)))\n    for addr in self.SCC_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), -1)\n\n    self.safety.set_timer(1_000_000 + self.TIMEOUT_US - 1)\n    self.assertEqual(self.safety.safety_fwd_hook(2, 0x421), -1)\n    self.safety.set_timer(1_000_000 + self.TIMEOUT_US)\n    for addr in self.SCC_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)\n\n  def test_rejected_scc12_does_not_claim_ownership(self):\n    self.safety.set_controls_allowed(True)\n    self.assertFalse(self.safety.safety_tx_hook(self._accel_msg(3.0)))\n    for addr in self.SCC_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)\n\n  def test_factory_fca_is_never_claimed(self):\n    self.safety.set_controls_allowed(True)\n    self.assertTrue(self.safety.safety_tx_hook(self._accel_msg(0.0)))\n    for addr in self.FCA_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)\n\n  def test_non_nexo_longitudinal_keeps_static_scc_block(self):\n    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.LONG)\n    self.safety.init_tests()\n    for addr in self.SCC_ADDRS:\n      self.assertEqual(self.safety.safety_fwd_hook(2, addr), -1)\n\n\nif __name__ == "__main__":\n  unittest.main()\n''',
)

# Static validator used by the lightweight NEXO CI job.
Path("tools/check_nexo_ai_scc_ownership.py").write_text('''#!/usr/bin/env python3\nfrom pathlib import Path\n\nvalues = Path("opendbc_repo/opendbc/car/hyundai/values.py").read_text()\ninterface = Path("opendbc_repo/opendbc/car/hyundai/interface.py").read_text()\nsafety_common = Path("opendbc_repo/opendbc/safety/modes/hyundai_common.h").read_text()\nsafety = Path("opendbc_repo/opendbc/safety/modes/hyundai.h").read_text()\nhyundaican = Path("opendbc_repo/opendbc/car/hyundai/hyundaican.py").read_text()\ntests = Path("opendbc_repo/opendbc/safety/tests/test_hyundai.py").read_text()\n\nrequired = {\n  "values flag": (values, "NEXO_DYNAMIC_SCC = 1024"),\n  "interface gating": (interface, "if is_nexo:\\n        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value"),\n  "common flag decode": (safety_common, "HYUNDAI_PARAM_NEXO_DYNAMIC_SCC = 1024"),\n  "dedicated tx list": (safety, "HYUNDAI_NEXO_LONG_TX_MSGS"),\n  "accepted SCC12 ownership": (safety, "tx && hyundai_nexo_dynamic_scc && (msg->bus == 0U) && (msg->addr == 0x421U)"),\n  "400ms timeout": (safety, "HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US = 400000U"),\n  "bus2 hook": (safety, "hyundai_nexo_dynamic_scc && (bus_num == 2) && hyundai_nexo_is_scc_addr(addr)"),\n  "fwd hook wired": (safety, ".fwd = hyundai_fwd_hook"),\n  "AI object state": (hyundaican, '"ObjValid": 1 if is_nexo'),\n  "AI jerk lower": (hyundaican, '"JerkLowerLimit": 5.0 if is_nexo'),\n  "ownership tests": (tests, "class TestHyundaiNexoDynamicSCCOwnership"),\n  "rejected SCC test": (tests, "test_rejected_scc12_does_not_claim_ownership"),\n  "FCA pass-through test": (tests, "test_factory_fca_is_never_claimed"),\n}\nfor name, (text, token) in required.items():\n  if token not in text:\n    raise SystemExit(f"missing {name}: {token}")\n\n# Guardrails: the normal longitudinal list must stay statically blocked and the\n# NEXO implementation must not reintroduce the AI brake/gas bypass.\nnormal_macro = safety.split("#define HYUNDAI_LONG_COMMON_TX_MSGS", 1)[1].split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[0]\nif "disable_static_blocking" in normal_macro:\n  raise SystemExit("generic Hyundai longitudinal static forwarding was weakened")\nif "gas_pressed = brake_pressed = false" in safety:\n  raise SystemExit("unsafe AI pedal bypass detected")\nif "is_fca_addr" in safety:\n  raise SystemExit("NEXO ownership must not claim stock FCA")\nprint("NEXO AI SCC ownership and compatibility checks passed")\n''', encoding="utf-8")

# Wire the lightweight validator into the existing standard workflow.
replace_once(
  ".github/workflows/nexo-validation.yml",
  "      - name: Check NEXO DBC contract\n        run: python tools/check_nexo_dbc_contract.py\n",
  "      - name: Check NEXO DBC contract\n        run: python tools/check_nexo_dbc_contract.py\n\n      - name: Check NEXO AI SCC ownership\n        run: python tools/check_nexo_ai_scc_ownership.py\n",
)

print("Applied NEXO AI SCC ownership and compatibility patch")
