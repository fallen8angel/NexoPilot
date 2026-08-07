#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path("/data/nexopilot/panda_fw")
READY_FILE = STATE_DIR / "ready.json"
STATUS_FILE = STATE_DIR / "status.json"
BUILD_LOG = STATE_DIR / "build.log"
APP_NAME = "panda_h7.bin.signed"
BOOTSTUB_NAME = "bootstub.panda_h7.bin"
GENERATED_DIR = ROOT / "panda/board/obj"
BUILD_APP = GENERATED_DIR / APP_NAME
BUILD_BOOTSTUB = GENERATED_DIR / BOOTSTUB_NAME
BUILD_VERSION = GENERATED_DIR / "version"


def _git(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
  return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True,
                        timeout=timeout, check=False)


def _git_value(*args: str) -> str:
  try:
    result = _git(*args)
    return result.stdout.strip() if result.returncode == 0 else ""
  except Exception:
    return ""


def _is_nexo_installation() -> bool:
  try:
    if Path("/data/nexopilot/force_nexo").read_text(encoding="utf-8").strip() == "1":
      return True
  except (FileNotFoundError, OSError):
    pass
  return _git_value("branch", "--show-current") == "NEXO"


def _source_files() -> list[Path]:
  files: list[Path] = []
  safety_root = ROOT / "opendbc_repo/opendbc/safety"
  panda_board = ROOT / "panda/board"

  for path in safety_root.rglob("*"):
    if path.is_file() and path.suffix in {".h", ".c"}:
      files.append(path)

  for path in panda_board.rglob("*"):
    if not path.is_file() or GENERATED_DIR in path.parents:
      continue
    if "certs" in path.parts:
      # Signing keys do not change runtime safety logic and private key contents
      # must never be copied into diagnostic state.
      continue
    if path.suffix.lower() in {".h", ".c", ".s", ".ld"}:
      files.append(path)

  files.append(ROOT / "panda/SConscript")
  return sorted(set(files), key=lambda path: str(path.relative_to(ROOT)))


def source_hash() -> str:
  digest = hashlib.sha256()
  for path in _source_files():
    rel = str(path.relative_to(ROOT)).replace(os.sep, "/")
    digest.update(rel.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(b"\0")
  return digest.hexdigest()


def _sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_suffix(path.suffix + ".tmp")
  temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
  temporary.replace(path)


def _ready_matches(expected_hash: str) -> bool:
  try:
    ready = json.loads(READY_FILE.read_text(encoding="utf-8"))
    app = STATE_DIR / APP_NAME
    bootstub = STATE_DIR / BOOTSTUB_NAME
    return (
      ready.get("state") == "ready"
      and ready.get("sourceHash") == expected_hash
      and app.is_file()
      and bootstub.is_file()
      and ready.get("appSha256") == _sha256(app)
      and ready.get("bootstubSha256") == _sha256(bootstub)
    )
  except (OSError, ValueError, TypeError, json.JSONDecodeError):
    return False


def _disable_longitudinal_after_build_failure(reason: str) -> bool:
  try:
    from openpilot.common.params import Params
    Params().put_bool("AlphaLongitudinalEnabled", False, block=True)
    return True
  except Exception as error:
    print(f"NEXO Panda firmware: failed to disable longitudinal after build failure: {error}", file=sys.stderr)
    return False


def _restore_generated_tree() -> tuple[bool, str]:
  result = _git("checkout", "--", "panda/board/obj", timeout=30)
  if result.returncode == 0:
    return True, ""
  return False, (result.stderr.strip() or result.stdout.strip() or "git checkout failed")


def _write_failure(expected_hash: str, reason: str, started: float) -> bool:
  READY_FILE.unlink(missing_ok=True)
  stock_fallback = _disable_longitudinal_after_build_failure(reason)
  _atomic_json(STATUS_FILE, {
    "state": "failed",
    "sourceHash": expected_hash,
    "gitCommit": _git_value("rev-parse", "HEAD"),
    "reason": reason,
    "stockCruiseFallback": stock_fallback,
    "elapsedSec": round(time.monotonic() - started, 3),
  })
  return stock_fallback


def ensure_firmware() -> bool:
  if not _is_nexo_installation():
    return True

  started = time.monotonic()
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  expected_hash = source_hash()

  if _ready_matches(expected_hash):
    try:
      ready = json.loads(READY_FILE.read_text(encoding="utf-8"))
      ready["gitCommit"] = _git_value("rev-parse", "HEAD")
      ready["checkedAtMonotonic"] = round(time.monotonic(), 3)
      _atomic_json(READY_FILE, ready)
      _atomic_json(STATUS_FILE, ready)
    except Exception:
      pass
    print(f"NEXO Panda firmware: current source already prepared ({expected_hash[:12]})")
    return True

  # Never overwrite a user's pre-existing generated-firmware edits. Generated
  # files produced by this function are restored before it returns.
  dirty = _git("status", "--porcelain", "--", "panda/board/obj", timeout=30)
  if dirty.returncode != 0:
    reason = dirty.stderr.strip() or "cannot inspect Panda generated tree"
    return _write_failure(expected_hash, reason, started)
  if dirty.stdout.strip():
    reason = "panda/board/obj contains pre-existing local changes; refusing to overwrite them"
    return _write_failure(expected_hash, reason, started)

  READY_FILE.unlink(missing_ok=True)
  command = [
    "scons", "-j2",
    "panda/board/obj/panda_h7.bin.signed",
    "panda/board/obj/bootstub.panda_h7.bin",
  ]

  build_env = dict(os.environ)
  build_env["PWD"] = str(ROOT)
  build_env.pop("RELEASE", None)  # build a matched development app + development bootstub
  build_env.pop("CERT", None)

  build_output = ""
  try:
    result = subprocess.run(command, cwd=ROOT, env=build_env, text=True,
                            capture_output=True, timeout=900, check=False)
    build_output = (result.stdout + "\n" + result.stderr).strip()
    BUILD_LOG.write_text(build_output[-200000:], encoding="utf-8")
    if result.returncode != 0:
      reason = f"Panda firmware build failed with exit {result.returncode}"
      restored, restore_error = _restore_generated_tree()
      if not restored:
        reason += f"; generated-tree restore failed: {restore_error}"
      return _write_failure(expected_hash, reason, started)

    if not BUILD_APP.is_file() or not BUILD_BOOTSTUB.is_file():
      reason = "Panda firmware build completed without required app/bootstub outputs"
      restored, restore_error = _restore_generated_tree()
      if not restored:
        reason += f"; generated-tree restore failed: {restore_error}"
      return _write_failure(expected_hash, reason, started)

    version = BUILD_VERSION.read_text(encoding="utf-8", errors="replace").strip() if BUILD_VERSION.is_file() else "unknown"
    app_tmp = STATE_DIR / f"{APP_NAME}.tmp"
    bootstub_tmp = STATE_DIR / f"{BOOTSTUB_NAME}.tmp"
    shutil.copyfile(BUILD_APP, app_tmp)
    shutil.copyfile(BUILD_BOOTSTUB, bootstub_tmp)
    app_tmp.replace(STATE_DIR / APP_NAME)
    bootstub_tmp.replace(STATE_DIR / BOOTSTUB_NAME)

    app_sha = _sha256(STATE_DIR / APP_NAME)
    bootstub_sha = _sha256(STATE_DIR / BOOTSTUB_NAME)
  except Exception as error:
    reason = f"Panda firmware preparation exception: {type(error).__name__}: {error}"
    restored, restore_error = _restore_generated_tree()
    if not restored:
      reason += f"; generated-tree restore failed: {restore_error}"
    return _write_failure(expected_hash, reason, started)

  restored, restore_error = _restore_generated_tree()
  if not restored:
    return _write_failure(expected_hash, f"generated-tree restore failed: {restore_error}", started)

  ready = {
    "state": "ready",
    "sourceHash": expected_hash,
    "gitCommit": _git_value("rev-parse", "HEAD"),
    "firmwareVersion": version,
    "appSha256": app_sha,
    "bootstubSha256": bootstub_sha,
    "firmwarePath": str(STATE_DIR),
    "stockCruiseFallback": False,
    "elapsedSec": round(time.monotonic() - started, 3),
  }
  _atomic_json(READY_FILE, ready)
  _atomic_json(STATUS_FILE, ready)
  print(f"NEXO Panda firmware: prepared {version} from current safety source ({expected_hash[:12]})")
  return True


def print_status() -> None:
  payload: dict[str, object] = {
    "nexoInstallation": _is_nexo_installation(),
    "sourceHash": source_hash(),
    "ready": False,
  }
  try:
    payload["status"] = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
  except Exception:
    payload["status"] = {"state": "missing"}
  try:
    payload["ready"] = _ready_matches(str(payload["sourceHash"]))
  except Exception:
    payload["ready"] = False
  print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--status", action="store_true")
  args = parser.parse_args()
  if args.status:
    print_status()
    return 0

  # If preparation fails but stock-cruise fallback was successfully selected,
  # continue boot in stock mode. If even that cannot be guaranteed, fail closed
  # and do not start manager/pandad from this launcher invocation.
  return 0 if ensure_firmware() else 1


if __name__ == "__main__":
  raise SystemExit(main())
