import pyray as rl
import cereal.messaging as messaging
from cereal import car
from openpilot.selfdrive.ui.mici.layouts.home import MiciHomeLayout
from openpilot.selfdrive.ui.mici.layouts.settings.settings import SettingsLayout
from openpilot.selfdrive.ui.mici.layouts.offroad_alerts import MiciOffroadAlerts
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.selfdrive.ui.mici.onroad.driver_camera_dialog import DriverCameraDialog
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.selfdrive.ui.mici.layouts.onboarding import OnboardingWindow
from openpilot.selfdrive.ui.body.layouts.onroad import BodyLayout
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.lib.application import gui_app


ONROAD_DELAY = 2.5  # seconds


def should_show_reverse_camera(enabled: bool, started: bool, reverse_selected: bool) -> bool:
  return bool(enabled and started and reverse_selected)


def reverse_camera_action(requested: bool, has_dialog: bool, in_stack: bool, closing: bool) -> str:
  if closing:
    return "wait"
  if requested:
    return "create" if not has_dialog else ("wait" if in_stack else "push")
  return "dismiss" if has_dialog and in_stack else ("close" if has_dialog else "wait")


class MiciMainLayout(Scroller):
  def __init__(self):
    super().__init__(snap_items=True, spacing=0, pad=0, scroll_indicator=False, edge_shadows=False)

    self._pm = messaging.PubMaster(['bookmarkButton'])

    self._prev_onroad = False
    self._prev_standstill = False
    self._onroad_time_delay: float | None = None
    self._setup = False
    self._reverse_driver_camera_dialog: DriverCameraDialog | None = None
    self._reverse_driver_camera_closing = False
    self._reverse_driver_camera_requested = False
    self._reverse_driver_camera_migration_checked = False
    self._reverse_driver_camera_migration_pending = False

    # Initialize widgets
    self._home_layout = MiciHomeLayout()
    self._alerts_layout = MiciOffroadAlerts()
    self._settings_layout = SettingsLayout()
    self._car_onroad_layout = AugmentedRoadView(bookmark_callback=self._on_bookmark_clicked)
    self._body_onroad_layout = BodyLayout()

    # Initialize widget rects
    for widget in (self._home_layout, self._alerts_layout, self._settings_layout,
                   self._car_onroad_layout, self._body_onroad_layout):
      # TODO: set parent rect and use it if never passed rect from render (like in Scroller)
      widget.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

    self._scroller.add_widgets([
      self._alerts_layout,
      self._home_layout,
      self._car_onroad_layout,
      self._body_onroad_layout,
    ])
    self._scroller.set_reset_scroll_at_show(False)

    # Disable scrolling when onroad is interacting with bookmark
    self._scroller.set_scrolling_enabled(lambda: not self._car_onroad_layout.is_swiping_left())

    # Set callbacks
    self._setup_callbacks()

    gui_app.add_nav_stack_tick(self._handle_transitions)
    gui_app.push_widget(self)

    # Start onboarding if terms or training not completed, make sure to push after self
    self._onboarding_window = OnboardingWindow(lambda: gui_app.pop_widgets_to(self))
    if not self._onboarding_window.completed:
      gui_app.push_widget(self._onboarding_window)

  @property
  def _onroad_layout(self) -> Widget:
    # For scroll_to
    return self._body_onroad_layout if ui_state.is_body else self._car_onroad_layout

  def _setup_callbacks(self):
    self._home_layout.set_callbacks(
      on_settings=lambda: gui_app.push_widget(self._settings_layout),
      on_alerts=lambda: self._scroll_to(self._alerts_layout),
      alert_count_callback=self._alerts_layout.active_alerts,
      max_severity_callback=self._alerts_layout.max_severity,
    )
    for layout in (self._car_onroad_layout, self._body_onroad_layout):
      layout.set_click_callback(lambda: self._scroll_to(self._home_layout))

    device.add_interactive_timeout_callback(self._on_interactive_timeout)
    ui_state.add_on_body_changed_callbacks(self._on_body_changed)

  def _scroll_to(self, layout: Widget):
    layout_x = int(layout.rect.x)
    self._scroller.scroll_to(layout_x, smooth=True)

  def _update_state(self):
    super()._update_state()
    # TODO: Hack to run alert updates while not in view. Add a nav stack tick?
    self._alerts_layout._update_state()

  def _render(self, _):
    if not self._setup:
      if self._alerts_layout.active_alerts() > 0:
        self._scroller.scroll_to(self._alerts_layout.rect.x)
      else:
        self._scroller.scroll_to(self._rect.width)
      self._setup = True

    # Render
    super()._render(self._rect)

  def _reverse_camera_enabled(self) -> bool:
    enabled = ui_state.params.get_bool("ReverseDriverCamera")

    if not self._reverse_driver_camera_migration_checked:
      cp = ui_state.CP
      if cp is None:
        return enabled

      fingerprint = getattr(cp, "carFingerprint", None)
      is_nexo = getattr(fingerprint, "name", str(fingerprint)) == "HYUNDAI_NEXO_1ST_GEN"
      self._reverse_driver_camera_migration_checked = True

      # Match XPlus: enable once by default on NEXO, then respect the user's toggle forever.
      if is_nexo and not ui_state.params.get_bool("ReverseDriverCameraNexoMigrated"):
        ui_state.params.put_bool("ReverseDriverCamera", True)
        ui_state.params.put_bool("ReverseDriverCameraNexoMigrated", True)
        self._reverse_driver_camera_migration_pending = True
        return True

    if self._reverse_driver_camera_migration_pending:
      if enabled:
        self._reverse_driver_camera_migration_pending = False
      else:
        return True

    return enabled

  def _handle_transitions(self):
    # Don't pop if onboarding
    if gui_app.widget_in_stack(self._onboarding_window):
      return

    CS = ui_state.sm["carState"]
    self._reverse_driver_camera_requested = should_show_reverse_camera(
      self._reverse_camera_enabled(), ui_state.started,
      CS.gearShifter == car.CarState.GearShifter.reverse,
    )
    reverse_camera_active = (self._reverse_driver_camera_requested or self._reverse_driver_camera_closing or
                             self._reverse_driver_camera_dialog is not None)

    if ui_state.started != self._prev_onroad:
      self._prev_onroad = ui_state.started

      # onroad: after delay, pop nav stack and scroll to onroad
      # offroad: immediately scroll to home, but don't interrupt an active reverse view
      if ui_state.started:
        self._onroad_time_delay = rl.get_time()
      elif not reverse_camera_active:
        self._scroll_to(self._home_layout)

    # Generic onroad navigation must not dismiss the reverse camera while R remains selected.
    if (not reverse_camera_active and self._onroad_time_delay is not None and
        rl.get_time() - self._onroad_time_delay >= ONROAD_DELAY):
      gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._onroad_layout))
      self._onroad_time_delay = None

    # When car leaves standstill, pop nav stack and scroll to onroad unless reverse camera owns the screen.
    if not reverse_camera_active and not CS.standstill and self._prev_standstill:
      gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._onroad_layout))
    self._prev_standstill = CS.standstill

    action = reverse_camera_action(
      self._reverse_driver_camera_requested,
      self._reverse_driver_camera_dialog is not None,
      self._reverse_driver_camera_dialog is not None and gui_app.widget_in_stack(self._reverse_driver_camera_dialog),
      self._reverse_driver_camera_closing,
    )
    if action == "create":
      # Reverse view is intentionally clean: camera only, no DMoji/eye/awareness overlays.
      self._reverse_driver_camera_dialog = DriverCameraDialog(close_on_timeout=False, show_dm_overlay=False)
      gui_app.push_widget(self._reverse_driver_camera_dialog)
    elif action == "push":
      gui_app.push_widget(self._reverse_driver_camera_dialog)
    elif action == "dismiss":
      self._reverse_driver_camera_closing = True
      gui_app.pop_widgets_to(self, self._finish_reverse_camera)
    elif action == "close":
      self._finish_reverse_camera()

  def _finish_reverse_camera(self) -> None:
    self._reverse_driver_camera_closing = False
    dialog = self._reverse_driver_camera_dialog
    if dialog is None:
      return

    # If R was re-selected during the closing animation, restore the same camera immediately.
    if self._reverse_driver_camera_requested:
      if not gui_app.widget_in_stack(dialog):
        gui_app.push_widget(dialog)
      return

    self._reverse_driver_camera_dialog = None
    dialog.close()
    if ui_state.started:
      self._scroll_to(self._onroad_layout)
    else:
      self._scroll_to(self._home_layout)

  def _on_interactive_timeout(self):
    # Don't pop if onboarding
    if gui_app.widget_in_stack(self._onboarding_window):
      return
    if self._reverse_driver_camera_requested or self._reverse_driver_camera_closing:
      return

    if ui_state.started:
      # Don't pop if at standstill
      if not ui_state.sm["carState"].standstill:
        gui_app.pop_widgets_to(self, lambda: self._scroll_to(self._onroad_layout))
    else:
      # Screen turns off on timeout offroad, so pop immediately without animation
      gui_app.pop_widgets_to(self, instant=True)
      self._scroll_to(self._home_layout)

  def _on_bookmark_clicked(self):
    user_bookmark = messaging.new_message('bookmarkButton')
    user_bookmark.valid = True
    self._pm.send('bookmarkButton', user_bookmark)

  def _on_body_changed(self):
    self._car_onroad_layout.set_visible(not ui_state.is_body)
    self._body_onroad_layout.set_visible(ui_state.is_body)
