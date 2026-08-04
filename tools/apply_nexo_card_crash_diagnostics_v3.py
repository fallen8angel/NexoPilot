#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
  return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
  target = ROOT / path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
  source = read(path)
  count = source.count(old)
  if count != 1:
    raise RuntimeError(f"{path}: expected one match, found {count}: {old[:100]!r}")
  write(path, source.replace(old, new, 1))


def create_runtime_diagnostics() -> None:
  write("selfdrive/car/nexo_runtime_diagnostics.py", '''from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


NEXO_CARD_CRASH_LOG = Path("/data/nexo_card_crash.txt")
NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")


def _param_text(params: Params, key: str) -> str:
  try:
    value = params.get(key)
    if value is None:
      return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
  except Exception:
    return ""


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
  temporary = path.with_suffix(path.suffix + ".tmp")
  temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
  temporary.replace(path)


def set_nexo_runtime_state(params: Params, state: str, stage: str, reason: str = "") -> None:
  """Publish low-rate NEXO card state for the port-7000 diagnostics page."""
  try:
    params.put("NexoCardSessionState", state)
    params.put("NexoCardStage", stage)
    if reason:
      params.put("NexoCardSessionReason", reason)
    else:
      params.remove("NexoCardSessionReason")
  except Exception as error:
    cloudlog.warning(f"NEXO runtime state publish failed: {error}")


def record_nexo_long_success(params: Params) -> None:
  payload = {
    "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
    "git_commit": _param_text(params, "GitCommit"),
    "git_branch": _param_text(params, "GitBranch"),
    "result": "radar tracks enabled; card runtime guard armed",
  }
  try:
    _write_json_atomic(NEXO_LONG_SUCCESS_LOG, payload)
    params.remove("NexoLongitudinalFailure")
    set_nexo_runtime_state(params, "active", "longitudinal_active")
  except Exception as error:
    cloudlog.warning(f"NEXO success marker write failed: {error}")


def record_nexo_card_crash(params: Params, stage: str, error: BaseException, traceback_text: str) -> None:
  """Persist an uncaught card exception before the process exits."""
  payload = {
    "wall_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
    "git_commit": _param_text(params, "GitCommit"),
    "git_branch": _param_text(params, "GitBranch"),
    "stage": stage,
    "exception_type": type(error).__name__,
    "reason": str(error),
    "traceback": traceback_text,
    "longitudinal_failure": _param_text(params, "NexoLongitudinalFailure"),
  }
  try:
    _write_json_atomic(NEXO_CARD_CRASH_LOG, payload)
    params.put("NexoCardLastCrash", f"{payload['wall_time']} {payload['exception_type']}: {payload['reason']}")
    set_nexo_runtime_state(params, "crashed", stage, str(error))
  except Exception as write_error:
    cloudlog.exception(f"NEXO card crash snapshot write failed: {write_error}")
''')


def patch_card() -> None:
  path = "selfdrive/car/card.py"
  replace_once(path, "import threading\n", "import threading\nimport traceback\n")
  replace_once(
    path,
    "from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard\n",
    "from openpilot.selfdrive.car.nexo_guard import NexoStockSccRuntimeGuard\n"
    "from openpilot.selfdrive.car.nexo_runtime_diagnostics import (\n"
    "  record_nexo_card_crash, record_nexo_long_success, set_nexo_runtime_state,\n"
    ")\n",
  )
  replace_once(
    path,
    "    self.nexo_long_init_failed = False\n\n    if self.CP.secOcRequired:",
    "    self.nexo_long_init_failed = False\n"
    "    self.nexo_stage = \"constructed\"\n"
    "    self.nexo_session_state = \"waiting_for_long_init\" if self.nexo_stock_scc_guard.enabled else \"stock_cruise\"\n"
    "    self.nexo_last_heartbeat = 0.0\n"
    "    if self.CP.carFingerprint == \"HYUNDAI_NEXO_1ST_GEN\":\n"
    "      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)\n\n"
    "    if self.CP.secOcRequired:",
  )
  replace_once(
    path,
    "  def _handle_nexo_long_failure(self, error: Exception) -> bool:\n",
    "  def _update_nexo_heartbeat(self, force: bool = False) -> None:\n"
    "    if self.CP.carFingerprint != \"HYUNDAI_NEXO_1ST_GEN\":\n"
    "      return\n"
    "    now = time.monotonic()\n"
    "    if not force and now - self.nexo_last_heartbeat < 1.0:\n"
    "      return\n"
    "    self.nexo_last_heartbeat = now\n"
    "    try:\n"
    "      self.params.put(\"NexoCardHeartbeatMono\", f\"{now:.3f}\")\n"
    "      self.params.put(\"NexoCardStage\", self.nexo_stage)\n"
    "      self.params.put(\"NexoCardSessionState\", self.nexo_session_state)\n"
    "    except Exception as error:\n"
    "      cloudlog.warning(f\"NEXO card heartbeat publish failed: {error}\")\n\n"
    "  def _handle_nexo_long_failure(self, error: Exception) -> bool:\n",
  )
  old = '''    # Restore the factory ECU stream when possible, but never change the user's
    # longitudinal/experimental settings and never request an automatic reboot.
    try:
      self.CI.deinit(self.CP, *self.can_callbacks)
    except Exception as restore_error:
      cloudlog.exception(f"NEXO stock SCC restore request failed: {restore_error}")

    self.nexo_stock_scc_guard.disarm()
    self.nexo_long_init_failed = True
    self.last_actuators_output = structs.CarControl.Actuators()
    return True
'''
  new = '''    # Do not issue a second diagnostic sequence here. Initial radar failure paths
    # restore stock communication inside CarInterface.init(), and a runtime guard
    # trip already proves that the factory SCC stream is present. Re-entering UDS
    # from the card loop can terminate the process or create a new cluster fault.
    self.nexo_stock_scc_guard.disarm()
    self.nexo_long_init_failed = True
    self.nexo_session_state = "failed_latched"
    self.nexo_stage = "longitudinal_failed_latched"
    self.last_actuators_output = structs.CarControl.Actuators()
    set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage, str(error))
    self._update_nexo_heartbeat(force=True)
    return True
'''
  replace_once(path, old, new)
  replace_once(
    path,
    "  def state_update(self) -> tuple[car.CarState, structs.RadarDataT | None]:\n    \"\"\"carState update loop, driven by can\"\"\"\n\n",
    "  def state_update(self) -> tuple[car.CarState, structs.RadarDataT | None]:\n"
    "    \"\"\"carState update loop, driven by can\"\"\"\n\n"
    "    self.nexo_stage = \"state_update\"\n",
  )
  replace_once(
    path,
    "    if self.nexo_long_init_failed:\n      return\n\n    if not self.initialized_prev:",
    "    if self.nexo_long_init_failed:\n"
    "      self.nexo_stage = \"longitudinal_failed_latched\"\n"
    "      self._update_nexo_heartbeat()\n"
    "      return\n\n"
    "    if not self.initialized_prev:\n"
    "      self.nexo_stage = \"longitudinal_initializing\"\n"
    "      self.nexo_session_state = \"initializing\"\n"
    "      set_nexo_runtime_state(self.params, self.nexo_session_state, self.nexo_stage)\n",
  )
  replace_once(
    path,
    "      self.params.remove(\"NexoLongitudinalFailure\")\n      # Arm the raw-CAN guard only after the diagnostic takeover completed.\n",
    "      self.params.remove(\"NexoLongitudinalFailure\")\n"
    "      self.nexo_session_state = \"active\"\n"
    "      self.nexo_stage = \"longitudinal_active\"\n"
    "      record_nexo_long_success(self.params)\n"
    "      # Arm the raw-CAN guard only after the diagnostic takeover completed.\n",
  )
  replace_once(
    path,
    "    if self.sm.all_alive(['carControl']):\n      # send car controls over can\n",
    "    if self.sm.all_alive(['carControl']):\n"
    "      self.nexo_stage = \"carcontroller_apply\"\n"
    "      # send car controls over can\n",
  )
  replace_once(
    path,
    "  def step(self):\n    CS, RD = self.state_update()\n\n    self.state_publish(CS, RD)\n",
    "  def step(self):\n"
    "    CS, RD = self.state_update()\n\n"
    "    self.nexo_stage = \"state_publish\"\n"
    "    self.state_publish(CS, RD)\n",
  )
  replace_once(
    path,
    "    self.initialized_prev = initialized\n    self.CS_prev = CS\n",
    "    self.initialized_prev = initialized\n"
    "    self.CS_prev = CS\n"
    "    self.nexo_stage = \"idle\" if not self.nexo_long_init_failed else \"longitudinal_failed_latched\"\n"
    "    self._update_nexo_heartbeat()\n",
  )
  replace_once(
    path,
    "def main():\n  config_realtime_process(4, Priority.CTRL_HIGH)\n  car = Car()\n  car.card_thread()\n",
    "def main():\n"
    "  config_realtime_process(4, Priority.CTRL_HIGH)\n"
    "  params = Params()\n"
    "  card_process = None\n"
    "  try:\n"
    "    card_process = Car()\n"
    "    card_process.card_thread()\n"
    "  except Exception as error:\n"
    "    stage = getattr(card_process, \"nexo_stage\", \"card_constructor\")\n"
    "    record_nexo_card_crash(params, stage, error, traceback.format_exc())\n"
    "    raise\n",
  )


def patch_interface() -> None:
  path = "opendbc_repo/opendbc/car/hyundai/interface.py"
  old = '''  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                   0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                   uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)
'''
  new = '''  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL,
                                   0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX,
                                   uds.MESSAGE_TYPE.NORMAL])

    # NEXO restoration must only re-enable the factory SCC stream. Calling init()
    # here would run the radar programming sequence again while handling a fault.
    if CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and CP.openpilotLongitudinalControl:
      addr, bus = 0x7D0, 0
      restored = disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)
      _trace_nexo_long_init(f"DEINIT stock SCC communication restore acknowledged={restored}")
      return

    CarInterface.init(CP, can_recv, can_send, communication_control)
'''
  replace_once(path, old, new)


def patch_web() -> None:
  path = "system/nexo_web/nexo_diagnostics_v2.py"
  replace_once(path, "import html\nimport time\n", "import html\nimport json\nimport time\n")
  replace_once(
    path,
    'NEXO_LAST_FAULT_LOG = Path("/data/nexo_last_fault.txt")\n',
    'NEXO_LAST_FAULT_LOG = Path("/data/nexo_last_fault.txt")\n'
    'NEXO_CARD_CRASH_LOG = Path("/data/nexo_card_crash.txt")\n'
    'NEXO_LONG_SUCCESS_LOG = Path("/data/nexo_long_success.txt")\n',
  )
  old = '''def last_fault_output() -> str:
  try:
    output = NEXO_LAST_FAULT_LOG.read_text(encoding="utf-8", errors="replace")
  except OSError:
    return "저장된 자동 복구 기록이 없습니다."
  return output[-60000:] or "자동 복구 기록이 비어 있습니다."
'''
  new = '''def _json_log(path: Path) -> dict[str, object] | None:
  try:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))
  except (OSError, json.JSONDecodeError):
    return None


def _record_freshness(core, payload: dict[str, object] | None) -> str:
  if not payload:
    return "기록 없음"
  current = core.git_value("rev-parse", "--short", "HEAD")
  recorded = str(payload.get("git_commit", ""))
  if recorded and current != "확인 불가" and not recorded.startswith(current) and not current.startswith(recorded[:9]):
    return f"과거 버전 기록 (기록 {recorded[:9]}, 현재 {current[:9]})"
  return "현재 버전 기록 후보"


def last_fault_output(core) -> str:
  payload = _json_log(NEXO_LAST_FAULT_LOG)
  if payload is None:
    return "저장된 롱컨 실패 기록이 없습니다."
  freshness = _record_freshness(core, payload)
  return f"[{freshness}]\n" + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]


def card_crash_output(core) -> str:
  payload = _json_log(NEXO_CARD_CRASH_LOG)
  if payload is None:
    return "저장된 card Python crash traceback이 없습니다."
  freshness = _record_freshness(core, payload)
  return f"[{freshness}]\n" + json.dumps(payload, ensure_ascii=False, indent=2)[-60000:]


def runtime_status_output(core) -> str:
  params = core.Params()
  def value(key: str) -> str:
    try:
      raw = params.get(key)
      if raw is None:
        return ""
      return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    except Exception:
      return ""

  code, processes = core.run_command(["ps", "-eo", "pid,args"], timeout=3)
  card_processes = [] if code != 0 else [
    line.strip() for line in processes.splitlines()
    if "selfdrive.car.card" in line or "./card" in line
  ]
  heartbeat = value("NexoCardHeartbeatMono")
  try:
    heartbeat_age = max(0.0, time.monotonic() - float(heartbeat))
    heartbeat_text = f"{heartbeat_age:.1f}초 전"
  except Exception:
    heartbeat_text = "확인 불가"

  success = _json_log(NEXO_LONG_SUCCESS_LOG)
  lines = [
    f"card 프로세스: {'실행 중' if card_processes else '실행 중 아님'}",
    *(card_processes[:4] or ["프로세스 행 없음"]),
    f"card heartbeat: {heartbeat_text}",
    f"세션 상태: {value('NexoCardSessionState') or '확인 불가'}",
    f"마지막 단계: {value('NexoCardStage') or '확인 불가'}",
    f"현재 실패 이유: {value('NexoCardSessionReason') or value('NexoLongitudinalFailure') or '없음'}",
    f"마지막 성공 기록: {json.dumps(success, ensure_ascii=False) if success else '없음'}",
  ]
  return "\n".join(lines)
'''
  replace_once(path, old, new)
  replace_once(
    path,
    '  lines.extend(["", "[마지막 롱컨 실패 기록]", last_fault_output()])\n  lines.extend(["", "[핵심 오류 로그]", core.important_log_output()])\n',
    '  lines.extend(["", "[card 런타임 상태]", runtime_status_output(core)])\n'
    '  lines.extend(["", "[마지막 롱컨 실패 기록]", last_fault_output(core)])\n'
    '  lines.extend(["", "[마지막 card crash traceback]", card_crash_output(core)])\n'
    '  lines.extend(["", "[핵심 오류 로그]", core.important_log_output()])\n',
  )
  old_card = '''  fault_card = (
    '<div class="card"><h2>마지막 롱컨 실패 기록</h2>'
    '<p class="desc">순정 SCC 재등장 또는 초기화 실패 직전 상태와 최근 5초 CAN 기록입니다. 설정 자동해제나 자동 재부팅 없이 저장됩니다.</p>'
    f'<pre>{html.escape(last_fault_output())}</pre></div>'
  )
  return page.replace(marker, fault_card + marker, 1)
'''
  new_card = '''  runtime_card = (
    '<div class="card"><h2>card 런타임·종료 진단</h2>'
    '<p class="desc">card 생존 여부와 heartbeat 및 마지막 실행 단계와 Python traceback을 구분해 표시합니다.</p>'
    f'<pre>{html.escape(runtime_status_output(__import__("system.nexo_web.web_core", fromlist=["*"])))}\n\n'
    f'{html.escape(card_crash_output(__import__("system.nexo_web.web_core", fromlist=["*"])))}</pre></div>'
  )
  fault_card = (
    '<div class="card"><h2>마지막 롱컨 실패 기록</h2>'
    '<p class="desc">현재 Git과 다른 과거 기록은 과거 버전 기록으로 표시합니다. 설정 자동해제나 자동 재부팅 없이 저장됩니다.</p>'
    f'<pre>{html.escape(last_fault_output(__import__("system.nexo_web.web_core", fromlist=["*"])))}</pre></div>'
  )
  return page.replace(marker, runtime_card + fault_card + marker, 1)
'''
  replace_once(path, old_card, new_card)


def patch_checks() -> None:
  path = "tools/check_nexo_diagnostics.py"
  replace_once(
    path,
    '  "selfdrive/car/nexo_diagnostics.py",\n',
    '  "selfdrive/car/nexo_diagnostics.py",\n  "selfdrive/car/nexo_runtime_diagnostics.py",\n',
  )
  replace_once(
    path,
    '  card = sources["selfdrive/car/card.py"]\n',
    '  card = sources["selfdrive/car/card.py"]\n  runtime = sources["selfdrive/car/nexo_runtime_diagnostics.py"]\n',
  )
  replace_once(
    path,
    '  for token in ("record_nexo_fault_snapshot", "selfdriveState", "radarState"):\n    require(token in card, f"card diagnostic connection missing: {token}")\n',
    '  for token in ("record_nexo_fault_snapshot", "record_nexo_card_crash", "record_nexo_long_success",\n'
    '                "NexoCardHeartbeatMono", "nexo_stage", "selfdriveState", "radarState"):\n'
    '    require(token in card, f"card diagnostic connection missing: {token}")\n'
    '  for token in ("NEXO_CARD_CRASH_LOG", "NEXO_LONG_SUCCESS_LOG", "record_nexo_card_crash",\n'
    '                "record_nexo_long_success", "set_nexo_runtime_state", "traceback"):\n'
    '    require(token in runtime, f"card runtime diagnostics missing: {token}")\n',
  )
  replace_once(
    path,
    '  for token in ("[SCC/FCA 분리 자동 판정]", "[sendcan 요청 → Panda 결과]",\n                "[주요 SCC/FCA 신호 DBC 해석]", "last_fault_output",\n                "순정 FCA11/FCA12 수신은 정상"):\n',
    '  for token in ("[SCC/FCA 분리 자동 판정]", "[sendcan 요청 → Panda 결과]",\n'
    '                "[주요 SCC/FCA 신호 DBC 해석]", "last_fault_output",\n'
    '                "runtime_status_output", "card_crash_output", "과거 버전 기록",\n'
    '                "순정 FCA11/FCA12 수신은 정상"):\n',
  )
  replace_once(
    path,
    '  require("elapsed_ms" in interface, "disable ECU timing trace missing")\n',
    '  require("elapsed_ms" in interface, "disable ECU timing trace missing")\n'
    '  require("DEINIT stock SCC communication restore" in interface, "NEXO deinit restore trace missing")\n'
    '  require("CarInterface.init(CP, can_recv, can_send, communication_control)" in interface,\n'
    '          "non-NEXO deinit fallback missing")\n',
  )

  policy = "tools/check_nexo_uds_failure_policy.py"
  source = read(policy)
  source = source.replace(
    '    "self.CI.deinit(self.CP, *self.can_callbacks)",\n',
    '    "record_nexo_card_crash",\n    "record_nexo_long_success",\n    "NexoCardHeartbeatMono",\n',
  )
  source = source.replace(
    '  require("def disarm(self)" in guard, "runtime guard disarm support missing")\n',
    '  require("self.CI.deinit(self.CP, *self.can_callbacks)" not in card,\n'
    '          "card failure handler must not re-enter the radar UDS sequence")\n'
    '  require("def disarm(self)" in guard, "runtime guard disarm support missing")\n',
  )
  write(policy, source)


def main() -> None:
  create_runtime_diagnostics()
  patch_card()
  patch_interface()
  patch_web()
  patch_checks()
  print("Applied NEXO card crash diagnostics v3 and safe failure latch")


if __name__ == "__main__":
  main()
