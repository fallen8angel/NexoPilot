#!/usr/bin/env python3
import html
import json
import socket
import subprocess
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from cereal import car, messaging
from openpilot.common.params import Params

HOST = "0.0.0.0"
PORT = 7000
REPO_DIR = Path("/data/openpilot")
STATE_DIR = Path("/data/nexopilot")
FORCE_NEXO_FILE = STATE_DIR / "force_nexo"
BRANCH = "NEXO"
MAX_REQUEST_BODY = 64 * 1024
WEBRTCD_URL = "http://127.0.0.1:5001/stream"
WEB_CAMERA_MARKER = STATE_DIR / "web_camera_active"
WEB_CAMERA_TIMEOUT = 0
_camera_lock = threading.Lock()
_camera_deadline = 0.0

# Only expose keys provided by the 11.1 base build. NEXO radar tracks are a
# required vehicle capability and are enabled automatically in CarInterface.
TOGGLES = [
  ("AlphaLongitudinalEnabled", "오픈파일럿 롱컨", "가속과 감속을 오픈파일럿이 제어합니다."),
  ("OpenpilotEnabledToggle", "오픈파일럿 사용", "오픈파일럿 기능 전체를 켜거나 끕니다."),
  ("ExperimentalMode", "실험 모드", "실험용 종방향 주행 기능을 사용합니다."),
  ("IsMetric", "미터법 사용", "속도와 거리를 km/h 및 m 단위로 표시합니다."),
  ("IsLdwEnabled", "차선이탈 경고", "방향지시등 없이 차선을 벗어나면 경고를 표시합니다."),
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


def start_web_camera() -> None:
  global _camera_deadline
  with _camera_lock:
    if not WEB_CAMERA_MARKER.exists():
      params = Params()
      STATE_DIR.mkdir(parents=True, exist_ok=True)
      WEB_CAMERA_MARKER.write_text("1" if param_bool(params, "IsDriverViewEnabled") else "0", encoding="utf-8")
    Params().put_bool("IsDriverViewEnabled", True)
    _camera_deadline = time.monotonic() + WEB_CAMERA_TIMEOUT


def touch_web_camera() -> None:
  global _camera_deadline
  with _camera_lock:
    if WEB_CAMERA_MARKER.exists():
      _camera_deadline = time.monotonic() + WEB_CAMERA_TIMEOUT


def restore_web_camera() -> None:
  global _camera_deadline
  with _camera_lock:
    try:
      previous = WEB_CAMERA_MARKER.read_text(encoding="utf-8").strip() == "1"
    except (FileNotFoundError, OSError):
      _camera_deadline = 0.0
      return
    try:
      Params().put_bool("IsDriverViewEnabled", previous)
      WEB_CAMERA_MARKER.unlink(missing_ok=True)
    finally:
      _camera_deadline = 0.0


def camera_watchdog() -> None:
  while True:
    time.sleep(1.0)

    if WEB_CAMERA_TIMEOUT <= 0:
      continue

    with _camera_lock:
      expired = _camera_deadline > 0.0 and time.monotonic() > _camera_deadline

    if expired:
      restore_web_camera()


def proxy_webrtc_offer(payload: bytes) -> tuple[int, bytes]:
  request = Request(WEBRTCD_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
  try:
    with urlopen(request, timeout=20) as response:
      return response.status, response.read()
  except Exception as error:
    return HTTPStatus.BAD_GATEWAY, json.dumps({"error": f"영상 연결 실패: {error}"}).encode("utf-8")


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


def live_vehicle_output() -> str:
  lines = []
  try:
    sock = messaging.sub_sock("carState", conflate=True, timeout=1200)
    message = messaging.recv_one(sock)
    if message is None:
      return "carState 수신 없음"
    state = message.carState
    lines.extend([
      f"속도: {state.vEgo * 3.6:.1f} km/h",
      f"기어: {state.gearShifter}",
      f"브레이크: {state.brakePressed}",
      f"가속페달: {state.gasPressed}",
      f"ACC 고장: {state.accFaulted}",
      f"크루즈 사용 가능: {state.cruiseState.available}",
      f"크루즈 활성: {state.cruiseState.enabled}",
      f"크루즈 속도: {state.cruiseState.speed * 3.6:.1f} km/h",
    ])
  except Exception as error:
    lines.append(f"carState 읽기 실패: {error}")

  try:
    sock = messaging.sub_sock("selfdriveState", conflate=True, timeout=1200)
    message = messaging.recv_one(sock)
    if message is not None:
      state = message.selfdriveState
      lines.extend([
        f"주행 상태: {state.state}",
        f"시스템 활성/제어 중: {state.enabled}/{state.active}",
        f"활성 경고: {state.alertText1} {state.alertText2}".strip(),
      ])
  except Exception as error:
    lines.append(f"selfdriveState 읽기 실패: {error}")

  try:
    sock = messaging.sub_sock("radarState", conflate=True, timeout=1200)
    message = messaging.recv_one(sock)
    if message is not None:
      errors = message.radarState.radarErrors
      lines.append(
        f"레이더 오류: CAN={errors.canError}, 장치={errors.radarFault}, "
        f"설정={errors.wrongConfig}, 일시중지={errors.radarUnavailableTemporary}"
      )
  except Exception as error:
    lines.append(f"radarState 읽기 실패: {error}")
  return "\n".join(lines) or "차량 상태 수신 없음"


def raw_can_diagnostic_output() -> str:
  watched = {
    0x389: "SCC14", 0x38D: "FCA11", 0x420: "SCC11", 0x421: "SCC12",
    0x483: "FCA12", 0x4A2: "FRT_RADAR11", 0x50A: "SCC13",
  }
  counts = {}
  latest = {}
  track_counts = {}
  try:
    sock = messaging.sub_sock("can", timeout=300)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
      event = messaging.recv_one(sock)
      if event is None:
        continue
      for frame in event.can:
        address = int(frame.address)
        bus = int(frame.src)
        key = (bus, address)
        if address in watched:
          counts[key] = counts.get(key, 0) + 1
          latest[key] = bytes(frame.dat).hex(" ")
        elif 0x500 <= address <= 0x51F:
          track_counts[bus] = track_counts.get(bus, 0) + 1
          latest[key] = bytes(frame.dat).hex(" ")
  except Exception as error:
    return f"CAN 수집 실패: {error}"

  lines = ["1.5초 실측 CAN 수신 결과"]
  for (bus, address), count in sorted(counts.items()):
    lines.append(f"bus {bus} {watched[address]} 0x{address:03X}: {count}회 | {latest[(bus, address)]}")
  if track_counts:
    for bus, count in sorted(track_counts.items()):
      track_ids = sorted(address for (src, address) in latest if src == bus and 0x500 <= address <= 0x51F)
      lines.append(f"bus {bus} RADAR 0x500~0x51F: {count}회 | 고유 ID {len(track_ids)}개")
  else:
    lines.append("RADAR 0x500~0x51F: 수신 없음")
  if len(lines) == 2 and not counts:
    lines.append("SCC/FCA 감시 메시지 수신 없음")
  return "\n".join(lines)


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
  page = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 실시간 카메라</title><style>__CSS__
.camera-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.camera-tabs button{font-size:14px;padding:12px 6px;margin:0;background:#41495b}.camera-tabs button.active{background:#3159d9}.video-wrap{position:relative;aspect-ratio:16/9;background:#000;border-radius:18px;overflow:hidden}.video-wrap video{width:100%;height:100%;object-fit:contain}.camera-status{position:absolute;left:12px;bottom:10px;background:#000b;padding:7px 10px;border-radius:10px;font-size:13px}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>실시간 카메라</h1><div class="card"><div class="camera-tabs"><button data-camera="wideRoad">1 전방 광각</button><button class="active" data-camera="road">2 전방 일반</button><button data-camera="driver">3 실내·운전자</button></div><div class="video-wrap"><video id="cameraVideo" autoplay muted playsinline></video><div id="cameraStatus" class="camera-status">카메라 준비 중</div></div><p class="desc">차량 연결 없이도 콤마 전원과 Wi-Fi만 연결되어 있으면 볼 수 있습니다. 3번은 차량 뒤쪽이 아니라 콤마가 실내를 향해 보는 운전자 카메라입니다.</p></div></main><script>
let pc=null;
let selected="road";
let heartbeat=null;
const video=document.getElementById("cameraVideo");
const statusBox=document.getElementById("cameraStatus");

function waitForIce(connection){
  if(connection.iceGatheringState==="complete") return Promise.resolve();
  return new Promise(resolve=>{
    const check=()=>{
      if(connection.iceGatheringState==="complete"){
        connection.removeEventListener("icegatheringstatechange",check);
        resolve();
      }
    };
    connection.addEventListener("icegatheringstatechange",check);
  });
}

async function post(path,body){
  return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:body||"{}"});
}

async function connectCamera(camera){
  selected=camera;
  document.querySelectorAll("[data-camera]").forEach(button=>button.classList.toggle("active",button.dataset.camera===camera));
  statusBox.textContent="카메라 연결 중";
  if(pc){pc.close();pc=null;}
  video.srcObject=null;
  await post("/camera/start");
  await new Promise(resolve=>setTimeout(resolve,1800));
  const connection=new RTCPeerConnection({sdpSemantics:"unified-plan"});
  pc=connection;
  connection.addTransceiver("video",{direction:"recvonly"});
  connection.addEventListener("track",event=>{
    video.srcObject=event.streams[0];
    statusBox.textContent=document.querySelector("[data-camera].active").textContent;
  });
  connection.addEventListener("connectionstatechange",()=>{
    if(["failed","disconnected","closed"].includes(connection.connectionState)){
      statusBox.textContent="영상 연결이 끊겼습니다";
    }
  });
  const offer=await connection.createOffer();
  await connection.setLocalDescription(offer);
  await waitForIce(connection);
  const response=await post("/webrtc",JSON.stringify({
    sdp:connection.localDescription.sdp,
    cameras:[camera],
    bridge_services_in:[],
    bridge_services_out:[]
  }));
  const answer=await response.json();
  if(!response.ok) throw new Error(answer.error||"영상 연결 실패");
  await connection.setRemoteDescription(answer);
}

document.querySelectorAll("[data-camera]").forEach(button=>button.addEventListener("click",()=>{
  connectCamera(button.dataset.camera).catch(error=>statusBox.textContent=error.message);
}));

post("/camera/start").then(()=>{
  heartbeat=setInterval(()=>post("/camera/heartbeat"),5000);
  return connectCamera(selected);
}).catch(error=>statusBox.textContent=error.message);

window.addEventListener("pagehide",()=>{
  if(heartbeat) clearInterval(heartbeat);
  if(pc) pc.close();
  navigator.sendBeacon("/camera/stop","");
});
</script></body></html>'''
  return page.replace("__CSS__", base_css())


def settings_page(message: str = "") -> str:
  params = Params()
  forced = force_nexo_enabled()
  rows = []
  for key, title, desc in TOGGLES:
    checked = " checked" if param_bool(params, key) else ""
    rows.append(f'''<form method="post" action="/toggle"><input type="hidden" name="key" value="{key}"><div class="row"><div><div class="title">{title}</div><div class="desc">{desc}</div></div><label class="switch"><input type="checkbox"{checked} onchange="this.form.submit()"><span class="slider"></span></label></div></form>''')
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 차량 설정</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>차량 설정</h1>{msg}<div class="card"><div class="title">차량 선택</div><form method="post" action="/vehicle"><select name="vehicle"><option value="auto"{' selected' if not forced else ''}>자동 인식</option><option value="nexo"{' selected' if forced else ''}>현대 넥쏘 1세대</option></select><button>차량 선택 저장</button></form><p class="warning">P단에서만 설정을 변경할 수 있습니다.</p></div><div class="card"><div class="row"><div><div class="title">레이더 트랙</div><div class="desc">넥쏘에서는 AI 방식으로 자동 활성화됩니다. 사용자가 끌 수 없습니다.</div></div><span class="value">자동</span></div></div><div class="card"><h2>주행 기능</h2>{''.join(rows)}</div><div class="card"><div class="title">설정 적용</div><div class="desc">차량 선택과 주행 기능을 모두 설정한 뒤 아래 버튼을 한 번만 누르세요.</div><form method="post" action="/settings/reboot"><button>저장하고 재부팅</button></form></div></main></body></html>'''


def diagnostic_page(message: str = "") -> str:
  status = car_status()
  msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 진단</title><style>{base_css()}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>진단 도구</h1>{msg}<div class="card"><div class="row"><span>차량</span><span class="value">{html.escape(status['car'])}</span></div><div class="row"><span>롱컨</span><span class="value">{html.escape(status['longitudinal'])}</span></div><div class="row"><span>레이더</span><span class="value">{html.escape(status['radar'])}</span></div><div class="row"><span>레이더 모드</span><span class="value">자동 활성화</span></div></div><div class="card">
<h2>NEXO 실시간 차량 상태</h2><pre>{html.escape(live_vehicle_output())}</pre>
</div>
<div class="card"><h2>SCC·FCA·레이더 실제 CAN</h2><pre>{html.escape(raw_can_diagnostic_output())}</pre></div>
<div class="card"><h2>레이더·FCA 프로그램 로그</h2><pre>{html.escape(radar_diagnostic_output())}</pre></div><div class="card"><h2>tmux 로그</h2><pre>{html.escape(tmux_output())}</pre></div><div class="card"><h2>프로세스 검사</h2><pre>{html.escape(process_output())}</pre></div><div class="card"><h2>시스템 검사</h2><pre>{html.escape(system_output())}</pre></div></main></body></html>'''


class Handler(BaseHTTPRequestHandler):
  
  server_version = "NexoPilotWeb/6.1"

  def log_message(self, fmt: str, *args) -> None:
    print(f"NEXO web: {self.address_string()} - {fmt % args}")

  def _require_auth(self) -> bool:
    return True

  def _same_origin(self) -> bool:
    expected = self.headers.get("Host", "")
    origin = self.headers.get("Origin")
    if origin:
      return urlparse(origin).netloc == expected
    referer = self.headers.get("Referer")
    if referer:
      return urlparse(referer).netloc == expected

    # Some mobile browsers omit both headers for local HTTP forms. Accept only
    # requests the browser identifies as same-site (or legacy clients without
    # Fetch Metadata); explicit cross-site requests remain blocked.
    fetch_site = self.headers.get("Sec-Fetch-Site")
    return fetch_site in (None, "same-origin", "same-site", "none")

  def _send(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
    data = body.encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
    self.send_header("Referrer-Policy", "same-origin")
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("X-Frame-Options", "DENY")
    self.end_headers()
    self.wfile.write(data)

  def _send_json(self, data: bytes, status: int = HTTPStatus.OK) -> None:
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
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
      body = self.rfile.read(length)
      if self.path == "/camera/start":
        start_web_camera()
        self._send_json(b'{"ok":true}')
        return
      if self.path == "/camera/heartbeat":
        touch_web_camera()
        self._send_json(b'{"ok":true}')
        return
      if self.path == "/camera/stop":
        global _camera_deadline
        with _camera_lock:
          if _camera_deadline > 0.0:
            _camera_deadline = min(_camera_deadline, time.monotonic() + 2.0)
        self._send_json(b'{"ok":true}')
        return
      if self.path == "/webrtc":
        start_web_camera()
        status, answer = proxy_webrtc_offer(body)
        self._send_json(answer, status)
        return
      values = parse_qs(body.decode("utf-8"))
      if self.path == "/vehicle":
        if not self._require_parked("/settings"): return
        mode = values.get("vehicle", ["auto"])[0]
        if mode not in ("auto", "nexo"):
          self._send("잘못된 차량 선택", HTTPStatus.BAD_REQUEST); return
        set_vehicle(mode)
        self._redirect("차량 선택을 저장했습니다. 모든 설정을 마친 뒤 아래의 저장하고 재부팅 버튼을 눌러주세요.", "/settings")
        return
      if self.path == "/toggle":
        if not self._require_parked("/settings"): return
        key = values.get("key", [""])[0]
        if key not in {item[0] for item in TOGGLES}:
          self._send("허용되지 않은 설정", HTTPStatus.BAD_REQUEST); return
        params = Params()
        ok, error = put_param_bool(params, key, not param_bool(params, key))
        if not ok:
          self._redirect(f"이 설정은 현재 11.1 빌드에서 지원되지 않습니다: {error}", "/settings"); return
        self._redirect("설정을 저장했습니다. 다른 항목도 변경한 뒤 아래의 저장하고 재부팅 버튼을 눌러주세요.", "/settings")
        return
      if self.path == "/settings/reboot":
        if not self._require_parked("/settings"): return
        clear_car_cache()
        self._send("<h2>설정을 모두 저장했습니다. 재부팅합니다.</h2>")
        schedule_reboot()
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
  restore_web_camera()
  threading.Thread(target=camera_watchdog, daemon=True).start()
  print(f"NexoPilot web: http://<device-ip>:{PORT}")
  ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
  main()
