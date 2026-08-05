#!/usr/bin/env python3
from pathlib import Path

values = Path("opendbc_repo/opendbc/car/hyundai/values.py").read_text()
interface = Path("opendbc_repo/opendbc/car/hyundai/interface.py").read_text()
safety_common = Path("opendbc_repo/opendbc/safety/modes/hyundai_common.h").read_text()
safety = Path("opendbc_repo/opendbc/safety/modes/hyundai.h").read_text()
hyundaican = Path("opendbc_repo/opendbc/car/hyundai/hyundaican.py").read_text()
tests = Path("opendbc_repo/opendbc/safety/tests/test_hyundai.py").read_text()
neutral_tests = Path("opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral_ownership.py").read_text()

required = {
  "values flag": (values, "NEXO_DYNAMIC_SCC = 1024"),
  "interface gating": (interface, "if is_nexo:\n        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value"),
  "common flag decode": (safety_common, "HYUNDAI_PARAM_NEXO_DYNAMIC_SCC = 1024"),
  "dedicated tx list": (safety, "HYUNDAI_NEXO_LONG_TX_MSGS"),
  "accepted SCC12 ownership": (safety, "tx && hyundai_nexo_dynamic_scc && (msg->bus == 0U) && (msg->addr == 0x421U)"),
  "neutral mode gate": (safety, "hyundai_nexo_dynamic_scc && !get_longitudinal_allowed() && (acc_mode != 0)"),
  "400ms timeout": (safety, "HYUNDAI_NEXO_SCC_OWNERSHIP_TIMEOUT_US = 400000U"),
  "bus2 hook": (safety, "hyundai_nexo_dynamic_scc && (bus_num == 2) && hyundai_nexo_is_scc_addr(addr)"),
  "fwd hook wired": (safety, ".fwd = hyundai_fwd_hook"),
  "AI object state": (hyundaican, '"ObjValid": 1 if is_nexo'),
  "AI jerk lower": (hyundaican, '"JerkLowerLimit": 5.0 if is_nexo'),
  "ownership tests": (tests, "class TestHyundaiNexoDynamicSCCOwnership"),
  "rejected SCC test": (tests, "test_rejected_scc12_does_not_claim_ownership"),
  "FCA pass-through test": (tests, "test_factory_fca_is_never_claimed"),
  "neutral ownership test": (neutral_tests, "test_controls_off_allows_only_neutral_scc12_and_arms_ownership"),
  "active mode blocked test": (neutral_tests, "test_controls_off_rejects_active_mode_even_with_zero_accel"),
  "nonzero accel blocked test": (neutral_tests, "test_controls_off_rejects_nonzero_accel"),
}
for name, (source, token) in required.items():
  if token not in source:
    raise SystemExit(f"missing {name}: {token}")

# Guardrails: generic Hyundai longitudinal behavior stays unchanged. Only the
# NEXO-specific dynamic SCC list opts out of the generic relay latch because the
# card runtime guard is the fail-closed source-0 authority.
normal_macro = safety.split("#define HYUNDAI_LONG_COMMON_TX_MSGS", 1)[1].split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[0]
nexo_macro = safety.split("#define HYUNDAI_NEXO_LONG_COMMON_TX_MSGS", 1)[1].split("#define HYUNDAI_COMMON_RX_CHECKS", 1)[0]
if "disable_static_blocking" in normal_macro:
  raise SystemExit("generic Hyundai longitudinal static forwarding was weakened")
if ".check_relay = true" not in normal_macro:
  raise SystemExit("generic Hyundai relay protection was changed")
if ".check_relay = true" in nexo_macro:
  raise SystemExit("NEXO SCC list still uses the generic relay latch")
for addr in ("0x420", "0x421", "0x50A", "0x389"):
  if f"{{{addr}, 0,       8, .check_relay = false, .disable_static_blocking = true}}" not in nexo_macro:
    raise SystemExit(f"NEXO dynamic SCC entry missing safe relay policy: {addr}")
if "gas_pressed = brake_pressed = false" in safety:
  raise SystemExit("unsafe AI pedal bypass detected")
if "is_fca_addr" in safety:
  raise SystemExit("NEXO ownership must not claim stock FCA")
print("NEXO AI SCC ownership, neutral handoff, and compatibility checks passed")
