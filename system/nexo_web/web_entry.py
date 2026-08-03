#!/usr/bin/env python3
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
