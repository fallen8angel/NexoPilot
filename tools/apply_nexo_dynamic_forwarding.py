#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HYUNDAI = ROOT / "opendbc_repo/opendbc/safety/modes/hyundai.h"
TEST = ROOT / "opendbc_repo/opendbc/safety/tests/test_hyundai.py"
VALIDATION = ROOT / "tools/check_nexo_integration.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
  count = text.count(old)
  if count != 1:
    raise RuntimeError(f"{label}: expected one match, found {count}")
  return text.replace(old, new, 1)


hyundai = HYUNDAI.read_text(encoding="utf-8")

for addr, comment in (
  ("0x420", "SCC11 Bus 0"),
  ("0x421", "SCC12 Bus 0"),
  ("0x50A", "SCC13 Bus 0"),
  ("0x389", "SCC14 Bus 0"),
):
  old = f"  {{{addr}, 0,       8, .check_relay = true}},   /* {comment}       */ \\\n"
  new = f"  {{{addr}, 0,       8, .check_relay = true, .disable_static_blocking = true}},   /* {comment} */ \\\n"
  hyundai = replace_once(hyundai, old, new, f"dynamic static-block opt-out {addr}")

hyundai = replace_once(
  hyundai,
  "static bool hyundai_legacy = false;\n",
  """static bool hyundai_legacy = false;

// NEXO is currently the only Hyundai FCEV platform in this integration. Keep
// relay-malfunction checks, but suppress camera-side stock SCC forwarding only
// while valid openpilot SCC frames are being sent. Stock forwarding resumes
// after 200 ms if openpilot stops transmitting.
static bool hyundai_nexo_dynamic_scc_fwd = false;
static bool hyundai_nexo_scc_tx_seen = false;
static bool hyundai_nexo_fca_tx_seen = false;
static uint32_t hyundai_nexo_scc_last_tx = 0U;
static uint32_t hyundai_nexo_fca_last_tx = 0U;
static const uint32_t HYUNDAI_NEXO_FWD_TIMEOUT_US = 200000U;

static bool hyundai_nexo_is_scc_addr(const int addr) {
  return (addr == 0x389) || (addr == 0x420) || (addr == 0x421) || (addr == 0x50A);
}

static bool hyundai_nexo_is_fca_addr(const int addr) {
  return (addr == 0x38D) || (addr == 0x483);
}
""",
  "NEXO forwarding state",
)

hyundai = replace_once(
  hyundai,
  """  return tx;
}

static safety_config hyundai_init(uint16_t param) {
""",
  """  if (tx && hyundai_nexo_dynamic_scc_fwd && (msg->bus == 0U)) {
    const uint32_t now = microsecond_timer_get();
    if (hyundai_nexo_is_scc_addr(msg->addr)) {
      hyundai_nexo_scc_tx_seen = true;
      hyundai_nexo_scc_last_tx = now;
    } else if (hyundai_nexo_is_fca_addr(msg->addr)) {
      hyundai_nexo_fca_tx_seen = true;
      hyundai_nexo_fca_last_tx = now;
    } else {
    }
  }

  return tx;
}

static safety_config hyundai_init(uint16_t param) {
""",
  "record accepted NEXO longitudinal TX",
)

hyundai = replace_once(
  hyundai,
  """  hyundai_common_init(param);
  hyundai_legacy = false;

  safety_config ret;
""",
  """  hyundai_common_init(param);
  hyundai_legacy = false;
  hyundai_nexo_dynamic_scc_fwd = hyundai_longitudinal && hyundai_fcev_gas_signal;
  hyundai_nexo_scc_tx_seen = false;
  hyundai_nexo_fca_tx_seen = false;
  hyundai_nexo_scc_last_tx = 0U;
  hyundai_nexo_fca_last_tx = 0U;

  safety_config ret;
""",
  "initialize NEXO dynamic forwarding",
)

hyundai = replace_once(
  hyundai,
  """  return ret;
}

static safety_config hyundai_legacy_init(uint16_t param) {
""",
  """  return ret;
}

static bool hyundai_fwd_hook(int bus_num, int addr) {
  // SCC messages travel from the camera-side bus 2 toward powertrain bus 0.
  // Other Hyundai longitudinal modes retain their existing always-block policy.
  if ((bus_num == 2) && hyundai_longitudinal && hyundai_nexo_is_scc_addr(addr)) {
    if (!hyundai_nexo_dynamic_scc_fwd) {
      return true;
    }

    const uint32_t now = microsecond_timer_get();
    return hyundai_nexo_scc_tx_seen &&
           (safety_get_ts_elapsed(now, hyundai_nexo_scc_last_tx) < HYUNDAI_NEXO_FWD_TIMEOUT_US);
  }

  // NEXO preserves stock FCA unless a valid openpilot FCA frame was accepted.
  if ((bus_num == 2) && hyundai_nexo_dynamic_scc_fwd && hyundai_nexo_is_fca_addr(addr)) {
    const uint32_t now = microsecond_timer_get();
    return hyundai_nexo_fca_tx_seen &&
           (safety_get_ts_elapsed(now, hyundai_nexo_fca_last_tx) < HYUNDAI_NEXO_FWD_TIMEOUT_US);
  }

  return false;
}

static safety_config hyundai_legacy_init(uint16_t param) {
""",
  "NEXO forwarding hook",
)

hyundai = replace_once(
  hyundai,
  """  hyundai_longitudinal = false;
  hyundai_camera_scc = false;
  return BUILD_SAFETY_CFG(hyundai_legacy_rx_checks, HYUNDAI_TX_MSGS);
""",
  """  hyundai_longitudinal = false;
  hyundai_camera_scc = false;
  hyundai_nexo_dynamic_scc_fwd = false;
  hyundai_nexo_scc_tx_seen = false;
  hyundai_nexo_fca_tx_seen = false;
  return BUILD_SAFETY_CFG(hyundai_legacy_rx_checks, HYUNDAI_TX_MSGS);
""",
  "legacy forwarding reset",
)

hyundai = replace_once(
  hyundai,
  """  .rx = hyundai_rx_hook,
  .tx = hyundai_tx_hook,
  .get_counter = hyundai_get_counter,
""",
  """  .rx = hyundai_rx_hook,
  .tx = hyundai_tx_hook,
  .fwd = hyundai_fwd_hook,
  .get_counter = hyundai_get_counter,
""",
  "register Hyundai forwarding hook",
)

HYUNDAI.write_text(hyundai, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
  test,
  """class TestHyundaiSafetyFCEVLong(TestHyundaiLongitudinalSafety, TestHyundaiSafetyFCEV):
  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG)
    self.safety.init_tests()


if __name__ == "__main__":
""",
  """class TestHyundaiSafetyFCEVLong(TestHyundaiLongitudinalSafety, TestHyundaiSafetyFCEV):
  # NEXO dynamically blocks stock SCC only while openpilot is actively sending.
  FWD_BLACKLISTED_ADDRS = {2: [0x340, 0x485]}

  def setUp(self):
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, HyundaiSafetyFlags.FCEV_GAS | HyundaiSafetyFlags.LONG)
    self.safety.init_tests()

  def test_nexo_dynamic_scc_forwarding(self):
    scc_addrs = (0x389, 0x420, 0x421, 0x50A)

    for addr in scc_addrs:
      self.assertEqual(0, self.safety.safety_fwd_hook(2, addr))

    self.safety.set_timer(1000000)
    self.assertTrue(self._tx(self._accel_msg(0)))
    for addr in scc_addrs:
      self.assertEqual(-1, self.safety.safety_fwd_hook(2, addr))

    self.safety.set_timer(1200001)
    for addr in scc_addrs:
      self.assertEqual(0, self.safety.safety_fwd_hook(2, addr))

  def test_nexo_stock_fca_preserved_without_openpilot_fca(self):
    for addr in (0x38D, 0x483):
      self.assertEqual(0, self.safety.safety_fwd_hook(2, addr))


if __name__ == "__main__":
""",
  "NEXO forwarding tests",
)
TEST.write_text(test, encoding="utf-8")

validation = VALIDATION.read_text(encoding="utf-8")
validation = replace_once(
  validation,
  """  require("longitudinal_accel_checks" in source, "longitudinal acceleration safety check missing")

  for address in ("0x38D", "0x483", "0x7D0"):
""",
  """  require("longitudinal_accel_checks" in source, "longitudinal acceleration safety check missing")
  require("hyundai_nexo_dynamic_scc_fwd" in source, "NEXO dynamic SCC forwarding state missing")
  require("HYUNDAI_NEXO_FWD_TIMEOUT_US" in source, "NEXO forwarding failover timeout missing")
  require(".fwd = hyundai_fwd_hook" in source, "Hyundai forwarding hook registration missing")
  require(".disable_static_blocking = true" in source, "dynamic SCC static-block opt-out missing")
  require("hyundai_nexo_scc_tx_seen" in source, "NEXO SCC TX liveness tracking missing")

  for address in ("0x38D", "0x483", "0x7D0"):
""",
  "NEXO integration safety validation",
)
VALIDATION.write_text(validation, encoding="utf-8")

print("Applied NEXO dynamic SCC/FCA forwarding integration")
