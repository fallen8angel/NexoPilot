#!/usr/bin/env python3
from pathlib import Path

source_path = Path(__file__).with_name("apply_nexo_neutral_scc_ownership_patch.py")
source = source_path.read_text(encoding="utf-8")

start_marker = 'replace_once(\n  ".github/workflows/nexo-validation.yml",'
end_marker = '\n# Remove the one-shot patch mechanism from the resulting branch.'
start = source.index(start_marker)
end = source.index(end_marker, start)
source = source[:start] + source[end:]
source = source.replace(
  '(ROOT / ".github/workflows/nexo-neutral-scc-ownership-patch.yml").unlink(missing_ok=True)\n',
  '',
)

namespace = {
  "__file__": str(source_path),
  "__name__": "__main__",
}
exec(compile(source, str(source_path), "exec"), namespace)
Path(__file__).unlink(missing_ok=True)
