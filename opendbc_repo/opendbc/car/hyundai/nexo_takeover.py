from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from opendbc.car import make_tester_present_msg
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery
from opendbc.car.nexo_session_owner import claim_owner


# Physical source-0 longitudinal streams that must be silent before openpilot
# takes ownership. XPlus' verified NEXO takeover also replaces FCA11 (0x38D),
# so letting a stock FCA11 survive can create a duplicate FCA/SCC status stream.
NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x38D, 0x420, 0x421, 0x50A))
NEXO_TAKEOVER_VERIFY_LOG = Path("/data/nexo_scc_takeover_verification.json")
NEXO_CARD_CMDLINE_MARKERS = (b"selfdrive.car.card", b"selfdrive/car/card.py")


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


def _process_start_ticks(proc_dir: Path) -> str:
  try:
    raw = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace")
    # The process name is parenthesized and can contain spaces.
    tail = raw[raw.rfind(") ") + 2:].split()
    return tail[19]  # field 22, with tail starting at field 3
  except (OSError, IndexError):
    return ""


def _other_card_process_tokens(proc_root: Path = Path("/proc")) -> list[str]:
  current_pid = os.getpid()
  try:
    entries = list(proc_root.iterdir())
  except OSError:
    return ["proc-scan-unavailable"]

  tokens: list[str] = []
  for entry in entries:
    if not entry.name.isdigit() or int(entry.name) == current_pid:
      continue
    try:
      cmdline = (entry / "cmdline").read_bytes()
    except OSError:
      continue
    if not any(marker in cmdline for marker in NEXO_CARD_CMDLINE_MARKERS):
      continue
    start_ticks = _process_start_ticks(entry)
    if start_ticks:
      tokens.append(f"{entry.name}:{start_ticks}")
  return sorted(tokens)


def _wait_for_exclusive_card_process(timeout_s: float, *, quiet_s: float = 0.75,
                                     poll_s: float = 0.05) -> tuple[bool, list[str], float]:
  """Require a continuous period with no older card process."""
  started = time.monotonic()
  quiet_started: float | None = None
  last_others: list[str] = []

  while True:
    now = time.monotonic()
    last_others = _other_card_process_tokens()
    if not last_others:
      quiet_started = now if quiet_started is None else quiet_started
      if timeout_s <= 0.0 or now - quiet_started >= quiet_s:
        return True, [], now - started
    else:
      quiet_started = None

    if now - started >= timeout_s:
      return False, last_others, now - started
    time.sleep(min(poll_s, max(0.0, timeout_s - (now - started))))


def _observe_source0_scc(can_recv, duration_s: float,
                         keepalive: Callable[[], None] | None = None) -> dict[str, object]:
  deadline = time.monotonic() + duration_s
  source0_frames = 0
  source0_scc: Counter[int] = Counter()

  while time.monotonic() < deadline:
    if keepalive is not None:
      keepalive()
    for batch in can_recv(wait_for_one=True):
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


def _send_communication_control_direct(can_recv, can_send, *, bus: int, addr: int,
                                       communication_control: bytes) -> tuple[bool, str]:
  """Send 0x28 in the ECU's current diagnostic session without forcing 0x10 03 first.

  A hot NEXO restart can leave the MANDO radar already streaming tracks while the
  ECU no longer acknowledges another extended-session request. In that state the
  normal disable_ecu() path can stop before it ever sends CommunicationControl.
  This fallback only sends the requested 0x28 frame; takeover is still accepted
  exclusively by the physical source-0 SCC silence verification below.
  """
  try:
    query = IsoTpParallelQuery(can_send, can_recv, bus, [(addr, None)],
                               [communication_control], [b""])
    query.get_data(0)
    return True, ""
  except Exception as error:
    return False, f"{type(error).__name__}: {error}"


def _suppress_once(can_recv, can_send, *, bus: int, addr: int, communication_control: bytes,
                   sample_s: float, settle_s: float, min_source0_frames: int,
                   keepalive: Callable[[], None], label: str, owner: str) -> dict[str, object]:
  try:
    can_recv(wait_for_one=False)
  except Exception:
    pass

  started = time.monotonic()
  detail = ""
  try:
    acknowledged = bool(disable_ecu(can_recv, can_send, bus=bus, addr=addr,
                                    com_cont_req=communication_control))
  except Exception as error:
    acknowledged = False
    detail = f"{type(error).__name__}: {error}"

  direct_attempted = not acknowledged
  direct_sent = False
  if direct_attempted:
    direct_sent, direct_detail = _send_communication_control_direct(
      can_recv, can_send, bus=bus, addr=addr, communication_control=communication_control,
    )
    if direct_detail:
      detail = f"{detail}; " if detail else ""
      detail += f"direct CommunicationControl {direct_detail}"

  try:
    can_recv(wait_for_one=False)
  except Exception:
    pass
  if settle_s > 0:
    time.sleep(settle_s)

  try:
    observation = _observe_source0_scc(can_recv, sample_s, keepalive=keepalive)
  except Exception as error:
    acknowledged = False
    detail = f"{detail}; " if detail else ""
    detail += f"observation {type(error).__name__}: {error}"
    observation = {"source0_frames": 0, "source0_scc_total": 0, "source0_scc_counts": {}}

  enough_bus_data = int(observation["source0_frames"]) >= min_source0_frames
  # 0x28 0x83 0x01 suppresses the positive UDS response. On NEXO an ECU can
  # therefore be physically silent even when disable_ecu() cannot report an
  # acknowledgement. Treat the live source-0 bus observation as authoritative:
  # the bus itself must be alive and every stock SCC/FCA stream must be absent.
  # The direct fallback above does not relax this condition; it only makes sure
  # 0x28 is actually attempted when a hot diagnostic session rejects 0x10 03.
  # The longer stability window and runtime guard below still fail closed if
  # factory SCC returns after takeover.
  success = enough_bus_data and int(observation["source0_scc_total"]) == 0
  return {
    "label": label,
    "acknowledged": acknowledged,
    "directCommunicationControlAttempted": direct_attempted,
    "directCommunicationControlSent": direct_sent,
    "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
    "detail": detail,
    "enough_bus_data": enough_bus_data,
    "success": success,
    "owner": owner,
    **observation,
  }


def ensure_nexo_stock_scc_silent(can_recv, can_send, *, bus: int, addr: int,
                                  communication_control: bytes, trace: Callable[[str], None],
                                  attempts: int = 3, sample_s: float = 0.35,
                                  settle_s: float = 0.05, min_source0_frames: int = 20,
                                  exclusive_wait_s: float = 10.0,
                                  stability_observation_s: float = 2.0,
                                  stability_quiet_s: float = 1.0,
                                  stability_sample_s: float = 0.25,
                                  stability_timeout_s: float = 12.0,
                                  stability_reassertions: int = 4,
                                  tester_present_period_s: float = 0.8) -> bool:
  """Prove SCC silence after old card processes have fully exited.

  During a git update, legacy card processes can send a late factory-SCC restore
  after a new process has already verified takeover. Ownership is claimed first,
  older card processes are quarantined until they exit, and SCC is suppressed
  again. A bounded stability window keeps the diagnostic session alive and
  re-verifies any physical source-0 SCC relapse before longitudinal safety is
  enabled.
  """
  owner = claim_owner()
  trace(f"STEP 3 takeover owner claim success={bool(owner)} token={owner or 'none'}")
  if not owner:
    _write_state({"state": "failed", "success": False,
                  "reason": "takeover owner claim failed", "attempts": []})
    return False

  exclusive, others, waited_s = _wait_for_exclusive_card_process(exclusive_wait_s)
  trace(f"STEP 3 card-process quarantine exclusive={exclusive} waited_ms={waited_s * 1000.0:.1f} remaining={others}")
  if not exclusive:
    _write_state({
      "state": "failed",
      "success": False,
      "reason": "other card processes remained during takeover",
      "owner": owner,
      "otherCardProcesses": others,
      "exclusiveWaitMs": round(waited_s * 1000.0, 1),
      "attempts": [],
    })
    return False

  last_tester_present = [0.0]

  def keepalive() -> None:
    now = time.monotonic()
    if now - last_tester_present[0] >= tester_present_period_s:
      can_send([make_tester_present_msg(addr, bus, suppress_response=True)])
      last_tester_present[0] = now

  attempt_records: list[dict[str, object]] = []

  def suppress(label: str) -> bool:
    for retry in range(1, attempts + 1):
      record = _suppress_once(
        can_recv, can_send, bus=bus, addr=addr, communication_control=communication_control,
        sample_s=sample_s, settle_s=settle_s, min_source0_frames=min_source0_frames,
        keepalive=keepalive, label=f"{label}-{retry}", owner=owner,
      )
      attempt_records.append(record)
      trace(
        f"STEP 3 {label} attempt={retry}/{attempts} acknowledged={record['acknowledged']} "
        f"direct28={record['directCommunicationControlSent']} "
        f"source0_frames={record['source0_frames']} source0_scc={record['source0_scc_total']} "
        f"counts={record['source0_scc_counts']} success={record['success']}"
      )
      if bool(record["success"]):
        return True
    return False

  if not suppress("initial"):
    _write_state({
      "state": "failed",
      "success": False,
      "reason": "initial SCC silence verification failed",
      "owner": owner,
      "exclusiveCardProcess": True,
      "attempts": attempt_records,
    })
    return False

  if stability_observation_s <= 0.0:
    _write_state({
      "state": "verified",
      "success": True,
      "owner": owner,
      "exclusiveCardProcess": True,
      "exclusiveWaitMs": round(waited_s * 1000.0, 1),
      "stability": {"requiredSec": 0.0, "reassertions": 0, "source0Frames": 0},
      "attempts": attempt_records,
    })
    return True

  started = time.monotonic()
  last_reasserted = started
  deadline = started + max(stability_timeout_s, stability_observation_s + stability_quiet_s)
  reassertions = 0
  source0_frames = 0
  windows: list[dict[str, object]] = []

  while True:
    now = time.monotonic()
    required_until = max(started + stability_observation_s,
                         last_reasserted + stability_quiet_s if reassertions else started)
    if now >= required_until:
      success = source0_frames >= min_source0_frames
      _write_state({
        "state": "verified" if success else "failed",
        "success": success,
        "reason": "" if success else "insufficient physical source0 traffic during stability verification",
        "owner": owner,
        "exclusiveCardProcess": True,
        "exclusiveWaitMs": round(waited_s * 1000.0, 1),
        "stability": {
          "requiredSec": stability_observation_s,
          "quietAfterReassertSec": stability_quiet_s,
          "elapsedSec": round(now - started, 3),
          "reassertions": reassertions,
          "source0Frames": source0_frames,
          "windows": windows,
        },
        "attempts": attempt_records,
      })
      trace(f"STEP 3 stability complete success={success} elapsed_s={now - started:.2f} reassertions={reassertions}")
      return success

    if now >= deadline:
      _write_state({
        "state": "failed",
        "success": False,
        "reason": "SCC silence stability timeout",
        "owner": owner,
        "stability": {"reassertions": reassertions, "source0Frames": source0_frames, "windows": windows},
        "attempts": attempt_records,
      })
      return False

    window_s = min(stability_sample_s, max(0.01, required_until - now), max(0.01, deadline - now))
    try:
      observation = _observe_source0_scc(can_recv, window_s, keepalive=keepalive)
    except Exception as error:
      _write_state({
        "state": "failed",
        "success": False,
        "reason": f"stability observation failed: {type(error).__name__}: {error}",
        "owner": owner,
        "attempts": attempt_records,
      })
      return False

    source0_frames += int(observation["source0_frames"])
    windows.append({"elapsedSec": round(time.monotonic() - started, 3), **observation})
    if int(observation["source0_scc_total"]) == 0:
      continue

    reassertions += 1
    trace(
      f"STEP 3 stability relapse={reassertions}/{stability_reassertions} "
      f"source0_scc={observation['source0_scc_total']} counts={observation['source0_scc_counts']}"
    )
    if reassertions > stability_reassertions or not suppress(f"stability-{reassertions}"):
      _write_state({
        "state": "failed",
        "success": False,
        "reason": "physical source0 SCC returned and could not be stabilized",
        "owner": owner,
        "stability": {"reassertions": reassertions, "source0Frames": source0_frames, "windows": windows},
        "attempts": attempt_records,
      })
      return False
    last_reasserted = time.monotonic()
