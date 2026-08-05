#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "opendbc_repo/opendbc/safety/modes/hyundai.h"
text = path.read_text(encoding="utf-8")

for address in ("0x420", "0x421", "0x50A", "0x389"):
  old = f"{{{address}, 0,       8, .check_relay = false, .disable_static_blocking = true}}"
  new = f"{{{address}, 0,       8, .check_relay = true, .disable_static_blocking = true}}"
  if old not in text and new not in text:
    raise RuntimeError(f"NEXO SCC relay entry not found: {address}")
  text = text.replace(old, new, 1)

old_comment = """// NEXO suppresses the radar SCC over UDS and then owns SCC through the dynamic
// forwarding hook below. Do not let the generic relay detector permanently
// latch all TX while the short takeover transition is being observed; the
// source-0 runtime guard in card remains the fail-closed authority.
"""
new_comment = """// NEXO suppresses the radar SCC over UDS and then owns SCC through the dynamic
// forwarding hook below. Keep generic relay detection enabled as an independent
// fail-closed layer alongside the post-radar verifier and card runtime guard.
"""
text = text.replace(old_comment, new_comment, 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
print("NEXO Panda relay protection restored")
