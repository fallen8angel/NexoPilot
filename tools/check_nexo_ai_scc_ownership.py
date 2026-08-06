#!/usr/bin/env python3
from pathlib import Path

values = Path("opendbc_repo/opendbc/car/hyundai/values.py").read_text()
interface = Path("opendbc_repo/opendbc/car/hyundai/interface.py").read_text()
safety_common = Path("opendbc_repo/opendbc/safety/modes/hyundai_common.h").read_text()
safety = Path("opendbc_repo/opendbc/safety/modes/hyundai.h").read_text()
hyundaican = Path("opendbc_repo/opendbc/car/hyundai/hyundaican.py").read_text()
standard_tests = Path("opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_standard_long.py").read_text()
workflow = Path(".github/workflows/nexo-validation.yml").read_text()

required = {
  "LONG flag": (values, "LONG = 4"),
  "FCEV flag": (values, "FCEV_GAS = 256"),
  "mode-aware longitudinal": (interface, "ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable"),
  "standard LONG safety": (interface, "ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value"),
  "NEXO FCEV pedal safety": (interface, "ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.FCEV_GAS.value"),
  "standard LONG tx list": (safety, "HYUNDAI_LONG_TX_MSGS"),
  "SCC11 allowlist": (safety, "{0x420, 0,       8, .check_relay = true}"),
  "SCC12 allowlist": (safety, "{0x421, 0,       8, .check_relay = true}"),
  "SCC13 allowlist": (safety, "{0x50A, 0,       8, .check_relay = true}"),
  "SCC14 allowlist": (safety, "{0x389, 0,       8, .check_relay = true}"),
  "tester present allowlist": (safety, "{0x7D0, 0, 8, .check_relay = false}"),
  "exact tester present payload": (safety, "GET_BYTES(msg, 0, 4) != 0x00803E02U"),
  "FCEV accelerator source": (safety_common, "HYUNDAI_PARAM_FCEV_GAS = 256"),
  "AI object state": (hyundaican, '"ObjValid": 1 if is_nexo'),
  "AI jerk lower": (hyundaican, '"JerkLowerLimit": 5.0 if is_nexo'),
  "targeted standard LONG test": (standard_tests, "class TestHyundaiNexoStandardLong"),
  "expected safetyParam test": (standard_tests, "self.assertEqual(int(self.PARAM), 260)"),
  "tester present test": (standard_tests, "test_exact_tester_present_is_allowed"),
  "acceleration limit test": (standard_tests, "test_scc12_keeps_normal_longitudinal_limits"),
  "static SCC block test": (standard_tests, "test_standard_long_statically_blocks_camera_side_scc"),
  "workflow FCEV LONG test": (workflow, "test_hyundai_nexo_standard_long"),
}
for name, (source, token) in required.items():
  if token not in source:
    raise SystemExit(f"missing {name}: {token}")

# NEXOdriveAI's proven runtime uses ordinary Hyundai LONG safety. The custom
# dynamic-forwarding flag may remain in the source for historical comparison,
# but the production NEXO interface must never select it.
if "safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value" in interface:
  raise SystemExit("experimental NEXO dynamic SCC safety is still enabled")

# Generic Hyundai longitudinal relay protection and pedal safety must remain
# intact. The physical radar SCC is disabled over UDS and verified separately.
normal_macro = safety.split("#define HYUNDAI_LONG_COMMON_TX_MSGS", 1)[1].split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[0]
if "disable_static_blocking" in normal_macro:
  raise SystemExit("generic Hyundai longitudinal static forwarding was weakened")
if ".check_relay = true" not in normal_macro:
  raise SystemExit("generic Hyundai relay protection was changed")
if "gas_pressed = brake_pressed = false" in safety:
  raise SystemExit("unsafe AI pedal bypass detected")

print("NEXO standard Hyundai LONG, FCEV pedal safety, SCC/Tester Present parity checks passed")
