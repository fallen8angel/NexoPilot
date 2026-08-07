#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
  if not condition:
    raise SystemExit(message)


firmware = read("tools/nexo_panda_firmware.py")
constants = read("panda/python/constants.py")
launcher = read("launch_chffrplus.sh")
pandad = read("selfdrive/pandad/pandad.py")
panda_build = read("panda/SConscript")
panda_main = read("panda/board/main.c")
stm_config = read("panda/board/stm32h7/stm32h7_config.h")
interface = read("opendbc_repo/opendbc/car/hyundai/interface.py")

for path, source in (
  ("tools/nexo_panda_firmware.py", firmware),
  ("panda/python/constants.py", constants),
  ("selfdrive/pandad/pandad.py", pandad),
):
  ast.parse(source, filename=path)

for token in (
  'STATE_DIR = Path("/data/nexopilot/panda_fw")',
  '"opendbc_repo/opendbc/safety"',
  '"panda/board"',
  '"panda/board/obj/panda_h7.bin.signed"',
  '"panda/board/obj/bootstub.panda_h7.bin"',
  'build_env.pop("RELEASE", None)',
  '_restore_generated_tree()',
  'put_bool("AlphaLongitudinalEnabled", False',
  'READY_FILE.unlink(missing_ok=True)',
):
  require(token in firmware, f"missing Panda freshness contract: {token}")

for token in (
  'NEXO_FW_PATH = "/data/nexopilot/panda_fw/"',
  'NEXO_FW_READY',
  'NEXO_FW_APP',
  'NEXO_FW_BOOTSTUB',
  'FW_PATH = NEXO_FW_PATH if',
):
  require(token in constants, f"external Panda firmware selection missing: {token}")

verify_pos = launcher.find('python3 "$DIR/tools/nexo_panda_firmware.py"')
manager_pos = launcher.find('./manager.py')
require(verify_pos >= 0 and manager_pos >= 0 and verify_pos < manager_pos,
        "Panda firmware verification must run before manager/pandad")
require("exit 1" in launcher[verify_pos:manager_pos],
        "launcher must fail closed when firmware preparation and stock fallback both fail")

for token in (
  "panda_signature != fw_signature",
  "panda.flash()",
  "HARDWARE.recover_internal_panda()",
):
  require(token in pandad, f"standard Panda flash/recovery path missing: {token}")

require("opendbc.INCLUDE_PATH" in panda_build,
        "Panda firmware must compile against the checkout's opendbc safety headers")
require('#include "opendbc/safety/safety.h"' in panda_main,
        "Panda firmware main must include opendbc safety hooks")
require("#define CAN_INTERRUPT_RATE 16000U" in stm_config,
        "do not mask interruptRateCan2 by changing the Panda interrupt threshold")

# Keep the first physical-firmware correction on the already-tested standard
# Hyundai LONG + FCEV path. Do not silently resurrect the old 1284 experiment.
require("ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.NEXO_DYNAMIC_SCC.value" not in interface,
        "NEXO_DYNAMIC_SCC must not be re-enabled while physical firmware provenance is under test")
require("ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value" in interface,
        "standard Hyundai LONG safety path missing")
require("ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.FCEV_GAS.value" in interface,
        "NEXO FCEV pedal safety path missing")

print("NEXO Panda firmware freshness/fail-closed path PASS")
