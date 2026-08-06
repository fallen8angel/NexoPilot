from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.nexo_session_owner import claim_owner


NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_TAKEOVER_VERIFY_LOG = Path("/data/nexo_scc_takeover_verification.json")


def _boot_id() -> str:
  try:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def _write_state(payload: dict[str, object]) -> None:
  try:
    output = {
      "wall_time": time.time(),
      "monotonic": time.monotonic(),
      "boot_id": _boot_id(),
      **payload,
    }
    temporary = NEXO_TAKEOVER_VERIFY_LOG.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(NEXO_TAKEOVER_VERIFY_LOG)
  except OSError:
    pass


def _observe_source0_scc(can_recv, duration_s: float) -> dict[str, object]:
  deadline = time.monotonic() + duration_s
  source0_frames = 0
  source0_scc: Counter[int] = Counter()

  while time.monotonic() < deadline:
    batches = can_recv(wait_for_one=True)
    for batch in batches:
      for message in batch:
        source = int(getattr(message, "src", -1))
        address = int(getattr(message, "address", -1))
        if source != 0:
          continue
        source0_frames += 1
        if address in NEXO_STOCK_SCC_ADDRS:
          source0_scc[address] += 1

  return {
    "source0_frames": source0_frames,
    "source0_scc_total": sum(source0_scc.values()),
    "source0_scc_counts": {f"0x{address:03X}": count for address, count in sorted(source0_scc.items())},
  }


def ensure_nexo_stock_scc_silent(can_recv, can_send, *, bus: int, addr: int,
                                  communication_control: bytes, trace: Callable[[str], None],
                                  attempts: int = 3, sample_s: float = 0.35,
                                  settle_s: float = 0.05, min_source0_frames: int = 20) -> bool:
  """Re-assert communication control after radar programming and prove SCC silence.

  NEXOdriveAI was observed with no physical source-0 SCC11/12/13/14 after
  takeover. Radar programming changes the diagnostic session on the same 0x7D0
  ECU, so communication control is re-issued after the DID write and verified
  against live CAN before Panda is switched into Hyundai longitudinal safety.
  """
  owner = claim_owner()
  trace(f"STEP 3 takeover owner claim success={bool(owner)} token={owner or 'none'}")
  if not owner:
    _write_state({"state": "failed", "success": False, "reason": "takeover owner claim failed", "attempts": []})
    return False

  attempt_records: list[dict[str, object]] = []

  for attempt in range(1, attempts + 1):
    try:
      can_recv(wait_for_one=False)  # discard frames queued before this attempt
    except Exception:
      pass

    started = time.monotonic()
    try:
      acknowledged = bool(disable_ecu(can_recv, can_send, bus=bus, addr=addr,
                                      com_cont_req=communication_control))
      detail = ""
    except Exception as error:
      acknowledged = False
      detail = f"{type(error).__name__}: {error}"

    try:
      can_recv(wait_for_one=False)  # discard traffic captured before the ACK
    except Exception:
      pass
    if settle_s > 0:
      time.sleep(settle_s)

    observation = _observe_source0_scc(can_recv, sample_s)
    enough_bus_data = int(observation["source0_frames"]) >= min_source0_frames
    silent = int(observation["source0_scc_total"]) == 0
    success = acknowledged and enough_bus_data and silent
    record = {
      "attempt": attempt,
      "acknowledged": acknowledged,
      "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
      "detail": detail,
      "enough_bus_data": enough_bus_data,
      "success": success,
      "owner": owner,
      **observation,
    }
    attempt_records.append(record)
    trace(
      f"STEP 3 attempt={attempt}/{attempts} re-suppress acknowledged={acknowledged} "
      f"source0_frames={observation['source0_frames']} source0_scc={observation['source0_scc_total']} "
      f"counts={observation['source0_scc_counts']} success={success} owner={owner}"
    )
    _write_state({"state": "verified" if success else "checking", "success": success,
                  "owner": owner, "attempts": attempt_records})
    if success:
      return True

  _write_state({"state": "failed", "success": False, "owner": owner, "attempts": attempt_records})
  return False
