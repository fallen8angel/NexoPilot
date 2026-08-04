#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import apply_nexo_ai_runtime_guard as patch


_original_subn = re.subn


def literal_subn(pattern, replacement, string, count=0, flags=0):
  if isinstance(replacement, str):
    return _original_subn(pattern, lambda _match: replacement, string, count=count, flags=flags)
  return _original_subn(pattern, replacement, string, count=count, flags=flags)


patch.re.subn = literal_subn
patch.main()

checker = Path(__file__).resolve().parents[1] / "tools/check_nexo_integration.py"
source = checker.read_text(encoding="utf-8")
source = source.replace(
  '    "getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE",\n',
  '    \'getattr(msg, "src", -1) == NEXO_STOCK_SCC_SOURCE\',\n',
)
checker.write_text(source, encoding="utf-8")
