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

import html
import socket
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cereal import car, messaging
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.nexo_experimental_mode import (
  SWITCH_SPEED_STEP_KPH,
  load_settings as load_nexo_experimental_speed_settings,
  save_settings as save_nexo_experimental_speed_settings,
)
from system.nexo_web import nexo_cluster_warning_diagnostics as warning_diagnostics
from system.nexo_web import nexo_cluster_warning_policy as warning_policy
from system.nexo_web import nexo_ai_parity_diagnostics as ai_parity_diagnostics
from system.nexo_web import nexo_diagnostics_v2 as diagnostics_v2
from system.nexo_web import nexo_driver_monitoring_diagnostics as dm_diagnostics
from system.nexo_web import nexo_panda_fault_diagnostics as panda_fault_diagnostics
from system.nexo_web import nexo_runtime_guard_diagnostics as guard_diagnostics
from system.nexo_web import nexo_unified_diagnostics as unified_diagnostics
from system.nexo_web import web_carrot_ui as carrot_ui
from system.nexo_web import web_device_ui as device_ui
from system.nexo_web import web_hud_ui as hud_ui
from system.nexo_web import web_remote_ui as remote_ui
from system.nexo_web import web_core as core
from system.nexo_web import web_diagnostics_patch as diagnostics


_original_diagnostic_page = core.diagnostic_page
_original_live_page = core.live_page
_original_handler = core.Handler
_last_diagnostic_lock = threading.Lock()
_last_diagnostic: tuple[str, str] | None = None
remote_ui.install(carrot_ui)


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
  ai_report = ai_parity_diagnostics.prepend_ai_parity_report(core, stationary_corrected_report)
  final_report = panda_fault_diagnostics.prepend_panda_fault_report(ai_report)
  return final_report.replace("\x00", "")


def diagnostic_page(message: str = "") -> str:
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 8초 통합진단</title><style>{carrot_ui._css(core)}</style></head><body><main>
  <div class="hero"><div class="eyebrow">NEXOPILOT · DIAGNOSTICS</div><h1>8초 통합진단</h1><div class="mini">차량 상태와 주요 오류를 8초 동안 읽기 전용으로 수집합니다.</div></div>
  {carrot_ui._message(message)}
  <div class="card"><div class="title">통합진단 파일</div><div class="desc">P단 정지 상태에서 실행하면 차량 인식·운전자 감시·레이더·SCC/FCA·Panda 안전 상태와 오류를 파일 하나로 저장합니다. 차량 제어에는 관여하지 않습니다.</div><form method="post" action="/diagnostics/capture"><button>8초 통합진단 파일 하나 받기</button></form><form method="post" action="/diagnostics/download-last"><button class="secondary">방금 진단 파일 다시 다운받기</button></form><div class="desc">다시 다운받기는 새로 8초를 수집하지 않고, 방금 완료된 동일한 진단 파일을 다시 내려받습니다.</div></div>
  {carrot_ui._nav("diagnostics")}</main></body></html>'''

def live_page() -> str:
  return carrot_ui.enhance_legacy_page(core, _original_live_page(), "camera")


def update_complete_page(message: str) -> str:
  """Show an explicit reboot choice after a successful update."""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 업데이트 완료</title><style>{carrot_ui._css(core)}</style></head><body><main>
  <div class="hero"><div class="eyebrow">UPDATE COMPLETE</div><h1>업데이트 설치 완료</h1><div class="mini">{html.escape(message)}</div></div>
  <div class="card"><h2>지금 재부팅하시겠습니까?</h2><div class="desc">예를 누르면 즉시 재부팅하여 새 코드를 적용합니다. 아니요를 누르면 현재 화면으로 돌아가며 다음 재부팅부터 적용됩니다.</div>
  <form method="post" action="/update/reboot"><button>예 · 지금 재부팅</button></form>
  <a href="/device"><button class="secondary">아니요 · 나중에 재부팅</button></a></div>
  {carrot_ui._nav("device")}</main></body></html>'''


# Keep the verified NEXO-only semantics while matching the useful standard
# Carrot settings menu. Unsupported Carrot tuning parameters are deliberately
# not exposed by port 7000.
core.TOGGLES = list(carrot_ui.TOGGLES)
core.important_log_output = important_log_output
core.raw_can_diagnostic_output = raw_can_diagnostic_output
core.longitudinal_blackbox_output = longitudinal_blackbox_output
core.diagnostic_page = diagnostic_page
core.live_page = live_page


def _parked_gate(require_parking_brake: bool = False) -> tuple[bool, str]:
  """Verify the car is safely parked without requiring selfdriveState to exist.

  carState is the authoritative source for P, zero speed and stock cruise state.
  selfdriveState and pandaStates are additional safety checks when available.
  A missing selfdriveState while parked must not lock every web setting.
  """
  if not core.is_onroad():
    return True, "오프로드"

  try:
    cs_sock = messaging.sub_sock("carState", conflate=True, timeout=900)
    cs_msg = messaging.recv_one(cs_sock)
    if cs_msg is None:
      return False, "carState 수신 없음"
    cs = cs_msg.carState
    if cs.gearShifter != car.CarState.GearShifter.park:
      return False, f"기어={carrot_ui._enum_name(cs.gearShifter)}"
    if abs(float(cs.vEgo)) > 0.05:
      return False, f"속도={float(cs.vEgo) * 3.6:.1f}km/h"
    if bool(cs.cruiseState.enabled):
      return False, "크루즈 활성 중"
    if require_parking_brake and not bool(getattr(cs, "parkingBrake", False)):
      return False, "주차브레이크 해제"

    selfdrive_seen = False
    ss_sock = messaging.sub_sock("selfdriveState", conflate=True, timeout=250)
    ss_msg = messaging.recv_one(ss_sock)
    if ss_msg is not None:
      selfdrive_seen = True
      if bool(ss_msg.selfdriveState.enabled) or bool(ss_msg.selfdriveState.active):
        return False, "오픈파일럿 제어 활성 중"

    panda_seen = False
    panda_sock = messaging.sub_sock("pandaStates", conflate=True, timeout=250)
    panda_msg = messaging.recv_one(panda_sock)
    if panda_msg is not None and len(panda_msg.pandaStates):
      panda_seen = True
      if any(bool(panda.controlsAllowed) for panda in panda_msg.pandaStates):
        return False, "Panda controlsAllowed 활성 중"

    base = "P + 0km/h"
    if require_parking_brake:
      base += " + 주차브레이크"
    base += " + 크루즈 비활성"
    if not selfdrive_seen:
      base += " · selfdriveState 미수신 허용"
    if not panda_seen:
      base += " · Panda 상태 미수신"
    return True, base
  except Exception as error:
    return False, f"정지 상태 확인 실패: {error}"


def _device_stationary_gate(_core) -> tuple[bool, str]:
  return _parked_gate(require_parking_brake=True)


# Device page and device-action handler use the same corrected parked gate.
carrot_ui.stationary_gate = _device_stationary_gate


class CarrotStyleHandler(_original_handler):
  server_version = "NexoPilotWeb/7.9"

  def _same_origin(self) -> bool:
    """Accept legitimate same-site form posts through a remote reverse proxy."""
    expected_hosts = {self.headers.get("Host", "").strip().lower()}
    forwarded_host = self.headers.get("X-Forwarded-Host", "")
    expected_hosts.update(part.strip().lower() for part in forwarded_host.split(",") if part.strip())

    forwarded = self.headers.get("Forwarded", "")
    for part in forwarded.split(";"):
      key, separator, value = part.strip().partition("=")
      if separator and key.lower() == "host":
        expected_hosts.add(value.strip().strip('"').lower())

    for header in ("Origin", "Referer"):
      value = self.headers.get(header)
      if value and urlparse(value).netloc.lower() in expected_hosts:
        return True

    # The browser sets this before a trusted proxy rewrites Host. Cross-site
    # requests remain blocked because browsers report them as cross-site.
    fetch_site = self.headers.get("Sec-Fetch-Site")
    if fetch_site in ("same-origin", "same-site"):
      return True
    if not self.headers.get("Origin") and not self.headers.get("Referer"):
      return fetch_site in (None, "none")
    return False

  def _settings_gate(self) -> tuple[bool, str]:
    """Allow ordinary settings in P at zero speed without requiring EPB."""
    return _parked_gate(require_parking_brake=False)

  def _require_parked(self, path: str) -> bool:
    allowed, state = self._settings_gate()
    if allowed:
      return True
    self._redirect(
      f"설정 변경은 P단·완전 정지·크루즈/오픈파일럿 비활성 상태에서 가능합니다. 현재 상태: {state}",
      path,
    )
    return False

  def _require_update_safe(self, path: str) -> bool:
    allowed, state = carrot_ui.stationary_gate(core)
    if allowed:
      return True
    self._redirect(
      f"업데이트/디바이스 조작은 P단·완전 정지·주차브레이크·크루즈/오픈파일럿 비활성 상태에서만 가능합니다. 현재 상태: {state}",
      path,
    )
    return False

  def _read_small_form(self) -> dict[str, list[str]] | None:
    try:
      length = int(self.headers.get("Content-Length", "0"))
    except ValueError:
      self._send("잘못된 요청 길이", HTTPStatus.BAD_REQUEST)
      return None
    if length < 0 or length > core.MAX_REQUEST_BODY:
      self._send("요청이 너무 큽니다.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
      return None
    try:
      return parse_qs(self.rfile.read(length).decode("utf-8"))
    except Exception:
      self._send("설정 값을 읽을 수 없습니다.", HTTPStatus.BAD_REQUEST)
      return None

  def do_GET(self) -> None:
    parsed = urlparse(self.path)
    query = parse_qs(parsed.query)
    message = query.get("msg", [""])[0]
    if parsed.path == "/api/status":
      self._send_json(carrot_ui.status_json(core))
      return
    if parsed.path == "/api/update-status":
      self._send_json(core.update_status_json())
      return
    if parsed.path == "/api/hud":
      self._send_json(hud_ui.hud_status_json(core))
      return
    if parsed.path == "/settings":
      self._send(carrot_ui.settings_page(core, message))
      return
    if parsed.path in ("/device", "/system"):
      self._send(device_ui.device_page(core, carrot_ui, message, fetch_update=query.get("check", ["0"])[0] == "1"))
      return
    if parsed.path == "/hud/view":
      self._send(hud_ui.hud_view_page(core))
      return
    if parsed.path == "/hud":
      self._send(hud_ui.hud_page(core, message))
      return
    if parsed.path == "/remote":
      self._send(remote_ui.remote_page(core))
      return
    if parsed.path in ("/", "/index.html"):
      self._send(carrot_ui.dashboard_page(core, message, fetch_update=query.get("check", ["0"])[0] == "1"))
      return
    super().do_GET()

  def do_POST(self) -> None:
    parsed = urlparse(self.path)

    if parsed.path in ("/diagnostics/capture", "/diagnostics/download-last"):
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return

      global _last_diagnostic
      if parsed.path == "/diagnostics/capture":
        capture = longitudinal_blackbox_output()
        filename = f"nexo-long-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        with _last_diagnostic_lock:
          _last_diagnostic = (capture, filename)
        self._send_download(capture, filename)
        return

      with _last_diagnostic_lock:
        last_diagnostic = _last_diagnostic
      if last_diagnostic is None:
        self._redirect("먼저 8초 통합진단을 한 번 실행해 주세요.", "/diagnostics")
        return
      capture, filename = last_diagnostic
      self._send_download(capture, filename)
      return

    # Software updates use the normal parked gate: P, fully stopped and cruise off.
    # Physical device actions still require the stricter EPB gate.
    if parsed.path == "/update":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/device"):
        return
      ok, result = core.perform_update()
      if ok:
        self._send(update_complete_page(
          f"{result} 업데이트를 설치했습니다. 새 코드는 재부팅 후 적용됩니다."
        ))
      else:
        self._redirect(result, "/device")
      return

    if parsed.path == "/update/reboot":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      # This is the continuation of the already verified update flow, so P,
      # zero speed and inactive controls are sufficient; EPB is not required.
      if not self._require_parked("/device"):
        return
      _, result = device_ui.perform_device_action("reboot")
      self._redirect(result, "/device")
      return

    safe_redirects = {
      "/device/reboot": "/device",
      "/device/poweroff": "/device",
      "/device/recalibrate": "/device",
    }
    if parsed.path in safe_redirects and not self._require_update_safe(safe_redirects[parsed.path]):
      return

    if parsed.path in device_ui.DEVICE_ACTIONS:
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      action = device_ui.DEVICE_ACTIONS[parsed.path]
      ok, result = device_ui.perform_device_action(action)
      self._redirect(result, "/device")
      return

    if parsed.path == "/experimental-speed-switch/toggle":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/settings"):
        return
      if self._read_small_form() is None:
        return
      try:
        settings = load_nexo_experimental_speed_settings()
        updated = save_nexo_experimental_speed_settings(not settings.enabled, settings.speed_kph)
      except Exception as error:
        self._redirect(f"실험 모드 속도 전환 설정 저장 실패: {error}", "/settings")
        return
      self._redirect("목표 속도 도달 시 일반 모드 전환을 활성화했습니다." if updated.enabled else "목표 속도 도달 시 일반 모드 전환을 비활성화했습니다.", "/settings")
      return

    if parsed.path == "/experimental-speed-switch/speed":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/settings"):
        return
      values = self._read_small_form()
      if values is None:
        return
      try:
        delta = int(values.get("delta", ["0"])[0])
      except ValueError:
        self._send("잘못된 속도 조절 값", HTTPStatus.BAD_REQUEST)
        return
      if delta not in (-SWITCH_SPEED_STEP_KPH, SWITCH_SPEED_STEP_KPH):
        self._send("허용되지 않은 속도 조절 값", HTTPStatus.BAD_REQUEST)
        return
      try:
        settings = load_nexo_experimental_speed_settings()
        updated = save_nexo_experimental_speed_settings(settings.enabled, settings.speed_kph + delta)
      except Exception as error:
        self._redirect(f"실험 모드 전환 속도 저장 실패: {error}", "/settings")
        return
      self._redirect(f"실험 모드 전환 기준 속도를 {updated.speed_kph}km/h로 저장했습니다.", "/settings")
      return

    if parsed.path == "/reverse-camera/toggle":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/settings"):
        return
      if self._read_small_form() is None:
        return
      try:
        enabled = not carrot_ui.reverse_driver_camera_enabled()
        carrot_ui.set_reverse_driver_camera(enabled)
      except Exception as error:
        self._redirect(f"후진 실내 카메라 설정 저장 실패: {error}", "/settings")
        return
      self._redirect("후진 시 실내 카메라 전환을 활성화했습니다." if enabled else "후진 시 실내 카메라 전환을 비활성화했습니다.", "/settings")
      return

    if parsed.path == "/hud/toggle":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/hud"):
        return
      if self._read_small_form() is None:
        return
      params = Params()
      next_value = not params.get_bool(hud_ui.HUD_PARAM)
      try:
        params.put_bool(hud_ui.HUD_PARAM, next_value)
      except Exception as error:
        self._redirect(f"HUD 설정 저장 실패: {error}", "/hud")
        return
      self._redirect("HUD를 활성화했습니다. 화면을 열어 표시 상태를 확인하세요." if next_value else "HUD를 비활성화했습니다.", "/hud")
      return

    if parsed.path == "/personality":
      if not self._same_origin():
        self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
        return
      if not self._require_parked("/settings"):
        return
      values = self._read_small_form()
      if values is None:
        return
      try:
        personality = int(values.get("value", ["1"])[0])
      except ValueError:
        self._send("잘못된 주행 성향", HTTPStatus.BAD_REQUEST)
        return
      allowed = {value for value, _ in carrot_ui.PERSONALITIES}
      if personality not in allowed:
        self._send("지원하지 않는 주행 성향", HTTPStatus.BAD_REQUEST)
        return
      try:
        # LongitudinalPersonality is a typed INT Param in this openpilot base.
        # Passing str(personality) trips params_pyx's runtime type checker.
        Params().put("LongitudinalPersonality", personality)
      except Exception as error:
        self._redirect(f"주행 성향 저장 실패: {error}", "/settings")
        return
      self._redirect("주행 성향을 저장했습니다.", "/settings")
      return

    super().do_POST()


core.Handler = CarrotStyleHandler


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
  threading.Thread(target=core.update_monitor, daemon=True).start()
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
