"""Fixed answers with the profile a careful person would fill from them.

The expected profiles are our judgement of a right reading, not measured outcomes.
"""

MACHINE = "conveyor-07"


def answers(machine_type=None, drive=None, speed_rpm=None, mounting=None, sensor_location=None):
    """Five questions as the Setup page might ask them. None is "I don't know"."""
    asked = {
        "machine_type": "What kind of machine is it?",
        "drive": "How is it driven?",
        "speed_rpm": "How fast does the motor turn, in rpm?",
        "mounting": "How is it mounted?",
        "sensor_location": "Where will the sensor sit?",
    }
    given = dict(machine_type=machine_type, drive=drive, speed_rpm=speed_rpm, mounting=mounting,
                 sensor_location=sensor_location)
    return [{"about": field, "question": question, "answer": given[field]} for field, question in asked.items()]


EMPTY = dict.fromkeys(("machine_type", "drive", "speed_rpm", "mounting", "sensor_location"))

# (answers, the right profile)
CASES = {
    "belt_conveyor": (
        answers("a belt conveyor", "a motor drives the belt through pulleys", "I don't know",
                "bolted to the concrete floor", None),
        {**EMPTY, "machine_type": "conveyor", "drive": "belt", "mounting": "rigid"},
    ),
    "pump_direct": (
        answers("a cooling water pump with its motor", "the motor is coupled straight to the pump", "1450 rpm",
                "on rubber mounts", "on the motor, at the drive end"),
        {"machine_type": "pump", "drive": "electric_motor_direct", "speed_rpm": 1450, "mounting": "flexible",
         "sensor_location": "motor_drive_end"},
    ),
    "fan_range": (
        answers("an extraction fan", "through a gearbox", "somewhere between 1,400 and 1,500 rpm",
                "not sure", "on the frame"),
        {**EMPTY, "machine_type": "fan", "drive": "gearbox", "speed_rpm": [1400, 1500], "sensor_location": "frame"},
    ),
    "all_unknown": (answers(), dict(EMPTY)),
    "hz_is_not_rpm": (
        answers("a motor", None, "about 50 Hz", None, None),
        {**EMPTY, "machine_type": "motor"},
    ),
    "two_drives_is_unsure": (
        answers("a press", "a belt and a gearbox", None, None, None),
        dict(EMPTY),
    ),
}
