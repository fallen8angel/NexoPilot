import pyray as rl

SIDE_PANEL_WIDTH = 60


def blend_colors(a: rl.Color, b: rl.Color, f: float) -> rl.Color:
  h0, s0, v0 = (hsv0 := rl.color_to_hsv(a)).x, hsv0.y, hsv0.z
  h1, s1, v1 = (hsv1 := rl.color_to_hsv(b)).x, hsv1.y, hsv1.z
  dh = ((h1 - h0 + 180) % 360) - 180  # shortest hue delta
  return rl.color_from_hsv((h0 + f * dh) % 360,
                           s0 + f * (s1 - s0),
                           v0 + f * (v1 - v0))


def _is_nexo(module) -> bool:
  cp = module.ui_state.CP
  fingerprint = getattr(cp, "carFingerprint", None) if cp is not None else None
  return getattr(fingerprint, "name", str(fingerprint)) == "HYUNDAI_NEXO_1ST_GEN"


def _patch_nexo_always_lane_lines(module) -> None:
  """Keep NEXO lane lines visible while disengaged using the existing black inactive style."""
  ModelRenderer = module.ModelRenderer
  if getattr(ModelRenderer, "_nexo_always_lane_lines_patched", False):
    return

  original_render = ModelRenderer._render

  def _render(self, rect):
    original_render(self, rect)

    try:
      if not _is_nexo(module):
        return
      if module.ui_state.status != module.UIStatus.DISENGAGED:
        return

      sm = module.ui_state.sm
      if (sm.recv_frame["liveCalibration"] < module.ui_state.started_frame or
          sm.recv_frame["modelV2"] < module.ui_state.started_frame):
        return
    except Exception:
      return

    # Mici already renders lane lines/road edges black in DISENGAGED state.
    # The stock render path only hid them completely, so draw just the lines here.
    self._draw_lane_lines()

  ModelRenderer._render = _render
  ModelRenderer._nexo_always_lane_lines_patched = True


def _patch_opkr_blind_spot(module) -> None:
  """Draw NEXO blind-spot warnings as OPKR-style road-surface areas."""
  ModelRenderer = module.ModelRenderer
  if getattr(ModelRenderer, "_nexo_opkr_blind_spot_patched", False):
    return

  original_render = ModelRenderer._render

  def _render(self, rect):
    original_render(self, rect)

    try:
      if not _is_nexo(module):
        return
      sm = module.ui_state.sm
      if not sm.valid["carState"]:
        return
      car_state = sm["carState"]
      left_blind_spot = bool(car_state.leftBlindspot)
      right_blind_spot = bool(car_state.rightBlindspot)
    except Exception:
      return

    if not (left_blind_spot or right_blind_spot):
      return
    if len(self._lane_lines) < 3 or self._path.raw_points.shape[0] == 0:
      return

    max_distance = float(module.np.clip(
      self._path.raw_points[-1, 0],
      module.MIN_DRAW_DISTANCE,
      module.MAX_DRAW_DISTANCE,
    ))
    warn_color = module.rl.Color(255, 0, 0, 190)
    offset = module.np.array([self._rect.x, self._rect.y], dtype=module.np.float32)

    def draw_area(lane_index: int, center_shift: float) -> None:
      line = self._lane_lines[lane_index].raw_points
      if line.shape[0] == 0:
        return

      line_max_distance = min(max_distance, float(line[-1, 0]))
      max_idx = self._get_path_length_idx(line[:, 0], line_max_distance)

      # Model-space +Y points to the vehicle's right. Fill 2.8 m outward
      # from each ego-lane boundary instead of inward across the ego lane.
      shifted = line.copy()
      shifted[:, 1] += center_shift
      points = self._map_line_to_polygon(
        shifted,
        1.4,
        0.0,
        max_idx,
        True,
      )
      if points.size != 0:
        module.draw_polygon(self._rect, points + offset, warn_color)

    if left_blind_spot:
      draw_area(1, -1.4)
    if right_blind_spot:
      draw_area(2, 1.4)

  ModelRenderer._render = _render
  ModelRenderer._nexo_opkr_blind_spot_patched = True


# Load and patch the Mici model renderer after blend_colors is available.
# model_renderer imports blend_colors from this package, so this eager import
# safely applies the patches before AugmentedRoadView creates ModelRenderer.
try:
  from openpilot.selfdrive.ui.mici.onroad import model_renderer as _model_renderer
  _patch_nexo_always_lane_lines(_model_renderer)
  _patch_opkr_blind_spot(_model_renderer)
except Exception as e:
  print(f"NEXO Mici UI patch failed: {e}")
