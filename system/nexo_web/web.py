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
MAX_REQUEST_BODY = 64 * 1024

# Only expose keys provided by the 11.1 base build. NEXO radar tracks are a
# required vehicle capability and are enabled automatically in CarInterface.
TOGGLES = [
  ("AlphaLongitudinalEnabled", "오픈파일럿 롱컨", "가속과 감속을 오픈파일럿이 제어합니다.", True),
  ("OpenpilotEnabledToggle", "오픈파일럿 사용", "오픈파일럿 기능 전체를 켜거나 끕니다.", True),
  ("ExperimentalMode", "실험 모드", "실험용 종방향 주행 기능을 사용합니다.", False),
  ("IsMetric", "미터법 사용", "속도와 거리를 km/h 및 m 단위로 표시합니다.", False),
  ("IsLdwEnabled", "차선이탈 경고", "방향지시등 없이 차선을 벗어나면 경고를 표시합니다.", False),
]


def param_bool(params: Params, key: str) -> bool:
  try:
    return params.get_bool(key)
  except Exception:
    return False


def put_param_bool(params: Params, key: str, value: bool) -> tuple[bool, str]:
  try:
    params.put_bool(key, value)
    return True, ""
  except Exception as error:
    return False, str(error)


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
    result = git_run(*args, timeout=15)
    return result.stdout.strip() if result.returncode == 0 else "확인 불가"
  except Exception:
    return "확인 불가"


def car_status() -> dict[str, str]:
  result = {
    "car": "아직 인식되지 않음",
    "longitudinal": "확인 불가",
    "radar": "확인 불가",
    "radar_mode": "넥쏘 자동 활성화",
  }
  try:
    raw = Params().get("CarParams")
  except Exception as error:
    result["car"] = f"CarParams 읽기 실패: {error}"
    return result

  if raw:
    try:
      with car.CarParams.from_bytes(raw) as cp:
        result["car"] = str(cp.carFingerprint)
        result["longitudinal"] = "활성" if cp.openpilotLongitudinalControl else "비활성"
        result["radar"] = "사용 불가" if cp.radarUnavailable else "사용 가능"
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
  result: dict[str, str | bool] = {
    "current": git_value("rev-parse", "--short", "HEAD"),
    "remote": "확인 전",
    "available": False,
    "error": "",
  }
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
  dirty = git_run("status", "--porcelain", timeout=30)
  if dirty.returncode != 0:
    return False, dirty.stderr.strip() or "Git 상태 확인 실패"
  if dirty.stdout.strip():
    return False, "로컬 변경사항이 있어 업데이트를 중단했습니다. 변경사항을 커밋하거나 직접 정리한 뒤 다시 시도하세요."
  fetched = git_run("fetch", "origin", BRANCH, timeout=60)
  if fetched.returncode != 0:
    return False, fetched.stderr.strip() or "git fetch 실패"
  merged = git_run("merge", "--ff-only", f"origin/{BRANCH}", timeout=60)
  if merged.returncode != 0:
    return False, merged.stderr.strip() or merged.stdout.strip() or "업데이트 적용 실패"
  return True, merged.stdout.strip() or "최신 버전입니다."


def tmux_output() -> str:
  code, sessions = run_command(["tmux", "list-sessions", "-F", "#{session_name}"], timeout=3)
  if code != 0:
    return f"tmux 세션을 찾지 못했습니다.\n{sessions}"
  blocks = []
  for session in sessions.splitlines()[:8]:
    session = session.strip()
    if not session:
      continue
    _, output = run_command(["tmux", "capture-pane", "-p", "-t", session, "-S", "-180"], timeout=5)
    blocks.append(f"[{session}]\n{output}")
  return "\n\n".join(blocks) or "tmux 출력 없음"


def radar_diagnostic_output() -> str:
  _, output = run_command([
    "bash", "-lc",
    "tmux list-sessions -F '#{session_name}' 2>/dev/null | while read s; do "
    "tmux capture-pane -p -t \"$s\" -S -500 2>/dev/null; done | "
    "grep -Ei 'NEXO radar|radar track|radarUnavailable|canError|FCA|AEB|FCW|0x500' | tail -120",
  ], timeout=10)
  return output


def process_output() -> str:
  lines = []
  for name in ("manager", "card", "pandad", "controlsd", "selfdrived", "radard", "nexo_web"):
    code, output = run_command(["pgrep", "-af", name], timeout=3)
    lines.append(f"[{name}]\n{output if code == 0 else '실행 중 아님'}")
  return "\n\n".join(lines)


def system_output() -> str:
  blocks = []
  commands = (
    ("가동시간", ["uptime"], None, 5),
    ("디스크", ["df", "-h", "/data"], None, 5),
    ("메모리", ["free", "-h"], None, 5),
    ("네트워크", ["ip", "-brief", "address"], None, 5),
    ("Git 상태", ["git", "status", "--short", "--branch"], REPO_DIR, 20),
  )
  for title, command, cwd, timeout in commands:
    _, output = run_command(command, timeout=timeout, cwd=cwd)
    blocks.append(f"[{title}]\n{output}")
  return "\n\n".join(blocks)


def base_css() -> str:
  return """
body{margin:0;background:#05070b;color:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif}main{max-width:820px;margin:auto;padding:22px}
a{color:#8eafff;text-decoration:none}.card{background:#151821;border:1px solid #2a3140;border-radius:22px;padding:18px;margin:14px 0}.row{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 2px;border-bottom:1px solid #292f3b}.row:last-child{border:0}.title{font-size:17px;font-weight:700}.desc{font-size:13px;color:#9ca5b5;margin-top:4px;line-height:1.4}.value{font-weight:700;text-align:right;word-break:break-all}button{width:100%;padding:15px;border:0;border-radius:14px;background:#3159d9;color:white;font-size:17px;font-weight:700;margin-top:10px}button.secondary{background:#41495b}.message{background:#173b2a;border:1px solid #2d7750;padding:14px;border-radius:14px;margin:10px 0}.warning{color:#ffcf70;font-size:14px;line-height:1.55}select{width:100%;box-sizing:border-box;padding:14px;border-radius:14px;background:#0e1118;color:white;border:1px solid #394154;font-size:17px}.switch{position:relative;display:inline-block;width:52px;height:31px;flex:0 0 auto}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;inset:0;background:#4b4f58;border-radius:31px;transition:.2s}.slider:before{content:'';position:absolute;width:27px;height:27px;left:2px;top:2px;background:white;border-radius:50%;transition:.2s}.switch input:checked+.slider{background:#34c759}.switch input:checked+.slider:before{transform:translateX(21px)}pre{white-space:pre-wrap;word-break:break-word;background:#080b10;border-radius:12px;padding:14px;max-height:480px;overflow:auto}
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
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 차량 설정</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>차량 설정</h1>{msg}<div class="card"><div class="title">차량 선택</div><form method="post" action="/vehicle"><select name="vehicle"><option value="auto"{' selected' if not forced else ''}>자동 인식</option><option value="nexo"{' selected' if forced else ''}>현대 넥쏘 1세대</option></select><button>차량 저장 후 재부팅</button></form><p class="warning">P단에서만 설정을 변경할 수 있습니다.</p></div><div class="card"><div class="row"><div><div class="title">레이더 트랙</div><div class="desc">넥쏘에서는 AI 방식으로 자동 활성화됩니다. 사용자가 끌 수 없습니다.</div></div><span class="value">자동</span></div></div><div class="card"><h2>주행 기능</h2>{''.join(rows)}</div></main></body></html>'''


def diagnostic_page(message: str = "") -> str:
  status = car_status()
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 진단</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>진단 도구</h1>{msg}<div class="card"><div class="row"><span>차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div><div class="row"><span>레이더 모드</span><span class="value">자동 활성화</span></div></div><div class="card"><h2>레이더·FCA 핵심 로그</h2><pre>{html.escape(radar_diagnostic_output())}</pre></div><div class="card"><h2>tmux 로그</h2><pre>{html.escape(tmux_output())}</pre></div><div class="card"><h2>프로세스 검사</h2><pre>{html.escape(process_output())}</pre></div><div class="card"><h2>시스템 검사</h2><pre>{html.escape(system_output())}</pre></div></main></body></html>'''


class Handler(BaseHTTPRequestHandler):
  server_version = "NexoPilotWeb/6.0"

  def log_message(self, fmt: str, *args) -> None:
    print(f"NEXO web: {self.address_string()} - {fmt % args}")

  def _same_origin(self) -> bool:
    expected = self.headers.get("Host", "")
    origin = self.headers.get("Origin")
    if origin:
      return urlparse(origin).netloc == expected
    referer = self.headers.get("Referer")
    return bool(referer) and urlparse(referer).netloc == expected

  def _send(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
    data = body.encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
    self.send_header("Referrer-Policy", "no-referrer")
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("X-Frame-Options", "DENY")
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
      self._send(f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot</title><style>{base_css()}</style></head><body><main><h1>NexoPilot</h1><div class="desc">콤마4 로컬 설정 · http://{html.escape(local_ip())}:{PORT}</div>{msg}<div class="card"><div class="row"><span>현재 차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div><div class="row"><span>레이더 트랙</span><span class="value">넥쏘 자동</span></div></div><div class="card"><a href="/live"><button>실시간 전방 화면 열기</button></a><a href="/settings"><button class="secondary">차량 설정 열기</button></a><a href="/diagnostics"><button class="secondary">진단 도구 열기</button></a></div><div class="card"><h2>웹 업데이트</h2><div class="row"><span>현재 버전</span><span class="value">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value">{html.escape(str(update['remote']))}</span></div><form method="get"><input type="hidden" name="check" value="1"><button class="secondary">업데이트 확인</button></form><form method="post" action="/update"><button>업데이트 설치 후 재부팅</button></form></div></main></body></html>''')
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>페이지 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)

  def do_POST(self) -> None:
    if not self._same_origin():
      self._send("요청 출처를 확인할 수 없습니다.", HTTPStatus.FORBIDDEN)
      return
    try:
      length = int(self.headers.get("Content-Length", "0"))
      if length < 0 or length > MAX_REQUEST_BODY:
        self._send("요청이 너무 큽니다.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        return
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
        ok, error = put_param_bool(params, key, not param_bool(params, key))
        if not ok:
          self._redirect(f"이 설정은 현재 11.1 빌드에서 지원되지 않습니다: {error}", "/settings"); return
        if values.get("reboot", ["0"])[0] == "1":
          clear_car_cache()
          self._send("<h2>설정을 저장했습니다. 재부팅합니다.</h2>")
          schedule_reboot()
        else:
          self._redirect("설정을 저장했습니다.", "/settings")
        return
      if self.path == "/update":
        ok, result = perform_update()
        if not ok:
          self._redirect(result); return
        self._send(f"<h2>업데이트 완료</h2><pre>{html.escape(result)}</pre>")
        schedule_reboot(); return
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>설정 처리 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
  print(f"NexoPilot web: http://<device-ip>:{PORT}")
  ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
  main()
