#!/usr/bin/env python3
from __future__ import annotations

import re

import apply_nexo_ai_runtime_guard as patch


_original_subn = re.subn


def literal_subn(pattern, replacement, string, count=0, flags=0):
  if isinstance(replacement, str):
    return _original_subn(pattern, lambda _match: replacement, string, count=count, flags=flags)
  return _original_subn(pattern, replacement, string, count=count, flags=flags)


patch.re.subn = literal_subn
patch.main()
