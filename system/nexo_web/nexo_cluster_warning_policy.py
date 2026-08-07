from __future__ import annotations


_EXPECTED_PARK_EVENTS = (
  "onroadEvent wrongGear:",
  "onroadEvent seatbeltNotLatched:",
  "onroadEvent parkBrake:",
  "onroadEvent wrongCarMode:",
  "onroadEvent pcmDisable:",
)


def _is_stationary_park(report: str) -> bool:
  return ("gear=park" in report or "기어: park" in report) and (
    "speed=0.0km/h" in report or '"vEgoKph": 0.0' in report
  )


def correct_stationary_cluster_warning(report: str) -> str:
  """Downgrade expected P/standstill entry-block events in the warning section.

  wrongGear, seatbeltNotLatched, parkBrake, wrongCarMode and pcmDisable can all
  appear while a parked driver runs diagnostics with cruise/main control off.
  They can block engagement, but in this P/0 km/h context they are not evidence
  of an SCC/FCA/MDPS/ADAS cluster fault. This function only rewrites diagnostic
  text and never changes vehicle state, Params, Panda safety, or CAN traffic.
  """
  if not _is_stationary_park(report):
    return report

  section_end = report.find("\n============================================================\nNEXO runtime guard")
  if section_end < 0:
    return report

  section = report[:section_end]
  remainder = report[section_end:]
  lines = section.splitlines()

  removed: list[str] = []
  kept_critical = False
  caution_present = False
  output: list[str] = []

  for line in lines:
    stripped = line.strip()
    if stripped.startswith("- 치명: ") and any(token in stripped for token in _EXPECTED_PARK_EVENTS):
      removed.append(stripped.removeprefix("- 치명: "))
      continue
    if stripped.startswith("- 주의: ") and any(token in stripped for token in _EXPECTED_PARK_EVENTS):
      removed.append(stripped.removeprefix("- 주의: "))
      continue
    if stripped.startswith("- 치명: "):
      kept_critical = True
    if stripped.startswith("- 주의: "):
      caution_present = True
    output.append(line)

  if not removed:
    return report

  for index, line in enumerate(output):
    if line.startswith("판정: "):
      if kept_critical:
        pass
      elif caution_present:
        output[index] = "판정: [주의] 계기판 경고와 관련될 수 있는 신호가 감지됐습니다."
      else:
        output[index] = "판정: [원인 미검출] P단 정지 정상 조건을 제외하면 CAN에서 ADAS 경고 원인을 찾지 못했습니다."
      break

  # Remove the old generic no-candidate line before inserting a single corrected one.
  output = [line for line in output if line != "- CAN에서 원인 후보를 찾지 못했습니다."]
  try:
    can_index = output.index("[경고 관련 CAN 스냅샷 - 8초 수집 직후 0.7초]")
  except ValueError:
    can_index = len(output)

  insertion = [
    *(["- CAN에서 원인 후보를 찾지 못했습니다."] if not kept_critical and not caution_present else []),
    "",
    "[P단 정지에서 정상으로 제외한 항목]",
    *(f"- 정보: {item}" for item in removed),
    "- 설명: P단·정지·크루즈 비활성 진단에서는 기어·안전벨트·주차브레이크·크루즈 메인 OFF 관련 진입 차단이 정상입니다.",
    "",
  ]
  output[can_index:can_index] = insertion

  return "\n".join(output) + remainder
