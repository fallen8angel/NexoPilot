#!/usr/bin/env bash

if [ "$(cat /data/nexopilot/force_nexo 2>/dev/null)" = "1" ]; then
  export FINGERPRINT="$(python3 - <<'PY'
from opendbc.car.hyundai.values import CAR
print(CAR.HYUNDAI_NEXO_1ST_GEN.value)
PY
)"
  echo "NexoPilot: forcing fingerprint ${FINGERPRINT}"
fi

exec ./launch_chffrplus.sh
