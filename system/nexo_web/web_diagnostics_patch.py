import re
from collections import Counter

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
  return "\n".join(lines)
