#!/usr/bin/env python3
# simple pandad wrapper that updates the panda first
import os
import usb1
import time
import signal
import subprocess
import traceback
from pathlib import Path

from panda import Panda, PandaDFU, PandaProtocolMismatch, McuType, FW_PATH
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.system.hardware import HARDWARE
from openpilot.common.swaglog import cloudlog

PANDAD_ERROR_FILE = Path("/data/nexopilot/pandad_last_error.txt")


def get_expected_signature() -> bytes:
  fn = os.path.join(FW_PATH, McuType.H7.config.app_fn)
  return Panda.get_signature_from_firmware(fn)


def _record_pandad_error(label: str) -> None:
  try:
    PANDAD_ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
    PANDAD_ERROR_FILE.write_text(f"{label}\n{traceback.format_exc()}", encoding="utf-8")
  except Exception:
    pass


def flash_panda(panda_serial: str):
  panda = Panda(panda_serial)
  fw_signature = get_expected_signature()
  internal_panda = panda.is_internal()

  panda_version = "bootstub" if panda.bootstub else panda.get_version()
  panda_signature = b"" if panda.bootstub else panda.get_signature()
  cloudlog.warning(f"Panda {panda_serial} connected, version: {panda_version}, signature {panda_signature.hex()[:16]}, expected {fw_signature.hex()[:16]}")

  if panda.bootstub or panda_signature != fw_signature:
    cloudlog.info("Panda firmware out of date, update required")
    panda.flash()
    cloudlog.info("Done flashing")

  if panda.bootstub:
    bootstub_version = panda.get_version()
    cloudlog.info(f"Flashed firmware not booting, flashing development bootloader. {bootstub_version=}, {internal_panda=}")
    if internal_panda:
      HARDWARE.recover_internal_panda()
    panda.recover(reset=(not internal_panda))
    cloudlog.info("Done flashing bootstub")

  if panda.bootstub:
    cloudlog.info("Panda still not booting, exiting")
    raise AssertionError

  panda_signature = panda.get_signature()
  if panda_signature != fw_signature:
    cloudlog.info("Version mismatch after flashing, exiting")
    raise AssertionError

  panda.close()


def main() -> None:
  # signal pandad to close the relay and exit
  def signal_handler(signum, frame):
    cloudlog.info(f"Caught signal {signum}, exiting")
    nonlocal do_exit
    do_exit = True
    if process is not None:
      process.send_signal(signal.SIGINT)

  process = None
  do_exit = False
  signal.signal(signal.SIGINT, signal_handler)

  # check health for lost heartbeat
  try:
    for s in Panda.list():
      with Panda(s) as p:
        health = p.health()
        if p.is_internal() and health["heartbeat_lost"]:
          Params().put_bool("PandaHeartbeatLost", True, block=True)
          cloudlog.event("heartbeat lost", deviceState=health)
  except Exception:
    _record_pandad_error("startup health check")
    cloudlog.exception("pandad.uncaught_exception")

  count = 0
  while not do_exit:
    try:
      cloudlog.event("pandad.flash_and_connect", count=count)
      if (count % 2) == 0:
        HARDWARE.reset_internal_panda()
      else:
        HARDWARE.recover_internal_panda()
      count += 1

      # Flash all Pandas in DFU mode
      for serial in PandaDFU.list():
        cloudlog.info(f"Panda in DFU mode found, flashing recovery {serial}")
        PandaDFU(serial).recover()
        time.sleep(1)

      panda_serials = Panda.list()
      if len(panda_serials):
        assert len(panda_serials) == 1
        cloudlog.info(f"{len(panda_serials)} panda found, connecting - {panda_serials}")

        # This verifies the physical Panda signature against FW_PATH and flashes
        # when needed. FW_PATH already selects the prepared NEXO firmware only
        # when ready.json + both images are present.
        flash_panda(panda_serials[0])

        # Native pandad still compares against the repository prebuilt image,
        # which is intentionally different from the separately prepared NEXO
        # firmware. Skip only that redundant child-process check after the
        # wrapper above has just verified the exact physical signature.
        child_env = os.environ.copy()
        child_env["MANAGER_DAEMON"] = "pandad"
        child_env["BOARDD_SKIP_FW_CHECK"] = "1"
        process = subprocess.Popen(["./pandad"], cwd=os.path.join(BASEDIR, "selfdrive/pandad"), env=child_env)
        returncode = process.wait()
        if returncode != 0 and not do_exit:
          cloudlog.error(f"native pandad exited with returncode={returncode}")
          try:
            PANDAD_ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
            PANDAD_ERROR_FILE.write_text(f"native pandad exited with returncode={returncode}\n", encoding="utf-8")
          except Exception:
            pass
    # TODO: wrap all panda exceptions in a base panda exception
    except (usb1.USBErrorNoDevice, usb1.USBErrorPipe):
      _record_pandad_error("Panda USB exception while setting up")
      cloudlog.exception("Panda USB exception while setting up")
    except PandaProtocolMismatch:
      _record_pandad_error("pandad.protocol_mismatch")
      cloudlog.exception("pandad.protocol_mismatch")
    except Exception:
      _record_pandad_error("pandad.uncaught_exception")
      cloudlog.exception("pandad.uncaught_exception")


if __name__ == "__main__":
  main()
