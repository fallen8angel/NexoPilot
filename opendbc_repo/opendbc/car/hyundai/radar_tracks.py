import time

from opendbc.car.carlog import carlog
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\x01\x42"
RADAR_TRACK_CONFIG = b"\x00\x00\x00\x01\x00\x01"
RADAR_QUERY_TIMEOUT = 0.1
RADAR_QUERY_TOTAL_TIMEOUT = 0.35
NEXO_DISABLE_NORMAL_COMMUNICATION = b"\x28\x83\x01"
NEXO_STOCK_SCC_ADDRS = frozenset((0x389, 0x420, 0x421, 0x50A))
NEXO_RADAR_TRACK_ADDRS = frozenset(range(0x500, 0x520))
NEXO_POST_TRACK_VERIFY_TIMEOUT = 1.5
NEXO_STOCK_SCC_MIN_FRAMES = 2
NEXO_RADAR_TRACK_MIN_IDS = 4


def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  return query.get_data(timeout, total_timeout=max(timeout * 3, RADAR_QUERY_TOTAL_TIMEOUT))


def _iter_can_messages(can_data):
  for batch in can_data or ():
    try:
      yield from batch
    except TypeError:
      continue


def _verify_post_track_state(can_recv, scc_bus: int,
                             timeout: float = NEXO_POST_TRACK_VERIFY_TIMEOUT) -> tuple[bool, dict[int, int], set[int]]:
  """Confirm stock SCC stays silent while radar tracks remain available.

  Radar track programming changes the diagnostic session on some NEXO radar
  firmware. That can undo the earlier communication-control request. Drain any
  queued packets first, then observe fresh traffic long enough to catch SCC11,
  SCC12, SCC13, or SCC14 returning. Track frames can appear on a different
  physical CAN bus, so only their addresses are used for the availability check.
  """
  try:
    can_recv(wait_for_one=False)
  except Exception:
    pass

  deadline = time.monotonic() + timeout
  scc_counts: dict[int, int] = {}
  radar_ids: set[int] = set()

  while time.monotonic() < deadline:
    try:
      can_data = can_recv(wait_for_one=False)
    except Exception as error:
      carlog.warning(f"NEXO post-track CAN verification read failed: {error}")
      can_data = ()

    for msg in _iter_can_messages(can_data):
      if msg.src == scc_bus and msg.address in NEXO_STOCK_SCC_ADDRS:
        scc_counts[msg.address] = scc_counts.get(msg.address, 0) + 1
      if msg.src < 128 and msg.address in NEXO_RADAR_TRACK_ADDRS:
        radar_ids.add(msg.address)

    stock_scc_active = any(count >= NEXO_STOCK_SCC_MIN_FRAMES for count in scc_counts.values())
    if stock_scc_active:
      return False, scc_counts, radar_ids

    time.sleep(0.01)

  tracks_available = len(radar_ids) >= NEXO_RADAR_TRACK_MIN_IDS
  return tracks_available, scc_counts, radar_ids


def enable_radar_tracks(can_recv, can_send, bus, retries=40) -> bool:
  """Enable NEXO MANDO radar tracks and verify the final ECU state.

  The radar is first programmed using the proven NEXOdriveAI sequence. Because
  entering the track-programming diagnostic session can wake stock SCC traffic
  again, communication suppression is requested once more afterwards. The
  function only succeeds when stock SCC stays silent and fresh radar-track
  frames remain visible. Otherwise the caller restores stock cruise and falls
  back instead of leaving the cluster in a faulted mixed-control state.
  """
  for attempt in range(1, retries + 1):
    try:
      session = _query(can_recv, can_send, bus, b"\x10\x07", b"\x50\x07")
      if not session:
        raise RuntimeError("no diagnostic-session response")

      write = _query(can_recv, can_send, bus,
                     b"\x2e" + RADAR_TRACK_CONFIG_DID + RADAR_TRACK_CONFIG,
                     b"\x6e" + RADAR_TRACK_CONFIG_DID)
      if not write:
        raise RuntimeError("no write-data response")

      try:
        read = _query(can_recv, can_send, bus,
                      b"\x22" + RADAR_TRACK_CONFIG_DID,
                      b"\x62" + RADAR_TRACK_CONFIG_DID)
        if read:
          payload = next(iter(read.values()))
          if RADAR_TRACK_CONFIG not in payload:
            raise RuntimeError(f"unexpected radar configuration: {payload.hex()}")
      except Exception as read_error:
        # Older NEXO radar firmware acknowledges the write but may not support
        # ReadDataByIdentifier for this DID.
        carlog.warning(f"NEXO radar track read-back unavailable: {read_error}")

      # Track programming enters a new diagnostic session and can cancel the
      # communication-control state established before this function. Re-apply
      # it now, then verify both sides of the contract: SCC silent, tracks alive.
      disabled_after_tracks = disable_ecu(
        can_recv, can_send, bus=bus, addr=RADAR_ADDR,
        com_cont_req=NEXO_DISABLE_NORMAL_COMMUNICATION,
      )
      if not disabled_after_tracks:
        carlog.error("NEXO post-track SCC suppression was not acknowledged")
        return False

      verified, scc_counts, radar_ids = _verify_post_track_state(can_recv, bus)
      if not verified:
        if any(count >= NEXO_STOCK_SCC_MIN_FRAMES for count in scc_counts.values()):
          counts = ", ".join(f"0x{addr:X}={count}" for addr, count in sorted(scc_counts.items()))
          carlog.error(f"NEXO stock SCC returned after radar programming: {counts}")
        else:
          carlog.error(f"NEXO radar tracks disappeared after final SCC suppression: ids={len(radar_ids)}")
        return False

      carlog.info(
        f"NEXO radar tracks enabled and stock SCC verified silent on bus {bus}, "
        f"attempt {attempt}, track_ids={len(radar_ids)}"
      )
      return True
    except Exception as error:
      carlog.warning(f"NEXO radar track activation attempt {attempt}/{retries} failed on bus {bus}: {error}")
      time.sleep(0.05)

  carlog.error(f"NEXO radar tracks could not be enabled on bus {bus}")
  return False
