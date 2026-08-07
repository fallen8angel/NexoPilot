from __future__ import annotations

import html


NAV_ITEMS = (
  ("home", "/", "홈"),
  ("settings", "/settings", "설정"),
  ("camera", "/live", "카메라"),
  ("diagnostics", "/diagnostics", "진단"),
  ("system", "/system", "시스템"),
  ("remote", "/remote", "원격"),
)


def nav(active: str) -> str:
  return '<nav class="nav" style="grid-template-columns:repeat(6,1fr)">' + "".join(
    f'<a class="{"active" if key == active else ""}" href="{path}">{label}</a>'
    for key, path, label in NAV_ITEMS
  ) + "</nav>"


def install(carrot_ui) -> None:
  """Extend the existing Carrot-style bottom navigation without changing controls."""
  carrot_ui._nav = nav


def remote_page(core) -> str:
  return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoPilot 원격</title><style>{core.base_css()}
:root{{color-scheme:dark}}body{{background:#080a0d}}main{{max-width:940px;margin:auto;padding:18px 18px 94px}}.hero{{padding:22px;border-radius:24px;background:#191d22;border:1px solid #2d333b;margin:14px 0}}.hero h1{{margin:0;font-size:30px}}.eyebrow{{font-size:12px;color:#8b949e;letter-spacing:.08em}}.status{{display:flex;align-items:center;gap:12px;margin-top:18px}}.dot{{width:14px;height:14px;border-radius:50%;background:#8b949e}}.status-label{{font-size:22px;font-weight:800}}.mini{{font-size:12px;color:#8b949e;line-height:1.55}}.nav{{position:fixed;z-index:50;left:50%;bottom:10px;transform:translateX(-50%);width:min(900px,calc(100% - 22px));display:grid;grid-template-columns:repeat(6,1fr);gap:5px;background:#15191ef2;border:1px solid #30363d;border-radius:20px;padding:7px;backdrop-filter:blur(18px)}}.nav a{{color:#8b949e;text-align:center;padding:11px 3px;border-radius:14px;font-size:12px;font-weight:700;text-decoration:none}}.nav a.active{{background:#2b3139;color:white}}@media(max-width:620px){{.nav a{{font-size:10px}}.hero h1{{font-size:26px}}}}
</style></head><body><main>
  <div class="hero"><div class="eyebrow">NEXOPILOT · REMOTE EXPERIMENT</div><h1>원격</h1><div class="status"><span class="dot"></span><div><div class="status-label">미지원 · 업데이트 예정</div><div class="mini">원격 기능은 나중에 추가 및 실험하기 위한 메뉴 자리만 마련했습니다.</div></div></div></div>
  <div class="card"><div class="title">현재 상태</div><div class="row"><span>원격 제어</span><span class="value">미지원</span></div><div class="row"><span>원격 주차·이동</span><span class="value">미지원</span></div><div class="row"><span>원격 카메라·센서 연동</span><span class="value">미지원</span></div><p class="warning">현재 이 화면에는 차량을 움직이거나 Panda/CAN 명령을 보내는 기능이 없습니다. 향후 기능을 추가할 때 별도 안전검증 후 단계적으로 활성화합니다.</p></div>
  {nav("remote")}</main></body></html>'''
