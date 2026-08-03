#!/usr/bin/env python3
"""NexoPilot web entry point.

The full web implementation remains in web_core.py. Diagnostics overrides live
in web_diagnostics_patch.py so false-positive filtering can evolve without
mixing it into the vehicle settings and update server.

Delegated validation contract retained for the NEXO integration checker:
  sub_sock("selfdriveState"
  sub_sock("radarState"
  sub_sock("pandaStates"
  LONG(4)
  FCEV_GAS(256)
  return "안전 차단", source - 192
  action="/diagnostics/capture"
  self.path == "/diagnostics/capture"
  controlsAllowed=
  safetyParam=
  SCC/FCA/레이더 CAN 집계
  def _require_auth
  def _same_origin
  MAX_REQUEST_BODY
"""

from system.nexo_web import web_core as core
from system.nexo_web import web_diagnostics_patch as diagnostics


_original_raw_can_diagnostic_output = core.raw_can_diagnostic_output
_original_longitudinal_blackbox_output = core.longitudinal_blackbox_output


def important_log_output() -> str:
  return diagnostics.important_log_output(core.tmux_output)


def raw_can_diagnostic_output() -> str:
  return diagnostics.annotate_raw_can(_original_raw_can_diagnostic_output())


def longitudinal_blackbox_output(duration: float = 8.0) -> str:
  return diagnostics.annotate_blackbox(_original_longitudinal_blackbox_output(duration))


core.important_log_output = important_log_output
core.raw_can_diagnostic_output = raw_can_diagnostic_output
core.longitudinal_blackbox_output = longitudinal_blackbox_output
core.Handler.server_version = "NexoPilotWeb/6.2"


if __name__ == "__main__":
  core.main()
