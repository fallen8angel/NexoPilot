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
neutral_test_path = "opendbc_repo/opendbc/safety/tests/test_hyundai_nexo_neutral_ownership.py"

assert "ensure_nexo_stock_scc_silent" in interface
assert 'RuntimeError("NEXO stock SCC remained active")' in interface
assert "ret.openpilotLongitudinalControl = alpha_long and ret.alphaLongitudinalAvailable" in interface
assert "alpha_long or is_nexo" not in interface
assert "attempts: int = 3" in takeover
assert "source0_scc_total" in takeover
assert "disable_ecu" in takeover
assert "prepend_ai_parity_report" in web
assert 'NexoPilotWeb/7.7' in web
assert "StableThreadingHTTPServer" in web
assert "allow_reuse_address = True" in web
assert "daemon_threads = True" in web
assert "request_queue_size = 32" in web
assert 'messaging.sub_sock("can"' in diagnostics
assert 'messaging.sub_sock("sendcan"' in diagnostics
assert "AlphaLongitudinalEnabled" in diagnostics
assert "일반 크루즈 모드입니다" in diagnostics
assert "ECU 중지·레이더 UDS·Tester Present를 실행하지 않습니다" in diagnostics
assert "PubMaster" not in diagnostics
assert "pub_sock" not in diagnostics
assert "TestHyundaiNexoNeutralOwnership" in text(neutral_test_path)
assert "TestNexoTakeoverVerification" in text("opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py")
assert "Check NEXO verified SCC takeover and AI parity diagnostics" in workflow
assert "Test NEXO post-radar SCC silence verification" in workflow
assert "Test NEXO neutral SCC ownership" in workflow
assert "test_hyundai_nexo_neutral_ownership" in workflow

for path in (
  "opendbc_repo/opendbc/car/hyundai/interface.py",
  "opendbc_repo/opendbc/car/hyundai/nexo_takeover.py",
  "system/nexo_web/nexo_ai_parity_diagnostics.py",
  "opendbc_repo/opendbc/car/hyundai/tests/test_nexo_takeover.py",
  neutral_test_path,
):
  ast.parse(text(path), filename=path)

print("NEXO stock-cruise isolation and AI parity diagnostics: OK")
