from __future__ import annotations

import html
import json
import time
from pathlib import Path

from cereal import car, messaging
from openpilot.common.params import Params


PANDA_FW_STATUS = Path("/data/nexopilot/panda_fw/status.json")
PANDA_FW_READY = Path("/data/nexopilot/panda_fw/ready.json")
REVERSE_DRIVER_CAMERA_SETTING = Path("/data/nexopilot/reverse_driver_camera")


def reverse_driver_camera_enabled() -> bool:
  try:
    return REVERSE_DRIVER_CAMERA_SETTING.read_text(encoding="utf-8").strip() == "1"
  except (OSError, UnicodeError):
    return False


def set_reverse_driver_camera(enabled: bool) -> None:
  REVERSE_DRIVER_CAMERA_SETTING.parent.mkdir(parents=True, exist_ok=True)
  REVERSE_DRIVER_CAMERA_SETTING.write_text("1" if enabled else "0", encoding="utf-8")

# Carrot's current settings panel exposes these standard openpilot controls.
# NexoPilot maps the longitudinal-alpha item to its existing
# AlphaLongitudinalEnabled key and only exposes keys registered by this fork.
TOGGLE_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
  ("주행", (
    ("OpenpilotEnabledToggle", "오픈파일럿 사용", "차선 유지와 지원되는 주행 보조 기능을 사용합니다."),
    ("AlphaLongitudinalEnabled", "오픈파일럿 롱컨 (Alpha)", "넥쏘의 가속·감속 제어권을 오픈파일럿으로 전환합니다. 변경 후 재부팅이 필요합니다."),
    ("ExperimentalMode", "실험 모드", "롱컨이 활성화된 경우 실험형 종방향 모델 기능을 사용합니다."),
    ("DisengageOnAccelerator", "가속페달로 오픈파일럿 해제", "가속페달을 밟으면 오픈파일럿을 해제합니다."),
  )),
  ("안전·표시", (
    ("IsLdwEnabled", "차선이탈 경고", "방향지시등 없이 차선을 벗어날 때 차선이탈 경고를 사용합니다."),
    ("IsMetric", "미터법 사용", "속도와 거리를 km/h 및 m 단위로 표시합니다."),
    ("ShowDebugInfo", "디버그 정보 표시", "주행 화면에 지원되는 디버그 정보를 표시합니다."),
  )),
  ("운전자 감시·카메라", (
    ("AlwaysOnDM", "운전자 감시 항상 분석", "크루즈가 꺼져 있어도 운전자 영상 분석 프로세스를 유지합니다. NexoPilot의 경고 타이머는 실제 크루즈 활성 + D/L 조건을 계속 따릅니다."),
    ("RecordFront", "운전자 카메라 기록", "운전자 방향 카메라 기록 기능을 사용합니다."),
  )),
)

TOGGLES = tuple(item for _, group in TOGGLE_GROUPS for item in group)
TOGGLE_KEYS = {item[0] for item in TOGGLES}
PERSONALITIES = ((0, "공격적"), (1, "표준"), (2, "여유"))


def _param_text(params: Params, key: str, default: str = "") -> str:
  try:
    value = params.get(key)
    if value is None:
      return default
    if isinstance(value, bytes):
      return value.decode("utf-8", errors="replace")
    return str(value)
  except Exception:
    return default


def _enum_name(value) -> str:
  text = str(value)
  return text.rsplit(".", 1)[-1] if text else "확인 불가"


def _fault_names(panda) -> list[str]:
  try:
    names = {_enum_name(fault) for fault in panda.faults}
  except Exception:
    return []
  return sorted(name for name in names if name not in ("none", "0", "확인 불가"))


def _firmware_status() -> dict[str, object]:
  status: dict[str, object] = {"state": "missing", "ready": False}
  try:
    raw = json.loads(PANDA_FW_STATUS.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
      status.update(raw)
  except Exception:
    pass
  status["ready"] = PANDA_FW_READY.is_file() and status.get("state") == "ready"
  return status


def live_status(core) -> dict[str, object]:
  params = Params()
  result: dict[str, object] = {
    "timestamp": time.time(),
    "car": core.car_status(),
    "alphaLongitudinalEnabled": core.param_bool(params, "AlphaLongitudinalEnabled"),
    "experimentalMode": core.param_bool(params, "ExperimentalMode"),
    "openpilotEnabled": core.param_bool(params, "OpenpilotEnabledToggle"),
    "git": {
      "branch": core.git_value("branch", "--show-current"),
      "commit": core.git_value("rev-parse", "--short", "HEAD"),
      "dirty": bool(core.git_value("status", "--porcelain").strip()),
    },
    "firmware": _firmware_status(),
    "carState": {"seen": False},
    "selfdriveState": {"seen": False},
    "panda": {"seen": False},
    "radar": {"seen": False},
  }

  try:
    sm = messaging.SubMaster(["carState", "selfdriveState", "pandaStates", "radarState"])
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline and not all(sm.seen.values()):
      sm.update(50)

    if sm.seen["carState"]:
      cs = sm["carState"]
      result["carState"] = {
        "seen": True,
        "gear": _enum_name(cs.gearShifter),
        "speedKph": round(float(cs.vEgo) * 3.6, 1),
        "brakePressed": bool(cs.brakePressed),
        "gasPressed": bool(cs.gasPressed),
        "parkingBrake": bool(getattr(cs, "parkingBrake", False)),
        "accFaulted": bool(cs.accFaulted),
        "cruiseAvailable": bool(cs.cruiseState.available),
        "cruiseEnabled": bool(cs.cruiseState.enabled),
      }

    if sm.seen["selfdriveState"]:
      ss = sm["selfdriveState"]
      result["selfdriveState"] = {
        "seen": True,
        "state": _enum_name(ss.state),
        "enabled": bool(ss.enabled),
        "active": bool(ss.active),
        "alert1": str(ss.alertText1),
        "alert2": str(ss.alertText2),
      }

    if sm.seen["pandaStates"] and len(sm["pandaStates"]):
      panda = sm["pandaStates"][0]
      result["panda"] = {
        "seen": True,
        "safetyModel": _enum_name(panda.safetyModel),
        "safetyParam": int(panda.safetyParam),
        "controlsAllowed": bool(panda.controlsAllowed),
        "rxInvalid": bool(panda.safetyRxChecksInvalid),
        "faultStatus": _enum_name(getattr(panda, "faultStatus", "확인 불가")),
        "faults": _fault_names(panda),
        "interruptLoad": round(float(getattr(panda, "interruptLoad", 0.0)), 4),
      }

    if sm.seen["radarState"]:
      errors = sm["radarState"].radarErrors
      result["radar"] = {
        "seen": True,
        "canError": bool(errors.canError),
        "radarFault": bool(errors.radarFault),
        "wrongConfig": bool(errors.wrongConfig),
        "temporary": bool(errors.radarUnavailableTemporary),
      }
  except Exception as error:
    result["readError"] = str(error)

  result["verdict"] = _verdict(result)
  return result


def _verdict(status: dict[str, object]) -> dict[str, str]:
  blockers: list[str] = []
  warnings: list[str] = []
  cs = status.get("carState", {})
  panda = status.get("panda", {})
  radar = status.get("radar", {})
  firmware = status.get("firmware", {})
  car_info = status.get("car", {})

  if isinstance(panda, dict) and panda.get("seen"):
    faults = panda.get("faults", [])
    if faults:
      blockers.append("Panda fault: " + ", ".join(str(f) for f in faults))
    if panda.get("rxInvalid") is True:
      blockers.append("Panda RX 안전검사 invalid")
  else:
    warnings.append("Panda 상태 확인 불가")

  if isinstance(cs, dict) and cs.get("seen"):
    if cs.get("accFaulted") is True:
      blockers.append("ACC fault")
  else:
    warnings.append("차량 상태 확인 불가")

  if isinstance(radar, dict) and radar.get("seen"):
    if any(radar.get(key) is True for key in ("canError", "radarFault", "wrongConfig", "temporary")):
      blockers.append("레이더 오류")
  else:
    warnings.append("레이더 상태 확인 불가")

  long_active = isinstance(car_info, dict) and car_info.get("longitudinal") == "활성"
  if long_active and isinstance(panda, dict) and panda.get("seen") and panda.get("safetyParam") != 260:
    blockers.append(f"넥쏘 롱컨 safetyParam={panda.get('safetyParam')} (예상 260)")
  if long_active and isinstance(firmware, dict) and firmware.get("ready") is not True:
    blockers.append("현재 safety 소스와 일치하는 Panda 펌웨어 준비 확인 실패")

  if blockers:
    return {"level": "block", "label": "주행 금지", "detail": blockers[0]}
  if warnings:
    return {"level": "warn", "label": "확인 필요", "detail": warnings[0]}
  return {"level": "candidate", "label": "정상 후보", "detail": "현재 수신된 핵심 진단값에 치명 신호가 없습니다."}


def stationary_gate(core) -> tuple[bool, str]:
  """Fail closed for vehicle-affecting web writes while ignition/onroad is active."""
  if not core.is_onroad():
    return True, "오프로드"

  try:
    cs_sock = messaging.sub_sock("carState", conflate=True, timeout=900)
    cs_msg = messaging.recv_one(cs_sock)
    if cs_msg is None:
      return False, "carState 수신 없음"
    cs = cs_msg.carState
    if cs.gearShifter != car.CarState.GearShifter.park:
      return False, f"기어={_enum_name(cs.gearShifter)}"
    if abs(float(cs.vEgo)) > 0.05:
      return False, f"속도={float(cs.vEgo) * 3.6:.1f}km/h"
    if bool(cs.cruiseState.enabled):
      return False, "크루즈 활성 중"
    if not bool(getattr(cs, "parkingBrake", False)):
      return False, "주차브레이크 해제"

    ss_sock = messaging.sub_sock("selfdriveState", conflate=True, timeout=700)
    ss_msg = messaging.recv_one(ss_sock)
    if ss_msg is None:
      return False, "selfdriveState 수신 없음"
    if bool(ss_msg.selfdriveState.enabled) or bool(ss_msg.selfdriveState.active):
      return False, "오픈파일럿 제어 활성 중"
  except Exception as error:
    return False, f"정지 상태 확인 실패: {error}"

  return True, "P + 0km/h + 주차브레이크 + 크루즈 비활성"


def _css(core) -> str:
  return core.base_css() + """
:root{color-scheme:dark}body{background:#080a0d}main{max-width:940px;padding:18px 18px 94px}.hero{padding:22px;border-radius:24px;background:linear-gradient(145deg,#191d22,#101318);border:1px solid #2d333b;margin:14px 0}.hero h1{margin:0;font-size:30px}.eyebrow{font-size:12px;color:#8b949e;letter-spacing:.08em;text-transform:uppercase}.status{display:flex;align-items:center;gap:12px;margin-top:18px}.dot{width:14px;height:14px;border-radius:50%;background:#8b949e;box-shadow:0 0 18px #8b949e66}.status.candidate .dot{background:#30d158;box-shadow:0 0 18px #30d15888}.status.warn .dot{background:#ffd60a;box-shadow:0 0 18px #ffd60a88}.status.block .dot{background:#ff453a;box-shadow:0 0 18px #ff453a88}.status-label{font-size:22px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.metric{background:#11151a;border:1px solid #2a3038;border-radius:18px;padding:15px}.metric .k{font-size:12px;color:#8b949e}.metric .v{font-size:18px;font-weight:750;margin-top:6px;word-break:break-word}.nav{position:fixed;z-index:50;left:50%;bottom:10px;transform:translateX(-50%);width:min(900px,calc(100% - 22px));display:grid;grid-template-columns:repeat(5,1fr);gap:5px;background:#15191ef2;border:1px solid #30363d;border-radius:20px;padding:7px;backdrop-filter:blur(18px)}.nav a{color:#8b949e;text-align:center;padding:11px 3px;border-radius:14px;font-size:12px;font-weight:700}.nav a.active{background:#2b3139;color:white}.section-title{font-size:13px;color:#8b949e;margin:22px 4px 8px;font-weight:700}.pill{display:inline-block;border:1px solid #3b434d;border-radius:999px;padding:5px 9px;font-size:12px}.pill.good{border-color:#257a3e;color:#58d779}.pill.bad{border-color:#8e302b;color:#ff746d}.personality{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}.personality button{margin:0;background:#2a3038}.personality button.selected{background:#3159d9}.danger{background:#351715!important;color:#ff8b84!important}.mini{font-size:12px;color:#8b949e;line-height:1.5}.refresh{float:right;font-size:12px}@media(max-width:620px){.grid{grid-template-columns:1fr 1fr}.nav a{font-size:11px}.hero h1{font-size:26px}}
"""


def _nav(active: str) -> str:
  items = (("home", "/", "홈"), ("settings", "/settings", "설정"), ("camera", "/live", "카메라"),
           ("diagnostics", "/diagnostics", "진단"), ("system", "/system", "시스템"))
  return '<nav class="nav">' + "".join(
    f'<a class="{"active" if key == active else ""}" href="{path}">{label}</a>'
    for key, path, label in items
  ) + "</nav>"


def _message(message: str) -> str:
  return f'<div class="message">{html.escape(message)}</div>' if message else ""


def dashboard_page(core, message: str = "", fetch_update: bool = False) -> str:
  status = live_status(core)
  verdict = status["verdict"]
  cs = status["carState"]
  ss = status["selfdriveState"]
  panda = status["panda"]
  radar = status["radar"]
  car_info = status["car"]
  fw = status["firmware"]

  mode = "오픈파일럿 롱컨" if car_info.get("longitudinal") == "활성" else "순정 ACC / 일반 크루즈"
  safety = f"{panda.get('safetyModel','-')}({panda.get('safetyParam','-')})" if panda.get("seen") else "확인 불가"
  fault_text = ", ".join(panda.get("faults", [])) if panda.get("faults") else "없음"
  radar_text = "정상 후보" if radar.get("seen") and not any(radar.get(k) for k in ("canError", "radarFault", "wrongConfig", "temporary")) else "확인 필요"
  fw_text = str(fw.get("firmwareVersion", "확인 불가")) if fw.get("ready") else "준비 확인 필요"

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 7000</title><style>{_css(core)}</style></head><body><main>
  <div class="hero"><div class="eyebrow">NEXOPILOT · LOCAL CONTROL CENTER</div><h1>넥쏘 파일럿</h1><div class="status {html.escape(str(verdict['level']))}"><span class="dot"></span><div><div class="status-label">{html.escape(str(verdict['label']))}</div><div class="mini">{html.escape(str(verdict['detail']))}</div></div></div></div>
  {_message(message)}
  <div class="grid">
    <div class="metric"><div class="k">주행 모드</div><div class="v">{html.escape(mode)}</div></div>
    <div class="metric"><div class="k">차량</div><div class="v">{html.escape(str(car_info.get('car','확인 불가')))}</div></div>
    <div class="metric"><div class="k">속도 / 기어</div><div class="v">{html.escape(str(cs.get('speedKph','-')))} km/h · {html.escape(str(cs.get('gear','-')))}</div></div>
    <div class="metric"><div class="k">크루즈 / selfdrive</div><div class="v">{str(cs.get('cruiseEnabled','-'))} · {str(ss.get('enabled','-'))}</div></div>
    <div class="metric"><div class="k">Panda Safety</div><div class="v">{html.escape(safety)}</div></div>
    <div class="metric"><div class="k">Panda fault</div><div class="v">{html.escape(fault_text)}</div></div>
    <div class="metric"><div class="k">레이더</div><div class="v">{radar_text}</div></div>
    <div class="metric"><div class="k">Panda 펌웨어</div><div class="v">{html.escape(fw_text)}</div></div>
  </div>
  {_nav('home')}</main></body></html>'''


def settings_page(core, message: str = "") -> str:
  params = Params()
  forced = core.force_nexo_enabled()
  sections: list[str] = []
  for group_name, group in TOGGLE_GROUPS:
    rows: list[str] = []
    for key, title, desc in group:
      checked = " checked" if core.param_bool(params, key) else ""
      extra = "<span class='pill bad'>재부팅 필요</span>" if key == "AlphaLongitudinalEnabled" else ""
      rows.append(f'''<form method="post" action="/toggle"><input type="hidden" name="key" value="{key}"><div class="row"><div><div class="title">{html.escape(title)} {extra}</div><div class="desc">{html.escape(desc)}</div></div><label class="switch"><input type="checkbox"{checked} onchange="this.form.submit()"><span class="slider"></span></label></div></form>''')
    if group_name == "주행":
      reverse_checked = " checked" if reverse_driver_camera_enabled() else ""
      rows.append(f'''<form method="post" action="/reverse-camera/toggle"><div class="row"><div><div class="title">후진 시 실내 카메라 전환</div><div class="desc">R단에 들어가면 주행 화면 대신 실내 운전자 카메라를 표시하고 R단 해제 시 원래 화면으로 돌아갑니다.</div></div><label class="switch"><input type="checkbox"{reverse_checked} onchange="this.form.submit()"><span class="slider"></span></label></div></form>''')
    sections.append(f'<div class="section-title">{html.escape(group_name)}</div><div class="card">{"".join(rows)}</div>')

  try:
    personality = int(_param_text(params, "LongitudinalPersonality", "1"))
  except ValueError:
    personality = 1
  personality_buttons = "".join(
    f'''<form method="post" action="/personality"><input type="hidden" name="value" value="{value}"><button class="{'selected' if personality == value else ''}">{label}</button></form>'''
    for value, label in PERSONALITIES
  )
  long_note = "롱컨 활성 시 적용" if core.param_bool(params, "AlphaLongitudinalEnabled") else "현재 일반 크루즈이므로 저장은 가능하지만 순정 ACC에는 적용되지 않음"

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 설정</title><style>{_css(core)}</style></head><body><main><div class="hero"><div class="eyebrow">CARROT-STYLE SETTINGS</div><h1>차량 설정</h1><div class="mini">설정 변경은 P + 완전 정지 + 크루즈/오픈파일럿 비활성 상태에서만 허용됩니다.</div></div>{_message(message)}
  <div class="section-title">차량</div><div class="card"><form method="post" action="/vehicle"><select name="vehicle"><option value="auto"{' selected' if not forced else ''}>자동 인식</option><option value="nexo"{' selected' if forced else ''}>현대 넥쏘 1세대</option></select><button>차량 선택 저장</button></form><div class="row"><div><div class="title">레이더 트랙</div><div class="desc">넥쏘에서는 자동 관리합니다. 안전상 별도 ON/OFF 메뉴를 두지 않습니다.</div></div><span class="value">자동</span></div></div>
  {''.join(sections)}
  <div class="section-title">롱컨 성향</div><div class="card"><div class="title">주행 성향</div><div class="desc">공격적 / 표준 / 여유. {html.escape(long_note)}</div><div class="personality">{personality_buttons}</div></div>
  <div class="card"><div class="title">설정 적용</div><div class="desc">특히 롱컨 변경 후에는 CarParams를 다시 만들기 위해 재부팅합니다.</div><form method="post" action="/settings/reboot"><button>저장하고 재부팅</button></form></div>
  {_nav('settings')}</main></body></html>'''


def system_page(core, message: str = "", fetch_update: bool = False) -> str:
  update = core.update_status(fetch=fetch_update)
  firmware = _firmware_status()
  allowed, gate = stationary_gate(core)
  dirty = core.git_value("status", "--porcelain")
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 시스템</title><style>{_css(core)}</style></head><body><main><div class="hero"><div class="eyebrow">DEVICE · SOFTWARE</div><h1>시스템</h1><div class="status {'candidate' if allowed else 'warn'}"><span class="dot"></span><div><div class="status-label">{'설정 변경 가능' if allowed else '설정 변경 잠금'}</div><div class="mini">{html.escape(gate)}</div></div></div></div>{_message(message)}
  <div class="card"><h2>소프트웨어</h2><div class="row"><span>현재 버전</span><span class="value">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value">{html.escape(str(update['remote']))}</span></div><div class="row"><span>작업 트리</span><span class="value">{'변경 있음' if dirty.strip() else 'Clean'}</span></div><a href="/system?check=1"><button class="secondary">업데이트 확인</button></a><form method="post" action="/update"><button>업데이트만 설치</button></form></div>
  <div class="card"><h2>Panda 펌웨어</h2><div class="row"><span>현재 safety 소스 일치 준비</span><span class="value">{'Ready' if firmware.get('ready') else '확인 필요'}</span></div><div class="row"><span>준비된 버전</span><span class="value">{html.escape(str(firmware.get('firmwareVersion','확인 불가')))}</span></div><div class="desc">빌드 실패 시 NexoPilot은 롱컨을 끄고 일반 크루즈 경로로 되돌리는 fail-closed 정책을 유지합니다.</div></div>
  <div class="card"><h2>장치</h2><a href="/live"><button class="secondary">전방·운전자 카메라 보기</button></a><form method="post" action="/settings/reboot"><button class="secondary">콤마 재부팅</button></form></div>
  <div class="card"><h2>시스템 정보</h2><pre>{html.escape(core.system_output())}</pre></div>
  {_nav('system')}</main></body></html>'''


def enhance_legacy_page(core, page: str, active: str) -> str:
  if 'class="nav"' in page:
    return page
  page = page.replace("</style>", _css(core) + "</style>", 1)
  return page.replace("</main>", _nav(active) + "</main>", 1)


def status_json(core) -> bytes:
  return json.dumps(live_status(core), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
