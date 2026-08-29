"""First-generation NEXO MODE/MED cruise state management.

The stock SCC ECU is silent while openpilot longitudinal control is active, so
TCS13.ACC_REQ cannot be used as the NEXO cruise state. The physical CLU11
buttons own this state instead:

  OFF --MODE--> MED_WAIT --SET/RES--> SPEED_CONTROL
   ^                 <--brake/CANCEL--       |
   +--------------------CANCEL---------------+

The first CANCEL returns to steering-only MED_WAIT. A second CANCEL turns the
MED selection off. Longitudinal activation is accepted only in D/L.
"""
from __future__ import annotations


class NexoMedStateManager:
  MIN_SPEED_KPH = 10.0
  DEFAULT_SPEED_KPH = 30.0
  MAX_SPEED_KPH = 145.0
  LONG_PRESS_FRAMES = 70
  MAIN_RELEASE_ARM_FRAMES = 3

  def __init__(self, button_type, buttons_dict, create_button_events,
               kph_to_ms: float, mph_to_kph: float):
    self.ButtonType = button_type
    self.buttons_dict = buttons_dict
    self.create_button_events = create_button_events
    self.KPH_TO_MS = float(kph_to_ms)
    self.MPH_TO_KPH = float(mph_to_kph)

    # Always boot fully OFF. A real MODE press is required before MED can own
    # either steering or speed control.
    self.available = False
    self.enabled = False
    self.speed_kph = self.DEFAULT_SPEED_KPH

    self.main_armed = False
    self.main_release_frames = 0
    self.prev_raw_main = 0
    self.enable_pulse = False
    self.prev_driving_gear = False

    self.prev_raw_button = 0
    self.held_button = 0
    self.held_frames = 0
    self.long_press_fired = False
    self.suppress_cancel_until_release = False
    self.prev_brake_pressed = False

  @staticmethod
  def _clip(value, lo, hi):
    return max(lo, min(hi, float(value)))

  def _current_speed_kph(self, car_state) -> float:
    try:
      return max(0.0, float(car_state.vEgoCluster) / self.KPH_TO_MS)
    except Exception:
      try:
        return max(0.0, float(car_state.vEgo) / self.KPH_TO_MS)
      except Exception:
        return 0.0

  def _step_kph(self, is_metric: bool, long_press: bool) -> float:
    if long_press:
      return 10.0 if is_metric else 5.0 * self.MPH_TO_KPH
    return 1.0 if is_metric else self.MPH_TO_KPH

  def _apply_speed_button(self, car_state, button_type, is_metric: bool,
                          driving_gear: bool, long_press: bool = False) -> None:
    if not self.available or not driving_gear:
      return

    current_kph = self._current_speed_kph(car_state)
    step = self._step_kph(is_metric, long_press)

    if not self.enabled:
      # SET captures the current speed. RES returns to the retained target, but
      # never requests less than the current speed.
      if button_type == self.ButtonType.decelCruise:
        self.speed_kph = self._clip(max(current_kph, self.MIN_SPEED_KPH),
                                    self.MIN_SPEED_KPH, self.MAX_SPEED_KPH)
        self.enabled = True
      elif button_type == self.ButtonType.accelCruise:
        self.speed_kph = self._clip(max(self.speed_kph, current_kph, self.MIN_SPEED_KPH),
                                    self.MIN_SPEED_KPH, self.MAX_SPEED_KPH)
        self.enabled = True
      return

    if button_type == self.ButtonType.accelCruise:
      if long_press:
        self.speed_kph += step - (self.speed_kph % step)
      else:
        self.speed_kph += step
    elif button_type == self.ButtonType.decelCruise:
      if long_press:
        remainder = self.speed_kph % step
        self.speed_kph -= remainder if remainder > 0.01 else step
      else:
        self.speed_kph -= step

    self.speed_kph = self._clip(self.speed_kph, self.MIN_SPEED_KPH, self.MAX_SPEED_KPH)

  def _handle_speed_release(self, car_state, raw_button: int, is_metric: bool,
                            driving_gear: bool) -> None:
    try:
      release_events = list(self.create_button_events(0, raw_button, self.buttons_dict))
    except Exception:
      release_events = []

    for event in release_events:
      if not bool(event.pressed) and event.type in (self.ButtonType.accelCruise, self.ButtonType.decelCruise):
        if not self.long_press_fired:
          self._apply_speed_button(car_state, event.type, is_metric, driving_gear)

  def _handle_cancel_press(self) -> None:
    if self.enabled:
      # Drop the longitudinal request on the press edge, in the same cycle that
      # Panda enters MED_WAIT. Suppress this first CANCEL from selfdrived so the
      # lateral MED session stays enabled through the release edge.
      self.enabled = False
      self.suppress_cancel_until_release = True
    elif self.available:
      self.available = False
      self.enabled = False
      self.enable_pulse = False

  @staticmethod
  def _event_key(event):
    event_type = getattr(event.type, "raw", event.type)
    try:
      event_type = int(event_type)
    except Exception:
      event_type = str(event_type)
    return event_type, bool(event.pressed)

  def update(self, car_state, raw_main: int, raw_button: int, is_metric: bool,
             decoded_events, driving_gear: bool) -> list:
    """Update MED state and return de-duplicated physical button events."""
    raw_main = int(raw_main)
    raw_button = int(raw_button)
    driving_gear = bool(driving_gear)

    # Ignore a boot-time high/stuck MODE value until the physical line has been
    # observed released for several frames.
    if raw_main == 0:
      self.main_release_frames += 1
      if self.main_release_frames >= self.MAIN_RELEASE_ARM_FRAMES:
        self.main_armed = True
    else:
      self.main_release_frames = 0

    main_pressed = self.main_armed and raw_main != 0 and self.prev_raw_main == 0
    if main_pressed:
      if self.available:
        self.available = False
        self.enabled = False
        self.enable_pulse = False
      else:
        self.available = True
        self.enabled = False
        self.enable_pulse = driving_gear
    self.prev_raw_main = raw_main

    # If MODE was deliberately selected outside D/L, retain that selection and
    # create one enable request when the driver later returns to a forward gear.
    if self.available and driving_gear and not self.prev_driving_gear:
      self.enable_pulse = True

    try:
      raw_events = list(self.create_button_events(raw_button, self.prev_raw_button, self.buttons_dict))
    except Exception:
      raw_events = []

    old_button = self.prev_raw_button
    old_type = self.buttons_dict.get(old_button)
    new_type = self.buttons_dict.get(raw_button)
    cancel_cycle_ended = old_type == self.ButtonType.cancel and raw_button != old_button

    if raw_button != old_button:
      if old_button != 0:
        self._handle_speed_release(car_state, old_button, bool(is_metric), driving_gear)

      if raw_button == 0:
        self.held_button = 0
        self.held_frames = 0
        self.long_press_fired = False
      else:
        self.held_button = raw_button
        self.held_frames = 1
        self.long_press_fired = False
        if new_type == self.ButtonType.cancel:
          self._handle_cancel_press()

    elif raw_button != 0:
      self.held_frames += 1
      if self.held_frames > self.LONG_PRESS_FRAMES and self.held_frames % self.LONG_PRESS_FRAMES == 1:
        try:
          press_events = list(self.create_button_events(raw_button, 0, self.buttons_dict))
        except Exception:
          press_events = []
        for event in press_events:
          if event.pressed and event.type in (self.ButtonType.accelCruise, self.ButtonType.decelCruise):
            self._apply_speed_button(car_state, event.type, bool(is_metric), driving_gear, long_press=True)
            self.long_press_fired = True

    self.prev_raw_button = raw_button

    # D/L and brake are hard longitudinal gates. Both retain MODE and the last
    # requested speed so RES can restore it later.
    if not driving_gear:
      self.enabled = False

    brake_pressed = bool(getattr(car_state, "brakePressed", False))
    if brake_pressed and not self.prev_brake_pressed:
      self.enabled = False
    self.prev_brake_pressed = brake_pressed

    suppress_cancel = self.suppress_cancel_until_release
    merged = []
    seen = set()
    for event in list(decoded_events) + raw_events:
      if suppress_cancel and event.type == self.ButtonType.cancel:
        continue
      key = self._event_key(event)
      if key in seen:
        continue
      seen.add(key)
      merged.append(event)

    if cancel_cycle_ended:
      self.suppress_cancel_until_release = False
    self.prev_driving_gear = driving_gear
    return merged

  def consume_enable_pulse(self) -> bool:
    pulse = self.enable_pulse
    self.enable_pulse = False
    return pulse

  def apply_to_car_state(self, car_state) -> None:
    car_state.cruiseState.available = bool(self.available)
    car_state.cruiseState.enabled = bool(self.available and self.enabled)
    car_state.cruiseState.standstill = False
    speed_ms = self.speed_kph * self.KPH_TO_MS if self.available and self.enabled else 0.0
    car_state.cruiseState.speed = speed_ms
    car_state.cruiseState.speedCluster = speed_ms
