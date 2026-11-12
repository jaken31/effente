# effente sensor test: the work happens in the sketch (MPU6050 readings on the OLED screen).
# This Linux side only keeps the app alive; the readings also appear on the serial monitor.
import time

from arduino.app_utils import App

print("effente sensor test started", flush=True)


def loop():
    time.sleep(10)


App.run(user_loop=loop)
