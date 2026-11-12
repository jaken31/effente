"""Model-slot loader for effente model files (written by training/model/train.py).

    python3 -m device.intake.live --sim conveyor --loader training/model/effente_loader.py --model models/conveyor.json

The device's model slot imports this file and calls load(path). The model file itself records the
features it was trained on, and Model() refuses a file made with a different feature list, so the
slot reports that instead of running a mismatched model. NumPy only.
"""

from effente.model_runtime import Model, Watcher, _version

FEATURES_VERSION = _version()  # the slot's own check; the file's version is checked in load()


def load(path):
    return Watcher(Model.load(path))
