#!/bin/sh
# Copy a finished recording and its model from the board (over adb) to the laptop.
#   device/app/pull_recording.sh                 the newest recording, into <repo>/data/real/board/<name>/
#   device/app/pull_recording.sh rec-20261113-141500
#   device/app/pull_recording.sh "" /some/other/repo/data/real/board     another place (name "" = newest)
# It copies recordings/<name>/ (features, raw clips, events.jsonl, history.npz) and, if they exist,
# models/<name>.json and models/<name>.early.json, plus state.json and settings.json as they are now.
# Nothing on the board is changed. data/ is never committed.
set -e
BOARD=/home/arduino/ArduinoApps/effente/data
NAME="$1"
DEST_ROOT="${2:-$(cd "$(dirname "$0")/../.." && pwd)/data/real/board}"
if [ -z "$NAME" ]; then
  NAME=$(adb shell "ls -1 $BOARD/recordings 2>/dev/null | sort | tail -1" | tr -d '\r')
fi
if [ -z "$NAME" ]; then
  echo "error: no recordings on the board ($BOARD/recordings)"
  exit 1
fi
DEST="$DEST_ROOT/$NAME"
mkdir -p "$DEST/models"
adb pull "$BOARD/recordings/$NAME" "$DEST/" >/dev/null
[ -d "$DEST/$NAME" ] && mv "$DEST/$NAME" "$DEST/recording"
for f in "models/$NAME.json" "models/$NAME.early.json"; do
  adb shell "test -f $BOARD/$f" && adb pull "$BOARD/$f" "$DEST/models/" >/dev/null || echo "note: no $f on the board"
done
for f in state.json settings.json; do
  adb shell "test -f $BOARD/$f" && adb pull "$BOARD/$f" "$DEST/" >/dev/null || true
done
echo "pulled $NAME to $DEST"
find "$DEST" -type f | sort | while read -r f; do printf '  %8s  %s\n' "$(wc -c < "$f" | tr -d ' ')" "${f#"$DEST"/}"; done
