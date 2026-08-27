import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car.hyundai import radar_tracks


class TestNexoRadarTrackHotRestart(unittest.TestCase):
  @staticmethod
  def _radar_batch(source=radar_tracks.RADAR_TRACK_RX_BUS):
    return [[
      SimpleNamespace(src=source, address=address)
      for address in range(
        radar_tracks.RADAR_TRACK_START_ADDR,
        radar_tracks.RADAR_TRACK_START_ADDR + radar_tracks.RADAR_TRACK_MSG_COUNT,
      )
    ]]

  def test_live_full_track_stream_is_verified(self):
    batch = self._radar_batch()

    def can_recv(wait_for_one=False):
      return batch

    active, frames, unique = radar_tracks._observe_radar_track_stream(
      can_recv, duration_s=0.05, min_unique=24, min_frames=48,
    )

    self.assertTrue(active)
    self.assertGreaterEqual(frames, 48)
    self.assertEqual(radar_tracks.RADAR_TRACK_MSG_COUNT, unique)

  def test_wrong_bus_track_range_does_not_pass_precheck(self):
    batch = self._radar_batch(source=0)

    def can_recv(wait_for_one=False):
      return batch

    active, _, unique = radar_tracks._observe_radar_track_stream(
      can_recv, duration_s=0.01, min_unique=24, min_frames=48,
    )

    self.assertFalse(active)
    self.assertEqual(0, unique)

  def test_hot_restart_skips_redundant_uds_when_tracks_are_live(self):
    with patch.object(radar_tracks, "_observe_radar_track_stream", return_value=(True, 64, 32)), \
         patch.object(radar_tracks, "_query") as query:
      enabled = radar_tracks.enable_radar_tracks(object(), object(), bus=0, retries=1)

    self.assertTrue(enabled)
    query.assert_not_called()

  def test_fresh_start_still_runs_configuration_uds(self):
    session_result = {radar_tracks.RADAR_ADDR: b""}
    write_result = {radar_tracks.RADAR_ADDR: b""}

    with patch.object(radar_tracks, "_observe_radar_track_stream", return_value=(False, 0, 0)), \
         patch.object(radar_tracks, "_query", side_effect=[session_result, write_result]) as query:
      enabled = radar_tracks.enable_radar_tracks(object(), object(), bus=0, retries=1)

    self.assertTrue(enabled)
    self.assertEqual(2, query.call_count)
    self.assertEqual(b"\x10\x07", query.call_args_list[0].args[3])
    self.assertTrue(query.call_args_list[1].args[3].startswith(b"\x2e\x01\x42"))


if __name__ == "__main__":
  unittest.main()
