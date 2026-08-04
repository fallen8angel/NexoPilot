#!/usr/bin/env python3
from pathlib import Path
import re

import apply_nexo_card_crash_diagnostics_v3_fix  # noqa: F401 - applies base patch and integration contract


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "system/nexo_web/nexo_diagnostics_v2.py"
source = path.read_text(encoding="utf-8")

source = source.replace(
  'return f"[{freshness}]\n" + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]',
  'return f"[{freshness}]" + chr(10) + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]',
)

pattern = re.compile(
  r'  runtime_card = \(.*?  return page\.replace\(marker, runtime_card \+ fault_card \+ marker, 1\)',
  re.DOTALL,
)
replacement = '''  core = __import__("system.nexo_web.web_core", fromlist=["*"])
  runtime_card = (
    '<div class="card"><h2>card 런타임·종료 진단</h2>'
    '<p class="desc">card 생존 여부와 heartbeat 및 마지막 실행 단계와 Python traceback을 구분해 표시합니다.</p>'
    f'<pre>{html.escape(runtime_status_output(core))}' + chr(10) + chr(10) +
    f'{html.escape(card_crash_output(core))}</pre></div>'
  )
  fault_card = (
    '<div class="card"><h2>마지막 롱컨 실패 기록</h2>'
    '<p class="desc">현재 Git과 다른 과거 기록은 과거 버전 기록으로 표시합니다. 설정 자동해제나 자동 재부팅 없이 저장됩니다.</p>'
    f'<pre>{html.escape(last_fault_output(core))}</pre></div>'
  )
  return page.replace(marker, runtime_card + fault_card + marker, 1)'''
source, count = pattern.subn(lambda _match: replacement, source, count=1)
if count != 1:
  raise RuntimeError("generated diagnostics page card block not found")

path.write_text(source, encoding="utf-8")
