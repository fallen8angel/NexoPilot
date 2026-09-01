from openpilot.selfdrive.selfdrived.nexo_experimental_mode import (
  NexoExperimentalModeController,
  is_nexo_fingerprint,
  nexo_experimental_icon_visible,
  nexo_med_phase,
  normalize_speed_kph,
)


def test_speed_normalization_uses_five_kph_steps():
  assert normalize_speed_kph(8) == 10
  assert normalize_speed_kph(22) == 20
  assert normalize_speed_kph(23) == 25
  assert normalize_speed_kph(105) == 100


def test_disabled_follows_manual_experimental_mode():
  controller = NexoExperimentalModeController()
  assert not controller.update(False, False, False, 10.0, False, 30)
  assert controller.update(False, True, True, 80.0, True, 30)


def test_med_wait_and_configurable_speed_hysteresis():
  controller = NexoExperimentalModeController()

  # XPlus behavior: MED wait prepares Experimental Mode before SET/RES.
  assert controller.update(True, False, True, 0.0, False, 30)

  # First speed-control frame selects the mode from the configured threshold.
  assert controller.update(True, True, True, 25.0, False, 30)
  assert controller.update(True, True, True, 30.0, False, 30)

  # The +2/-2 km/h band prevents rapid mode flapping.
  assert not controller.update(True, True, True, 32.0, False, 30)
  assert not controller.update(True, True, True, 30.0, False, 30)
  assert controller.update(True, True, True, 28.0, False, 30)


def test_med_phase_and_mici_experimental_visibility():
  assert is_nexo_fingerprint("HYUNDAI_NEXO_1ST_GEN")
  assert not is_nexo_fingerprint("HYUNDAI_IONIQ_5")
  assert nexo_med_phase(True, False, False) == ""
  assert nexo_med_phase(True, True, False) == "MED"
  assert nexo_med_phase(True, True, True) == "SPEED"

  # MED wait can prepare the model, but the compact EXP icon means active
  # experimental speed control and therefore stays hidden until SET/RES.
  assert not nexo_experimental_icon_visible(True, False, True)
  assert nexo_experimental_icon_visible(True, True, True)
  assert nexo_experimental_icon_visible(False, False, True)
