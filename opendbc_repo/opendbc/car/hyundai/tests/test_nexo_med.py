from dataclasses import dataclass
import math
from types import SimpleNamespace
import unittest

from opendbc.car.hyundai.nexo_med import NexoMedStateManager


class ButtonType:
  unknown = 0
  accelCruise = 1
  decelCruise = 2
  cancel = 3
  mainCruise = 4


BUTTONS = {1: ButtonType.accelCruise, 2: ButtonType.decelCruise, 4: ButtonType.cancel}


@dataclass
class ButtonEvent:
  pressed: bool
  type: int


def create_button_events(current, previous, buttons):
  events = []
  if current == previous:
    return events
  if previous:
    events.append(ButtonEvent(False, buttons.get(previous, ButtonType.unknown)))
  if current:
    events.append(ButtonEvent(True, buttons.get(current, ButtonType.unknown)))
  return events


def car_state(speed_kph=50.0):
  return SimpleNamespace(
    vEgoCluster=speed_kph / 3.6,
    vEgo=speed_kph / 3.6,
    brakePressed=False,
    cruiseState=SimpleNamespace(available=False, enabled=False, standstill=False,
                                speed=0.0, speedCluster=0.0),
  )


def manager():
  return NexoMedStateManager(ButtonType, BUTTONS, create_button_events, 1.0 / 3.6, 1.609344)


def update(mgr, state, main=0, button=0, driving=True, reverse=False, events=None):
  return mgr.update(state, main, button, True, events or [], driving, reverse)


def enter_med(mgr, state, driving=True):
  for _ in range(mgr.MAIN_RELEASE_ARM_FRAMES):
    update(mgr, state, driving=driving)
  update(mgr, state, main=1, driving=driving,
         events=[ButtonEvent(True, ButtonType.mainCruise)])
  update(mgr, state, main=0, driving=driving,
         events=[ButtonEvent(False, ButtonType.mainCruise)])
  mgr.apply_to_car_state(state)


def tap(mgr, state, button, driving=True):
  press_events = create_button_events(button, 0, BUTTONS)
  release_events = create_button_events(0, button, BUTTONS)
  press = update(mgr, state, button=button, driving=driving, events=press_events)
  release = update(mgr, state, button=0, driving=driving, events=release_events)
  mgr.apply_to_car_state(state)
  return press, release


def test_mode_enters_med_and_emits_one_enable_pulse():
  mgr = manager()
  state = car_state()

  enter_med(mgr, state)

  assert state.cruiseState.available
  assert not state.cruiseState.enabled
  assert mgr.consume_enable_pulse()
  assert not mgr.consume_enable_pulse()


def test_set_starts_speed_control_and_repeated_buttons_change_target():
  mgr = manager()
  state = car_state(50.0)
  enter_med(mgr, state)

  tap(mgr, state, 2)
  assert state.cruiseState.enabled
  assert mgr.speed_kph == 50.0

  tap(mgr, state, 1)
  tap(mgr, state, 1)
  tap(mgr, state, 2)
  assert mgr.speed_kph == 51.0
  assert math.isclose(state.cruiseState.speed, 51.0 / 3.6)


def test_brake_returns_to_med_and_res_uses_retained_speed():
  mgr = manager()
  state = car_state(50.0)
  enter_med(mgr, state)
  tap(mgr, state, 2)
  tap(mgr, state, 1)
  retained_speed = mgr.speed_kph

  state.brakePressed = True
  update(mgr, state)
  mgr.apply_to_car_state(state)
  assert state.cruiseState.available
  assert not state.cruiseState.enabled

  state.brakePressed = False
  update(mgr, state)
  tap(mgr, state, 1)
  assert state.cruiseState.enabled
  assert mgr.speed_kph == retained_speed


def test_first_cancel_is_hidden_and_second_cancel_turns_med_off():
  mgr = manager()
  state = car_state()
  enter_med(mgr, state)
  tap(mgr, state, 2)

  first_press, first_release = tap(mgr, state, 4)
  assert state.cruiseState.available
  assert not state.cruiseState.enabled
  assert all(event.type != ButtonType.cancel for event in first_press + first_release)

  second_press, second_release = tap(mgr, state, 4)
  assert not state.cruiseState.available
  assert not state.cruiseState.enabled
  assert any(event.type == ButtonType.cancel for event in second_press + second_release)


def test_non_driving_gear_blocks_speed_and_reentering_drive_requests_enable():
  mgr = manager()
  state = car_state()
  enter_med(mgr, state, driving=False)
  assert state.cruiseState.available
  assert not mgr.consume_enable_pulse()

  tap(mgr, state, 2, driving=False)
  assert not state.cruiseState.enabled

  update(mgr, state, driving=True)
  assert mgr.consume_enable_pulse()
  tap(mgr, state, 2, driving=True)
  assert state.cruiseState.enabled


def test_reverse_requires_fresh_set_or_resume_before_med_reengages():
  mgr = manager()
  state = car_state()
  enter_med(mgr, state)
  assert mgr.consume_enable_pulse()
  tap(mgr, state, 2)
  assert state.cruiseState.enabled

  update(mgr, state, driving=False, reverse=True)
  mgr.apply_to_car_state(state)
  assert mgr.reverse_reengage_required
  assert state.cruiseState.available
  assert not state.cruiseState.enabled
  assert not mgr.consume_enable_pulse()

  # Returning to Drive and even cycling MODE must not auto-resume MED.
  update(mgr, state, driving=True)
  assert not mgr.consume_enable_pulse()
  update(mgr, state, main=1, driving=True,
         events=[ButtonEvent(True, ButtonType.mainCruise)])
  update(mgr, state, main=0, driving=True,
         events=[ButtonEvent(False, ButtonType.mainCruise)])
  update(mgr, state, main=1, driving=True,
         events=[ButtonEvent(True, ButtonType.mainCruise)])
  update(mgr, state, main=0, driving=True,
         events=[ButtonEvent(False, ButtonType.mainCruise)])
  assert state.cruiseState.available
  assert mgr.reverse_reengage_required
  assert not mgr.consume_enable_pulse()

  tap(mgr, state, 1, driving=True)
  assert not mgr.reverse_reengage_required
  assert state.cruiseState.enabled
  assert mgr.consume_enable_pulse()


def test_boot_high_mode_value_does_not_arm_med():
  mgr = manager()
  state = car_state()

  for _ in range(10):
    update(mgr, state, main=1)
  mgr.apply_to_car_state(state)
  assert not state.cruiseState.available
  assert not mgr.consume_enable_pulse()


class TestNexoMedStateManager(unittest.TestCase):
  def test_mode_enters_med(self):
    test_mode_enters_med_and_emits_one_enable_pulse()

  def test_set_and_speed_adjust(self):
    test_set_starts_speed_control_and_repeated_buttons_change_target()

  def test_brake_and_resume(self):
    test_brake_returns_to_med_and_res_uses_retained_speed()

  def test_two_stage_cancel(self):
    test_first_cancel_is_hidden_and_second_cancel_turns_med_off()

  def test_gear_gate(self):
    test_non_driving_gear_blocks_speed_and_reentering_drive_requests_enable()

  def test_startup_main_guard(self):
    test_boot_high_mode_value_does_not_arm_med()

  def test_reverse_reengage_guard(self):
    test_reverse_requires_fresh_set_or_resume_before_med_reengages()


if __name__ == "__main__":
  unittest.main()
