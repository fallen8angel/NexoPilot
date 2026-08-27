import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from cereal import messaging


REAL_ERROR_PATTERN = re.compile(
  r"(unknown signal|traceback|exception|fatal|mismatch|bus off|relay malfunction|"
  r"not valid \(timeout or missing\)|commissue|cruise fault|"
  r"radar[^\n]*(?:fault|error|unavailable)|safety[^\n]*(?:invalid|fault|violation)|"
  r"(?:^|[^a-z])error(?:[^a-z]|$))",
  re.IGNORECASE,
)
PROCESS_LIST_ONLY = re.compile(
  r"^(?:\[?[a-z0-9_.-]+\]?\s+)*(?:radard|hardwared|modem|tombstoned|feedbackd|webrtcd)"
  r"(?:\s+(?:radard|hardwared|modem|tombstoned|feedbackd|webrtcd))*$",
  re.IGNORECASE,
)

MODEM_STATE = Path("/dev/shm/modem")
TAILSCALE_DIR = Path("/data/tailscale")
TAILSCALE_BIN = TAILSCALE_DIR / "tailscale"
TAILSCALE_SOCKET = TAILSCALE_DIR / "tailscaled.sock"


def important_log_output(tmux_output) -> str:
  raw = tmux_output()
  matches = Counter()
  samples = {}
  for raw_line in raw.splitlines():
    line = re.sub(r"\s+", " ", raw_line.strip())
    if not line or PROCESS_LIST_ONLY.fullmatch(line) or not REAL_ERROR_PATTERN.search(line):
      continue
    lowered = line.lower().replace(" ", "")
    if ('"error":false' in lowered or "error=false" in lowered) and not any(
        token in lowered for token in ("traceback", "exception", "fatal", "fault", "notvalid", "commissue")):
      continue
    key = re.sub(r"^\d\d:\d\d:\d\d(?:\.\d+)?\s+", "", line)
    key = re.sub(r"\b((?:count|attempt)\s*[=:]?)\s*\d+\b", r"\1 <N>", key, flags=re.IGNORECASE)
    matches[key] += 1
    samples.setdefault(key, line)

  if not matches:
    return "최근 로그에서 실제 롱컨·레이더·안전 오류를 찾지 못했습니다."

  lines = ["실제 오류만 표시하며 중복 로그는 한 줄로 합쳤습니다."]
  for key, count in matches.most_common(80):
    suffix = f"  (반복 {count}회)" if count > 1 else ""
    lines.append(f"{samples[key]}{suffix}")
  return "\n".join(lines)


def control_state() -> tuple[bool, bool]:
  try:
    sm = messaging.SubMaster(["selfdriveState", "pandaStates"])
    sm.update(500)
    active = bool(sm["selfdriveState"].active)
    pandas = sm["pandaStates"]
    allowed = bool(pandas[0].controlsAllowed) if len(pandas) else False
    return active, allowed
  except Exception:
    return False, False


def annotate_raw_can(output: str) -> str:
  active, allowed = control_state()
  label = "안전 차단" if active or allowed else "비활성 중 예상 차단"
  output = output.replace("안전 차단", label) if label != "안전 차단" else output
  lines = output.splitlines()
  insert_at = 1 if lines else 0
  lines[insert_at:insert_at] = [
    f"제어 상태: active={active}, controlsAllowed={allowed}",
    "※ P단 또는 크루즈 비활성 상태의 Panda 차단은 정상일 수 있습니다.",
    "※ active=True 또는 controlsAllowed=True인 동안 차단이 계속될 때만 실제 안전 문제입니다.",
  ]
  return "\n".join(lines)


def _sendcan_request_totals(output: str) -> tuple[int, int]:
  """Count only real sendcan requests, not Panda CAN echo/accepted metadata."""
  scc_requests = 0
  fca_requests = 0
  for match in re.finditer(
      r"^(SCC11|SCC12|SCC13|SCC14|FCA11|FCA12|FRT_RADAR11)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
      output,
      re.MULTILINE,
  ):
    name = match.group(1)
    requested = int(match.group(2))
    if name.startswith("SCC"):
      scc_requests += requested
    elif name.startswith("FCA"):
      fca_requests += requested
  return scc_requests, fca_requests


def _metric(output: str, pattern: str) -> int:
  match = re.search(pattern, output)
  return int(match.group(1)) if match else 0


def _correct_tx_echo_verdict(output: str) -> str:
  """Do not mistake Panda accepted/echo frames for openpilot control requests.

  src=128~191 can be present even when the sendcan subscriber observed zero
  SCC/FCA requests. The request stream is therefore the authoritative signal
  for whether openpilot actually attempted SCC/FCA control in this diagnostic.
  """
  scc_requests, fca_requests = _sendcan_request_totals(output)
  stock_scc = _metric(output, r"순정 SCC:\s*(\d+)")

  output = re.sub(
    r"openpilot SCC:\s*(\d+)",
    lambda match: f"openpilot SCC: {scc_requests} | Panda 반환 echo SCC: {match.group(1)}",
    output,
    count=1,
  )
  output = re.sub(
    r"openpilot FCA:\s*(\d+)",
    lambda match: f"openpilot FCA: {fca_requests} | Panda 반환 echo FCA: {match.group(1)}",
    output,
    count=1,
  )

  if stock_scc and scc_requests:
    verdict = "위험: 물리 source0 순정 SCC와 실제 sendcan SCC 요청이 동시에 관측됐습니다."
  elif scc_requests:
    verdict = "정상 후보: 물리 source0 순정 SCC 없이 실제 sendcan SCC 요청이 관측됐습니다."
  elif stock_scc:
    verdict = "정상: 물리 source0 순정 SCC만 관측됐고 실제 sendcan SCC 요청은 0회입니다."
  else:
    verdict = "정보: 물리 source0 순정 SCC와 실제 sendcan SCC 요청을 모두 관측하지 못했습니다."

  output = re.sub(r"(?m)^판정: .*?$", f"판정: {verdict}", output, count=1)
  output = output.replace(
    "※ src=128~191은 Panda가 돌려준 openpilot TX echo/accepted이며 물리 bus=src-128입니다.",
    "※ src=128~191은 Panda가 돌려준 accepted/echo 메타데이터입니다. 실제 openpilot SCC/FCA 송신 여부는 sendcan 요청을 우선합니다.",
  )
  output = output.replace(
    "메시지        요청     성공     차단   source0수신",
    "메시지        요청  Panda반환     차단   source0수신",
  )
  return output


def _run_readonly(command: list[str], timeout: float = 1.5) -> tuple[int, str]:
  try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode, output[:12000]
  except (OSError, subprocess.SubprocessError) as error:
    return -1, str(error)


def _modem_snapshot() -> dict[str, object]:
  safe_keys = (
    "state", "connected", "ip_address", "mcc_mnc", "signal_strength", "signal_quality",
    "network_type", "operator", "band", "channel", "registration", "extra", "tx_bytes", "rx_bytes",
  )
  try:
    raw = json.loads(MODEM_STATE.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(raw, dict):
      return {"available": False, "error": "invalid modem state"}
    return {"available": True, **{key: raw.get(key) for key in safe_keys}}
  except Exception as error:
    return {"available": False, "error": str(error)}


def _remote_access_report() -> str:
  reasons: list[str] = []
  warnings: list[str] = []
  modem = _modem_snapshot()

  ppp0 = Path("/sys/class/net/ppp0").exists()
  tailscale0 = Path("/sys/class/net/tailscale0").exists()
  wlan0 = Path("/sys/class/net/wlan0").exists()
  socket_exists = TAILSCALE_SOCKET.exists()

  proc_code, proc_out = _run_readonly(["pgrep", "-a", "tailscaled"], timeout=0.8)
  tailscaled_running = proc_code == 0 and bool(proc_out)

  ip_code, ip_out = _run_readonly(["ip", "-br", "addr"], timeout=0.8)
  route_code, route_out = _run_readonly(["ip", "route"], timeout=0.8)
  rule_code, rule_out = _run_readonly(["ip", "rule"], timeout=0.8)
  listen_code, listen_out = _run_readonly(["ss", "-lnt"], timeout=0.8)

  ts_ip = ""
  if tailscale0:
    ts_code, ts_out = _run_readonly(["ip", "-4", "-o", "addr", "show", "dev", "tailscale0"], timeout=0.8)
    if ts_code == 0:
      match = re.search(r"\binet\s+(100\.\d+\.\d+\.\d+)/", ts_out)
      if match:
        ts_ip = match.group(1)

  web7000_listening = listen_code == 0 and bool(re.search(r"(?m)\b(?:0\.0\.0\.0|\*|\[::\]):7000\b", listen_out))
  ssh22_listening = listen_code == 0 and bool(re.search(r"(?m)\b(?:0\.0\.0\.0|\*|\[::\]):22\b", listen_out))

  lte_ping_ok = False
  if ppp0:
    ping_code, _ = _run_readonly(["ping", "-I", "ppp0", "-c", "1", "-W", "1", "1.1.1.1"], timeout=1.8)
    lte_ping_ok = ping_code == 0

  tailscale_cli_ok = False
  if tailscaled_running and socket_exists and TAILSCALE_BIN.is_file():
    ts_status_code, _ = _run_readonly(
      [str(TAILSCALE_BIN), f"--socket={TAILSCALE_SOCKET}", "status"],
      timeout=1.2,
    )
    tailscale_cli_ok = ts_status_code == 0

  firewall_code, firewall_out = _run_readonly(
    ["sudo", "-n", "iptables-legacy", "-S", "INPUT"],
    timeout=1.0,
  )
  input_drop = firewall_code == 0 and "-P INPUT DROP" in firewall_out
  tailscale_accept = firewall_code == 0 and any(
    "tailscale0" in line and "-j ACCEPT" in line for line in firewall_out.splitlines()
  )

  if not modem.get("available"):
    reasons.append("modem_state_missing")
  elif modem.get("connected") is not True:
    reasons.append("modem_not_connected")

  if not ppp0:
    reasons.append("ppp0_missing")
  elif not lte_ping_ok:
    reasons.append("lte_internet_failed")

  if not tailscaled_running:
    reasons.append("tailscaled_not_running")
    warnings.append("재부팅 뒤 tailscaled 자동 실행이 안 된 가능성이 큽니다.")
  if not socket_exists:
    reasons.append("tailscale_socket_missing")
  if not tailscale0:
    reasons.append("tailscale0_missing")
  if tailscale0 and not ts_ip:
    reasons.append("tailscale_ip_missing")
  if tailscaled_running and socket_exists and TAILSCALE_BIN.is_file() and not tailscale_cli_ok:
    reasons.append("tailscale_control_unreachable")

  if not web7000_listening:
    reasons.append("web7000_not_listening")
  if not ssh22_listening:
    reasons.append("ssh22_not_listening")
  if input_drop and not tailscale_accept:
    reasons.append("firewall_blocks_tailscale")

  ready = not reasons
  if ready:
    verdict = "정상: LTE·Tailscale·7000/SSH 원격 접속 조건이 모두 확인됐습니다."
  elif "tailscaled_not_running" in reasons:
    verdict = "원격 접속 불가: Tailscale 데몬이 실행 중이 아닙니다. 부팅 자동 실행 여부를 우선 확인하세요."
  elif "ppp0_missing" in reasons or "modem_not_connected" in reasons:
    verdict = "원격 접속 불가: 차량 LTE 데이터 연결이 올라오지 않았습니다."
  elif "web7000_not_listening" in reasons:
    verdict = "원격 접속 불가: NexoPilot 7000 서버가 열려 있지 않습니다."
  elif "firewall_blocks_tailscale" in reasons:
    verdict = "원격 접속 불가: INPUT 방화벽이 tailscale0 트래픽을 허용하지 않습니다."
  else:
    verdict = "원격 접속 불가 후보가 감지됐습니다. 아래 원인 코드와 네트워크 상태를 확인하세요."

  modem_summary = (
    f"state={modem.get('state')} connected={modem.get('connected')} "
    f"registration={modem.get('registration')} network={modem.get('network_type')} "
    f"operator={modem.get('operator')} signal={modem.get('signal_strength')}/{modem.get('signal_quality')} "
    f"ip={modem.get('ip_address')}"
    if modem.get("available") else f"읽기 실패: {modem.get('error', 'unknown')}"
  )

  lines = [
    "",
    "============================================================",
    "원격 접속·LTE·Tailscale 진단",
    "============================================================",
    f"REMOTE_READY: {'YES' if ready else 'NO'}",
    f"판정: {verdict}",
    f"원인 코드: {', '.join(reasons) if reasons else '없음'}",
    *(f"주의: {warning}" for warning in warnings),
    "",
    f"모뎀: {modem_summary}",
    f"인터페이스: ppp0={ppp0} tailscale0={tailscale0} wlan0={wlan0}",
    f"Tailscale: process={tailscaled_running} socket={socket_exists} cli={tailscale_cli_ok} ip={ts_ip or '없음'}",
    f"서비스 포트: 7000={web7000_listening} 22={ssh22_listening}",
    f"LTE 인터넷 ping(1.1.1.1): {'OK' if lte_ping_ok else 'FAIL/미실행'}",
    f"방화벽: legacy조회={'OK' if firewall_code == 0 else 'FAIL'} INPUT_DROP={input_drop} tailscale0_ACCEPT={tailscale_accept}",
    "",
    "[ip -br addr]",
    ip_out if ip_code == 0 else f"실패: {ip_out}",
    "",
    "[ip route]",
    route_out if route_code == 0 else f"실패: {route_out}",
    "",
    "[ip rule]",
    rule_out if rule_code == 0 else f"실패: {rule_out}",
    "",
    "※ ICCID·IMEI·Tailscale 인증 URL·계정 토큰은 진단 파일에 기록하지 않습니다.",
    "※ 이 원격 접속 검사는 읽기 전용이며 네트워크·방화벽 설정을 변경하지 않습니다.",
  ]
  return "\n".join(lines)


def annotate_blackbox(output: str) -> str:
  output = _correct_tx_echo_verdict(output)
  active = bool(re.search(r"selfdrive=[^\n]*/True(?:/|\s)", output))
  allowed = "controlsAllowed=True" in output
  verdict = (
    "차단 판정: 실제 제어 중 Panda 차단이 관측됐습니다. 안전 조건과 송신 메시지를 확인하세요."
    if active or allowed else
    "차단 판정: 크루즈 비활성 중 차단입니다. 현재 상태에서는 정상적인 Panda 차단일 수 있습니다."
  )
  marker = "Panda 차단 SCC/FCA 프레임:"
  lines = output.splitlines()
  for index, line in enumerate(lines):
    if line.startswith(marker):
      lines.insert(index + 1, verdict)
      break
  return "\n".join(lines) + "\n" + _remote_access_report() + "\n"
