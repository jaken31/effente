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
adb shell "arduino-app-cli app stop user:effente >/dev/null 2>&1; arduino-app-cli app restart user:effente 2>&1 | grep -E 'error:|✓'"
