from dataclasses import dataclass


@dataclass(frozen=True)
class NexoAccFaultDecision:
  raw_fault: bool
  qualified_fault: bool
  reason: str
  raw_fault_duration_s: float
  healthy_seen: bool


class NexoAccFaultQualifier:
  """Qualify the NEXO TCS13.ACCEnable fault without hiding real faults.

  NEXO can briefly report a non-zero ACCEnable while the longitudinal takeover
  is starting. A bounded grace is allowed only until the first healthy
  ACCEnable==0 sample is observed. A fault that survives the grace, or any
  fault that appears after a healthy sample, is reported immediately.
  """

  def __init__(self, startup_grace_s: float = 2.0):
    self.startup_grace_s = max(float(startup_grace_s), 0.0)
    self.healthy_seen = False
    self.first_raw_fault_at: float | None = None

  def update(self, raw_fault: bool, now: float) -> NexoAccFaultDecision:
    raw_fault = bool(raw_fault)
    now = float(now)

    if not raw_fault:
      self.healthy_seen = True
      self.first_raw_fault_at = None
      return NexoAccFaultDecision(False, False, "healthy", 0.0, True)

    if self.first_raw_fault_at is None:
      self.first_raw_fault_at = now
    duration = max(0.0, now - self.first_raw_fault_at)

    if self.healthy_seen:
      return NexoAccFaultDecision(True, True, "fault_after_healthy", duration, True)
    if duration >= self.startup_grace_s:
      return NexoAccFaultDecision(True, True, "startup_fault_persisted", duration, False)
    return NexoAccFaultDecision(True, False, "startup_transient_grace", duration, False)
