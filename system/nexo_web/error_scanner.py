#!/usr/bin/env python3
"""Read-only runtime error scanner for the NexoPilot local diagnostics page.

This module does not change vehicle controls, CAN messages, radar configuration,
or any Params values. It only reads recent logs and summarizes suspicious lines.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


KEYWORDS = re.compile(
  r"(traceback|exception|fatal|error|fault|can\s*(error|invalid|timeout)|"
  r"controls?\s*mismatch|radar|fca|aeb|scc|disable_ecu|ecu|bus\s*off|"
  r"panda.*(lost|fault|error)|process.*(died|crash|exit))",
  re.IGNORECASE,
)

IGNORE = re.compile(
  r"(error\s*count\s*[:=]\s*0|fault\s*count\s*[:=]\s*0|no\s+error|without\s+error)",
  re.IGNORECASE,
)


@dataclass(frozen=True)
class ScanResult:
  level: str
  summary: str
  details: str


def _run(args: list[str], timeout: int = 8) -> tuple[int, str]:
  try:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    text = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    return result.returncode, text
  except Exception as error:
    return -1, str(error)


def _tmux_log(lines: int = 1200) -> str:
  code, sessions = _run(["tmux", "list-sessions", "-F", "#{session_name}"], timeout=3)
  if code != 0 or not sessions.strip():
    return f"tmux 세션을 찾지 못했습니다.\n{sessions}".strip()

  session = sessions.splitlines()[0].strip()
  _, output = _run(["tmux", "capture-pane", "-p", "-t", session, "-S", f"-{lines}"], timeout=6)
  return output


def scan_errors(max_matches: int = 120) -> ScanResult:
  """Return a read-only summary of recent suspicious runtime log lines."""
  log = _tmux_log()
  if not log:
    return ScanResult("주의", "검사할 로그가 없습니다.", "tmux 로그가 비어 있습니다.")

  matches: list[str] = []
  seen: set[str] = set()
  for raw_line in log.splitlines():
    line = raw_line.strip()
    if not line or IGNORE.search(line) or not KEYWORDS.search(line):
      continue
    normalized = re.sub(r"\s+", " ", line)
    if normalized in seen:
      continue
    seen.add(normalized)
    matches.append(normalized)
    if len(matches) >= max_matches:
      break

  if not matches:
    return ScanResult(
      "정상",
      "최근 로그에서 주요 오류 키워드가 발견되지 않았습니다.",
      "CAN·레이더·FCA/AEB 경고는 차량 자체 DTC가 남아 있으면 openpilot 로그에 나타나지 않을 수도 있습니다.",
    )

  critical_words = re.compile(r"(traceback|fatal|controls?\s*mismatch|bus\s*off|process.*(died|crash))", re.IGNORECASE)
  level = "오류" if any(critical_words.search(line) for line in matches) else "주의"
  return ScanResult(level, f"의심 로그 {len(matches)}건을 찾았습니다.", "\n".join(matches))


if __name__ == "__main__":
  result = scan_errors()
  print(f"[{result.level}] {result.summary}")
  print(result.details)
