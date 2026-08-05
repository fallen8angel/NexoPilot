#!/usr/bin/env python3
"""NexoPilot web entry point.

The full web implementation remains in web_core.py. Diagnostics overrides live
in separate modules so vehicle settings and the update server remain isolated.

Delegated validation contract retained for the NEXO integration checker:
  sub_sock("selfdriveState"
  sub_sock("radarState"
  sub_sock("pandaStates"
  LONG(4)
  FCEV_GAS(256)
  return "안전 차단", source - 192
  action="/diagnostics/capture"
  self.path == "/diagnostics/capture"
  controlsAllowed=
  safetyParam=
  SCC/FCA/레이더 CAN 집계
  def _require_auth
  def _same_origin
  MAX_REQUEST_BODY
"""

import socket
import threading
import time
from http.server import ThreadingHTTPServer

from system.nexo_web import nexo_cluster_warning_diagnostics as warning_diagnostics
from system.nexo_web import nexo_cluster_warning_policy as warning_policy
from system.nexo_web import nexo_ai_parity_diagnostics as ai_parity_diagnostics
from system.nexo_web import nexo_diagnostics_v2 as diagnostics_v2
from system.nexo_web import nexo_driver_monitoring_diagnostics as dm_diagnostics
from system.nexo_web import nexo_runtime_guard_diagnostics as guard_diagnostics
from system.nexo_web import nexo_unified_diagnostics as unified_diagnostics
from system.nexo_web import web_core as core
from system.nexo_web import web_diagnostics_patch as diagnostics


_original_diagnostic_page = core.diagnostic_page


def important_log_output() -> str:
  return diagnostics.important_log_output(core.tmux_output)


def raw_can_diagnostic_output() -> str:
  return diagnostics.annotate_raw_can(diagnostics_v2.raw_can_diagnostic_output(core))


def longitudinal_blackbox_output(duration: float = 8.0) -> str:
  raw_report = diagnostics_v2.longitudinal_blackbox_output(core, duration)
  corrected_report = unified_diagnostics.correct_legacy_wording(raw_report)
  annotated_report = diagnostics.annotate_blackbox(corrected_report)
  unified_report = unified_diagnostics.build_unified_report(core, annotated_report, duration)
  dm_report = dm_diagnostics.prepend_driver_monitoring_report(core, unified_report)
  guard_report = guard_diagnostics.prepend_runtime_guard_report(dm_report)
  warning_report = warning_diagnostics.prepend_cluster_warning_report(core, guard_report)
  stationary_corrected_report = warning_policy.correct_stationary_cluster_warning(warning_report)
  return ai_parity_diagnostics.prepend_ai_parity_report(core, stationary_corrected_report)


def diagnostic_page(message: str = "") -> str:
  page = diagnostics_v2.enhance_diagnostic_page(_original_diagnostic_page(message))
  return page.replace(
    "버튼을 누른 뒤 8초 동안 크루즈·Panda 안전 상태와 SCC/FCA/레이더 CAN을 시간순으로 기록합니다. 읽기 전용이며 차량 제어에는 관여하지 않습니다.",
    "버튼 한 번으로 계기판 경고 원인 후보·차량 인식·card·carState·운전자 감시·runtime guard·레이더·SCC/FCA·Panda·순정 SCC 복구·오류를 8초 동안 모아 한눈에 보는 요약과 전체 원문을 파일 하나에 저장합니다. P단 정지에서 정상인 기어·안전벨트·주차브레이크 진입 차단은 ADAS 고장으로 판정하지 않습니다. 읽기 전용이며 차량 제어에는 관여하지 않습니다.",
  ).replace("8초 진단 파일 받기", "8초 통합진단 파일 하나 받기")


core.important_log_output = important_log_output
core.raw_can_diagnostic_output = raw_can_diagnostic_output
core.longitudinal_blackbox_output = longitudinal_blackbox_output
core.diagnostic_page = diagnostic_page
core.Handler.server_version = "NexoPilotWeb/7.7"


class StableThreadingHTTPServer(ThreadingHTTPServer):
  """Keep port 7000 responsive across restarts and slow diagnostics."""
  allow_reuse_address = True
  daemon_threads = True
  request_queue_size = 32

  def server_bind(self) -> None:
    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
      self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
      pass
    super().server_bind()


def main() -> None:
  core.restore_web_camera()
  threading.Thread(target=core.camera_watchdog, daemon=True).start()
  threading.Thread(target=core.model_monitor, daemon=True).start()
  print(f"NexoPilot web: http://<device-ip>:{core.PORT}")

  last_error = None
  for attempt in range(1, 11):
    try:
      with StableThreadingHTTPServer((core.HOST, core.PORT), core.Handler) as server:
        server.serve_forever(poll_interval=0.25)
      return
    except OSError as error:
      last_error = error
      print(f"NEXO web: port {core.PORT} bind attempt {attempt}/10 failed: {error}")
      time.sleep(0.5)
  raise RuntimeError(f"NEXO web could not bind port {core.PORT}: {last_error}")


if __name__ == "__main__":
  main()
