def _is_nexo(module) -> bool:
  cp = module.ui_state.CP
  fingerprint = getattr(cp, "carFingerprint", None) if cp is not None else None
  return getattr(fingerprint, "name", str(fingerprint)) == "HYUNDAI_NEXO_1ST_GEN"


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
        line_max_distance,
        True,
      )
      if points.size != 0:
        module.draw_polygon(self._rect, points, warn_color)

    if left_blind_spot:
      draw_area(1, -1.4)
    if right_blind_spot:
      draw_area(2, 1.4)

  ModelRenderer._render = _render
  ModelRenderer._nexo_opkr_blind_spot_patched = True


try:
  from openpilot.selfdrive.ui.onroad import model_renderer as _model_renderer
  _patch_opkr_blind_spot(_model_renderer)
except Exception as e:
  print(f"NEXO blind-spot patch failed: {e}")
