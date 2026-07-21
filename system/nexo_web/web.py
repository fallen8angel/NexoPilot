#!/usr/bin/env python3
import html
import socket
import subprocess
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from cereal import car, messaging
from openpilot.common.params import Params

HOST = "0.0.0.0"
PORT = 7000
REPO_DIR = Path("/data/openpilot")
STATE_DIR = Path("/data/nexopilot")
FORCE_NEXO_FILE = STATE_DIR / "force_nexo"
BRANCH = "NEXO"

TOGGLES = [
  ("AlphaLongitudinalEnabled", "오픈파일럿 롱컨", "가속과 감속을 오픈파일럿이 제어합니다.", True),
  ("OpenpilotEnabledToggle", "오픈파일럿 사용", "오픈파일럿 기능 전체를 켜거나 끕니다.", True),
  ("ExperimentalMode", "실험 모드", "실험용 종방향 주행 기능을 사용합니다.", False),
  ("IsMetric", "미터법 사용", "속도와 거리를 km/h 및 m 단위로 표시합니다.", False),
  ("EnableRadarTracks", "레이더 트랙 활성화", "넥쏘 만도 레이더의 다중 트랙 정보를 사용합니다.", True),
  ("AutoLaneChangeEnabled", "자동 차선 변경", "방향지시등 사용 시 자동 차선 변경을 허용합니다.", False),
  ("LaneDepartureWarningEnabled", "차선이탈 경고", "방향지시등 없이 차선을 벗어나면 경고를 표시합니다.", False),
  ("AutoResumeFromStop", "정지 후 자동 출발", "앞차가 출발하면 정지 상태에서 자동으로 다시 출발합니다.", False),
]

NUMERIC_SETTINGS = [
  ("SteerSensitivity", "조향 감도", "핸들 반응 속도를 조절합니다. 높을수록 빠르게 반응합니다.", "50~150%", 50.0, 150.0, 1.0, "100"),
  ("LongitudinalKp", "롱컨 비례값 Kp", "앞차 속도 변화에 반응하는 정도를 조절합니다.", "0.10~3.00", 0.10, 3.00, 0.01, "1.00"),
  ("LongitudinalKi", "롱컨 적분값 Ki", "지속되는 속도 오차를 누적해서 보정합니다.", "0.00~1.00", 0.00, 1.00, 0.01, "0.10"),
  ("StartAccel", "출발 가속값", "정지 후 출발할 때의 초기 가속 강도를 조절합니다.", "0.10~2.00 m/s²", 0.10, 2.00, 0.05, "1.00"),
  ("StopDistance", "정지거리", "앞차 뒤에서 멈출 때의 목표 정차 거리를 조절합니다.", "2.0~10.0 m", 2.0, 10.0, 0.1, "5.9"),
]


def param_text(params: Params, key: str, default: str = "") -> str:
  try:
    value = params.get(key)
  except Exception:
    return default
  if value is None:
    return default
  if isinstance(value, bytes):
    try:
      return value.decode("utf-8")
    except UnicodeDecodeError:
      return default
  return str(value)


def param_bool(params: Params, key: str) -> bool:
  try:
    return params.get_bool(key)
  except Exception:
    return False


def local_ip() -> str:
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    sock.connect(("8.8.8.8", 80))
    return sock.getsockname()[0]
  except OSError:
    return "확인 불가"
  finally:
    sock.close()


def run_command(args: list[str], timeout: int = 10, cwd: Path | None = None) -> tuple[int, str]:
  try:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    return result.returncode, output or "출력 없음"
  except Exception as error:
    return -1, str(error)


def git_run(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
  return subprocess.run(["git", *args], cwd=REPO_DIR, text=True, capture_output=True, timeout=timeout, check=False)


def git_value(*args: str) -> str:
  try:
    result = git_run(*args, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else "확인 불가"
  except Exception:
    return "확인 불가"


def car_status() -> dict[str, str]:
  raw = Params().get("CarParams")
  result = {"car": "아직 인식되지 않음", "longitudinal": "확인 불가", "radar": "확인 불가", "fingerprint_source": "확인 불가"}
  if raw:
    try:
      with car.CarParams.from_bytes(raw) as cp:
        result["car"] = str(cp.carFingerprint)
        result["longitudinal"] = "활성" if cp.openpilotLongitudinalControl else "비활성"
        result["radar"] = "사용 불가" if cp.radarUnavailable else "사용 가능"
        result["fingerprint_source"] = str(cp.fingerprintSource)
    except Exception as error:
      result["car"] = f"CarParams 읽기 실패: {error}"
  return result


def force_nexo_enabled() -> bool:
  try:
    return FORCE_NEXO_FILE.read_text(encoding="utf-8").strip() == "1"
  except (FileNotFoundError, OSError):
    return False


def clear_car_cache() -> None:
  params = Params()
  for key in ("CarParams", "CarParamsCache", "CarParamsPersistent", "CarParamsPrevRoute"):
    try:
      params.remove(key)
    except Exception:
      pass


def set_vehicle(mode: str) -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  FORCE_NEXO_FILE.write_text("1" if mode == "nexo" else "0", encoding="utf-8")
  clear_car_cache()


def is_onroad() -> bool:
  params = Params()
  return param_bool(params, "IsOnroad") and not param_bool(params, "IsOffroad")


def parked_state() -> tuple[bool, str]:
  """Allow settings offroad and while the live gear is confirmed as Park."""
  if not is_onroad():
    return True, "오프로드"
  try:
    sock = messaging.sub_sock("carState", conflate=True, timeout=1500)
    message = messaging.recv_one(sock)
    if message is None:
      return False, "기어 상태 확인 불가"
    gear = message.carState.gearShifter
    if gear == car.CarState.GearShifter.park:
      return True, "P"
    return False, str(gear)
  except Exception as error:
    return False, f"기어 상태 확인 실패: {error}"


def schedule_reboot(delay: float = 1.5) -> None:
  def reboot() -> None:
    time.sleep(delay)
    subprocess.Popen(["sudo", "reboot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  threading.Thread(target=reboot, daemon=True).start()


def update_status(fetch: bool = False) -> dict[str, str | bool]:
  result: dict[str, str | bool] = {"current": git_value("rev-parse", "--short", "HEAD"), "remote": "확인 전", "available": False, "dirty": bool(git_value("status", "--porcelain")), "error": ""}
  if fetch:
    fetched = git_run("fetch", "origin", BRANCH, timeout=60)
    if fetched.returncode != 0:
      result["error"] = fetched.stderr.strip() or fetched.stdout.strip() or "업데이트 확인 실패"
      return result
    current = git_value("rev-parse", "HEAD")
    remote = git_value("rev-parse", f"origin/{BRANCH}")
    result["current"], result["remote"], result["available"] = current[:9], remote[:9], current != remote
  return result


def perform_update() -> tuple[bool, str]:
  parked, gear = parked_state()
  if not parked:
    return False, f"업데이트는 차량이 P단일 때만 가능합니다. 현재 상태: {gear}"
  dirty = git_run("status", "--porcelain")
  if dirty.returncode != 0:
    return False, dirty.stderr.strip() or "Git 상태 확인 실패"
  stash_message = ""
  if dirty.stdout.strip():
    stashed = git_run("stash", "push", "--include-untracked", "-m", "NexoPilot web auto-stash before update", timeout=60)
    if stashed.returncode != 0:
      return False, stashed.stderr.strip() or stashed.stdout.strip() or "로컬 변경 보관 실패"
    stash_message = "\n로컬 변경 파일은 Git stash에 안전하게 보관했습니다."
  fetched = git_run("fetch", "origin", BRANCH, timeout=60)
  if fetched.returncode != 0:
    return False, fetched.stderr.strip() or "git fetch 실패"
  merged = git_run("merge", "--ff-only", f"origin/{BRANCH}", timeout=60)
  if merged.returncode != 0:
    return False, merged.stderr.strip() or merged.stdout.strip() or "업데이트 적용 실패"
  return True, (merged.stdout.strip() or "최신 버전입니다.") + stash_message


def tmux_output() -> str:
  code, sessions = run_command(["tmux", "list-sessions", "-F", "#{session_name}"], timeout=3)
  if code != 0:
    return f"tmux 세션을 찾지 못했습니다.\n{sessions}"
  session = sessions.splitlines()[0].strip()
  _, output = run_command(["tmux", "capture-pane", "-p", "-t", session, "-S", "-300"], timeout=5)
  return f"세션: {session}\n\n{output}"


def process_output() -> str:
  lines = []
  for name in ("manager", "card", "pandad", "controlsd", "selfdrived", "radard", "nexo_web"):
    code, output = run_command(["pgrep", "-af", name], timeout=3)
    lines.append(f"[{name}]\n{output if code == 0 else '실행 중 아님'}")
  return "\n\n".join(lines)


def system_output() -> str:
  blocks = []
  for title, command in (("가동시간", ["uptime"]), ("디스크", ["df", "-h", "/data"]), ("메모리", ["free", "-h"]), ("네트워크", ["ip", "-brief", "address"]), ("Git 상태", ["git", "status", "--short", "--branch"])):
    _, output = run_command(command, timeout=5, cwd=REPO_DIR if command[0] == "git" else None)
    blocks.append(f"[{title}]\n{output}")
  return "\n\n".join(blocks)


def base_css() -> str:
  return """
body{margin:0;background:#05070b;color:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif}main{max-width:820px;margin:auto;padding:22px}
a{color:#8eafff;text-decoration:none}.card{background:#151821;border:1px solid #2a3140;border-radius:22px;padding:18px;margin:14px 0}.row{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 2px;border-bottom:1px solid #292f3b}.row:last-child{border:0}.title{font-size:17px;font-weight:700}.desc{font-size:13px;color:#9ca5b5;margin-top:4px;line-height:1.4}.value{font-weight:700;text-align:right;word-break:break-all}button{width:100%;padding:15px;border:0;border-radius:14px;background:#3159d9;color:white;font-size:17px;font-weight:700;margin-top:10px}button.secondary{background:#41495b}button.danger{background:#ad4242}.message{background:#173b2a;border:1px solid #2d7750;padding:14px;border-radius:14px;margin:10px 0}.warning{color:#ffcf70;font-size:14px;line-height:1.55}select,input[type=number]{width:100%;box-sizing:border-box;padding:14px;border-radius:14px;background:#0e1118;color:white;border:1px solid #394154;font-size:17px}.switch{position:relative;display:inline-block;width:52px;height:31px;flex:0 0 auto}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;inset:0;background:#4b4f58;border-radius:31px;transition:.2s}.slider:before{content:'';position:absolute;width:27px;height:27px;left:2px;top:2px;background:white;border-radius:50%;transition:.2s}.switch input:checked+.slider{background:#34c759}.switch input:checked+.slider:before{transform:translateX(21px)}pre{white-space:pre-wrap;word-break:break-word;background:#080b10;border-radius:12px;padding:14px;max-height:480px;overflow:auto}.number-item{background:#0d1118;border-radius:16px;padding:14px;margin:10px 0}
"""


def live_page() -> str:
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 실시간 화면</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>실시간 전방 화면</h1><div class="card"><img style="width:100%;border-radius:18px" src="/stream.mjpeg"></div></main></body></html>'''


def stream_camera(handler: BaseHTTPRequestHandler) -> None:
  handler.send_response(HTTPStatus.OK)
  handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
  handler.send_header("Cache-Control", "no-store")
  handler.end_headers()
  sock = messaging.sub_sock("thumbnail", conflate=True)
  try:
    while True:
      message = messaging.recv_one(sock)
      if message is None:
        continue
      frame = bytes(message.thumbnail.thumbnail)
      if not frame:
        continue
      handler.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
      handler.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
      handler.wfile.write(frame + b"\r\n")
      handler.wfile.flush()
  except (BrokenPipeError, ConnectionResetError):
    pass


def settings_page(message: str = "") -> str:
  params = Params()
  forced = force_nexo_enabled()
  rows = []
  for key, title, desc, reboot in TOGGLES:
    checked = " checked" if param_bool(params, key) else ""
    rows.append(f'''<form method="post" action="/toggle"><input type="hidden" name="key" value="{key}"><input type="hidden" name="reboot" value="{'1' if reboot else '0'}"><div class="row"><div><div class="title">{title}</div><div class="desc">{desc}</div></div><label class="switch"><input type="checkbox"{checked} onchange="this.form.submit()"><span class="slider"></span></label></div></form>''')
  numbers = []
  for key, title, desc, hint, minimum, maximum, step, default in NUMERIC_SETTINGS:
    raw = param_text(params, key, default)
    numbers.append(f'''<div class="number-item"><div class="title">{title}</div><div class="desc">{desc}</div><div class="desc">범위 {hint} · 권장값 {default}</div><form method="post" action="/value"><input type="hidden" name="key" value="{key}"><input type="number" name="value" value="{html.escape(raw)}" min="{minimum}" max="{maximum}" step="{step}"><button class="secondary">저장</button></form></div>''')
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 차량 설정</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>차량 설정</h1>{msg}<div class="card"><div class="title">차량 선택</div><form method="post" action="/vehicle"><select name="vehicle"><option value="auto"{' selected' if not forced else ''}>자동 인식</option><option value="nexo"{' selected' if forced else ''}>현대 넥쏘 1세대</option></select><button>차량 저장 후 재부팅</button></form><p class="warning">P단에서는 설정을 변경할 수 있습니다. D·R·N단에서는 차단됩니다.</p></div><div class="card"><h2>주행 기능</h2>{''.join(rows)}</div><div class="card"><h2>고급 수치 설정</h2>{''.join(numbers)}</div></main></body></html>'''


def diagnostic_page(message: str = "") -> str:
  status = car_status()
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 진단</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>진단 도구</h1>{msg}<div class="card"><div class="row"><span>차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div></div><div class="card"><h2>tmux a 화면</h2><pre>{html.escape(tmux_output())}</pre></div><div class="card"><h2>프로세스 검사</h2><pre>{html.escape(process_output())}</pre></div><div class="card"><h2>시스템 검사</h2><pre>{html.escape(system_output())}</pre></div></main></body></html>'''


class Handler(BaseHTTPRequestHandler):
  server_version = "NexoPilotWeb/5.8"

  def log_message(self, fmt: str, *args) -> None:
    print(f"NEXO web: {self.address_string()} - {fmt % args}")

  def _send(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
    data = body.encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(data)

  def _redirect(self, message: str, path: str = "/") -> None:
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", f"{path}?msg={quote(message)}")
    self.end_headers()

  def _require_parked(self, path: str) -> bool:
    allowed, state = parked_state()
    if allowed:
      return True
    self._redirect(f"설정 변경은 P단에서만 가능합니다. 현재 상태: {state}", path)
    return False

  def do_GET(self) -> None:
    try:
      parsed = urlparse(self.path)
      query = parse_qs(parsed.query)
      message = query.get("msg", [""])[0]
      if parsed.path == "/live":
        self._send(live_page()); return
      if parsed.path == "/stream.mjpeg":
        stream_camera(self); return
      if parsed.path == "/settings":
        self._send(settings_page(message)); return
      if parsed.path == "/diagnostics":
        self._send(diagnostic_page(message)); return
      if parsed.path not in ("/", "/index.html"):
        self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND); return
      status = car_status()
      update = update_status(fetch=query.get("check", ["0"])[0] == "1")
      msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
      self._send(f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot</title><style>{base_css()}</style></head><body><main><h1>NexoPilot</h1><div class="desc">콤마4 로컬 설정 · http://{html.escape(local_ip())}:{PORT}</div>{msg}<div class="card"><div class="row"><span>현재 차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div></div><div class="card"><a href="/live"><button>실시간 전방 화면 열기</button></a><a href="/settings"><button class="secondary">차량 설정 열기</button></a><a href="/diagnostics"><button class="secondary">진단 도구 열기</button></a></div><div class="card"><h2>웹 업데이트</h2><div class="row"><span>현재 버전</span><span class="value">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value">{html.escape(str(update['remote']))}</span></div><form method="get"><input type="hidden" name="check" value="1"><button class="secondary">업데이트 확인</button></form><form method="post" action="/update"><button>업데이트 설치 후 재부팅</button></form></div></main></body></html>''')
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>페이지 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)

  def do_POST(self) -> None:
    try:
      length = int(self.headers.get("Content-Length", "0"))
      values = parse_qs(self.rfile.read(length).decode("utf-8"))
      if self.path == "/vehicle":
        if not self._require_parked("/settings"): return
        mode = values.get("vehicle", ["auto"])[0]
        if mode not in ("auto", "nexo"):
          self._send("잘못된 차량 선택", HTTPStatus.BAD_REQUEST); return
        set_vehicle(mode)
        self._send("<h2>차량 설정 저장 완료. 재부팅합니다.</h2>")
        schedule_reboot(); return
      if self.path == "/toggle":
        if not self._require_parked("/settings"): return
        key = values.get("key", [""])[0]
        if key not in {item[0] for item in TOGGLES}:
          self._send("허용되지 않은 설정", HTTPStatus.BAD_REQUEST); return
        params = Params()
        params.put_bool(key, not param_bool(params, key))
        if values.get("reboot", ["0"])[0] == "1":
          clear_car_cache()
          self._send("<h2>설정을 저장했습니다. 재부팅합니다.</h2>")
          schedule_reboot()
        else:
          self._redirect("설정을 저장했습니다.", "/settings")
        return
      if self.path == "/value":
        if not self._require_parked("/settings"): return
        key = values.get("key", [""])[0]
        raw_value = values.get("value", [""])[0].strip()
        spec = next((item for item in NUMERIC_SETTINGS if item[0] == key), None)
        if spec is None:
          self._send("허용되지 않은 수치 설정", HTTPStatus.BAD_REQUEST); return
        try:
          number = float(raw_value)
        except ValueError:
          self._redirect("숫자를 정확히 입력하세요.", "/settings"); return
        if not spec[4] <= number <= spec[5]:
          self._redirect(f"허용 범위는 {spec[4]}~{spec[5]}입니다.", "/settings"); return
        Params().put(key, raw_value)
        self._redirect(f"{spec[1]} 값을 저장했습니다.", "/settings"); return
      if self.path == "/update":
        ok, result = perform_update()
        if not ok:
          self._redirect(result); return
        self._send(f"<h2>업데이트 완료</h2><pre>{html.escape(result)}</pre>")
        schedule_reboot(); return
      if self.path == "/clear-cache":
        if not self._require_parked("/diagnostics"): return
        clear_car_cache()
        self._send("<h2>캐시를 초기화했습니다. 재부팅합니다.</h2>")
        schedule_reboot(); return
      if self.path == "/reboot":
        if not self._require_parked("/diagnostics"): return
        self._send("<h2>콤마4를 재부팅합니다.</h2>")
        schedule_reboot(); return
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>설정 처리 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
  main()
