#!/usr/bin/env python3
import html
import socket
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from cereal import car
from openpilot.common.params import Params

HOST = "0.0.0.0"
PORT = 7000
REPO_DIR = Path("/data/openpilot")
STATE_DIR = Path("/data/nexopilot")
FORCE_NEXO_FILE = STATE_DIR / "force_nexo"
BRANCH = "NEXO"


def local_ip() -> str:
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    sock.connect(("8.8.8.8", 80))
    return sock.getsockname()[0]
  except OSError:
    return "확인 불가"
  finally:
    sock.close()


def git_run(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
  return subprocess.run(["git", *args], cwd=REPO_DIR, text=True, capture_output=True,
                        timeout=timeout, check=False)


def git_value(*args: str) -> str:
  try:
    result = git_run(*args, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else "확인 불가"
  except Exception:
    return "확인 불가"


def car_status() -> dict[str, str]:
  params = Params()
  raw = params.get("CarParams")
  result = {
    "car": "아직 인식되지 않음",
    "longitudinal": "확인 불가",
    "radar": "확인 불가",
    "dashcam": "확인 불가",
  }
  if raw:
    try:
      with car.CarParams.from_bytes(raw) as cp:
        result["car"] = str(cp.carFingerprint)
        result["longitudinal"] = "활성" if cp.openpilotLongitudinalControl else "비활성"
        result["radar"] = "사용 불가" if cp.radarUnavailable else "사용 가능"
        result["dashcam"] = "예" if cp.dashcamOnly else "아니오"
    except Exception as error:
      result["car"] = f"CarParams 읽기 실패: {error}"
  return result


def force_nexo_enabled() -> bool:
  try:
    return FORCE_NEXO_FILE.read_text(encoding="utf-8").strip() == "1"
  except FileNotFoundError:
    return False


def clear_car_cache() -> None:
  params = Params()
  for key in ("CarParams", "CarParamsCache", "CarParamsPersistent"):
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
  return params.get_bool("IsOnroad") and not params.get_bool("IsOffroad")


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
    try:
      fetched = git_run("fetch", "origin", BRANCH, timeout=60)
      if fetched.returncode != 0:
        result["error"] = fetched.stderr.strip() or fetched.stdout.strip() or "업데이트 확인 실패"
        return result
      current = git_value("rev-parse", "HEAD")
      remote = git_value("rev-parse", f"origin/{BRANCH}")
      result["current"] = current[:9]
      result["remote"] = remote[:9]
      result["available"] = current != remote
    except Exception as error:
      result["error"] = str(error)
  return result


def perform_update() -> tuple[bool, str]:
  if is_onroad():
    return False, "주행 중에는 업데이트할 수 없습니다. 시동을 끄고 다시 시도하세요."

  dirty = git_run("status", "--porcelain")
  if dirty.returncode != 0:
    return False, "저장소 상태를 확인하지 못했습니다."
  if dirty.stdout.strip():
    return False, "로컬 변경 파일이 있어 업데이트를 중단했습니다. SSH에서 git status를 확인하세요."

  fetched = git_run("fetch", "origin", BRANCH, timeout=60)
  if fetched.returncode != 0:
    return False, fetched.stderr.strip() or "git fetch 실패"

  merged = git_run("merge", "--ff-only", f"origin/{BRANCH}", timeout=60)
  if merged.returncode != 0:
    return False, merged.stderr.strip() or merged.stdout.strip() or "업데이트 적용 실패"

  return True, merged.stdout.strip() or "최신 버전입니다."


class Handler(BaseHTTPRequestHandler):
  server_version = "NexoPilotWeb/2.0"

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

  def _redirect(self, message: str) -> None:
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", f"/?msg={quote(message)}")
    self.end_headers()

  def do_GET(self) -> None:
    parsed = urlparse(self.path)
    if parsed.path not in ("/", "/index.html"):
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
      return

    query = parse_qs(parsed.query)
    message = query.get("msg", [""])[0]
    check_update = query.get("check", ["0"])[0] == "1"
    status = car_status()
    forced = force_nexo_enabled()
    update = update_status(fetch=check_update)
    ip = local_ip()
    branch = git_value("branch", "--show-current")
    commit = git_value("log", "-1", "--oneline")
    selected_auto = "" if forced else " selected"
    selected_nexo = " selected" if forced else ""
    onroad_text = "주행 중" if is_onroad() else "정차 상태"
    update_text = "업데이트 있음" if update["available"] else ("최신 버전" if check_update and not update["error"] else "확인 전")
    message_html = f'<div class="message">{html.escape(message)}</div>' if message else ""
    error_html = f'<p class="error">{html.escape(str(update["error"]))}</p>' if update["error"] else ""

    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NexoPilot 설정</title>
<style>
body{{margin:0;background:#0b0d12;color:#f4f7ff;font-family:Arial,sans-serif}}main{{max-width:760px;margin:auto;padding:24px}}
h1{{font-size:30px;margin:0 0 8px}}.sub{{color:#9aa7bd;margin-bottom:22px}}.card{{background:#151a23;border:1px solid #273044;border-radius:16px;padding:18px;margin:12px 0}}
.row{{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #252d3d}}.row:last-child{{border:0}}.key{{color:#aeb9cc}}.value{{text-align:right;font-weight:700;word-break:break-all}}
button{{width:100%;padding:15px;border:0;border-radius:12px;background:#3159d9;color:white;font-size:17px;font-weight:700;margin-top:10px}}button.secondary{{background:#394354}}button.danger{{background:#a83a3a}}
select{{width:100%;padding:14px;border-radius:12px;background:#0f131b;color:white;border:1px solid #364158;font-size:17px}}.warning{{color:#ffcf70;font-size:14px;line-height:1.55}}.error{{color:#ff8585}}.message{{background:#173b2a;border:1px solid #2d7750;padding:14px;border-radius:12px;margin-bottom:12px;white-space:pre-wrap}}a{{color:#87a8ff}}
</style></head><body><main>
<h1>NexoPilot</h1><div class="sub">콤마4 로컬 설정 · http://{html.escape(ip)}:{PORT}</div>
{message_html}
<div class="card"><div class="row"><div class="key">현재 차량</div><div class="value">{html.escape(status['car'])}</div></div>
<div class="row"><div class="key">롱컨</div><div class="value">{html.escape(status['longitudinal'])}</div></div>
<div class="row"><div class="key">레이더</div><div class="value">{html.escape(status['radar'])}</div></div>
<div class="row"><div class="key">대시캠 전용</div><div class="value">{html.escape(status['dashcam'])}</div></div>
<div class="row"><div class="key">장치 상태</div><div class="value">{onroad_text}</div></div></div>

<div class="card"><h2>차량 선택</h2>
<form method="post" action="/vehicle">
<select name="vehicle"><option value="auto"{selected_auto}>자동 인식</option><option value="nexo"{selected_nexo}>HYUNDAI NEXO</option></select>
<button type="submit">차량 저장 후 재부팅</button></form>
<p class="warning">NexoPilot은 넥쏘 전용입니다. NEXO 강제 선택은 자동 지문 인식을 건너뛰므로 정상 하네스와 넥쏘 차량에서만 사용하세요.</p></div>

<div class="card"><h2>웹 업데이트</h2>
<div class="row"><div class="key">현재 버전</div><div class="value">{html.escape(str(update['current']))}</div></div>
<div class="row"><div class="key">원격 버전</div><div class="value">{html.escape(str(update['remote']))}</div></div>
<div class="row"><div class="key">업데이트 상태</div><div class="value">{update_text}</div></div>
<div class="row"><div class="key">로컬 변경</div><div class="value">{'있음' if update['dirty'] else '없음'}</div></div>
{error_html}
<form method="get" action="/"><input type="hidden" name="check" value="1"><button class="secondary" type="submit">업데이트 확인</button></form>
<form method="post" action="/update" onsubmit="return confirm('업데이트 후 콤마4를 재부팅합니다. 계속할까요?')"><button type="submit">업데이트 설치 후 재부팅</button></form>
<p class="warning">차량이 정차된 상태에서만 사용하세요. 로컬 변경 파일이 있으면 안전을 위해 업데이트하지 않습니다.</p></div>

<div class="card"><div class="row"><div class="key">브랜치</div><div class="value">{html.escape(branch)}</div></div><div class="row"><div class="key">커밋</div><div class="value">{html.escape(commit)}</div></div></div>
<div class="card"><button class="secondary" onclick="location.reload()">상태 새로고침</button></div>
</main></body></html>"""
    self._send(page)

  def do_POST(self) -> None:
    length = int(self.headers.get("Content-Length", "0"))
    values = parse_qs(self.rfile.read(length).decode("utf-8"))

    if self.path == "/vehicle":
      if is_onroad():
        self._redirect("주행 중에는 차량 설정을 바꿀 수 없습니다.")
        return
      mode = values.get("vehicle", ["auto"])[0]
      if mode not in ("auto", "nexo"):
        self._send("잘못된 차량 선택", HTTPStatus.BAD_REQUEST)
        return
      set_vehicle(mode)
      self._send("<html><body style='background:#0b0d12;color:white;font-family:Arial;padding:30px'><h2>차량 설정을 저장했습니다.</h2><p>콤마4가 재부팅됩니다.</p></body></html>")
      schedule_reboot()
      return

    if self.path == "/update":
      ok, result = perform_update()
      if not ok:
        self._redirect(result)
        return
      self._send(f"<html><body style='background:#0b0d12;color:white;font-family:Arial;padding:30px'><h2>업데이트 완료</h2><pre>{html.escape(result)}</pre><p>콤마4가 재부팅됩니다.</p></body></html>")
      schedule_reboot()
      return

    self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)


def main() -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  server = ThreadingHTTPServer((HOST, PORT), Handler)
  print(f"NexoPilot web listening on {HOST}:{PORT}")
  server.serve_forever()


if __name__ == "__main__":
  main()
