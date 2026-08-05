#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


interface = text("opendbc_repo/opendbc/car/hyundai/interface.py")
takeover = text("opendbc_repo/opendbc/car/hyundai/nexo_takeover.py")
web = text("system/nexo_web/web.py")
diagnostics = text("system/nexo_web/nexo_ai_parity_diagnostics.py")
workflow = text(".github/workflows/nexo-validation.yml")

assert "ensure_nexo_stock_scc_silent" in interface
assert 'RuntimeError("NEXO stock SCC remained active")' in interface
assert "attempts: int = 3" in takeover
assert "source0_scc_total" in takeover
assert "disable_ecu" in takeover
assert "prepend_ai_parity_report" in web
assert 'NexoPilotWeb/7.7' in web
assert "StableThreadingHTTPServer" in web
assert "allow_reuse_address = True" in web
assert "daemon_threads = True" in web
assert "request_queue_size = 32" in web
assert "messaging.sub_sock("can"" in diagnostics
assert "messaging.sub_sock("sendcan"" in diagnostics
assert "PubMaster" not in diagnostics
assert "pub_sock" not in diagnostics
assert "TestHyundaiNexoNeutralSccOwnership" in text("opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral.py")
assert "TestNexoTakeoverVerification" in text("opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py")
assert "Check NEXO verified SCC takeover and AI parity diagnostics" in workflow
assert "Test NEXO post-radar SCC silence verification" in workflow
assert "Test NEXO neutral SCC ownership" in workflow

for path in (
  "opendbc_repo/opendbc/car/hyundai/nexo_takeover.py",
  "system/nexo_web/nexo_ai_parity_diagnostics.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py",
  "opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral.py",
):
  ast.parse(text(path), filename=path)

print("NEXO verified SCC takeover and AI parity diagnostics: OK")
