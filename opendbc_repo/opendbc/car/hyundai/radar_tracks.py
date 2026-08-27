import time

from opendbc.car.carlog import carlog
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\x01\x42"
RADAR_TRACK_CONFIG = b"\x00\x00\x00\x01\x00\x01"
RADAR_QUERY_TIMEOUT = 0.1
RADAR_QUERY_TOTAL_TIMEOUT = 0.35
NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"

# NEXO radar tracks are parsed on physical bus 1. A hot card restart can happen
# while the radar is still in the already-programmed track mode from the same
# ignition cycle. In that state the ECU may stop answering a repeated 0x10 07
# request even though all 32 track messages are still streaming normally.
# Require broad, live coverage of the track address range before treating the
# existing session as usable; one colliding CAN address is never sufficient.
RADAR_TRACK_START_ADDR = 0x500
RADAR_TRACK_MSG_COUNT = 32
RADAR_TRACK_RX_BUS = 1
RADAR_TRACK_PRECHECK_DURATION_S = 0.30
RADAR_TRACK_PRECHECK_MIN_UNIQUE = 24
RADAR_TRACK_PRECHECK_MIN_FRAMES = 48


def _trace_radar_uds(message: str) -> None:
  try:
    with open(NEXO_LONG_INIT_LOG, "a", encoding="utf-8") as trace:
      trace.write(f"{time.monotonic():.3f} {message}\n")
  except OSError:
    pass


def _format_isotp_address(address) -> str:
  """Render AddrType values without allowing diagnostics to affect UDS control flow."""
  try:
    if isinstance(address, tuple):
      tx_addr, sub_addr = address
      rendered = f"0x{int(tx_addr):X}"
      return rendered if sub_addr is None else f"{rendered}:sub=0x{int(sub_addr):X}"
    return f"0x{int(address):X}"
  except Exception:
    return repr(address)


def _render_isotp_result(result) -> str:
  try:
    items = []
    for address, payload in result.items():
      try:
        payload_text = bytes(payload).hex(" ")
      except Exception:
        payload_text = repr(payload)
      items.append(f"{_format_isotp_address(address)}:{payload_text}")
    return ", ".join(items) or "none"
  except Exception as error:
    return f"unavailable({type(error).__name__}: {error})"


def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):
  started = time.monotonic()
  _trace_radar_uds(
    f"UDS TX ecu=0x{RADAR_ADDR:X} bus={bus} request={request.hex(' ')} expected={response.hex(' ')} "
    f"timeout_ms={timeout * 1000:.0f}"
  )
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  try:
    result = query.get_data(timeout, total_timeout=max(timeout * 3, RADAR_QUERY_TOTAL_TIMEOUT))
    rendered = _render_isotp_result(result)
    _trace_radar_uds(
      f"UDS RX ecu=0x{RADAR_ADDR:X} bus={bus} elapsed_ms={(time.monotonic() - started) * 1000:.1f} "
      f"payloads={rendered}"
    )
    return result
  except Exception as error:
    _trace_radar_uds(
      f"UDS ERROR ecu=0x{RADAR_ADDR:X} bus={bus} request={request.hex(' ')} "
      f"elapsed_ms={(time.monotonic() - started) * 1000:.1f} detail={error}"
    )
    raise


def _observe_radar_track_stream(can_recv, *, duration_s: float = RADAR_TRACK_PRECHECK_DURATION_S,
                                source: int = RADAR_TRACK_RX_BUS,
                                min_unique: int = RADAR_TRACK_PRECHECK_MIN_UNIQUE,
                                min_frames: int = RADAR_TRACK_PRECHECK_MIN_FRAMES) -> tuple[bool, int, int]:
  """Verify that a broad set of NEXO radar-track frames is live right now.

  This is intentionally stricter than seeing a single 0x500-range address since
  SCC13 and other traffic can overlap the numeric range on other buses. Only the
  dedicated radar receive bus is counted, and a majority of the 32 track IDs
  must be observed repeatedly before a hot restart may skip UDS programming.
  """
  try:
    # Discard stale queued data so the decision is based on current traffic.
    can_recv(wait_for_one=False)
  except Exception:
    pass

  deadline = time.monotonic() + max(0.01, duration_s)
  frames = 0
  addresses: set[int] = set()

  try:
    while time.monotonic() < deadline:
      for batch in can_recv(wait_for_one=True):
        for message in batch:
          if int(getattr(message, "src", -1)) != source:
            continue
          address = int(getattr(message, "address", -1))
          if RADAR_TRACK_START_ADDR <= address < RADAR_TRACK_START_ADDR + RADAR_TRACK_MSG_COUNT:
            frames += 1
            addresses.add(address)

      if frames >= min_frames and len(addresses) >= min_unique:
        return True, frames, len(addresses)
  except Exception as error:
    _trace_radar_uds(
      f"RADAR PRECHECK observation failed source={source} frames={frames} unique={len(addresses)} "
      f"detail={type(error).__name__}: {error}"
    )
    return False, frames, len(addresses)

  return False, frames, len(addresses)


def enable_radar_tracks(can_recv, can_send, bus, retries=40) -> bool:
  """Enable NEXO MANDO radar tracks without breaking a healthy hot-restart session.

  A fresh ignition normally needs the proven NEXOdriveAI sequence: enter radar
  configuration session (0x10 07) and write DID 0x0142. During a card-process
  restart in the same ignition cycle, however, the radar can keep streaming all
  32 configured tracks while no longer acknowledging another 0x10 07 request.
  Prove the live track stream first and skip redundant UDS programming only in
  that already-active case. Otherwise run the normal bounded UDS sequence.
  """
  already_active, precheck_frames, precheck_unique = _observe_radar_track_stream(can_recv)
  _trace_radar_uds(
    f"RADAR PRECHECK active={already_active} source={RADAR_TRACK_RX_BUS} "
    f"frames={precheck_frames} unique={precheck_unique}/{RADAR_TRACK_MSG_COUNT}"
  )
  if already_active:
    carlog.info(
      f"NEXO radar tracks already streaming on bus {RADAR_TRACK_RX_BUS}; "
      "skipping redundant radar UDS programming"
    )
    return True

  for attempt in range(1, retries + 1):
    try:
      session = _query(can_recv, can_send, bus, b"\x10\x07", b"\x50\x07")
      if not session:
        raise RuntimeError("no diagnostic-session response")

      write_result = _query(
        can_recv, can_send, bus,
        b"\x2e" + RADAR_TRACK_CONFIG_DID + RADAR_TRACK_CONFIG,
        b"\x6e" + RADAR_TRACK_CONFIG_DID,
      )
      if not write_result:
        raise RuntimeError("no write-data response")

      _trace_radar_uds(f"RADAR ATTEMPT {attempt}/{retries} completed")
      carlog.info(f"NEXOdriveAI radar-track sequence completed on bus {bus}, attempt {attempt}")
      return True
    except Exception as error:
      _trace_radar_uds(f"RADAR ATTEMPT {attempt}/{retries} failed detail={error}")
      carlog.warning(f"NEXO radar track activation attempt {attempt}/{retries} failed on bus {bus}: {error}")
      time.sleep(0.05)

  _trace_radar_uds(f"RADAR FAILED after {retries} attempts on bus {bus}")
  carlog.error(f"NEXO radar tracks could not be enabled on bus {bus}")
  return False
