#!/bin/sh
# Copy the effente app (sketch, Linux side and our Python code) onto a UNO Q over adb, then restart it.
#   device/app/deploy.sh            (run from the repo root, with the board connected by USB)
# Data on the board (recordings, models, state in ~/ArduinoApps/effente/data) is left alone.
set -e
cd "$(dirname "$0")/../.."
APP=/home/arduino/ArduinoApps/effente
TMP=$(mktemp -d)
mkdir -p "$TMP/effente/python/lib/device/intake" "$TMP/effente/python/lib/effente" "$TMP/effente/sketch"
cp device/app/effente/app.yaml "$TMP/effente/"
cp device/app/effente/sketch/sketch.ino device/app/effente/sketch/sketch.yaml "$TMP/effente/sketch/"
cp device/app/effente/python/main.py "$TMP/effente/python/"
cp device/__init__.py "$TMP/effente/python/lib/device/"
cp device/intake/*.py "$TMP/effente/python/lib/device/intake/"
cp effente/*.py "$TMP/effente/python/lib/effente/"
(cd "$TMP" && COPYFILE_DISABLE=1 tar --no-xattrs -czf app.tgz effente)
adb shell "test -d $APP || arduino-app-cli app new effente -i '📈' -d 'effente condition monitoring' >/dev/null"
adb push "$TMP/app.tgz" /tmp/effente-app.tgz >/dev/null
adb shell "cd /home/arduino/ArduinoApps && rm -rf effente/python/lib && tar -xzf /tmp/effente-app.tgz 2>/dev/null; rm /tmp/effente-app.tgz"
rm -rf "$TMP"
# Start it, then wait for samples to arrive (the app's own stream, read on the board itself).
# Now and then the sketch doesn't start after the app starts (12-13 Nov: no heartbeat at all, the screen
# frozen); starting the app again has always fixed it, so try up to 3 times.
for try in 1 2 3; do
  adb shell "arduino-app-cli app stop user:effente >/dev/null 2>&1; arduino-app-cli app start user:effente 2>&1 | grep -E 'error:'" || true
  for i in $(seq 1 9); do
    sleep 5
    n=$(adb shell "curl -s --max-time 3 localhost:8765/latest | grep -o '\"samples\": *[0-9]*' | head -1 | grep -o '[0-9]*$'" || true)
    if [ -n "$n" ] && [ "$n" -gt 0 ] 2>/dev/null; then
      echo "✓ App \"effente\" running: $n samples in (start $try)"
      exit 0
    fi
  done
  echo "the sketch didn't start (start $try); starting the app again"
done
echo "error: the sketch didn't start after 3 tries: unplug the board for a few seconds, plug it back in, then run this again"
exit 1
