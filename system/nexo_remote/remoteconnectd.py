#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from openpilot.common.swaglog import cloudlog


REPO_DIR = Path(__file__).resolve().parents[2]
TAILSCALE_HELPER = REPO_DIR / "scripts" / "ensure_tailscale.sh"
NTFY_TOPIC_FILE = Path("/data/nexopilot/ntfy_topic")
POLL_INTERVAL = 2.0
RECOVERY_INTERVAL = 30.0
FAILURE_NOTIFY_DELAY = 60.0


def ntfy_topic() -> str:
  try:
    return NTFY_TOPIC_FILE.read_text(encoding="utf-8").strip()
  except (FileNotFoundError, OSError, UnicodeError):
    return ""


def internet_available() -> bool:
  try:
    with socket.create_connection(("1.1.1.1", 443), timeout=2.0):
      return True
  except OSError:
    return False


def local_port_ready(port: int) -> bool:
  try:
    with socket.create_connection(("127.0.0.1", port), timeout=1.0):
      return True
  except OSError:
    return False


def tailscale_ipv4() -> str:
  ip_bin = shutil.which("ip")
  if ip_bin is None:
    for candidate in ("/usr/sbin/ip", "/usr/bin/ip", "/sbin/ip", "/bin/ip"):
      if Path(candidate).is_file():
        ip_bin = candidate
        break
  if ip_bin is None:
    return ""

  try:
    result = subprocess.run(
      [ip_bin, "-4", "-o", "addr", "show", "dev", "tailscale0"],
      text=True,
      capture_output=True,
      timeout=3,
      check=False,
    )
  except Exception:
    return ""

  if result.returncode != 0:
    return ""
  match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", result.stdout)
  return match.group(1) if match else ""


def ensure_tailscale() -> None:
  if not TAILSCALE_HELPER.is_file():
    return
  try:
    result = subprocess.run(
      ["sudo", "-n", "bash", str(TAILSCALE_HELPER)],
      text=True,
      capture_output=True,
      timeout=20,
      check=False,
    )
    if result.returncode not in (0, 10):
      detail = (result.stderr or result.stdout).strip()
      cloudlog.warning(f"NEXO remote: Tailscale recovery rc={result.returncode}: {detail[-500:]}")
  except Exception as error:
    cloudlog.warning(f"NEXO remote: Tailscale recovery failed: {error}")


def send_ntfy(message: str, *, priority: str = "default") -> bool:
  topic = ntfy_topic()
  if not topic:
    return False

  url = f"https://ntfy.sh/{quote(topic, safe='')}"
  request = Request(
    url,
    data=message.encode("utf-8"),
    headers={
      "Title": "NexoPilot",
      "Priority": priority,
      "Tags": "car",
      "User-Agent": "NexoPilot-remoteconnectd/1",
      "Content-Type": "text/plain; charset=utf-8",
    },
    method="POST",
  )
  try:
    with urlopen(request, timeout=10) as response:
      response.read(256)
    return True
  except Exception as error:
    cloudlog.warning(f"NEXO remote: ntfy send failed: {error}")
    return False


def main() -> None:
  started_at = time.monotonic()
  success_notified = False
  failure_notified = False
  last_recovery = 0.0
  last_internet_check = 0.0
  internet = False

  cloudlog.info("NEXO remote: startup connectivity monitor started")

  while True:
    now = time.monotonic()

    if now - last_internet_check >= 5.0:
      internet = internet_available()
      last_internet_check = now

    ts_ip = tailscale_ipv4()
    if not ts_ip and now - last_recovery >= RECOVERY_INTERVAL:
      ensure_tailscale()
      last_recovery = now
      ts_ip = tailscale_ipv4()

    web_ready = local_port_ready(7000)

    # Notify once per remoteconnectd start as soon as the comma has internet,
    # Tailscale and the NexoPilot web server ready. This deliberately does not
    # depend on IsOnroad so a parked/just-booted comma can announce readiness.
    if internet and ts_ip and web_ready and not success_notified:
      message = (
        "🚗 콤마 온라인\n"
        "NexoPilot 원격접속 준비 완료\n"
        f"Tailscale: {ts_ip}\n"
        f"7000 서버: http://{ts_ip}:7000"
      )
      if send_ntfy(message):
        success_notified = True
        cloudlog.info(f"NEXO remote: startup online notification sent ({ts_ip})")

    # If internet works but Tailscale or port 7000 is still not ready after a
    # minute, send one diagnostic warning. If internet itself is down, ntfy
    # cannot be reached, so keep waiting and send the success notice later.
    if (internet and not success_notified and not failure_notified and
        now - started_at >= FAILURE_NOTIFY_DELAY and (not ts_ip or not web_ready)):
      if not ts_ip and not web_ready:
        reason = "Tailscale과 7000 서버가 아직 준비되지 않았습니다."
      elif not ts_ip:
        reason = "Tailscale 연결에 실패했습니다. LTE 인터넷은 연결되어 있습니다."
      else:
        reason = "Tailscale은 연결됐지만 7000 서버가 아직 준비되지 않았습니다."

      if send_ntfy(f"⚠️ 콤마는 온라인이지만 원격접속 준비 실패\n{reason}", priority="high"):
        failure_notified = True
        cloudlog.warning(f"NEXO remote: startup readiness warning sent, tailscale={bool(ts_ip)} web={web_ready}")

    time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
  main()
