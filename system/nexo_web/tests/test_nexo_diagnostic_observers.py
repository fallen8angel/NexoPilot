import ast
from collections.abc import Iterable
from pathlib import Path


WEB_ROOT = Path(__file__).parents[1]


def _load_function(filename: str, name: str, namespace=None):
  path = WEB_ROOT / filename
  tree = ast.parse(path.read_text(encoding="utf-8"))
  function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
  module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
  loaded = dict(namespace or {})
  exec(compile(module, str(path), "exec"), loaded)
  return loaded[name]


_signal_for_sources = _load_function(
  "nexo_cluster_warning_diagnostics.py", "_signal_for_sources", {"Iterable": Iterable},
)
parking_sensor_verdict_lines = _load_function(
  "nexo_diagnostics_v2.py", "parking_sensor_verdict_lines",
)


def test_fca_signal_sources_are_not_mixed() -> None:
  snapshot = {
    "can": {
      (0, "FCA11"): {"FCA_Status": 1},
      (128, "FCA11"): {"FCA_Status": 0},
      (0, "FCA12"): {"FCA_USM": 0},
      (128, "FCA12"): {"FCA_USM": 1, "FCA_DrvSetState": 2},
    },
  }

  assert _signal_for_sources(snapshot, "FCA11", "FCA_Status", (0,)) == 1
  assert _signal_for_sources(snapshot, "FCA11", "FCA_Status", range(128, 192)) == 0
  assert _signal_for_sources(snapshot, "FCA12", "FCA_USM", range(128, 192)) == 1


def test_parking_sensor_static_zero_is_inconclusive() -> None:
  verdict = parking_sensor_verdict_lines(True, True, False, False, False)

  assert verdict[0].startswith("[현재 신호로 상태 판정 불가]")
  assert any("0이라는 이유만으로" in line for line in verdict)


def test_parking_sensor_decoded_change_is_confirmed() -> None:
  verdict = parking_sensor_verdict_lines(True, True, True, True, True)

  assert verdict == ("[주차센서 신호 확인] 위치별 표시·경고 값이 실제로 변했습니다.",)
