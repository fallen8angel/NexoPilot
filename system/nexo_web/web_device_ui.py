from __future__ import annotations

import html

from openpilot.common.params import Params


# Keep only the low-risk reboot action on the port 7000 device page.
# Power-off and calibration reset are deliberately not exposed so they cannot
# be triggered accidentally from the web UI.
DEVICE_ACTIONS = {
  "/device/reboot": "reboot",
}


def perform_device_action(action: str) -> tuple[bool, str]:
  """Run the limited device actions exposed by NexoPilot port 7000."""
  params = Params()
  try:
    if action == "reboot":
      params.put_bool("DoReboot", True)
      return True, "재부팅을 요청했습니다."
  except Exception as error:
    return False, f"디바이스 조작 실패: {error}"

  return False, "지원하지 않는 디바이스 조작입니다."


def device_page(core, carrot_ui, message: str = "", fetch_update: bool = False) -> str:
  update = core.update_status(fetch=fetch_update)
  allowed, gate = carrot_ui.stationary_gate(core)
  dirty = core.git_value("status", "--porcelain")
  disabled = "" if allowed else " disabled"
  msg = carrot_ui._message(message)
  if update.get("error"):
    update_label = "자동 확인 실패"
  elif update.get("checking"):
    update_label = "자동 확인 중"
  elif update.get("available"):
    update_label = "새 업데이트 있음"
  else:
    update_label = "최신 버전"

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 내 디바이스</title><style>{carrot_ui._css(core)}</style></head><body><main>
  <div class="hero"><div class="eyebrow">MY DEVICE · XPLUS-STYLE CONTROL</div><h1>내 디바이스</h1><div class="status {'candidate' if allowed else 'warn'}"><span class="dot"></span><div><div class="status-label">{'디바이스 조작 가능' if allowed else '디바이스 조작 잠금'}</div><div class="mini">{html.escape(gate)}</div></div></div></div>{msg}
  <div class="card"><h2>디바이스 조작</h2><div class="desc">실수로 위험한 조작을 누르지 않도록 전원 끄기와 캘리브레이션 초기화 메뉴는 표시하지 않습니다. 재부팅은 P단·완전 정지·주차브레이크·크루즈/오픈파일럿 비활성 상태에서만 허용됩니다.</div><a href="/live"><button class="secondary">전방·운전자 카메라 보기</button></a><form method="post" action="/device/reboot" onsubmit="return confirm('콤마를 재부팅하시겠습니까?')"><button class="secondary"{disabled}>콤마 재부팅</button></form></div>
  <div class="card"><h2>소프트웨어</h2><div class="row"><span>업데이트 상태</span><span class="value" id="updateState">{html.escape(update_label)}</span></div><div class="row"><span>현재 버전</span><span class="value" id="currentVersion">{html.escape(str(update['current']))}</span></div><div class="row"><span>원격 버전</span><span class="value" id="remoteVersion">{html.escape(str(update['remote']))}</span></div><div class="row"><span>작업 트리</span><span class="value">{'변경 있음' if dirty.strip() else 'Clean'}</span></div><form method="post" action="/update"><button>업데이트 설치</button></form><div class="desc">업데이트는 P단·완전 정지·크루즈/오픈파일럿 비활성 상태에서 설치할 수 있으며 주차브레이크는 필요하지 않습니다. 설치가 끝나면 지금 재부팅할지 선택할 수 있습니다.</div></div>
  {carrot_ui._nav('device')}</main><script>
async function refreshUpdateStatus(){{
  try{{
    const response=await fetch("/api/update-status",{{cache:"no-store"}});
    const data=await response.json();
    document.getElementById("currentVersion").textContent=data.current;
    document.getElementById("remoteVersion").textContent=data.remote;
    document.getElementById("updateState").textContent=data.error?"자동 확인 실패":(data.checking?"자동 확인 중":(data.available?"새 업데이트 있음":"최신 버전"));
  }}catch(error){{
    document.getElementById("updateState").textContent="연결 확인 필요";
  }}
}}
refreshUpdateStatus();
setInterval(refreshUpdateStatus,10000);
</script></body></html>'''
