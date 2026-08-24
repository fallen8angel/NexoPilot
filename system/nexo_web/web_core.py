#!/usr/bin/env python3
import html
import json
import re
import socket
import subprocess
import threading
import time
import traceback
from collections import Counter
from datetime import datetime
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
NEXO_LONG_INIT_LOG = Path("/data/nexo_long_init.log")
BRANCH = "NEXO"
MAX_REQUEST_BODY = 64 * 1024
WEBRTCD_URL = "http://127.0.0.1:5001/stream"
WEB_CAMERA_MARKER = STATE_DIR / "web_camera_active"
WEB_CAMERA_TIMEOUT = 0
_camera_lock = threading.Lock()
_camera_deadline = 0.0
_model_lock = threading.Lock()
_model_snapshot: dict[str, object] = {"ready": False}
_model_deadline = 0.0

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


def nexo_raw_can_parked(timeout: float = 1.5, required_samples: int = 3) -> bool:
  """Confirm NEXO Park directly from live EMS20 when card is unavailable."""
  sock = messaging.sub_sock("can", conflate=False, timeout=250)
  deadline = time.monotonic() + timeout
  parked_samples = 0

  while time.monotonic() < deadline:
    packet = messaging.recv_one(sock)
    if packet is None:
      continue

    for frame in packet.can:
      if frame.src != 0 or frame.address != 0x200 or len(frame.dat) < 2:
        continue

      # EMS20.HYDROGEN_GEAR_SHIFTER: little-endian bits 11..13.
      # Learned NEXO values: P=0, D=5, N=6, R=7.
      raw_gear = (int.from_bytes(frame.dat, "little") >> 11) & 0x7
      if raw_gear != 0:
        return False

      parked_samples += 1
      if parked_samples >= required_samples:
        return True

  return False


def parked_state() -> tuple[bool, str]:
  if not is_onroad():
    return True, "오프로드"
  try:
    sock = messaging.sub_sock("carState", conflate=True, timeout=1500)
    message = messaging.recv_one(sock)
    if message is None:
      if nexo_raw_can_parked():
        return True, "P (원시 CAN)"
      return False, "기어 상태 확인 불가"
    gear = message.carState.gearShifter
    if gear == car.CarState.GearShifter.park:
      return True, "P"
    return False, str(gear)
  except Exception as error:
    try:
      if nexo_raw_can_parked():
        return True, "P (원시 CAN)"
    except Exception:
      pass
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


def model_monitor() -> None:
  """Cache lightweight model geometry for the optional browser overlay."""
  global _model_snapshot
  while True:
    with _model_lock:
      active = time.monotonic() < _model_deadline
    if not active:
      time.sleep(0.5)
      continue
    try:
      sm = messaging.SubMaster(["modelV2", "carState", "selfdriveState"], poll="modelV2")
      while time.monotonic() < _model_deadline:
        sm.update(500)
        if not sm.updated["modelV2"]:
          continue

        model = sm["modelV2"]
        snapshot: dict[str, object] = {
          "ready": True,
          "monoTime": int(sm.logMonoTime["modelV2"]),
          "laneLines": [
            {"x": list(line.x), "y": list(line.y)}
            for line in model.laneLines
          ],
          "laneLineProbs": list(model.laneLineProbs),
          "roadEdges": [
            {"x": list(edge.x), "y": list(edge.y)}
            for edge in model.roadEdges
          ],
          "roadEdgeStds": list(model.roadEdgeStds),
          "path": {"x": list(model.position.x), "y": list(model.position.y)},
          "speedKph": round(float(sm["carState"].vEgo) * 3.6, 1),
          "enabled": bool(sm["selfdriveState"].enabled),
        }
        with _model_lock:
          _model_snapshot = snapshot
    except Exception as error:
      with _model_lock:
        _model_snapshot = {"ready": False, "error": str(error)}
      time.sleep(1.0)


def model_snapshot_json() -> bytes:
  global _model_deadline
  with _model_lock:
    _model_deadline = time.monotonic() + 3.0
    snapshot = dict(_model_snapshot)
  return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


_update_status_lock = threading.Lock()
_update_last_error = ""
_update_last_check = 0.0


def update_status(fetch: bool = False) -> dict[str, str | bool | float]:
  global _update_last_error, _update_last_check
  if fetch:
    fetched = git_run("fetch", "origin", BRANCH, timeout=60)
    with _update_status_lock:
      _update_last_check = time.time()
      _update_last_error = "" if fetched.returncode == 0 else (fetched.stderr.strip() or fetched.stdout.strip() or "업데이트 확인 실패")

  current = git_value("rev-parse", "HEAD")
  remote = git_value("rev-parse", f"origin/{BRANCH}")
  with _update_status_lock:
    error = _update_last_error
    checked_at = _update_last_check
  remote_known = remote != "확인 불가"
  return {
    "current": current[:9],
    "remote": remote[:9] if remote_known else "확인 중",
    "available": bool(remote_known and current != remote),
    "error": error,
    "checkedAt": checked_at,
    "checking": checked_at == 0.0,
  }


def update_status_json() -> bytes:
  return json.dumps(update_status(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def update_monitor(interval: float = 300.0) -> None:
  """Check origin/NEXO immediately, then refresh the cached ref every five minutes."""
  while True:
    try:
      update_status(fetch=True)
    except Exception as error:
      global _update_last_error, _update_last_check
      with _update_status_lock:
        _update_last_error = f"업데이트 자동 확인 실패: {error}"
        _update_last_check = time.time()
    time.sleep(interval)


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


IMPORTANT_LOG_PATTERN = re.compile(
  r"(unknown signal|traceback|exception|fatal|error|fault|mismatch|bus off|"
  r"controlsallowed|cruise|radar|scc|fca|aeb|fcw|disable[_ ]ecu|safety)",
  re.IGNORECASE,
)


def important_log_output() -> str:
  """Return a compact, de-duplicated summary instead of flooding the page."""
  raw = tmux_output()
  matches = Counter()
  samples = {}
  for raw_line in raw.splitlines():
    line = re.sub(r"\s+", " ", raw_line.strip())
    if not line or not IMPORTANT_LOG_PATTERN.search(line):
      continue
    # Runtime timestamps and counters make otherwise identical errors unique.
    key = re.sub(r"^\d\d:\d\d:\d\d(?:\.\d+)?\s+", "", line)
    key = re.sub(r"\b((?:count|attempt)\s*[=:]?)\s*\d+\b", r"\1 <N>", key, flags=re.IGNORECASE)
    matches[key] += 1
    samples.setdefault(key, line)

  if not matches:
    return "최근 로그에서 롱컨·레이더·안전 관련 오류를 찾지 못했습니다."

  lines = ["중복 로그는 한 줄로 합쳤습니다."]
  for key, count in matches.most_common(80):
    suffix = f"  (반복 {count}회)" if count > 1 else ""
    lines.append(f"{samples[key]}{suffix}")
  return "\n".join(lines)


def nexo_long_init_output() -> str:
  try:
    output = NEXO_LONG_INIT_LOG.read_text(encoding="utf-8", errors="replace")
  except OSError:
    return "아직 롱컨 초기화 기록이 없습니다. 재부팅 후 다시 확인해 주세요."
  return output[-12000:] or "롱컨 초기화 기록이 비어 있습니다."


def longitudinal_blackbox_output(duration: float = 8.0) -> str:
  """Capture a read-only time line around a NEXO longitudinal fault."""
  started = time.monotonic()
  wall_time = datetime.now().astimezone().isoformat(timespec="seconds")
  services = ("carState", "selfdriveState", "pandaStates", "radarState")
  sm = messaging.SubMaster(list(services))
  can_sock = messaging.sub_sock("can", timeout=20)
  watched = {0x389: "SCC14", 0x38D: "FCA11", 0x420: "SCC11", 0x421: "SCC12",
             0x483: "FCA12", 0x4A2: "FRT_RADAR11", 0x50A: "SCC13"}
  can_counts: Counter[tuple[int, int]] = Counter()
  can_latest: dict[tuple[int, int], str] = {}
  timeline = []
  previous = None
  previous_error = None
  first_acc_fault_at = None

  while time.monotonic() - started < duration:
    sm.update(50)
    try:
      event = messaging.recv_one_or_none(can_sock)
      if event is not None:
        for frame in event.can:
          address, source = int(frame.address), int(frame.src)
          if address in watched or 0x500 <= address <= 0x51F:
            can_counts[(source, address)] += 1
            can_latest[(source, address)] = bytes(frame.dat).hex(" ")
    except Exception:
      pass

    try:
      cs = sm["carState"]
      ss = sm["selfdriveState"]
      pandas = sm["pandaStates"]
      radar = sm["radarState"]
      panda = pandas[0] if len(pandas) else None
      errors = radar.radarErrors
      snapshot = (
        str(cs.gearShifter), bool(cs.brakePressed), bool(cs.gasPressed), bool(cs.accFaulted),
        bool(cs.cruiseState.available), bool(cs.cruiseState.enabled),
        str(ss.state), bool(ss.enabled), bool(ss.active), str(ss.alertText1), str(ss.alertText2),
        bool(panda.controlsAllowed) if panda is not None else None,
        int(panda.safetyParam) if panda is not None else None,
        bool(panda.safetyRxChecksInvalid) if panda is not None else None,
        bool(errors.canError), bool(errors.radarFault), bool(errors.wrongConfig),
        bool(errors.radarUnavailableTemporary),
      )
      if snapshot != previous:
        elapsed = time.monotonic() - started
        if snapshot[3] and (previous is None or not previous[3]) and first_acc_fault_at is None:
          first_acc_fault_at = elapsed
        timeline.append(
          f"{elapsed:6.2f}s gear={snapshot[0]} brake={snapshot[1]} gas={snapshot[2]} "
          f"accFault={snapshot[3]} cruise={snapshot[4]}/{snapshot[5]} "
          f"selfdrive={snapshot[6]}/{snapshot[7]}/{snapshot[8]} "
          f"controlsAllowed={snapshot[11]} safetyParam={snapshot[12]} "
          f"rxInvalid={snapshot[13]} radarErrors={snapshot[14:18]} "
          f"alert={snapshot[9]!r} {snapshot[10]!r}"
        )
        previous = snapshot
        previous_error = None
    except Exception as error:
      error_text = str(error)
      if error_text != previous_error:
        timeline.append(f"{time.monotonic() - started:6.2f}s 상태 읽기 실패: {error_text}")
        previous_error = error_text
    time.sleep(0.02)

  scc_addresses = {0x389, 0x38D, 0x420, 0x421, 0x483, 0x50A}
  stock_scc_count = sum(count for (source, address), count in can_counts.items()
                        if source < 128 and address in scc_addresses)
  openpilot_scc_count = sum(count for (source, address), count in can_counts.items()
                            if 128 <= source < 192 and address in scc_addresses)
  blocked_scc_count = sum(count for (source, address), count in can_counts.items()
                          if source >= 192 and address in scc_addresses)
  radar_track_count = sum(count for (_, address), count in can_counts.items()
                          if 0x500 <= address <= 0x51F and address != 0x50A)
  if stock_scc_count and openpilot_scc_count:
    overlap_verdict = "순정 SCC와 openpilot SCC가 동시에 관측됨: 순정 SCC 통신 억제 실패 또는 중복 제어 가능성이 큽니다."
  elif openpilot_scc_count:
    overlap_verdict = "openpilot SCC만 관측됨: 순정 SCC 통신 억제는 정상으로 보입니다."
  elif stock_scc_count:
    overlap_verdict = "순정 SCC만 관측됨: openpilot 종방향 송신이 시작되지 않았습니다."
  else:
    overlap_verdict = "SCC 송신을 관측하지 못했습니다."

  lines = [
    "NexoPilot NEXO 롱컨 블랙박스",
    f"수집 시각: {wall_time}",
    f"Git: {git_value('rev-parse', '--short', 'HEAD')}",
    f"수집 시간: {duration:.1f}초",
    "",
    "[상태 변화]",
    *(timeline or ["상태 메시지를 수신하지 못했습니다."]),
    "",
    "[NEXO 롱컨 자동 판정]",
    f"첫 accFault 전환: {first_acc_fault_at:.2f}초" if first_acc_fault_at is not None else "첫 accFault 전환: 관측되지 않음",
    f"순정 SCC/FCA 프레임: {stock_scc_count}",
    f"openpilot SCC/FCA 프레임: {openpilot_scc_count}",
    f"Panda 차단 SCC/FCA 프레임: {blocked_scc_count}",
    f"레이더 트랙 프레임: {radar_track_count}",
    f"판정: {overlap_verdict}",
    "",
    "[SCC/FCA/레이더 CAN 집계]",
  ]
  for (source, address), count in sorted(can_counts.items()):
    name = watched.get(address, "RADAR_TRACK")
    lines.append(f"src={source:3d} {name:12s} 0x{address:03X} {count:5d}회 | {can_latest[(source, address)]}")
  if not can_counts:
    lines.append("감시 대상 CAN 메시지를 수신하지 못했습니다.")
  lines.extend(["", "[롱컨 초기화 추적]", nexo_long_init_output()])
  lines.extend(["", "[핵심 오류 로그]", important_log_output()])
  return "\n".join(lines)


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

  try:
    sock = messaging.sub_sock("pandaStates", conflate=True, timeout=1200)
    message = messaging.recv_one(sock)
    if message is not None and len(message.pandaStates) > 0:
      panda = message.pandaStates[0]
      safety_param = int(panda.safetyParam)
      expected_param = 4 | 256
      lines.extend([
        "",
        "[판다 안전 설정]",
        f"안전 모델: {panda.safetyModel}",
        f"안전 파라미터: {safety_param} (넥쏘 롱컨 예상값: {expected_param})",
        f"롱컨 안전 허용 LONG(4): {bool(safety_param & 4)}",
        f"넥쏘 FCEV 페달 FCEV_GAS(256): {bool(safety_param & 256)}",
        f"안전 파라미터 정상: {(safety_param & expected_param) == expected_param}",
        f"제어 허용: {panda.controlsAllowed}",
        f"안전 RX 검사 오류: {panda.safetyRxChecksInvalid}",
      ])
  except Exception as error:
    lines.append(f"pandaStates 읽기 실패: {error}")
  return "\n".join(lines) or "차량 상태 수신 없음"


def can_source_info(source: int) -> tuple[str, int]:
  if source >= 192:
    return "안전 차단", source - 192
  if source >= 128:
    return "송신 성공", source - 128
  return "차량 수신", source


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

  status_totals = {"차량 수신": 0, "송신 성공": 0, "안전 차단": 0}
  for (source, _), count in counts.items():
    status, _ = can_source_info(source)
    status_totals[status] += count

  lines = [
    "1.5초 실제 CAN 수집 결과",
    (
      f"SCC/FCA 합계: 차량 수신 {status_totals['차량 수신']}회 | "
      f"송신 성공 {status_totals['송신 성공']}회 | 안전 차단 {status_totals['안전 차단']}회"
    ),
    "※ src 128~135는 송신 성공, src 192~199는 판다 안전 차단 표시입니다.",
  ]
  for (bus, address), count in sorted(counts.items()):
    status, physical_bus = can_source_info(bus)
    lines.append(
      f"{status} (물리 bus {physical_bus}, src {bus}) "
      f"{watched[address]} 0x{address:03X}: {count}회 | {latest[(bus, address)]}"
    )
  if track_counts:
    for bus, count in sorted(track_counts.items()):
      track_ids = sorted(address for (src, address) in latest if src == bus and 0x500 <= address <= 0x51F)
      status, physical_bus = can_source_info(bus)
      lines.append(
        f"{status} (물리 bus {physical_bus}, src {bus}) "
        f"RADAR 0x500~0x51F: {count}회 | 고유 ID {len(track_ids)}개"
      )
  else:
    lines.append("RADAR 0x500~0x51F: 수신 없음")
  if not counts:
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
.camera-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.camera-tabs button{font-size:14px;padding:12px 6px;margin:0;background:#41495b}.camera-tabs button.active{background:#3159d9}.video-wrap{position:relative;aspect-ratio:16/9;background:#000;border-radius:18px;overflow:hidden}.video-wrap video,.video-wrap canvas{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}.video-wrap canvas{pointer-events:none}.camera-status{position:absolute;left:12px;bottom:10px;background:#000b;padding:7px 10px;border-radius:10px;font-size:13px}.overlay-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}.legend{font-size:12px;color:#aeb7c8}.legend b{margin-right:10px}</style></head><body><main><p><a href="/">← 메인 화면</a></p><h1>실시간 콤마 화면</h1><div class="card"><div class="camera-tabs"><button data-camera="wideRoad">1 전방 광각</button><button class="active" data-camera="road">2 전방 일반</button><button data-camera="driver">3 실내·운전자</button></div><div class="video-wrap"><video id="cameraVideo" autoplay muted playsinline></video><canvas id="modelOverlay"></canvas><div id="cameraStatus" class="camera-status">카메라 준비 중</div></div><div class="overlay-row"><label><input id="showModel" type="checkbox" checked> 차선·주행경로 표시</label><span id="modelStatus" class="legend">모델 대기 중</span></div><div class="legend"><b style="color:#56e39f">■ 주행경로</b><b style="color:#fff">━ 차선</b><b style="color:#ff4d67">━ 도로 경계</b></div><p class="desc">콤마 주행 모델이 인식한 차선, 도로 경계와 예상 주행경로를 실시간으로 겹쳐 표시합니다. 웹 화면은 진단용 근사 투영이므로 실제 조향 가능 여부나 안전 판단 기준으로 사용하지 마세요. 3번은 실내를 향한 운전자 카메라입니다.</p></div></main><script>
let pc=null;
let selected="road";
let heartbeat=null;
let modelTimer=null;
const video=document.getElementById("cameraVideo");
const statusBox=document.getElementById("cameraStatus");
const modelStatus=document.getElementById("modelStatus");
const overlay=document.getElementById("modelOverlay");
const ctx=overlay.getContext("2d");

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

function projectPoint(x,y,width,height){
  const depth=Math.max(0,Math.min(1,x/100));
  const sy=height*(.96-Math.sqrt(depth)*.52);
  const lateralScale=width*(.13+.42*(1-depth));
  return [width*.5-y*lateralScale/4,sy];
}

function drawLine(line,color,width){
  const count=Math.min(line.x.length,line.y.length);
  if(count<2) return;
  ctx.beginPath();
  for(let i=0;i<count;i++){
    const point=projectPoint(line.x[i],line.y[i],overlay.width,overlay.height);
    if(i===0) ctx.moveTo(point[0],point[1]); else ctx.lineTo(point[0],point[1]);
  }
  ctx.strokeStyle=color;
  ctx.lineWidth=width;
  ctx.lineCap="round";
  ctx.stroke();
}

async function updateModel(){
  if(!document.getElementById("showModel").checked||selected==="driver"){
    ctx.clearRect(0,0,overlay.width,overlay.height);
    modelStatus.textContent=selected==="driver"?"전방 카메라에서만 표시":"표시 꺼짐";
    return;
  }
  try{
    const response=await fetch("/api/model",{cache:"no-store"});
    const data=await response.json();
    overlay.width=overlay.clientWidth*window.devicePixelRatio;
    overlay.height=overlay.clientHeight*window.devicePixelRatio;
    ctx.clearRect(0,0,overlay.width,overlay.height);
    if(!data.ready){modelStatus.textContent="모델 데이터 대기 중";return;}
    (data.roadEdges||[]).forEach(edge=>drawLine(edge,"rgba(255,77,103,.85)",3*window.devicePixelRatio));
    (data.laneLines||[]).forEach((line,index)=>{
      const probability=(data.laneLineProbs||[])[index]||0;
      drawLine(line,`rgba(255,255,255,${Math.max(.12,probability)})`,3*window.devicePixelRatio);
    });
    drawLine(data.path||{x:[],y:[]},"rgba(86,227,159,.95)",6*window.devicePixelRatio);
    modelStatus.textContent=`${data.speedKph||0} km/h · ${data.enabled?"제어 중":"대기"} · 차선확률 ${(data.laneLineProbs||[]).map(v=>Math.round(v*100)).join("/")}%`;
  }catch(error){
    modelStatus.textContent="모델 데이터 연결 실패";
  }
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
  modelTimer=setInterval(updateModel,200);
  return connectCamera(selected);
}).catch(error=>statusBox.textContent=error.message);

window.addEventListener("pagehide",()=>{
  if(heartbeat) clearInterval(heartbeat);
  if(modelTimer) clearInterval(modelTimer);
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
<div class="card"><h2>롱컨 블랙박스</h2><p class="desc">버튼을 누른 뒤 8초 동안 크루즈·Panda 안전 상태와 SCC/FCA/레이더 CAN을 시간순으로 기록합니다. 읽기 전용이며 차량 제어에는 관여하지 않습니다.</p><form method="post" action="/diagnostics/capture"><button>8초 진단 파일 받기</button></form></div>
<div class="card"><h2>롱컨 초기화 추적</h2><p class="desc">disable ECU 요청, 레이더 트랙 활성화, 최종 재차단 단계를 보여줍니다. 요청 성공만으로 순정 SCC 정지가 보장되지는 않으므로 실제 정지 여부는 블랙박스 자동 판정으로 확인합니다.</p><pre>{html.escape(nexo_long_init_output())}</pre></div>
<div class="card"><h2>핵심 오류 요약</h2><pre>{html.escape(important_log_output())}</pre></div>
<div class="card"><h2>SCC·FCA·레이더 실제 CAN</h2><pre>{html.escape(raw_can_diagnostic_output())}</pre></div>
<div class="card"><h2>핵심 프로세스 상태</h2><pre>{html.escape(process_output())}</pre></div></main></body></html>'''


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

  def _send_download(self, text: str, filename: str) -> None:
    data = text.encode("utf-8")
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/plain; charset=utf-8")
    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
      if parsed.path == "/api/model":
        self._send_json(model_snapshot_json()); return
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
      if self.path == "/diagnostics/capture":
        capture = longitudinal_blackbox_output()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._send_download(capture, f"nexo-long-{stamp}.txt")
        return
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
  threading.Thread(target=model_monitor, daemon=True).start()
  print(f"NexoPilot web: http://<device-ip>:{PORT}")
  ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
  main()
