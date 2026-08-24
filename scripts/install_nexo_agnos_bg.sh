#!/usr/bin/env bash
set -u

SOURCE="/data/openpilot/selfdrive/assets/images/nexo_agnos_bg.png"
TARGET="/usr/comma/bg.png"
BACKUP="/data/agnos-bg-original.png"
EXPECTED="72f48557ff24bf1bd6426283cc180849720571748ef72b77034f03372cd9dff4"

[ -f "$SOURCE" ] || exit 0
[ -f "$TARGET" ] || exit 0

current="$(sha256sum "$TARGET" | awk '{print $1}')"
[ "$current" = "$EXPECTED" ] && exit 0

source_hash="$(sha256sum "$SOURCE" | awk '{print $1}')"
if [ "$source_hash" != "$EXPECTED" ]; then
  echo "NexoPilot: refusing unexpected AGNOS background source"
  exit 1
fi

if [ ! -f "$BACKUP" ]; then
  sudo cp -p "$TARGET" "$BACKUP" || exit 1
fi

remounted=0
restore_ro() {
  if [ "$remounted" = "1" ]; then
    sync
    sudo mount -o remount,ro / >/dev/null 2>&1 || true
  fi
}
trap restore_ro EXIT

sudo mount -o remount,rw / || exit 1
remounted=1
sudo install -o root -g root -m 0644 "$SOURCE" "$TARGET" || exit 1
sync

installed="$(sha256sum "$TARGET" | awk '{print $1}')"
if [ "$installed" != "$EXPECTED" ]; then
  echo "NexoPilot: AGNOS background verification failed"
  exit 1
fi

echo "NexoPilot: AGNOS NEXO background installed; it will appear on the next reboot"
