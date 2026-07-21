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
  """Read a Params value without relying on the removed encoding= argument."""
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


def parked_for_update() -> tuple[bool, str]:
  if not is_onroad():
    return True, "오프로드"
  try:
    sock = messaging.sub_sock("carState", conflate=True, timeout=1200)
    message = messaging.recv_one(sock)
    if message is None:
      return False, "기어 상태 확인 불가"
    gear = message.carState.gearShifter
    return gear == car.CarState.GearShifter.park, "P" if gear == car.CarState.GearShifter.park else str(gear)
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
    "dirty": bool(git_value("status", "--porcelain")),
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
  parked, gear = parked_for_update()
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
  for title, command in (("가동시간", ["uptime"]), ("디스크", ["df", "-h", "/data"]),
                         ("메모리", ["free", "-h"]), ("네트워크", ["ip", "-brief", "address"]),
                         ("Git 상태", ["git", "status", "--short", "--branch"])):
    _, output = run_command(command, timeout=5, cwd=REPO_DIR if command[0] == "git" else None)
    blocks.append(f"[{title}]\n{output}")
  temperatures = []
  for path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
    try:
      temperatures.append(f"{path.parent.name}: {float(path.read_text().strip()) / 1000.0:.1f} °C")
    except Exception:
      pass
  blocks.append("[온도]\n" + ("\n".join(temperatures) if temperatures else "확인 불가"))
  return "\n\n".join(blocks)


def base_css() -> str:
  return """
body{margin:0;background:#05070b;color:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif}main{max-width:820px;margin:auto;padding:22px}
a{color:#8eafff;text-decoration:none}.card{background:#151821;border:1px solid #2a3140;border-radius:22px;padding:18px;margin:14px 0}.row{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 2px;border-bottom:1px solid #292f3b}.row:last-child{border:0}.title{font-size:17px;font-weight:700}.desc{font-size:13px;color:#9ca5b5;margin-top:4px;line-height:1.4}.value{font-weight:700;text-align:right;word-break:break-all}button{width:100%;padding:15px;border:0;border-radius:14px;background:#3159d9;color:white;font-size:17px;font-weight:700;margin-top:10px}button.secondary{background:#41495b}button.danger{background:#ad4242}.message{background:#173b2a;border:1px solid #2d7750;padding:14px;border-radius:14px;margin:10px 0}.warning{color:#ffcf70;font-size:14px;line-height:1.55}select,input[type=number]{width:100%;box-sizing:border-box;padding:14px;border-radius:14px;background:#0e1118;color:white;border:1px solid #394154;font-size:17px}.switch{position:relative;display:inline-block;width:52px;height:31px;flex:0 0 auto}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;inset:0;background:#4b4f58;border-radius:31px;transition:.2s}.slider:before{content:'';position:absolute;width:27px;height:27px;left:2px;top:2px;background:white;border-radius:50%;transition:.2s;box-shadow:0 1px 4px #0008}.switch input:checked+.slider{background:#34c759}.switch input:checked+.slider:before{transform:translateX(21px)}pre{white-space:pre-wrap;word-break:break-word;background:#080b10;border-radius:12px;padding:14px;max-height:480px;overflow:auto}.number-grid{display:grid;grid-template-columns:1fr;gap:12px}.number-item{background:#0d1118;border-radius:16px;padding:14px}
"""


def live_page() -> str:
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 실시간 화면</title><style>{base_css()}
.camera{{width:100%;display:block;background:#000;border-radius:18px;aspect-ratio:16/10;object-fit:contain}}.live{{display:inline-flex;align-items:center;gap:7px;color:#71e28f;font-weight:700}}.dot{{width:9px;height:9px;background:#34c759;border-radius:50%;box-shadow:0 0 9px #34c759}}
</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>실시간 전방 화면</h1>
<div class="card"><div class="live"><span class="dot"></span>콤마4 카메라 연결</div><p class="desc">카메라 프레임이 준비되면 자동으로 표시됩니다.</p><img class="camera" src="/stream.mjpeg" alt="전방 카메라 실시간 화면"></div>
<div class="card"><p class="warning">같은 내부 네트워크에서만 사용하세요. 화면은 상태 확인용이며 실제 도로 상황은 반드시 운전자가 직접 확인해야 합니다.</p><button class="secondary" onclick="location.reload()">영상 다시 연결</button></div>
</main></body></html>'''


def stream_camera(handler: BaseHTTPRequestHandler) -> None:
  handler.send_response(HTTPStatus.OK)
  handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
  handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
  handler.send_header("Pragma", "no-cache")
  handler.send_header("Connection", "close")
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
      handler.wfile.write(b"--frame\r\n")
      handler.wfile.write(b"Content-Type: image/jpeg\r\n")
      handler.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
      handler.wfile.write(frame)
      handler.wfile.write(b"\r\n")
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
  number_rows = []
  for key, title, desc, hint, minimum, maximum, step, default in NUMERIC_SETTINGS:
    raw = param_text(params, key, default)
    number_rows.append(f'''<div class="number-item"><div class="title">{title}</div><div class="desc">{desc}</div><div class="desc">범위 {hint} · 권장값 {default}</div><form method="post" action="/value"><input type="hidden" name="key" value="{key}"><input type="number" name="value" value="{html.escape(raw)}" min="{minimum}" max="{maximum}" step="{step}"><button class="secondary" type="submit">저장</button></form></div>''')
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 차량 설정</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>차량 설정</h1>{msg}
<div class="card"><div class="title">차량 선택</div><div class="desc">자동 인식 또는 넥쏘 1세대 강제 선택을 설정합니다.</div><form method="post" action="/vehicle"><select name="vehicle"><option value="auto"{' selected' if not forced else ''}>자동 인식</option><option value="nexo"{' selected' if forced else ''}>현대 넥쏘 1세대</option></select><button type="submit">차량 저장 후 재부팅</button></form><p class="warning">넥쏘 강제 선택은 정상 하네스와 넥쏘 차량에서만 사용하세요.</p></div>
<div class="card"><h2>주행 기능</h2>{''.join(rows)}</div>
<div class="card"><h2>고급 수치 설정</h2><div class="number-grid">{''.join(number_rows)}</div><p class="warning">값을 한 번에 크게 바꾸지 말고 조금씩 조절한 뒤 실제 주행에서 확인하세요. 실제 제어 코드가 값을 읽는 항목만 주행에 반영됩니다.</p></div>
<div class="card"><p class="warning">롱컨과 레이더 설정은 정차 상태에서만 변경하고 변경 후 재부팅하세요.</p></div>
</main></body></html>'''


def diagnostic_page(message: str = "") -> str:
  status = car_status()
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 진단</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>진단 도구</h1>{msg}
<div class="card"><h2>차량 상태</h2><div class="desc">현재 차량과 롱컨 및 레이더 상태를 확인합니다.</div><div class="row"><span>차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>지문 출처</span><span class="value">{html.escape(status['fingerprint_source'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div></div>
<div class="card"><h2>tmux a 화면</h2><div class="desc">실시간 시스템 로그를 확인합니다.</div><pre>{html.escape(tmux_output())}</pre></div><div class="card"><h2>프로세스 검사</h2><div class="desc">주요 프로그램의 실행 상태를 확인합니다.</div><pre>{html.escape(process_output())}</pre></div><div class="card"><h2>시스템 검사</h2><div class="desc">온도 메모리 저장공간 네트워크 상태를 확인합니다.</div><pre>{html.escape(system_output())}</pre></div>
<div class="card"><button class="secondary" onclick="location.reload()">전체 진단 새로고침</button><form method="post" action="/clear-cache"><button class="danger">차량 인식 캐시 초기화 후 재부팅</button></form><form method="post" action="/reboot"><button class="danger">콤마4 재부팅</button></form></div></main></body></html>'''


class Handler(BaseHTTPRequestHandler):
  server_version = "NexoPilotWeb/5.6"

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

  def do_GET(self) -> None:
    try:
      parsed = urlparse(self.path)
      query = parse_qs(parsed.query)
      message = query.get("msg", [""])[0]
      if parsed.path == "/live":
        self._send(live_page())
        return
      if parsed.path == "/stream.mjpeg":
        stream_camera(self)
        return
      if parsed.path == "/settings":
        self._send(settings_page(message))
        return
      if parsed.path == "/diagnostics":
        self._send(diagnostic_page(message))
        return
      if parsed.path not in ("/", "/index.html"):
        self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
        return
      check = query.get("check", ["0"])[0] == "1"
      status = car_status()
      update = update_status(fetch=check)
      msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
      page = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot</title><style>{base_css()}</style></head><body><main><h1>NexoPilot</h1><div class="desc">콤마4 로컬 설정 · http://{html.escape(local_ip())}:{PORT}</div>{msg}
<div class="card"><div class="row"><span>현재 차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div></div>
<div class="card"><a href="/live"><button>실시간 전방 화면 열기</button></a><a href="/settings"><button class="secondary">차량 설정 열기</button></a><a href="/diagnostics"><button class="secondary">진단 도구 열기</button></a></div>
<div class="card"><h2>웹 업데이트</h2><div class="desc">최신 버전을 확인하고 설치한 뒤 재부팅합니다.</div><div class="row"><span>현재 버전</span><span class="value">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value">{html.escape(str(update['remote']))}</span></div><p class="warning">차량 전원이 켜져 있을 때는 반드시 P단에서만 업데이트됩니다.</p><form method="get"><input type="hidden" name="check" value="1"><button class="secondary">업데이트 확인</button></form><form method="post" action="/update"><button>업데이트 설치 후 재부팅</button></form></div>
<div class="card"><div class="row"><span>브랜치</span><span class="value">{html.escape(git_value('branch','--show-current'))}</span></div><div class="row"><span>커밋</span><span class="value">{html.escape(git_value('log','-1','--oneline'))}</span></div></div></main></body></html>'''
      self._send(page)
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>페이지 처리 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)

  def do_POST(self) -> None:
    try:
      length = int(self.headers.get("Content-Length", "0"))
      values = parse_qs(self.rfile.read(length).decode("utf-8"))
      if self.path == "/vehicle":
        if is_onroad():
          self._redirect("주행 중에는 차량 설정을 바꿀 수 없습니다.", "/settings")
          return
        mode = values.get("vehicle", ["auto"])[0]
        if mode not in ("auto", "nexo"):
          self._send("잘못된 차량 선택", HTTPStatus.BAD_REQUEST)
          return
        set_vehicle(mode)
        self._send("<h2>차량 설정 저장 완료. 재부팅합니다.</h2>")
        schedule_reboot()
        return
      if self.path == "/toggle":
        if is_onroad():
          self._redirect("주행 중에는 설정을 바꿀 수 없습니다.", "/settings")
          return
        key = values.get("key", [""])[0]
        allowed = {item[0] for item in TOGGLES}
        if key not in allowed:
          self._send("허용되지 않은 설정", HTTPStatus.BAD_REQUEST)
          return
        params = Params()
        params.put_bool(key, not param_bool(params, key))
        reboot = values.get("reboot", ["0"])[0] == "1"
        if reboot:
          clear_car_cache()
          self._send("<h2>설정을 저장했습니다. 재부팅합니다.</h2>")
          schedule_reboot()
        else:
          self._redirect("설정을 저장했습니다.", "/settings")
        return
      if self.path == "/value":
        if is_onroad():
          self._redirect("주행 중에는 수치 설정을 바꿀 수 없습니다.", "/settings")
          return
        key = values.get("key", [""])[0]
        raw_value = values.get("value", [""])[0].strip()
        spec = next((item for item in NUMERIC_SETTINGS if item[0] == key), None)
        if spec is None:
          self._send("허용되지 않은 수치 설정", HTTPStatus.BAD_REQUEST)
          return
        try:
          number = float(raw_value)
        except ValueError:
          self._redirect("숫자를 정확히 입력하세요.", "/settings")
          return
        minimum, maximum = spec[4], spec[5]
        if not minimum <= number <= maximum:
          self._redirect(f"허용 범위는 {minimum}~{maximum}입니다.", "/settings")
          return
        Params().put(key, raw_value)
        self._redirect(f"{spec[1]} 값을 저장했습니다.", "/settings")
        return
      if self.path == "/update":
        ok, result = perform_update()
        if not ok:
          self._redirect(result)
          return
        self._send(f"<h2>업데이트 완료</h2><pre>{html.escape(result)}</pre>")
        schedule_reboot()
        return
      if self.path == "/clear-cache":
        if is_onroad():
          self._redirect("주행 중에는 캐시를 지울 수 없습니다.", "/diagnostics")
          return
        clear_car_cache()
        self._send("<h2>캐시를 초기화했습니다. 재부팅합니다.</h2>")
        schedule_reboot()
        return
      if self.path == "/reboot":
        if is_onroad():
          self._redirect("주행 중에는 재부팅할 수 없습니다.", "/diagnostics")
          return
        self._send("<h2>콤마4를 재부팅합니다.</h2>")
        schedule_reboot()
        return
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
    except Exception as error:
      traceback.print_exc()
      self._send(f"<h2>설정 처리 오류</h2><pre>{html.escape(str(error))}</pre>", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
  main()
