from __future__ import annotations

import html
import json
import math

from cereal import messaging
from openpilot.common.params import Params


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


def hud_page(core, message: str = "") -> str:
  active = enabled()
  msg = f'<div class="msg">{html.escape(message)}</div>' if message else ""
  if not active:
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXO HUD</title><style>{core.base_css()}
body{{background:#080a0d}}main{{max-width:760px;margin:auto;padding:24px}}.hero{{padding:24px;border:1px solid #30363d;border-radius:24px;background:#14181d}}.hero h1{{margin:0 0 8px}}.msg{{margin:12px 0;padding:12px;border-radius:12px;background:#1f2933}}.safe{{color:#8b949e;line-height:1.6}}
</style></head><body><main><p><a href="/">← 7000 홈</a></p><div class="hero"><h1>NEXO HUD</h1><h2>현재 비활성</h2><p class="safe">HUD는 carState · modelV2 · radarState · selfdriveState를 읽어 화면에 표시만 합니다. CAN 송신·Panda 설정·차량 제어는 하지 않습니다.</p>{msg}<form method="post" action="/hud/toggle"><button>HUD 활성화</button></form></div></main></body></html>'''

  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no"><title>NEXO HUD</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#050709;color:white;font-family:Arial,"Noto Sans KR",sans-serif}}#hud{{position:relative;width:100vw;height:100vh;background:radial-gradient(circle at 50% 70%,#111820,#050709 62%)}}canvas{{position:absolute;inset:0;width:100%;height:100%}}.top{{position:absolute;left:0;right:0;top:0;display:flex;justify-content:space-between;align-items:flex-start;padding:20px 26px;z-index:2}}.speed{{font-size:clamp(64px,10vw,128px);font-weight:850;line-height:.9;letter-spacing:-.06em}}.unit{{font-size:18px;color:#8b949e;margin-left:6px}}.set{{text-align:right}}.set .n{{font-size:42px;font-weight:800}}.set .k{{color:#8b949e;font-size:14px}}.center{{position:absolute;left:50%;top:47%;transform:translate(-50%,-50%);z-index:2;text-align:center;pointer-events:none}}.lead{{font-size:22px;font-weight:800;text-shadow:0 2px 8px #000}}.lead small{{display:block;font-size:14px;color:#b7c0ca;margin-top:4px}}.bottom{{position:absolute;left:0;right:0;bottom:0;padding:18px 24px 20px;display:flex;align-items:flex-end;justify-content:space-between;z-index:2;background:linear-gradient(transparent,#050709dd 45%)}}.chips{{display:flex;gap:8px;flex-wrap:wrap}}.chip{{padding:9px 13px;border:1px solid #30363d;border-radius:999px;background:#111820cc;font-size:14px;font-weight:750}}.on{{border-color:#268a46;color:#66e58a}}.bad{{border-color:#a73b35;color:#ff7c74}}.menu{{display:flex;gap:8px}}.menu a,.menu button{{appearance:none;border:1px solid #30363d;background:#111820cc;color:white;border-radius:12px;padding:10px 12px;text-decoration:none;font-size:13px}}.banner{{position:absolute;left:50%;top:16px;transform:translateX(-50%);z-index:3;padding:8px 14px;border-radius:999px;background:#5d1818;color:#ffaaa4;font-weight:800;display:none}}@media(max-height:520px){{.top{{padding:12px 18px}}.bottom{{padding:12px 18px}}.speed{{font-size:72px}}.set .n{{font-size:34px}}}}
</style></head><body><div id="hud"><canvas id="road"></canvas><div class="banner" id="banner"></div><div class="top"><div><span class="speed" id="speed">0</span><span class="unit">km/h</span></div><div class="set"><div class="k">SET</div><div class="n" id="setSpeed">--</div></div></div><div class="center"><div class="lead" id="lead">앞차 없음</div></div><div class="bottom"><div class="chips"><span class="chip" id="mode">ACC</span><span class="chip" id="lane">LANE</span><span class="chip" id="radar">RADAR</span><span class="chip" id="panda">PANDA</span><span class="chip" id="gear">P</span></div><div class="menu"><button id="full">전체화면</button><a href="/">7000</a><form method="post" action="/hud/toggle" style="margin:0"><button>HUD 끄기</button></form></div></div></div><script>
const canvas=document.getElementById('road'),ctx=canvas.getContext('2d');
function resize(){{canvas.width=innerWidth*devicePixelRatio;canvas.height=innerHeight*devicePixelRatio;ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0)}}
addEventListener('resize',resize);resize();
function project(x,y){{const w=innerWidth,h=innerHeight;const d=Math.max(0,Math.min(100,Number(x)||0));const p=Math.pow(d/100,.72);return [w/2+(Number(y)||0)*(w/10)*(1-p*.55),h*.91-p*h*.72]}}
function line(points,color,width){{if(!points||points.length<2)return;ctx.beginPath();let first=true;for(let i=0;i<points.length;i++){{const p=project(points[i][0],points[i][1]);if(first){{ctx.moveTo(...p);first=false}}else ctx.lineTo(...p)}}ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineCap='round';ctx.stroke()}}
function draw(s){{ctx.clearRect(0,0,innerWidth,innerHeight);const m=s.model||{{}};if(m.ready){{const path=m.path||{{}};const pp=[];for(let i=0;i<Math.min((path.x||[]).length,(path.y||[]).length);i++)pp.push([path.x[i],path.y[i]]);line(pp,'rgba(49,220,112,.95)',7);const lines=m.laneLines||[];for(let j=0;j<lines.length;j++){{const q=[];for(let i=0;i<Math.min((lines[j].x||[]).length,(lines[j].y||[]).length);i++)q.push([lines[j].x[i],lines[j].y[i]]);line(q,'rgba(230,240,255,.55)',3)}}}}
const lead=s.lead||{{}};if(lead.status){{const p=project(lead.distanceM,0);ctx.fillStyle='#ffffff';ctx.beginPath();ctx.roundRect(p[0]-22,p[1]-10,44,20,6);ctx.fill()}}
document.getElementById('speed').textContent=Math.round(s.speedKph||0);document.getElementById('setSpeed').textContent=(s.setSpeedKph||0)>0?Math.round(s.setSpeedKph):'--';document.getElementById('mode').textContent=s.mode||'ACC';document.getElementById('mode').className='chip '+(s.mode==='LONG'?'on':'');document.getElementById('lane').className='chip '+((s.selfdriveEnabled||s.selfdriveActive)?'on':'');document.getElementById('radar').className='chip '+((s.radar||{{}}).ok?'on':'bad');const faults=(s.panda||{{}}).faults||[];document.getElementById('panda').className='chip '+(faults.length?'bad':'on');document.getElementById('panda').textContent=faults.length?'PANDA !':'PANDA';document.getElementById('gear').textContent=(s.gear||'-').toUpperCase();document.getElementById('lead').innerHTML=lead.status?`${{lead.distanceM.toFixed(1)}} m<small>${{lead.relativeKph>=0?'+':''}}${{lead.relativeKph.toFixed(1)}} km/h</small>`:'앞차 없음';const b=document.getElementById('banner');if(faults.length){{b.style.display='block';b.textContent='Panda fault · '+faults.join(', ')}}else{{b.style.display='none'}}}}
async function tick(){{try{{const r=await fetch('/api/hud',{{cache:'no-store'}});draw(await r.json())}}catch(e){{}}}}setInterval(tick,250);tick();document.getElementById('full').onclick=()=>document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen();
</script></body></html>'''
