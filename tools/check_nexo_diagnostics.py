#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DIAGNOSTIC_FILES = (
  "selfdrive/car/nexo_guard.py",
  "selfdrive/car/nexo_diagnostics.py",
  "selfdrive/car/nexo_runtime_diagnostics.py",
  "selfdrive/car/card.py",
  "system/manager/manager.py",
  "system/nexo_web/web.py",
  "system/nexo_web/nexo_diagnostics_v2.py",
  "system/nexo_web/nexo_unified_diagnostics.py",
  "system/nexo_web/nexo_runtime_guard_diagnostics.py",
  "system/nexo_web/nexo_cluster_warning_diagnostics.py",
  "system/nexo_web/nexo_cluster_warning_policy.py",
  "system/nexo_web/nexo_ai_parity_diagnostics.py",
  "opendbc_repo/opendbc/car/hyundai/radar_tracks.py",
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "opendbc_repo/opendbc/car/hyundai/nexo_takeover.py",
)


def require(value: bool, message: str) -> None:
  if not value:
    raise AssertionError(message)


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
  sources = {path: text(path) for path in DIAGNOSTIC_FILES}
  for path, source in sources.items():
    ast.parse(source, filename=path)
    print(f"syntax OK: {path}")

  guard = sources["selfdrive/car/nexo_guard.py"]
  writer = sources["selfdrive/car/nexo_diagnostics.py"]
  card = sources["selfdrive/car/card.py"]
  runtime = sources["selfdrive/car/nexo_runtime_diagnostics.py"]
  manager = sources["system/manager/manager.py"]
  entry = sources["system/nexo_web/web.py"]
  web = sources["system/nexo_web/nexo_diagnostics_v2.py"]
  unified = sources["system/nexo_web/nexo_unified_diagnostics.py"]
  guard_web = sources["system/nexo_web/nexo_runtime_guard_diagnostics.py"]
  warning_web = sources["system/nexo_web/nexo_cluster_warning_diagnostics.py"]
  warning_policy = sources["system/nexo_web/nexo_cluster_warning_policy.py"]
  ai_parity = sources["system/nexo_web/nexo_ai_parity_diagnostics.py"]
  radar = sources["opendbc_repo/opendbc/car/hyundai/radar_tracks.py"]
  interface = sources["opendbc_repo/opendbc/car/hyundai/interface.py"]
  takeover = sources["opendbc_repo/opendbc/car/hyundai/nexo_takeover.py"]

  require(
    unified.index('long_enabled = cp.get("openpilotLongitudinalControl") is True') <
    unified.index('fca_state_mismatch = long_enabled'),
    "unified diagnostics must initialize long_enabled before FCA classification",
  )
  for token in ("fca_heartbeat_missing", "FCA11 상태 스트림 단절", "FCA 상태 조합 불일치"):
    require(token in unified, f"unified FCA heartbeat diagnostic missing: {token}")

  for token in ("NEXO_FCA_ADDRS", "NEXO_CAN_HISTORY_S = 5.0", "fault_snapshot", "recent_can",
                "NEXO_GUARD_STATE_LOG", "_write_guard_state", '"state": "armed"',
                '"state": "fault"', '"boot_id"'):
    require(token in guard, f"runtime diagnostic guard missing: {token}")
  for token in ("NEXO_LAST_FAULT_LOG", "record_nexo_fault_snapshot", "safety_rx_checks_invalid", "radar_errors"):
    require(token in writer, f"persistent fault capture missing: {token}")
  for token in ("record_nexo_fault_snapshot", "record_nexo_card_crash", "record_nexo_long_success",
                "NexoCardHeartbeatMono", "nexo_stage", "selfdriveState", "radarState"):
    require(token in card, f"card diagnostic connection missing: {token}")
  for token in ("NEXO_CARD_CRASH_LOG", "NEXO_LONG_SUCCESS_LOG", "record_nexo_card_crash",
                "record_nexo_long_success", "set_nexo_runtime_state", "traceback"):
    require(token in runtime, f"card runtime diagnostics missing: {token}")
  for token in ("SELFDRIVED_PUBLISHER_RELEASE_GRACE", "selfdrived.stop()",
                "time.sleep(SELFDRIVED_PUBLISHER_RELEASE_GRACE)", "selfdrived.start()",
                "MultiplePublishersError"):
    require(token in manager, f"safe selfdrived publisher restart missing: {token}")
  for token in ("[SCC/FCA 분리 자동 판정]", "[sendcan 요청 → Panda 결과]",
                "[주요 SCC/FCA 신호 DBC 해석]", "last_fault_output",
                "runtime_status_output", "card_crash_output", "과거 버전 기록",
                "순정 FCA11/FCA12 수신은 정상"):
    require(token in web, f"web diagnostics missing: {token}")

  for token in ("NexoPilot 8초 통합진단 - 한눈에 보기", "build_unified_report",
                "_carparams_snapshot", "_process_snapshot", "_service_snapshot",
                "openpilotLongitudinalControl", "pcmCruise", "radarUnavailable", "sccBus",
                "safetyConfigs", "SCC12 핵심", "순정 SCC 복구", "기계 판독 JSON",
                "상세 원문 - 필요할 때만 아래를 확인하세요",
                "controlsAllowed=False와 Panda 차단은 P단·크루즈 비활성 중에는 정상",
                "def _marker_snapshot", "longitudinal_takeover_ready", "restore_pending",
                "def correct_legacy_wording", "brand", "card_healthy", "sm.seen",
                "takeover_verified", "marker_problem", "takeoverVerifiedByCurrentCapture"):
    require(token in unified, f"unified 8-second diagnostics missing: {token}")
  require("cp.carName" not in unified, "CarParams has no carName member in this schema")

  for token in ("NEXO runtime guard·검사 단계 확인", "P단 정지 검사", "주행 활성 검사:", "\"미실시\"",
                "현재 부팅 기록", "source0 SCC", "runtime guard가 무장되지 않았습니다",
                "prepend_runtime_guard_report"):
    require(token in guard_web, f"runtime guard web diagnostics missing: {token}")

  for token in ("계기판 경고등·ADAS 경고 확인", "WARNING_SIGNALS", "onroadEvents",
                "ACCFailInfo", "CF_VSM_Warn", "CF_Mdps_ToiFlt", "steerFaultPermanent",
                "Panda RX 안전검사 invalid", "계기판 전구 자체를 직접 읽는 기능은 아닙니다",
                "expected_stationary_events", "expected_reverse_alert",
                "현재 기어·정지 상태에서 정상으로 제외한 항목",
                "prepend_cluster_warning_report"):
    require(token in warning_web, f"cluster warning diagnostics missing: {token}")

  for token in ("correct_stationary_cluster_warning", "_EXPECTED_PARK_EVENTS", "wrongGear",
                "seatbeltNotLatched", "parkBrake", "P단 정지에서 정상으로 제외한 항목",
                "P단·정지·크루즈 비활성", "speed=-0.0km/h"):
    require(token in warning_policy, f"park warning policy missing: {token}")

  for token in ("AI 실차 기준·NEXO SCC 인계 확인", "NEXO_TAKEOVER_VERIFY_LOG",
                "Tester Present(0x7D0)", "source0 SCC", "prepend_ai_parity_report"):
    require(token in ai_parity, f"AI parity diagnostics missing: {token}")
  for token in ("ensure_nexo_stock_scc_silent", "source0_scc_total", "attempts: int = 3",
                "communication_control"):
    require(token in takeover, f"post-radar SCC verifier missing: {token}")

  read_only_sources = (unified, guard_web, warning_web, warning_policy, ai_parity)
  for forbidden in ("pub_sock(", "disable_ecu", "put_bool(", "schedule_reboot", "git_run(\"merge\""):
    for source in read_only_sources:
      require(forbidden not in source, f"web diagnostics must remain read-only: {forbidden}")

  require("diagnostics_v2.longitudinal_blackbox_output" in entry, "web v2 blackbox not wired")
  require("unified_diagnostics.correct_legacy_wording" in entry, "legacy false-positive correction not wired")
  require("unified_diagnostics.build_unified_report" in entry, "unified 8-second report not wired")
  require("guard_diagnostics.prepend_runtime_guard_report" in entry, "runtime guard report not wired")
  require("warning_diagnostics.prepend_cluster_warning_report" in entry, "cluster warning report not wired")
  require("warning_policy.correct_stationary_cluster_warning" in entry, "park warning policy not wired")
  require("ai_parity_diagnostics.prepend_ai_parity_report" in entry, "AI parity report not wired")
  require("NexoPilotWeb/7.9" in entry, "port 7000 server version not advanced for proxied remote diagnostics")
  require("8초 통합진단 파일 하나 받기" in entry, "single-file diagnostic button label missing")
  for token in ("_last_diagnostic_lock", "_last_diagnostic = (capture, filename)",
                'parsed.path == "/diagnostics/capture"', '"/diagnostics/download-last"',
                "방금 진단 파일 다시 다운받기",
                "새로 8초를 수집하지 않고, 방금 완료된 동일한 진단 파일"):
    require(token in entry, f"last completed diagnostic re-download contract missing: {token}")
  for token in ("UDS TX", "UDS RX", "UDS ERROR", "request.hex(' ')"):
    require(token in radar, f"radar UDS diagnostics missing: {token}")
  require("def _trace_nexo_long_init" in interface, "NEXO init trace helper missing")
  require("elapsed_ms" in interface, "disable ECU timing trace missing")
  require("ensure_nexo_stock_scc_silent" in interface, "post-radar SCC verifier not wired")
  require("DEINIT stock SCC communication restore" in interface, "NEXO deinit restore trace missing")
  require("CarInterface.init(CP, can_recv, can_send, communication_control)" in interface,
          "non-NEXO deinit fallback missing")
  print("NEXO diagnostics v8 AI parity policy PASS")


if __name__ == "__main__":
  main()
