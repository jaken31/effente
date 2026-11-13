"""Read the model library: every manifest, and one entry with its checks.

    manifests = load_catalog("training/library/entries")
    manifest, model = load_entry("training/library/entries", "conveyor")

A library folder holds <entry_id>/manifest.json and model.json (training/library/build.py writes
them). load_entry refuses a model file whose sha1 is not the manifest's, and one built on another
feature list than this code computes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from effente import model_runtime as RT

MANIFEST, MODEL = "manifest.json", "model.json"


def load_catalog(folder) -> list[dict]:
    """Every manifest in the library folder, by entry id."""
    return sorted((json.loads(p.read_text()) for p in Path(folder).glob(f"*/{MANIFEST}")), key=lambda m: m["entry_id"])


def load_entry(folder, entry_id: str) -> tuple[dict, dict]:
    """(manifest, model file dict) of one entry.

    Raises FileNotFoundError if the library has no such entry, and ValueError if the manifest is
    another entry's, the model file is not the one the manifest describes (sha1), or it does not
    load on the current feature list."""
    path = Path(folder) / entry_id / MANIFEST
    if not path.exists():
        raise FileNotFoundError(f"no entry {entry_id!r} in {folder}")
    manifest = json.loads(path.read_text())
    if manifest.get("entry_id") != entry_id:
        raise ValueError(f"{path} describes {manifest.get('entry_id')!r}, not {entry_id!r}")
    raw = (path.parent / MODEL).read_bytes()
    sha1 = hashlib.sha1(raw).hexdigest()
    if sha1 != manifest.get("model_sha1"):
        raise ValueError(f"{entry_id}: the model file's sha1 is {sha1}, the manifest says {manifest.get('model_sha1')}")
    here = RT._version()
    if manifest.get("features_version") != here:
        raise ValueError(f"{entry_id} was built on features {manifest.get('features_version')}, this code computes {here}")
    model = json.loads(raw)
    RT.Model(model)  # refuses another format or feature list
    return manifest, model
