#!/usr/bin/env bash
set -u

log() {
  local msg="NexoPilot Tailscale: $*"
  echo "$msg"
  command -v logger >/dev/null 2>&1 && logger -t nexopilot-tailscale "$msg" 2>/dev/null || true
}

if pgrep -x tailscaled >/dev/null 2>&1; then
  exit 0
fi

# Prefer the OS service when it exists because it preserves the installation's
# original state path, socket path and permissions.
if command -v systemctl >/dev/null 2>&1; then
  systemctl reset-failed tailscaled.service >/dev/null 2>&1 || true
  systemctl start tailscaled.service >/dev/null 2>&1 || true
  sleep 2
  if pgrep -x tailscaled >/dev/null 2>&1; then
    log "started with systemd"
    exit 0
  fi
fi

TAILSCALED=""
for candidate in \
  "$(command -v tailscaled 2>/dev/null || true)" \
  /usr/sbin/tailscaled \
  /usr/bin/tailscaled \
  /usr/local/sbin/tailscaled \
  /usr/local/bin/tailscaled \
  /data/tailscale/tailscaled \
  /data/tailscaled \
  /opt/tailscale/tailscaled; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    TAILSCALED="$candidate"
    break
  fi
done

if [ -z "$TAILSCALED" ]; then
  TAILSCALED="$(find /data /usr/local /opt -maxdepth 5 -type f -name tailscaled -perm /111 2>/dev/null | head -n 1 || true)"
fi

if [ -z "$TAILSCALED" ]; then
  log "tailscaled binary not found"
  exit 10
fi

STATE=""
for candidate in \
  /var/lib/tailscale/tailscaled.state \
  /data/tailscale/tailscaled.state \
  /persist/tailscale/tailscaled.state \
  /data/tailscaled.state; do
  if [ -s "$candidate" ]; then
    STATE="$candidate"
    break
  fi
done

if [ -z "$STATE" ]; then
  STATE="$(find /data /var/lib /persist -maxdepth 5 -type f -name tailscaled.state -size +0c 2>/dev/null | head -n 1 || true)"
fi

# Never start a fresh unauthenticated daemon with a blank state. This avoids
# replacing a previously paired Tailscale identity. A one-time local login is
# required if the old state file has actually been lost.
if [ -z "$STATE" ]; then
  log "existing Tailscale state not found; refusing unauthenticated restart"
  exit 11
fi

SOCKET_DIR=/run/tailscale
SOCKET="$SOCKET_DIR/tailscaled.sock"
mkdir -p "$SOCKET_DIR"

# There is no running daemon at this point, so an old socket is stale.
rm -f "$SOCKET"

LOG_DIR=/data/nexopilot
mkdir -p "$LOG_DIR"

nohup "$TAILSCALED" --state="$STATE" --socket="$SOCKET" \
  >>"$LOG_DIR/tailscaled.log" 2>&1 &
sleep 2

if pgrep -x tailscaled >/dev/null 2>&1; then
  log "restarted directly using persisted state"
  exit 0
fi

log "restart attempt failed; see $LOG_DIR/tailscaled.log"
exit 12
