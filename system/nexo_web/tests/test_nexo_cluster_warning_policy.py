from system.nexo_web.nexo_cluster_warning_policy import correct_stationary_cluster_warning


SECTION_END = "\n============================================================\nNEXO runtime guard"


def _report(*critical: str, live_comm_ok: bool = True, radar_ok: bool = True) -> str:
  candidates = "\n".join(f"- 치명: {item}" for item in critical)
  radar = (
    "Radar: canError=False radarFault=False wrongConfig=False temporary=False"
    if radar_ok else
    "Radar: canError=True radarFault=False wrongConfig=False temporary=False"
  )
  return (
    "판정: [주행 금지] 경고등 원인으로 볼 수 있는 치명 신호가 감지됐습니다.\n"
    f"{radar}\n"
    "[계기판 경고 원인 후보]\n"
    f"{candidates}\n\n"
    "[경고 관련 CAN 스냅샷 - 8초 수집 직후 0.7초]\n"
    f"{SECTION_END}\n"
    "gear=park | speed=0.0km/h\n"
    f'"liveCommOk": {str(live_comm_ok).lower()},\n'
    f'"liveCommProblems": {"[]" if live_comm_ok else "[\"carState\"]"}'
  )


def test_parked_transient_events_are_not_cluster_faults() -> None:
  report = _report(
    "onroadEvent commIssue: softDisable,noEntry",
    "onroadEvent locationdTemporaryError: softDisable,noEntry",
  )

  corrected = correct_stationary_cluster_warning(report)

  assert "판정: [원인 미검출]" in corrected
  assert "- 정보: onroadEvent commIssue:" in corrected
  assert "- 정보: onroadEvent locationdTemporaryError:" in corrected


def test_comm_issue_remains_critical_when_live_comm_is_bad() -> None:
  report = _report("onroadEvent commIssue: softDisable,noEntry", live_comm_ok=False)

  corrected = correct_stationary_cluster_warning(report)

  assert "판정: [주행 금지]" in corrected
  assert "- 치명: onroadEvent commIssue:" in corrected


def test_comm_issue_remains_critical_when_radar_has_fault() -> None:
  report = _report("onroadEvent commIssue: softDisable,noEntry", radar_ok=False)

  corrected = correct_stationary_cluster_warning(report)

  assert "판정: [주행 금지]" in corrected
  assert "- 치명: onroadEvent commIssue:" in corrected
