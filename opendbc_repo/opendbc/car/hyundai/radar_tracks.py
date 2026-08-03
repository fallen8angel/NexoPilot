import time

from opendbc.car.carlog import carlog
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


RADAR_ADDR = 0x7D0
RADAR_TRACK_CONFIG_DID = b"\x01\x42"
RADAR_TRACK_CONFIG = b"\x00\x00\x00\x01\x00\x01"
RADAR_QUERY_TIMEOUT = 0.1
RADAR_QUERY_TOTAL_TIMEOUT = 0.35


def _query(can_recv, can_send, bus, request, response, timeout=RADAR_QUERY_TIMEOUT):
  query = IsoTpParallelQuery(can_send, can_recv, bus, [RADAR_ADDR], [request], [response])
  return query.get_data(timeout, total_timeout=max(timeout * 3, RADAR_QUERY_TOTAL_TIMEOUT))


def enable_radar_tracks(can_recv, can_send, bus, retries=40) -> bool:
  """Enable NEXO MANDO radar multi-track output using the proven AI sequence.

  The NEXOdriveAI implementation retried the short diagnostic sequence many
  times because the radar may answer late during startup. Keep that behavior,
  while validating the correct UDS positive responses and retaining optional
  DID read-back when the radar firmware supports it.
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
        # ReadDataByIdentifier for this DID. Preserve the proven AI behavior.
        carlog.warning(f"NEXO radar track read-back unavailable: {read_error}")

      carlog.info(f"NEXO radar tracks enabled on bus {bus}, attempt {attempt}")
      return True
    except Exception as error:
      carlog.warning(f"NEXO radar track activation attempt {attempt}/{retries} failed on bus {bus}: {error}")
      time.sleep(0.05)

  carlog.error(f"NEXO radar tracks could not be enabled on bus {bus}")
  return False
