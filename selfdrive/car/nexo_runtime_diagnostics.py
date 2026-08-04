from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


NEXO_CARD_CRASH_LOG = Path("/data/nexo_card_crash.txt")
NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")


def _param_text(params: Params, key: str) -> str:
  try:
    value = params.get(key)
    if value is None:
      return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
  except Exception:
    return ""


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
  temporary = path.with_suffix(path.suffix + ".tmp")
  temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
  temporary.replace(path)


def set_nexo_runtime_state(params: Params, state: str, stage: str, reason: str = "") -> None:
  """Publish low-rate NEXO card state for the port-7000 diagnostics page."""
  try:
    params.put("NexoCardSessionState", state)
    params.put("NexoCardStage", stage)
    if reason:
      params.put("NexoCardSessionReason", reason)
    else:
      params.remove("NexoCardSessionReason")
  except Exception as error:
    cloudlog.warning(f"NEXO runtime state publish failed: {error}")


def record_nexo_long_success(params: Params) -> None:
  payload = {
    "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
    "git_commit": _param_text(params, "GitCommit"),
    "git_branch": _param_text(params, "GitBranch"),
    "result": "radar tracks enabled; card runtime guard armed",
  }
  try:
    _write_json_atomic(NEXO_LONG_SUCCESS_LOG, payload)
    params.remove("NexoLongitudinalFailure")
    set_nexo_runtime_state(params, "active", "longitudinal_active")
  except Exception as error:
    cloudlog.warning(f"NEXO success marker write failed: {error}")


def record_nexo_card_crash(params: Params, stage: str, error: BaseException, traceback_text: str) -> None:
  """Persist an uncaught card exception before the process exits."""
  payload = {
    "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
    "git_commit": _param_text(params, "GitCommit"),
    "git_branch": _param_text(params, "GitBranch"),
    "stage": stage,
    "exception_type": type(error).__name__,
    "reason": str(error),
    "traceback": traceback_text,
    "longitudinal_failure": _param_text(params, "NexoLongitudinalFailure"),
  }
  try:
    _write_json_atomic(NEXO_CARD_CRASH_LOG, payload)
    params.put("NexoCardLastCrash", f"{payload['wall_time']} {payload['exception_type']}: {payload['reason']}")
    set_nexo_runtime_state(params, "crashed", stage, str(error))
  except Exception as write_error:
    cloudlog.exception(f"NEXO card crash snapshot write failed: {write_error}")
