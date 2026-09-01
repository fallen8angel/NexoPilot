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
  "system/nexo_web/nexo_long_logger.py",
  "system/nexo_web/nexo_golden_backup.py",
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
  long_logger = sources["system/nexo_web/nexo_long_logger.py"]
  golden_backup = sources["system/nexo_web/nexo_golden_backup.py"]
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
                "순정 FCA11/FCA12 수신은 정상", "PARKING_SENSOR_ADDRS",
                "VEHICLE_NAVI_ADDRS", "OBSERVATION_ONLY_ADDRS",
                "parking_sensor_verdict_lines", "[전·후방 주차센서 CAN 후보 신호 · 읽기 전용]",
                "[순정 내비 vNAVI 호환성 · 읽기 전용]", "CAN·UDS를 송신"):
    require(token in web, f"web diagnostics missing: {token}")

  for token in ("NexoPilot 8초 통합진단 - 한눈에 보기", "build_unified_report",
                "_carparams_snapshot", "_process_snapshot", "_service_snapshot",
                "openpilotLongitudinalControl", "pcmCruise", "radarUnavailable", "sccBus",
                "safetyConfigs", "SCC12 핵심", "순정 SCC 복구", "기계 판독 JSON",
                "상세 원문 - 필요할 때만 아래를 확인하세요",
                "controlsAllowed=False와 Panda 차단은 P단·크루즈 비활성 중에는 정상",
                "def _marker_snapshot", "longitudinal_takeover_ready", "restore_pending",
                "def correct_legacy_wording", "brand", "card_healthy", "sm.seen",
                "takeover_verified", "marker_problem", "takeoverVerifiedByCurrentCapture",
                "_transport_sample_total", "diagnostic_transport_unavailable",
                'overall = _label("진단 불가", "openpilot 실시간 메시지 전체 수신 0건")',
                '"diagnosticTransportUnavailable"', '"transportSampleTotal"'):
    require(token in unified, f"unified 8-second diagnostics missing: {token}")
  require("cp.carName" not in unified, "CarParams has no carName member in this schema")

  unified_tree = ast.parse(unified, filename="system/nexo_web/nexo_unified_diagnostics.py")
  transport_helper = next(
    node for node in unified_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_transport_sample_total"
  )
  transport_namespace: dict[str, object] = {}
  exec(compile(ast.Module(body=[transport_helper], type_ignores=[]), "transport-helper", "exec"), transport_namespace)
  transport_count = transport_namespace["_transport_sample_total"]
  require(callable(transport_count), "transport sample helper is not callable")
  require(transport_count({"carState": {"sampleCount": 0}, "snapshotError": {"sampleCount": 99}}) == 0,
          "snapshotError must not make an all-zero cereal capture look available")
  require(transport_count({"carState": {"sampleCount": 2}, "radarState": {"sampleCount": 3}}) == 5,
          "transport sample helper must sum live service samples")

  for token in ("NEXO runtime guard·검사 단계 확인", "P단 정지 검사", "주행 활성 검사:", "\"미실시\"",
                "현재 부팅 기록", "source0 SCC", "runtime guard가 무장되지 않았습니다",
                "prepend_runtime_guard_report"):
    require(token in guard_web, f"runtime guard web diagnostics missing: {token}")

  for token in ("계기판 경고등·ADAS 경고 확인", "WARNING_SIGNALS", "onroadEvents",
                "ACCFailInfo", "CF_VSM_Warn", "CF_Mdps_ToiFlt", "steerFaultPermanent",
                "Panda RX 안전검사 invalid", "계기판 전구 자체를 직접 읽는 기능은 아닙니다",
                "expected_stationary_events", "expected_reverse_alert",
                "_signal_for_sources", "Never mix physical stock FCA state",
                "openpilot FCA11/FCA12 상태 스트림 송신",
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

  read_only_sources = (web, unified, guard_web, warning_web, warning_policy, ai_parity)
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
  require("NexoPilotWeb/8.0" in entry, "port 7000 server version not advanced for forensic diagnostics")
  require("8초 통합진단 파일 하나 받기" in entry, "single-file diagnostic button label missing")
  require("SCC/FCA·주차센서·순정 내비·Panda" in entry,
          "diagnostic page must disclose parking/navigation observation")
  for token in ("_last_diagnostic_lock", "_last_diagnostic = (capture, filename)",
                'parsed.path == "/diagnostics/capture"', '"/diagnostics/download-last"',
                "방금 진단 파일 다시 다운받기",
                "다시 다운받기는 새로 수집하지 않고 방금 완료된 동일한 파일"):
    require(token in entry, f"last completed diagnostic re-download contract missing: {token}")
  for token in ("def _wait_for_control_stack", "DIAGNOSTIC_READY_WAIT_SECONDS = 10.0",
                "DIAGNOSTIC_READY_STABLE_SECONDS = 0.25", 'params.get_bool("ControlsReady")',
                'messaging.SubMaster(["carState", "selfdriveState", "controlsState"])',
                "def _persist_last_diagnostic", "/data/media/nexopilot-8sec-diagnostic.txt"):
    require(token in entry, f"diagnostic readiness/persistence contract missing: {token}")
  entry_tree = ast.parse(entry, filename="system/nexo_web/web.py")
  warmup_function = next(
    node for node in entry_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_wait_for_control_stack"
  )
  warmup_source = ast.unparse(warmup_function)
  for forbidden in ("put_bool(", "pub_sock(", "can_send(", "set_safety_model("):
    require(forbidden not in warmup_source, f"diagnostic warm-up must remain observation-only: {forbidden}")
  for token in ('parsed.path == "/api/long-log-status"', 'parsed.path == "/api/golden-backup-status"',
                '"/diagnostics/long/start"', '"/diagnostics/long/stop"',
                '"/diagnostics/long/download"', '"/diagnostics/golden/start"',
                '"/diagnostics/golden/download"', "def _send_forensic_file"):
    require(token in entry, f"forensic web route missing: {token}")

  for token in ("NEXO_LONG_LOG_V1", "raw_events.bin", "events.jsonl", "can_rx.csv",
                "sendcan_tx.csv", "matching_rlog_qlog.json", "SOURCE_SCAN_ROOTS",
                "MIN_FREE_BYTES", "MAX_ROUTE_LOG_BUNDLE_BYTES", "_recover_interrupted_session",
                "def start", "def stop", "def status", "def report_path", "def archive_path"):
    require(token in long_logger, f"long logger contract missing: {token}")
  for token in ("NexoPilot 골든 레퍼런스 백업", "SOURCE_DIRTY", "CarParamsPersistent",
                "selected_rlog_qlog.json", "SHA256SUMS.txt", "NEXOPILOT_GOLDEN_COMPLETE",
                "MIN_FREE_BYTES", "_redact_git_remote", '"access", "dongleid"',
                "def start", "def status", "def manifest_path", "def archive_path"):
    require(token in golden_backup, f"golden backup contract missing: {token}")
  for source in (long_logger, golden_backup):
    for forbidden in ("messaging.pub_sock(", "can_send(", "set_safety_model(", "put_bool(", "disable_ecu("):
      require(forbidden not in source, f"forensic collectors must not change vehicle control state: {forbidden}")
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
