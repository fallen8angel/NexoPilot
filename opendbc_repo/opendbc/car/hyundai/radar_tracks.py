from opendbc.car.carlog import carlog
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\x01\x42"
RADAR_TRACK_CONFIG = b"\x00\x00\x00\x01\x00\x01"


def _query(can_recv, can_send, bus, request, response, timeout=0.5):
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  return query.get_data(timeout, total_timeout=max(timeout * 3, 1.0))


def enable_radar_tracks(can_recv, can_send, bus, retries=5) -> bool:
  """Enable and verify MANDO radar track output.

  The routine is intentionally fail-closed: callers should not start NEXO
  longitudinal control unless this returns True.
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

      # Confirm the value when the radar supports ReadDataByIdentifier.
      # Some firmware only acknowledges the write, so a valid write response
      # remains sufficient when read-back is unsupported.
      try:
        read = _query(can_recv, can_send, bus,
                      b"\x22" + RADAR_TRACK_CONFIG_DID,
                      b"\x62" + RADAR_TRACK_CONFIG_DID)
        if read:
          payload = next(iter(read.values()))
          if RADAR_TRACK_CONFIG not in payload:
            raise RuntimeError(f"unexpected radar configuration: {payload.hex()}")
      except Exception as read_error:
        carlog.warning(f"NEXO radar track read-back unavailable: {read_error}")

      carlog.info(f"NEXO radar tracks enabled on attempt {attempt}")
      return True
    except Exception as error:
      carlog.warning(f"NEXO radar track activation attempt {attempt}/{retries} failed: {error}")

  carlog.error("NEXO radar tracks could not be enabled; longitudinal control will not start")
  return False
