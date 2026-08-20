from __future__ import annotations

import html

from openpilot.common.params import Params


CALIBRATION_KEYS = (
  "CalibrationParams",
  "LiveTorqueParameters",
  "LiveParameters",
  "LiveParametersV2",
  "LiveDelay",
)

DEVICE_ACTIONS = {
  "/device/reboot": "reboot",
  "/device/poweroff": "poweroff",
  "/device/recalibrate": "recalibrate",
}


def perform_device_action(action: str) -> tuple[bool, str]:
  """Mirror XPlus device actions using manager-observed Params only.

  No CAN, Panda, longitudinal, MED, camera, or vehicle-control state is
  modified here. The caller is responsible for enforcing NexoPilot's strict
  stationary gate before invoking an action.
  """
  params = Params()
  try:
    if action == "reboot":
      params.put_bool("DoReboot", True)
      return True, "재부팅을 요청했습니다."

    if action == "poweroff":
      params.put_bool("DoShutdown", True)
      return True, "전원 끄기를 요청했습니다."

    if action == "recalibrate":
      for key in CALIBRATION_KEYS:
        try:
          params.remove(key)
        except Exception:
          pass
      try:
        params.put_bool("OnroadCycleRequested", True)
      except Exception:
        pass
      params.put_bool("DoReboot", True)
      return True, "캘리브레이션을 초기화하고 재부팅을 요청했습니다."
  except Exception as error:
    return False, f"디바이스 조작 실패: {error}"

  return False, "지원하지 않는 디바이스 조작입니다."


def device_page(core, carrot_ui, message: str = "", fetch_update: bool = False) -> str:
  update = core.update_status(fetch=fetch_update)
  firmware = carrot_ui._firmware_status()
  allowed, gate = carrot_ui.stationary_gate(core)
  dirty = core.git_value("status", "--porcelain")
  disabled = "" if allowed else " disabled"
  msg = carrot_ui._message(message)

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 내 디바이스</title><style>{carrot_ui._css(core)}</style></head><body><main>
  <div class="hero"><div class="eyebrow">MY DEVICE · XPLUS-STYLE CONTROL</div><h1>내 디바이스</h1><div class="status {'candidate' if allowed else 'warn'}"><span class="dot"></span><div><div class="status-label">{'디바이스 조작 가능' if allowed else '디바이스 조작 잠금'}</div><div class="mini">{html.escape(gate)}</div></div></div></div>{msg}
  <div class="card"><h2>디바이스 조작</h2><div class="desc">XPlus와 같은 시스템 Params 방식으로 실행합니다. 실제 조작은 P단·완전 정지·주차브레이크·크루즈/오픈파일럿 비활성 상태에서만 허용됩니다.</div><a href="/live"><button class="secondary">전방·운전자 카메라 보기</button></a><form method="post" action="/device/reboot" onsubmit="return confirm('콤마를 재부팅하시겠습니까?')"><button class="secondary"{disabled}>콤마 재부팅</button></form><form method="post" action="/device/recalibrate" onsubmit="return confirm('캘리브레이션을 초기화하고 재부팅하시겠습니까?')"><button class="secondary"{disabled}>캘리브레이션 초기화 후 재부팅</button></form><form method="post" action="/device/poweroff" onsubmit="return confirm('콤마 전원을 끄시겠습니까?')"><button class="danger"{disabled}>전원 끄기</button></form></div>
  <div class="card"><h2>소프트웨어</h2><div class="row"><span>현재 버전</span><span class="value">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value">{html.escape(str(update['remote']))}</span></div><div class="row"><span>작업 트리</span><span class="value">{'변경 있음' if dirty.strip() else 'Clean'}</span></div><a href="/device?check=1"><button class="secondary">업데이트 확인</button></a><form method="post" action="/update"><button{disabled}>업데이트 설치 후 재부팅</button></form></div>
  <div class="card"><h2>Panda 펌웨어</h2><div class="row"><span>현재 safety 소스 일치 준비</span><span class="value">{'Ready' if firmware.get('ready') else '확인 필요'}</span></div><div class="row"><span>준비된 버전</span><span class="value">{html.escape(str(firmware.get('firmwareVersion','확인 불가')))}</span></div><div class="desc">빌드 실패 시 NexoPilot은 롱컨을 끄고 일반 크루즈 경로로 되돌리는 기존 fail-closed 정책을 그대로 유지합니다.</div></div>
  <div class="card"><h2>시스템 정보</h2><pre>{html.escape(core.system_output())}</pre></div>
  {carrot_ui._nav('device')}</main></body></html>'''
