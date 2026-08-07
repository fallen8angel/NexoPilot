from __future__ import annotations

import html
import json
import math

from cereal import messaging
from openpilot.common.params import Params
from system.nexo_web import web_remote_ui as remote_ui


HUD_PARAM = "NexoHudEnabled"


def enabled() -> bool:
  try:
    return Params().get_bool(HUD_PARAM)
  except Exception:
    return False


def _enum_name(value) -> str:
  text = str(value)
  return text.rsplit(".", 1)[-1] if text else "-"


def _fault_names(panda) -> list[str]:
  try:
    names = {_enum_name(fault) for fault in panda.faults}
  except Exception:
    return []
  return sorted(name for name in names if name not in ("none", "0", "-"))


def _safe_float(value, default: float = 0.0) -> float:
  try:
    number = float(value)
    return number if math.isfinite(number) else default
  except Exception:
    return default


def hud_status(core) -> dict[str, object]:
  car_info = core.car_status()
  result: dict[str, object] = {
    "enabled": enabled(),
    "mode": "LONG" if car_info.get("longitudinal") == "활성" else "ACC",
    "car": car_info.get("car", "-"),
    "speedKph": 0.0,
    "setSpeedKph": 0.0,
    "gear": "-",
    "cruiseAvailable": False,
    "cruiseEnabled": False,
    "selfdriveEnabled": False,
    "selfdriveActive": False,
    "lead": {"status": False, "distanceM": 0.0, "relativeKph": 0.0},
    "panda": {"seen": False, "safetyParam": 0, "faults": []},
    "radar": {"seen": False, "ok": False},
    "model": {"ready": False},
  }

  try:
    sm = messaging.SubMaster(["carState", "selfdriveState", "radarState", "pandaStates"])
    for _ in range(5):
      sm.update(80)
      if sm.seen["carState"] and sm.seen["selfdriveState"]:
        break

    if sm.seen["carState"]:
      cs = sm["carState"]
      result["speedKph"] = round(_safe_float(cs.vEgo) * 3.6, 1)
      set_speed = _safe_float(getattr(cs.cruiseState, "speed", 0.0)) * 3.6
      result["setSpeedKph"] = round(set_speed if 0.0 < set_speed < 250.0 else 0.0, 0)
      result["gear"] = _enum_name(cs.gearShifter)
      result["cruiseAvailable"] = bool(cs.cruiseState.available)
      result["cruiseEnabled"] = bool(cs.cruiseState.enabled)

    if sm.seen["selfdriveState"]:
      ss = sm["selfdriveState"]
      result["selfdriveEnabled"] = bool(ss.enabled)
      result["selfdriveActive"] = bool(ss.active)

    if sm.seen["radarState"]:
      rs = sm["radarState"]
      errors = rs.radarErrors
      radar_ok = not any((bool(errors.canError), bool(errors.radarFault), bool(errors.wrongConfig), bool(errors.radarUnavailableTemporary)))
      result["radar"] = {"seen": True, "ok": radar_ok}
      lead = rs.leadOne
      if bool(lead.status):
        result["lead"] = {
          "status": True,
          "distanceM": round(max(0.0, _safe_float(lead.dRel)), 1),
          "relativeKph": round(_safe_float(lead.vRel) * 3.6, 1),
        }

    if sm.seen["pandaStates"] and len(sm["pandaStates"]):
      panda = sm["pandaStates"][0]
      result["panda"] = {
        "seen": True,
        "safetyParam": int(panda.safetyParam),
        "faults": _fault_names(panda),
      }
  except Exception as error:
    result["readError"] = str(error)

  try:
    model = json.loads(core.model_snapshot_json().decode("utf-8"))
    if isinstance(model, dict):
      result["model"] = model
  except Exception:
    pass

  return result


def hud_status_json(core) -> bytes:
  return json.dumps(hud_status(core), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _common_hud_css() -> str:
  return """
.hud-stage{position:relative;overflow:hidden;background:radial-gradient(circle at 50% 72%,#14202a,#070a0d 64%);border:1px solid #2a3038;border-radius:22px;min-height:520px;color:#fff}
.hud-stage canvas{position:absolute;inset:0;width:100%;height:100%}.hud-overlay{position:absolute;inset:0;z-index:2;pointer-events:none}
.hud-topline{position:absolute;left:18px;right:18px;top:12px;display:flex;justify-content:center;gap:22px;color:#d7dde5;font-size:12px}.hud-topline .ok{color:#66e58a}.hud-speed{position:absolute;left:30px;top:58px}.hud-speed .n{font-size:88px;font-weight:850;line-height:.88;letter-spacing:-.06em}.hud-speed .u{font-size:16px;color:#8b949e;margin-left:7px}.hud-set{position:absolute;left:34px;top:157px;border:1px solid #46505c;border-radius:12px;padding:7px 13px;background:#111820c9;font-size:23px;font-weight:800}.hud-side{position:absolute;right:22px;top:58px;width:128px;border:1px solid #30363d;border-radius:14px;background:#10161dcc;overflow:hidden}.hud-side div{display:flex;justify-content:space-between;padding:9px 11px;border-bottom:1px solid #252b32;font-size:12px}.hud-side div:last-child{border-bottom:0}.hud-lead{position:absolute;left:50%;top:43%;transform:translate(-50%,-50%);text-align:center;font-size:23px;font-weight:800;text-shadow:0 2px 8px #000}.hud-lead small{display:block;font-size:13px;color:#b7c0ca;margin-top:4px}.hud-chips{position:absolute;left:18px;right:18px;bottom:16px;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.hud-chip{text-align:center;padding:10px 7px;border:1px solid #3a424c;border-radius:13px;background:#10161de6;font-size:13px;font-weight:800}.hud-chip.on{border-color:#2c8a49;color:#66e58a}.hud-chip.bad{border-color:#9a3a34;color:#ff847d}.hud-fault{position:absolute;left:50%;top:52px;transform:translateX(-50%);padding:7px 12px;border-radius:999px;background:#5d1818;color:#ffaaa4;font-size:12px;font-weight:800;display:none}.hud-disabled{position:absolute;left:50%;bottom:73px;transform:translateX(-50%);padding:8px 12px;border-radius:999px;background:#222830e8;border:1px solid #414a54;color:#c5cbd2;font-size:12px;display:none}
@media(max-height:650px) and (min-width:900px){.hud-stage{min-height:455px}.hud-speed .n{font-size:72px}.hud-speed{top:50px}.hud-set{top:132px}.hud-side{top:50px}.hud-chips{bottom:12px}.hud-topline{top:8px}}
"""


def _hud_script(canvas_id: str) -> str:
  return f"""
const canvas=document.getElementById('{canvas_id}'),ctx=canvas.getContext('2d');
function resizeHud(){{const r=canvas.getBoundingClientRect();canvas.width=Math.max(1,r.width*devicePixelRatio);canvas.height=Math.max(1,r.height*devicePixelRatio);ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0)}}
addEventListener('resize',resizeHud);resizeHud();
function projectHud(x,y){{const r=canvas.getBoundingClientRect(),w=r.width,h=r.height;const d=Math.max(0,Math.min(100,Number(x)||0));const p=Math.pow(d/100,.72);return [w/2+(Number(y)||0)*(w/10)*(1-p*.55),h*.91-p*h*.72]}}
function hudLine(points,color,width){{if(!points||points.length<2)return;ctx.beginPath();let first=true;for(let i=0;i<points.length;i++){{const p=projectHud(points[i][0],points[i][1]);if(first){{ctx.moveTo(...p);first=false}}else ctx.lineTo(...p)}}ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineCap='round';ctx.stroke()}}
function drawHud(s){{const r=canvas.getBoundingClientRect();ctx.clearRect(0,0,r.width,r.height);const m=s.model||{{}};if(m.ready){{const path=m.path||{{}},pp=[];for(let i=0;i<Math.min((path.x||[]).length,(path.y||[]).length);i++)pp.push([path.x[i],path.y[i]]);hudLine(pp,'rgba(49,220,112,.95)',7);const lines=m.laneLines||[];for(let j=0;j<lines.length;j++){{const q=[];for(let i=0;i<Math.min((lines[j].x||[]).length,(lines[j].y||[]).length);i++)q.push([lines[j].x[i],lines[j].y[i]]);hudLine(q,'rgba(230,240,255,.58)',3)}}}}const lead=s.lead||{{}};if(lead.status){{const p=projectHud(lead.distanceM,0);ctx.fillStyle='#dfe7ef';ctx.fillRect(p[0]-20,p[1]-8,40,16)}}
document.querySelectorAll('[data-speed]').forEach(e=>e.textContent=Math.round(s.speedKph||0));document.querySelectorAll('[data-set]').forEach(e=>e.textContent=(s.setSpeedKph||0)>0?Math.round(s.setSpeedKph):'--');document.querySelectorAll('[data-gear]').forEach(e=>e.textContent=(s.gear||'-').toUpperCase());document.querySelectorAll('[data-mode]').forEach(e=>e.textContent=s.mode||'ACC');document.querySelectorAll('[data-lead]').forEach(e=>e.innerHTML=lead.status?`${{lead.distanceM.toFixed(1)}} m<small>${{lead.relativeKph>=0?'+':''}}${{lead.relativeKph.toFixed(1)}} km/h</small>`:'앞차 없음');
const laneOn=!!(s.selfdriveEnabled||s.selfdriveActive),radarOk=!!((s.radar||{{}}).ok),faults=(s.panda||{{}}).faults||[];document.querySelectorAll('[data-chip=mode]').forEach(e=>{{e.textContent=s.mode||'ACC';e.className='hud-chip '+(s.mode==='LONG'?'on':'')}});document.querySelectorAll('[data-chip=lane]').forEach(e=>e.className='hud-chip '+(laneOn?'on':''));document.querySelectorAll('[data-chip=radar]').forEach(e=>e.className='hud-chip '+(radarOk?'on':'bad'));document.querySelectorAll('[data-chip=op]').forEach(e=>{{e.textContent=(s.selfdriveActive?'OPENPILOT ON':'OPENPILOT OFF');e.className='hud-chip '+(s.selfdriveActive?'on':'')}});document.querySelectorAll('[data-chip=panda]').forEach(e=>{{e.textContent=faults.length?'PANDA !':'PANDA';e.className='hud-chip '+(faults.length?'bad':'on')}});document.querySelectorAll('[data-fault]').forEach(e=>{{if(faults.length){{e.style.display='block';e.textContent='Panda fault · '+faults.join(', ')}}else e.style.display='none'}});document.querySelectorAll('[data-disabled]').forEach(e=>e.style.display=s.enabled?'none':'block');document.querySelectorAll('[data-cruise]').forEach(e=>e.textContent=s.cruiseEnabled?'CRUISE ON':'CRUISE OFF');document.querySelectorAll('[data-opstate]').forEach(e=>e.textContent=s.selfdriveActive?'OP ON':'OP OFF');}}
async function tickHud(){{try{{const r=await fetch('/api/hud',{{cache:'no-store'}});drawHud(await r.json())}}catch(e){{}}}}setInterval(tickHud,250);tickHud();
"""


def _stage_html(canvas_id: str) -> str:
  return f'''<div class="hud-stage"><canvas id="{canvas_id}"></canvas><div class="hud-overlay"><div class="hud-topline"><span class="ok">● 정상</span><span><b data-speed>0</b> km/h</span><span data-gear>P</span><span>BRAKE</span><span data-cruise>CRUISE OFF</span><span data-opstate>OP OFF</span></div><div class="hud-fault" data-fault></div><div class="hud-speed"><span class="n" data-speed>0</span><span class="u">km/h</span></div><div class="hud-set">SET <span data-set>--</span></div><div class="hud-side"><div><span>기어</span><b data-gear>-</b></div><div><span>주행 모드</span><b data-mode>ACC</b></div></div><div class="hud-lead" data-lead>앞차 없음</div><div class="hud-disabled" data-disabled>HUD 비활성 · 미리보기만 표시 중</div><div class="hud-chips"><div class="hud-chip" data-chip="mode">ACC</div><div class="hud-chip" data-chip="lane">LANE</div><div class="hud-chip" data-chip="radar">RADAR</div><div class="hud-chip" data-chip="op">OPENPILOT OFF</div><div class="hud-chip" data-chip="panda">PANDA</div></div></div></div>'''


def hud_page(core, message: str = "") -> str:
  active = enabled()
  msg = f'<div class="hud-message">{html.escape(message)}</div>' if message else ""
  toggle_text = "HUD 비활성화" if active else "HUD 활성화"
  toggle_class = "danger" if active else "primary"
  open_button = '<a class="hud-open" href="/hud/view">▶ HUD 화면 열기</a>' if active else '<span class="hud-open disabled">HUD 활성화 후 화면 열기</span>'
  checked = "켜짐" if active else "꺼짐"
  nav = remote_ui.nav("hud")
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO HUD 설정</title><style>{core.base_css()}
:root{{color-scheme:dark}}body{{background:#080a0d}}main{{max-width:1500px;padding:14px 16px 86px}}{remote_ui.wide_nav_css()}
.hud-layout{{display:grid;grid-template-columns:285px minmax(0,1fr);gap:14px;align-items:stretch}}.hud-panel{{background:#11151a;border:1px solid #2a3038;border-radius:20px;padding:18px}}.hud-panel h1{{margin:0 0 4px;font-size:25px}}.hud-kicker{{font-size:11px;color:#8b949e;letter-spacing:.08em}}.hud-message{{margin:12px 0;padding:10px 12px;border-radius:12px;background:#1d2730;color:#dbe5ed;font-size:12px}}.hud-row{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:13px 0;border-bottom:1px solid #252b32}}.hud-row:last-child{{border-bottom:0}}.hud-value{{font-weight:800;color:#66e58a}}.hud-actions{{display:grid;gap:9px;margin:14px 0}}.hud-actions form{{margin:0}}.hud-actions button,.hud-open{{display:block;width:100%;box-sizing:border-box;text-align:center;border:0;border-radius:12px;padding:12px 10px;font-weight:800;text-decoration:none;color:white}}.hud-actions .primary,.hud-open{{background:#238c48}}.hud-actions .danger{{background:#55211e}}.hud-open.disabled{{background:#242a31;color:#777f88;pointer-events:none}}.hud-list{{display:grid;gap:8px;margin-top:11px}}.hud-check{{display:flex;gap:9px;align-items:center;padding:9px 10px;border-radius:10px;background:#0d1116;font-size:12px}}.hud-check b{{display:inline-flex;width:20px;height:20px;border-radius:6px;align-items:center;justify-content:center;background:#238c48;color:#fff}}.hud-note{{margin-top:12px;padding:11px;border:1px solid #35404a;border-radius:12px;color:#9aa4af;font-size:11px;line-height:1.55}}.hud-preview-wrap{{min-width:0}}.hud-preview-title{{display:flex;justify-content:space-between;align-items:end;margin:0 4px 8px}}.hud-preview-title h2{{margin:0;font-size:18px}}.hud-preview-title span{{font-size:11px;color:#8b949e}}{_common_hud_css()}
@media(max-width:899px){{main{{padding-bottom:92px}}.hud-layout{{grid-template-columns:1fr}}.hud-stage{{min-height:440px}}}}
</style></head><body><main>{nav}<div class="hud-layout"><section class="hud-panel"><div class="hud-kicker">NEXOPILOT · DISPLAY ONLY</div><h1>HUD 설정</h1>{msg}<div class="hud-row"><span>HUD 활성화</span><span class="hud-value">{checked}</span></div><div class="hud-actions"><form method="post" action="/hud/toggle"><button class="{toggle_class}">{toggle_text}</button></form>{open_button}</div><div class="hud-kicker">기본 표시 항목</div><div class="hud-list"><div class="hud-check"><b>✓</b><span>현재 속도 / 설정 속도</span></div><div class="hud-check"><b>✓</b><span>차선 / 예상 주행경로</span></div><div class="hud-check"><b>✓</b><span>앞차 / 레이더 트랙</span></div><div class="hud-check"><b>✓</b><span>크루즈 / 롱컨 상태</span></div><div class="hud-check"><b>✓</b><span>Panda 상태</span></div></div><div class="hud-note">HUD는 carState · modelV2 · radarState · selfdriveState를 읽어 표시만 합니다.<br><b>CAN 송신·Panda 설정·차량 제어는 하지 않습니다.</b></div></section><section class="hud-preview-wrap"><div class="hud-preview-title"><h2>HUD 실시간 미리보기</h2><span>보조 모니터에서 HUD 화면 열기 시 이 영역만 전체화면 표시</span></div>{_stage_html("hudPreview")}</section></div></main><script>{_hud_script("hudPreview")}</script></body></html>'''


def hud_view_page(core) -> str:
  if not enabled():
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO HUD</title><style>{core.base_css()}body{{background:#080a0d}}main{{max-width:680px;margin:auto;padding:28px}}.box{{padding:24px;border-radius:20px;border:1px solid #30363d;background:#14181d}}</style></head><body><main><div class="box"><h1>NEXO HUD</h1><h2>HUD가 비활성 상태입니다.</h2><p>7000 → HUD 메뉴에서 먼저 HUD 활성화를 켜주세요.</p><a href="/hud"><button>HUD 설정으로 돌아가기</button></a></div></main></body></html>'''

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no"><title>NEXO HUD</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#050709;color:#fff;font-family:Arial,"Noto Sans KR",sans-serif}}.full{{position:fixed;inset:0}}.full .hud-stage{{width:100%;height:100%;min-height:100%;border:0;border-radius:0}}.view-menu{{position:absolute;right:12px;bottom:68px;z-index:4;display:flex;gap:7px}}.view-menu a,.view-menu button{{border:1px solid #38414b;background:#10161ddd;color:#fff;border-radius:10px;padding:9px 11px;text-decoration:none;font-size:12px}}{_common_hud_css()}</style></head><body><div class="full">{_stage_html("hudFull")}<div class="view-menu"><button id="fullBtn">전체화면</button><a href="/hud">HUD 설정</a><a href="/">7000</a></div></div><script>{_hud_script("hudFull")}document.getElementById('fullBtn').addEventListener('click',()=>{{if(document.documentElement.requestFullscreen)document.documentElement.requestFullscreen();}});</script></body></html>'''