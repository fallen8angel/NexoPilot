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


def annotate_blackbox(output: str) -> str:
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
