#!/usr/bin/env python3
import html
import os
import socket
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from cereal import car
from openpilot.common.params import Params

HOST = "0.0.0.0"
PORT = 7000
STATE_DIR = Path("/data/nexopilot")
FORCE_NEXO_FILE = STATE_DIR / "force_nexo"


def local_ip() -> str:
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    sock.connect(("8.8.8.8", 80))
    return sock.getsockname()[0]
  except OSError:
    return "확인 불가"
  finally:
    sock.close()


def git_value(*args: str) -> str:
  try:
    return subprocess.check_output(["git", *args], cwd="/data/openpilot", text=True, timeout=2).strip()
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


def set_force_nexo(enabled: bool) -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  FORCE_NEXO_FILE.write_text("1" if enabled else "0", encoding="utf-8")
  params = Params()
  for key in ("CarParams", "CarParamsCache", "CarParamsPersistent"):
    try:
      params.remove(key)
    except Exception:
      pass


class Handler(BaseHTTPRequestHandler):
  server_version = "NexoPilotWeb/1.0"

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

  def do_GET(self) -> None:
    if self.path not in ("/", "/index.html"):
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
      return

    status = car_status()
    forced = force_nexo_enabled()
    ip = local_ip()
    branch = git_value("branch", "--show-current")
    commit = git_value("log", "-1", "--oneline")
    force_label = "강제 NEXO 선택됨" if forced else "자동 인식 사용 중"
    force_value = "0" if forced else "1"
    button_label = "자동 인식으로 되돌리기" if forced else "NEXO 강제 선택"

    page = f"""<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>NexoPilot 설정</title>
<style>
body{{margin:0;background:#0b0d12;color:#f4f7ff;font-family:Arial,sans-serif}}main{{max-width:760px;margin:auto;padding:24px}}
h1{{font-size:30px;margin:0 0 8px}}.sub{{color:#9aa7bd;margin-bottom:22px}}.card{{background:#151a23;border:1px solid #273044;border-radius:16px;padding:18px;margin:12px 0}}
.row{{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #252d3d}}.row:last-child{{border:0}}.key{{color:#aeb9cc}}.value{{text-align:right;font-weight:700;word-break:break-all}}
button{{width:100%;padding:15px;border:0;border-radius:12px;background:#3159d9;color:white;font-size:17px;font-weight:700}}button.secondary{{background:#394354}}
.warning{{color:#ffcf70;font-size:14px;line-height:1.55}}a{{color:#87a8ff}}
</style></head><body><main>
<h1>NexoPilot</h1><div class=\"sub\">콤마4 로컬 설정 · http://{html.escape(ip)}:{PORT}</div>
<div class=\"card\"><div class=\"row\"><div class=\"key\">현재 차량</div><div class=\"value\">{html.escape(status['car'])}</div></div>
<div class=\"row\"><div class=\"key\">롱컨</div><div class=\"value\">{html.escape(status['longitudinal'])}</div></div>
<div class=\"row\"><div class=\"key\">레이더</div><div class=\"value\">{html.escape(status['radar'])}</div></div>
<div class=\"row\"><div class=\"key\">대시캠 전용</div><div class=\"value\">{html.escape(status['dashcam'])}</div></div></div>
<div class=\"card\"><div class=\"row\"><div class=\"key\">차량 선택 방식</div><div class=\"value\">{force_label}</div></div>
<form method=\"post\" action=\"/force-nexo\"><input type=\"hidden\" name=\"enabled\" value=\"{force_value}\"><button>{button_label}</button></form>
<p class=\"warning\">변경 후 콤마4를 재부팅해야 적용됩니다. NEXO 강제 선택은 자동 지문 인식을 건너뛰므로 넥쏘 차량과 정상 하네스에서만 사용하세요.</p></div>
<div class=\"card\"><div class=\"row\"><div class=\"key\">브랜치</div><div class=\"value\">{html.escape(branch)}</div></div><div class=\"row\"><div class=\"key\">커밋</div><div class=\"value\">{html.escape(commit)}</div></div></div>
<div class=\"card\"><button class=\"secondary\" onclick=\"location.reload()\">상태 새로고침</button></div>
</main></body></html>"""
    self._send(page)

  def do_POST(self) -> None:
    if self.path != "/force-nexo":
      self._send("찾을 수 없습니다", HTTPStatus.NOT_FOUND)
      return
    length = int(self.headers.get("Content-Length", "0"))
    values = parse_qs(self.rfile.read(length).decode("utf-8"))
    enabled = values.get("enabled", ["0"])[0] == "1"
    set_force_nexo(enabled)
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", "/")
    self.end_headers()


def main() -> None:
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  server = ThreadingHTTPServer((HOST, PORT), Handler)
  print(f"NexoPilot web listening on {HOST}:{PORT}")
  server.serve_forever()


if __name__ == "__main__":
  main()
