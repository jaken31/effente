# effente on the UNO Q (Linux side, run by Arduino App Lab).
#
# Sensors arrive from the sketch over the Bridge ("imu" batches, "button" presses); our code
# (device.intake, effente) turns them into features, and DeviceApp runs the four modes:
# display -> recording -> training -> watching. The screen is drawn by the sketch ("screen"),
# and a live stream is served on port 8765 (curl -N http://BOARD:8765/stream). If EFFENTE_PLATFORM_URL is
# set (for example http://192.168.43.10:8000), the model, records and check-ins also go to the laptop
# platform (platform/CONTRACT.md); unset, nothing is sent.
# Data (recordings, models, state) lives in /app/data, which is ~/ArduinoApps/effente/data on the board.
import os
import sys
import threading
import time

# The screen shows local time: US Eastern unless EFFENTE_TZ says otherwise (the board itself runs in UTC).
os.environ["TZ"] = os.environ.get("EFFENTE_TZ", "America/New_York")
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))  # device/ and effente/, copied here by device/app/deploy.sh

from arduino.app_utils import App, Bridge  # noqa: E402

from device.intake.device_app import DeviceApp  # noqa: E402
from device.intake.source import UnoQSource  # noqa: E402
from device.intake.stream import Stream  # noqa: E402
from device.intake.uploader import ENV_URL, Uploader  # noqa: E402

DATA = os.environ.get("EFFENTE_DATA", "/app/data")
MACHINE = os.environ.get("EFFENTE_MACHINE", "machine-1")
DEVICE = "effente-01"

source = UnoQSource()
Bridge.provide("imu", source.on_imu)
stream = Stream(port=8765, device_id=DEVICE, machine_id=MACHINE).start()
uploader = Uploader.from_env(DEVICE, MACHINE)  # None when EFFENTE_PLATFORM_URL is not set


def screen(text):
    Bridge.call("screen", text, timeout=1)


app = DeviceApp(DATA, source, screen=screen, stream=stream, device_id=DEVICE, machine_id=MACHINE, uploader=uploader)
if uploader is not None:
    uploader.start()
Bridge.provide("button", app.on_button)
threading.Thread(target=app.run, name="pipeline", daemon=True).start()
print(f"effente started in {app.mode} mode; live stream on port 8765; data in {DATA}; platform "
      + (uploader.base if uploader is not None else f"off ({ENV_URL} not set)"), flush=True)


def loop():
    time.sleep(10)
    s = app.status()
    sensors = s.get("sensors", {})
    print(f"mode {s['mode']} | {sensors.get('samples', 0)} samples at {sensors.get('rate_hz')} Hz, lost "
          f"{sensors.get('lost', 0)}, dropped {sensors.get('dropped_packets', 0)} | IMU {sensors.get('imu_temp_c')} C"
          + (f" | {s['message']}" if s.get("message") else "") + (f" | {s['diagnosis']}" if s.get("diagnosis") else ""),
          flush=True)


App.run(user_loop=loop)
