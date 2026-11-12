"""Runs the JavaScript logic tests with node --test, so one pytest command
covers this part. Skipped when node is not installed."""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dashboard_logic_with_node():
    js = os.path.join(HERE, "dashboard_logic.test.js")
    res = subprocess.run(["node", "--test", js], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
