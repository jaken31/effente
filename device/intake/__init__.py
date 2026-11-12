"""Data intake: sensor packets -> aligned one-second windows -> features.

No model lives here. The output is feature files the training side learns
from, and the same feature stream a model will read on the device later.

  source.py     where packets come from (recording files now, the UNO Q later)
  windowing.py  aligns the sensors by timestamp into one-second windows
  extract.py    windows -> features -> a training-ready .npz file
Features themselves are computed by effente.features, shared with training.
"""
