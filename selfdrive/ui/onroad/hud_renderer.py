import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.common.nexo_vnavi import read_vnavi_state
from openpilot.selfdrive.selfdrived.nexo_experimental_mode import is_nexo_fingerprint, nexo_med_phase
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192
  set_speed_width_metric: int = 200
  set_speed_width_imperial: int = 172
  set_speed_height: int = 204
  wheel_icon_size: int = 144
  turn_indicator_radius: int = 48
  # XPlus lower-status HUD uses a 70 x 80 gear box.
  gear_box_width: int = 70
  gear_box_height: int = 80


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 40
  set_speed: int = 90
  exp_badge: int = 34
  med_badge: int = 28
  vnavi: int = 36
  gear: int = 70
  cruise_gap: int = 40


@dataclass(frozen=True)
class Colors:
  WHITE = rl.WHITE
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = rl.Color(145, 155, 149, 255)
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED_BG = rl.Color(0, 0, 0, 153)
  OVERRIDE_BG = rl.Color(145, 155, 149, 204)
  ENGAGED_BG = rl.Color(128, 216, 166, 204)
  GREY = rl.Color(166, 166, 166, 255)
  DARK_GREY = rl.Color(114, 114, 114, 255)
  BLACK_TRANSLUCENT = rl.Color(0, 0, 0, 166)
  WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)
  BORDER_TRANSLUCENT = rl.Color(255, 255, 255, 75)
  HEADER_GRADIENT_START = rl.Color(0, 0, 0, 114)
  HEADER_GRADIENT_END = rl.BLANK
  TURN_ACTIVE = rl.Color(80, 220, 120, 255)
  TURN_INACTIVE = rl.Color(170, 170, 170, 115)
  EXP_ACTIVE = rl.Color(128, 216, 166, 245)
  MED_WAIT = rl.Color(95, 170, 255, 245)
  VNAVI = rl.Color(210, 165, 255, 255)
  GEAR_GREEN = rl.Color(0, 255, 0, 210)


UI_CONFIG = UIConfig()
FONT_SIZES = FontSizes()
COLORS = Colors()


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False
    self.left_blinker: bool = False
    self.right_blinker: bool = False
    self.experimental_mode: bool = False
    self.med_phase: str = ""
    self.gear_text: str = "–"
    self.cruise_gap: int = 0
    self.vnavi_active: bool = False
    self.vnavi_speed_limit: float = 0.0
    self.vnavi_distance: float = 0.0

    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._exp_button: ExpButton = ExpButton(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)

  @staticmethod
  def _gear_label(gear) -> str:
    gear_name = str(gear).rsplit('.', 1)[-1].lower()
    return {
      'park': 'P',
      'reverse': 'R',
      'neutral': 'N',
      'drive': 'D',
      'low': 'L',
      'sport': 'S',
      'eco': 'E',
    }.get(gear_name, '–')

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    self.experimental_mode = bool(sm['selfdriveState'].experimentalMode)

    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      self.left_blinker = False
      self.right_blinker = False
      self.med_phase = ""
      self.gear_text = "–"
      self.cruise_gap = 0
      self.vnavi_active = False
      self.vnavi_speed_limit = 0.0
      self.vnavi_distance = 0.0
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']
    cp = ui_state.CP
    fingerprint = getattr(cp, "carFingerprint", None) if cp is not None else None
    self.med_phase = nexo_med_phase(
      is_nexo_fingerprint(fingerprint),
      bool(car_state.cruiseState.available),
      bool(car_state.cruiseState.enabled),
    )

    v_cruise_cluster = car_state.vCruiseCluster
    self.set_speed = (
      controls_state.deprecated.vCruise if v_cruise_cluster == 0.0 else v_cruise_cluster
    )
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE

    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

    # XPlus-style always-present physical turn indicators and gear position.
    self.left_blinker = bool(car_state.leftBlinker)
    self.right_blinker = bool(car_state.rightBlinker)
    self.gear_text = self._gear_label(car_state.gearShifter)

    # Keep the number beside the gear tied to the live following-distance setting.
    # controlsd publishes leadDistanceBars from the active longitudinal personality.
    try:
      gap = int(sm['carControl'].hudControl.leadDistanceBars)
    except Exception:
      gap = 0
    if gap <= 0:
      try:
        gap = int(ui_state.params.get_int("LongitudinalPersonality")) + 1
      except Exception:
        gap = 0
    self.cruise_gap = max(1, min(gap, 4)) if gap > 0 else 0

    vnavi_state = read_vnavi_state()
    self.vnavi_active = bool(vnavi_state and vnavi_state[0])
    if self.vnavi_active:
      self.vnavi_speed_limit = vnavi_state[1]
      self.vnavi_distance = vnavi_state[2]
    else:
      self.vnavi_speed_limit = 0.0
      self.vnavi_distance = 0.0

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START,
      COLORS.HEADER_GRADIENT_END,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)
    self._draw_vnavi(rect)
    self._draw_turn_indicators(rect)
    self._draw_gear(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))
    self._draw_experimental_badge(button_x, button_y)
    self._draw_med_badge(button_x, button_y)

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.ENGAGED
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = COLORS.DISENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = COLORS.OVERRIDE

    max_text = tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, FONT_SIZES.max_speed).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 27),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit."""
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(rect.x + rect.width / 2 - speed_text_size.x / 2, 180 - speed_text_size.y / 2)
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, COLORS.WHITE)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, 290 - unit_text_size.y / 2)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)

  def _draw_vnavi(self, rect: rl.Rectangle) -> None:
    if not self.vnavi_active:
      return
    speed = round(self.vnavi_speed_limit)
    distance = round(self.vnavi_distance)
    text = f"vNAVI {speed} · {distance}m"
    text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZES.vnavi)
    x = rect.x + rect.width / 2 - text_size.x / 2
    y = rect.y + 326
    rl.draw_text_ex(self._font_semi_bold, text, rl.Vector2(x, y), FONT_SIZES.vnavi, 0, COLORS.VNAVI)

  def _draw_turn_indicators(self, rect: rl.Rectangle) -> None:
    """Always show left/right arrows; brighten the side that is physically blinking."""
    radius = UI_CONFIG.turn_indicator_radius
    y = rect.y + UI_CONFIG.header_height + 72
    left_x = rect.x + UI_CONFIG.border_size + radius + 20
    right_x = rect.x + rect.width - UI_CONFIG.border_size - radius - 20

    for x, active, direction in (
      (left_x, self.left_blinker, -1),
      (right_x, self.right_blinker, 1),
    ):
      rl.draw_circle(int(x), int(y), radius, COLORS.BLACK_TRANSLUCENT)
      color = COLORS.TURN_ACTIVE if active else COLORS.TURN_INACTIVE
      # Draw a chevron without relying on a font glyph so it is always available.
      inner = 23
      outer = 30
      if direction < 0:
        tip_x = x - inner
        tail_x = x + inner
      else:
        tip_x = x + inner
        tail_x = x - inner
      rl.draw_line_ex(rl.Vector2(tail_x, y - outer), rl.Vector2(tip_x, y), 10, color)
      rl.draw_line_ex(rl.Vector2(tip_x, y), rl.Vector2(tail_x, y + outer), 10, color)

  def _draw_experimental_badge(self, button_x: float, button_y: float) -> None:
    """Show an explicit XPlus-style EXP mark whenever Experimental Mode is active."""
    if not self.experimental_mode:
      return

    badge_w = 112
    badge_h = 52
    x = button_x + (UI_CONFIG.button_size - badge_w) / 2
    y = button_y + UI_CONFIG.button_size + 8
    badge = rl.Rectangle(x, y, badge_w, badge_h)
    rl.draw_rectangle_rounded(badge, 0.45, 8, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(badge, 0.45, 8, 3, COLORS.EXP_ACTIVE)
    text = "EXP"
    text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZES.exp_badge)
    rl.draw_text_ex(self._font_semi_bold, text,
                    rl.Vector2(x + (badge_w - text_size.x) / 2, y + (badge_h - text_size.y) / 2),
                    FONT_SIZES.exp_badge, 0, COLORS.EXP_ACTIVE)

  def _draw_med_badge(self, button_x: float, button_y: float) -> None:
    """Show whether MED is waiting for SET/RES or actively controlling speed."""
    if not self.med_phase:
      return

    badge_w = 126
    badge_h = 48
    x = button_x + (UI_CONFIG.button_size - badge_w) / 2
    y = button_y + UI_CONFIG.button_size + (68 if self.experimental_mode else 8)
    badge = rl.Rectangle(x, y, badge_w, badge_h)
    color = COLORS.ENGAGED if self.med_phase == "SPEED" else COLORS.MED_WAIT
    rl.draw_rectangle_rounded(badge, 0.45, 8, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(badge, 0.45, 8, 3, color)
    text_size = measure_text_cached(self._font_semi_bold, self.med_phase, FONT_SIZES.med_badge)
    rl.draw_text_ex(self._font_semi_bold, self.med_phase,
                    rl.Vector2(x + (badge_w - text_size.x) / 2, y + (badge_h - text_size.y) / 2),
                    FONT_SIZES.med_badge, 0, color)

  def _draw_gear(self, rect: rl.Rectangle) -> None:
    """Show XPlus-sized P/R/N/D gear and following-distance number at lower-right."""
    width = UI_CONFIG.gear_box_width
    height = UI_CONFIG.gear_box_height
    x = rect.x + rect.width - UI_CONFIG.border_size - width
    y = rect.y + rect.height - UI_CONFIG.border_size - height
    gear_rect = rl.Rectangle(x, y, width, height)

    # Match the XPlus 70 x 80 gear tile while keeping the photo-style dark center
    # and green outline that is easy to read over the road camera image.
    rl.draw_rectangle_rounded(gear_rect, 0.20, 8, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(gear_rect, 0.20, 8, 3, COLORS.GEAR_GREEN)

    gear_size = measure_text_cached(self._font_bold, self.gear_text, FONT_SIZES.gear)
    rl.draw_text_ex(self._font_bold, self.gear_text,
                    rl.Vector2(x + (width - gear_size.x) / 2, y + (height - gear_size.y) / 2),
                    FONT_SIZES.gear, 0, COLORS.WHITE)

    # XPlus places the 40 px gap number 50 px to the left of the gear tile.
    if self.cruise_gap > 0:
      gap_text = str(self.cruise_gap)
      gap_size = measure_text_cached(self._font_bold, gap_text, FONT_SIZES.cruise_gap)
      gap_center_x = x - 50
      gap_y = y + (height - gap_size.y) / 2 + 8
      rl.draw_text_ex(self._font_bold, gap_text,
                      rl.Vector2(gap_center_x - gap_size.x / 2, gap_y),
                      FONT_SIZES.cruise_gap, 0, COLORS.WHITE)
