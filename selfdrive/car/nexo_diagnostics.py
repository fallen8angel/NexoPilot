from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import cereal.messaging as messaging

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard


NEXO_LAST_FAULT_LOG = Path("/data/nexo_last_fault.txt")


def _param_text(params: Params, key: str) -> str:
  try:
    value = params.get(key)
    if value is None:
      return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
  except Exception:
    return ""


def _enum_name(value) -> str:
  text = str(value)
  return text.rsplit(".", 1)[-1] if text else "확인 불가"


def _panda_snapshot(panda, index: int) -> dict[str, object]:
  try:
    faults = sorted({_enum_name(fault) for fault in panda.faults})
  except Exception:
    faults = []
  return {
    "index": index,
    "controls_allowed": bool(panda.controlsAllowed),
    "safety_model": str(panda.safetyModel),
    "safety_param": int(panda.safetyParam),
    "safety_rx_checks_invalid": bool(panda.safetyRxChecksInvalid),
    "fault_status": _enum_name(getattr(panda, "faultStatus", "확인 불가")),
    "faults": [fault for fault in faults if fault not in ("none", "0", "확인 불가")],
  }


def record_nexo_fault_snapshot(params: Params, guard: NexoStockSccRuntimeGuard,
                               sm: messaging.SubMaster, error: Exception) -> None:
  """Persist the exact pre-reboot state without changing vehicle control."""
  try:
    payload = guard.fault_snapshot()
    payload.update({
      "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
      "reason": str(error),
      "git_commit": _param_text(params, "GitCommit"),
      "git_branch": _param_text(params, "GitBranch"),
    })

    try:
      control = sm["carControl"]
      actuators = control.actuators
      payload["car_control"] = {
        "enabled": bool(control.enabled),
        "lat_active": bool(control.latActive),
        "long_active": bool(control.longActive),
        "requested_accel": float(actuators.accel),
        "long_control_state": _enum_name(actuators.longControlState),
        "cruise_cancel": bool(control.cruiseControl.cancel),
        "cruise_resume": bool(control.cruiseControl.resume),
        "cruise_override": bool(control.cruiseControl.override),
      }
    except Exception as state_error:
      payload["car_control_error"] = str(state_error)

    try:
      state = sm["selfdriveState"]
      payload["selfdrive_state"] = {
        "state": str(state.state),
        "enabled": bool(state.enabled),
        "active": bool(state.active),
        "alert_text_1": str(state.alertText1),
        "alert_text_2": str(state.alertText2),
      }
    except Exception as state_error:
      payload["selfdrive_state_error"] = str(state_error)

    try:
      pandas = sm["pandaStates"]
      panda_items = [_panda_snapshot(panda, index) for index, panda in enumerate(pandas)]
      payload["pandas"] = panda_items
      payload["panda"] = panda_items[0] if panda_items else None
      payload["panda_faults"] = sorted({fault for item in panda_items for fault in item["faults"]})
    except Exception as state_error:
      payload["panda_error"] = str(state_error)

    try:
      radar = sm["radarState"]
      errors = radar.radarErrors
      payload["radar_errors"] = {
        "can_error": bool(errors.canError),
        "radar_fault": bool(errors.radarFault),
        "wrong_config": bool(errors.wrongConfig),
        "temporary_unavailable": bool(errors.radarUnavailableTemporary),
      }
      lead = radar.leadOne
      payload["radar_lead_one"] = {
        "status": bool(lead.status),
        "d_rel_m": float(lead.dRel),
        "v_rel_mps": float(lead.vRel),
        "a_rel_mps2": float(lead.aRel),
        "v_lead_mps": float(lead.vLead),
      }
    except Exception as state_error:
      payload["radar_state_error"] = str(state_error)

    temporary = NEXO_LAST_FAULT_LOG.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(NEXO_LAST_FAULT_LOG)
  except Exception as snapshot_error:
    cloudlog.exception(f"NEXO fault snapshot write failed: {snapshot_error}")
