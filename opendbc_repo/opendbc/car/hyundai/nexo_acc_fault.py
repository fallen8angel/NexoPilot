from dataclasses import dataclass
from pathlib import Path


NEXO_SCC_TAKEOVER_MARKER = Path("/data/nexo_scc_takeover_active")


@dataclass(frozen=True)
class NexoAccFaultDecision:
  raw_fault: bool
  qualified_fault: bool
  reason: str
  raw_fault_duration_s: float
  healthy_seen: bool


def _nexo_takeover_active() -> bool:
  try:
    return NEXO_SCC_TAKEOVER_MARKER.exists()
  except OSError:
    # Fail safe for the NEXO takeover path: if marker state cannot be read,
    # do not promote ACCEnable alone to a longitudinal fault.
    return True


class NexoAccFaultQualifier:
  """Qualify the NEXO TCS13.ACCEnable fault without hiding real faults.

  NEXO can briefly report a non-zero ACCEnable while the longitudinal takeover
  is starting. Before takeover, a bounded grace is allowed only until the first
  healthy ACCEnable==0 sample is observed.

  Once the stock SCC takeover marker is active, ACCEnable is no longer a
  trustworthy fault source because silencing the factory SCC changes this
  signal on NEXO. During that state ACCEnable alone is ignored. Qualification
  automatically resumes when the takeover marker is no longer active.
  """

  def __init__(self, startup_grace_s: float = 2.0):
    self.startup_grace_s = max(float(startup_grace_s), 0.0)
    self.healthy_seen = False
    self.first_raw_fault_at: float | None = None

  def update(self, raw_fault: bool, now: float, *, takeover_active: bool | None = None) -> NexoAccFaultDecision:
    raw_fault = bool(raw_fault)
    now = float(now)
    if takeover_active is None:
      takeover_active = _nexo_takeover_active()

    if takeover_active:
      self.first_raw_fault_at = None
      return NexoAccFaultDecision(raw_fault, False, "signal_untrusted_takeover", 0.0, self.healthy_seen)

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
