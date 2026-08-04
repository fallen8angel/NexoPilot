import time

from opendbc.car.carlog import carlog
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\x01\x42"
RADAR_TRACK_CONFIG = b"\x00\x00\x00\x01\x00\x01"
RADAR_QUERY_TIMEOUT = 0.1
RADAR_QUERY_TOTAL_TIMEOUT = 0.35
NEXO_LONG_INIT_LOG = "/data/nexo_long_init.log"


def _trace_radar_uds(message: str) -> None:
  try:
    with open(NEXO_LONG_INIT_LOG, "a", encoding="utf-8") as trace:
      trace.write(f"{time.monotonic():.3f} {message}\n")
  except OSError:
    pass


def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):
  started = time.monotonic()
  _trace_radar_uds(
    f"UDS TX ecu=0x{RADAR_ADDR:X} bus={bus} request={request.hex(' ')} expected={response.hex(' ')} "
    f"timeout_ms={timeout * 1000:.0f}"
  )
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  try:
    result = query.get_data(timeout, total_timeout=max(timeout * 3, RADAR_QUERY_TOTAL_TIMEOUT))
    rendered = ", ".join(f"0x{address:X}:{bytes(payload).hex(' ')}" for address, payload in result.items())
    _trace_radar_uds(
      f"UDS RX ecu=0x{RADAR_ADDR:X} bus={bus} elapsed_ms={(time.monotonic() - started) * 1000:.1f} "
      f"payloads={rendered or 'none'}"
    )
    return result
  except Exception as error:
    _trace_radar_uds(
      f"UDS ERROR ecu=0x{RADAR_ADDR:X} bus={bus} request={request.hex(' ')} "
      f"elapsed_ms={(time.monotonic() - started) * 1000:.1f} detail={error}"
    )
    raise


def enable_radar_tracks(can_recv, can_send, bus, retries=40) -> bool:
  """Enable NEXO MANDO radar tracks using the proven NEXOdriveAI order.

  disable_ecu() has already entered extended diagnostics (0x10 03) and sent
  communication control (0x28 83 01). Match NEXOdriveAI by entering the radar
  configuration session (0x10 07) and writing DID 0x0142 immediately after it.
  Do not issue a read-back or a second communication-control request here since
  either can change the session state on older NEXO radar firmware.
  """
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
