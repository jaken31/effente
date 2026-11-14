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

# Sensors not fitted yet, off until wired (the pipeline waits for every sensor it is told about):
# EFFENTE_MIC=1 for the INMP441 microphone, EFFENTE_THERMAL=1 for the MLX90640-D55 thermal camera.
MIC = os.environ.get("EFFENTE_MIC") == "1"
THERMAL = os.environ.get("EFFENTE_THERMAL") == "1"

source = UnoQSource(audio=MIC, thermal=THERMAL)
Bridge.provide("imu", source.on_imu)
Bridge.provide("audio", source.on_audio)  # ignored unless EFFENTE_MIC=1
Bridge.provide("thermal", source.on_thermal)  # ignored unless EFFENTE_THERMAL=1

mcu = {"beat": None, "at": None}  # the sketch's heartbeat: alive, sensor and screen found, bus state


def on_heartbeat(ms, mpu_ok, oled_ok, who_am_i, bus_clocks, bus_tries, sda_high, scl_high, read_errors,
                 oled_errors=None, frames=None, mic_lines=None, mic_db10=None):
    # The last four only from sketches that send them. mic_lines: lines received from the ESP32 microphone
    # front end; mic_db10: its last level in tenths of a dB below full scale (screen only, not a feature yet).
    if mpu_ok:
        source.imu_who = int(who_am_i)  # the temperature formula depends on the chip (device.intake.source.imu_temp_c)
    mcu.update(beat=dict(up_s=round(ms / 1000), sensor=bool(mpu_ok), screen=bool(oled_ok), who_am_i=hex(who_am_i),
                         bus_clocks=bus_clocks, bus_tries=bus_tries, sda_high=bool(sda_high), scl_high=bool(scl_high),
                         read_errors=read_errors, screen_errors=oled_errors, frames=frames,
                         mic_lines=mic_lines, mic_db=None if mic_db10 is None else mic_db10 / 10), at=time.time())


Bridge.provide("hb", on_heartbeat)
stream = Stream(port=8765, device_id=DEVICE, machine_id=MACHINE).start()
uploader = Uploader.from_env(DEVICE, MACHINE)  # None when EFFENTE_PLATFORM_URL is not set


def screen(text):
    Bridge.call("screen", text, timeout=1)


app = DeviceApp(DATA, source, screen=screen, stream=stream, device_id=DEVICE, machine_id=MACHINE, uploader=uploader)
if uploader is not None:
    uploader.start()
Bridge.provide("button", app.on_button)
threading.Thread(target=app.run, name="pipeline", daemon=True).start()
print(f"effente started in {app.mode} mode; sensors {', '.join(source.sensors)}; live stream on port 8765; data in {DATA}; platform "
      + (uploader.base if uploader is not None else f"off ({ENV_URL} not set)"), flush=True)


def loop():
    time.sleep(10)
    s = app.status()
    sensors = s.get("sensors", {})
    print(f"mode {s['mode']} | {sensors.get('samples', 0)} samples at {sensors.get('rate_hz')} Hz, lost "
          f"{sensors.get('lost', 0)}, dropped {sensors.get('dropped_packets', 0)} | IMU {sensors.get('imu_temp_c')} C"
          + (f" | {s['message']}" if s.get("message") else "") + (f" | {s['diagnosis']}" if s.get("diagnosis") else ""),
          flush=True)
    b = mcu["beat"]
    print("microcontroller: no heartbeat yet (the sketch isn't running)" if b is None else
          f"microcontroller: last heartbeat {time.time() - mcu['at']:.0f} s ago: {b}", flush=True)


App.run(user_loop=loop)
