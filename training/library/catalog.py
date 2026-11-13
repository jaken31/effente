"""Read the model library: the manifests, and one entry's model with its checks.

    manifests = load_catalog("training/library/entries")
    manifest, model = load_entry("training/library/entries", "conveyor")

A library folder holds <entry>/<version>/manifest.json and model.json (training/library/build.py
writes them). load_entry refuses a model file whose sha1 is not the manifest's, and one built on
another feature list than this code computes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from effente import model_runtime as RT

MANIFEST = "effente-entry-1"


def _version_key(version: str):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in str(version).split(".")]


def load_catalog(folder) -> list[dict]:
    """Every manifest in the library folder, by entry name, then version (oldest first)."""
    out = []
    for path in Path(folder).glob("*/*/manifest.json"):
        m = json.loads(path.read_text())
        if m.get("manifest") != MANIFEST:
            raise ValueError(f"{path} is not an {MANIFEST} manifest")
        out.append(m)
    return sorted(out, key=lambda m: (m["entry"], _version_key(m["version"])))


def load_entry(folder, entry: str, version: str | None = None) -> tuple[dict, dict]:
    """(manifest, model dict) of one entry; the newest version if none is named.

    Raises FileNotFoundError if there is no such entry or version, and ValueError if the model file
    is not the one the manifest describes (sha1) or does not load on the current feature list."""
    root = Path(folder) / entry
    if version is None:
        versions = sorted((p.parent.name for p in root.glob("*/manifest.json")), key=_version_key)
        if not versions:
            raise FileNotFoundError(f"no entry {entry!r} in {folder}")
        version = versions[-1]
    path = root / version / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"no version {version} of entry {entry!r} in {folder}")
    manifest = json.loads(path.read_text())
    if manifest.get("manifest") != MANIFEST:
        raise ValueError(f"{path} is not an {MANIFEST} manifest")
    if (manifest["entry"], manifest["version"]) != (entry, version):
        raise ValueError(f"{path} describes {manifest['entry']} {manifest['version']}, not {entry} {version}")
    raw = (path.parent / manifest["model_file"]).read_bytes()
    sha1 = hashlib.sha1(raw).hexdigest()
    if sha1 != manifest["model_sha1"]:
        raise ValueError(f"{entry} {version}: the model file's sha1 is {sha1}, the manifest says {manifest['model_sha1']}")
    here = RT._version()
    if manifest["features_version"] != here:
        raise ValueError(f"{entry} {version} was built on features {manifest['features_version']}, "
                         f"this code computes {here}")
    model = json.loads(raw)
    RT.Model(model)  # refuses another format or feature list
    return manifest, model
