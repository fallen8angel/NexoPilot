#!/usr/bin/env python3
from pathlib import Path

values = Path("opendbc_repo/opendbc/car/hyundai/values.py").read_text()
interface = Path("opendbc_repo/opendbc/car/hyundai/interface.py").read_text()
safety_common = Path("opendbc_repo/opendbc/safety/modes/hyundai_common.h").read_text()
safety = Path("opendbc_repo/opendbc/safety/modes/hyundai.h").read_text()
hyundaican = Path("opendbc_repo/opendbc/car/hyundai/hyundaican.py").read_text()
tests = Path("opendbc_repo/opendbc/safety/tests/test_hyundai.py").read_text()

required = {
  "values flag": (values, "NEXO_DYNAMIC_SCC = 1024"),
  "interface gating": (interface, "if is_nexo:\n        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value"),
  "common flag decode": (safety_common, "HYUNDAI_PARAM_NEXO_DYNAMIC_SCC = 1024"),
  "dedicated tx list": (safety, "HYUNDAI_NEXO_LONG_TX_MSGS"),
  "accepted SCC12 ownership": (safety, "tx && hyundai_nexo_dynamic_scc && (msg->bus == 0U) && (msg->addr == 0x421U)"),
  "400ms timeout": (safety, "HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US = 400000U"),
  "bus2 hook": (safety, "hyundai_nexo_dynamic_scc && (bus_num == 2) && hyundai_nexo_is_scc_addr(addr)"),
  "fwd hook wired": (safety, ".fwd = hyundai_fwd_hook"),
  "AI object state": (hyundaican, '"ObjValid": 1 if is_nexo'),
  "AI jerk lower": (hyundaican, '"JerkLowerLimit": 5.0 if is_nexo'),
  "ownership tests": (tests, "class TestHyundaiNexoDynamicSCCOwnership"),
  "rejected SCC test": (tests, "test_rejected_scc12_does_not_claim_ownership"),
  "FCA pass-through test": (tests, "test_factory_fca_is_never_claimed"),
}
for name, (text, token) in required.items():
  if token not in text:
    raise SystemExit(f"missing {name}: {token}")

# Guardrails: the normal longitudinal list must stay statically blocked and the
# NEXO implementation must not reintroduce the AI brake/gas bypass.
normal_macro = safety.split("#define HYUNDAI_LONG_COMMON_TX_MSGS", 1)[1].split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[0]
if "disable_static_blocking" in normal_macro:
  raise SystemExit("generic Hyundai longitudinal static forwarding was weakened")
if "gas_pressed = brake_pressed = false" in safety:
  raise SystemExit("unsafe AI pedal bypass detected")
if "is_fca_addr" in safety:
  raise SystemExit("NEXO ownership must not claim stock FCA")
print("NEXO AI SCC ownership and compatibility checks passed")
