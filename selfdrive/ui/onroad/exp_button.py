import time
import pyray as rl
from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget


class ExpButton(Widget):
  def __init__(self, button_size: int, icon_size: int):
    super().__init__()
    self._params = Params()
    self._experimental_mode: bool = False
    self._engageable: bool = False
    self._cruise_active: bool = False
    self._steering_angle_deg: float = 0.0

    # State hold mechanism
    self._hold_duration = 2.0  # seconds
    self._held_mode: bool | None = None
    self._hold_end_time: float | None = None

    self._white_color: rl.Color = rl.Color(255, 255, 255, 255)
    self._active_green: rl.Color = rl.Color(0, 255, 0, 255)
    self._black_bg: rl.Color = rl.Color(0, 0, 0, 166)
    self._txt_wheel: rl.Texture = gui_app.texture('icons/chffr_wheel.png', icon_size, icon_size)
    self._rect = rl.Rectangle(0, 0, button_size, button_size)

  def set_rect(self, rect: rl.Rectangle) -> None:
    self._rect.x, self._rect.y = rect.x, rect.y

  def _update_state(self) -> None:
    selfdrive_state = ui_state.sm["selfdriveState"]
    car_state = ui_state.sm["carState"]
    self._experimental_mode = selfdrive_state.experimentalMode
    self._engageable = selfdrive_state.engageable or selfdrive_state.enabled
    self._cruise_active = bool(
      selfdrive_state.enabled or selfdrive_state.active or car_state.cruiseState.enabled
    )
    self._steering_angle_deg = float(car_state.steeringAngleDeg)

  def _handle_mouse_release(self, _):
    super()._handle_mouse_release(_)
    if self._is_toggle_allowed():
      new_mode = not self._experimental_mode
      self._params.put_bool("ExperimentalMode", new_mode)

      # Hold new state temporarily
      self._held_mode = new_mode
      self._hold_end_time = time.monotonic() + self._hold_duration

  def _render(self, rect: rl.Rectangle) -> None:
    center_x = int(self._rect.x + self._rect.width // 2)
    center_y = int(self._rect.y + self._rect.height // 2)

    icon_color = self._active_green if self._cruise_active else self._white_color
    icon_color.a = 180 if self.is_pressed else 255

    # Keep the wheel centered while rotating with the physical steering angle.
    # EXP remains visible through HudRenderer's separate badge, so this status
    # icon can consistently represent steering and cruise state.
    texture = self._txt_wheel
    size = float(texture.width)
    source = rl.Rectangle(0.0, 0.0, float(texture.width), float(texture.height))
    destination = rl.Rectangle(float(center_x), float(center_y), size, float(texture.height))
    origin = rl.Vector2(size / 2.0, float(texture.height) / 2.0)
    rotation = max(-180.0, min(180.0, -self._steering_angle_deg))
    rl.draw_circle(center_x, center_y, self._rect.width / 2, self._black_bg)
    rl.draw_texture_pro(texture, source, destination, origin, rotation, icon_color)

  def _held_or_actual_mode(self):
    now = time.monotonic()
    if self._hold_end_time and now < self._hold_end_time:
      return self._held_mode

    if self._hold_end_time and now >= self._hold_end_time:
      self._hold_end_time = self._held_mode = None

    return self._experimental_mode

  def _is_toggle_allowed(self):
    if not self._params.get_bool("ExperimentalModeConfirmed"):
      return False

    # Mirror exp mode toggle using persistent car params
    return ui_state.has_longitudinal_control
