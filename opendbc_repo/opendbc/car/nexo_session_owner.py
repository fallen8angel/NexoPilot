from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


NEXO_SCC_OWNER = Path("/data/nexo_scc_takeover_owner")
NEXO_SCC_OWNER_LOCK = Path("/data/nexo_scc_takeover_owner.lock")


def _process_start_ticks(pid: int) -> str:
  try:
    # /proc/<pid>/stat field 22 is the process start time in clock ticks.
    return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()[21]
  except (OSError, IndexError):
    return ""


def current_owner_token() -> str:
  return f"{os.getpid()}:{_process_start_ticks(os.getpid())}"


def _owner_alive(token: str) -> bool:
  try:
    pid_text, start_ticks = token.split(":", 1)
    pid = int(pid_text)
  except (TypeError, ValueError):
    return False
  return bool(start_ticks) and _process_start_ticks(pid) == start_ticks


@contextmanager
def owner_lock():
  handle = None
  try:
    NEXO_SCC_OWNER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = NEXO_SCC_OWNER_LOCK.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
  except OSError:
    if handle is not None:
      handle.close()
    handle = None

  try:
    yield
  finally:
    if handle is not None:
      try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
      finally:
        handle.close()


def read_owner_unlocked() -> str:
  try:
    return NEXO_SCC_OWNER.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def claim_owner() -> str:
  token = current_owner_token()
  try:
    with owner_lock():
      temporary = NEXO_SCC_OWNER.with_name(f"{NEXO_SCC_OWNER.name}.tmp.{os.getpid()}")
      temporary.write_text(token + "\n", encoding="utf-8")
      temporary.replace(NEXO_SCC_OWNER)
    return token
  except OSError:
    return ""


def current_process_owns() -> bool:
  token = current_owner_token()
  with owner_lock():
    return read_owner_unlocked() == token


def restore_allowed_unlocked(caller_token: str) -> tuple[bool, str]:
  owner = read_owner_unlocked()
  if not owner:
    return True, "legacy marker without owner"
  if owner == caller_token:
    return True, "caller owns active takeover"
  if not _owner_alive(owner):
    return True, f"stale owner {owner}"
  return False, f"active takeover owned by {owner}"


def clear_owner_if_current_unlocked(caller_token: str) -> bool:
  owner = read_owner_unlocked()
  if owner and owner != caller_token:
    return False
  try:
    NEXO_SCC_OWNER.unlink(missing_ok=True)
    return True
  except OSError:
    return False
