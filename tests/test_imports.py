"""Wiki: Modules > Import rules, checked with import-linter."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("lint-imports") is None, reason="import-linter not installed")
def test_import_rules():
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["lint-imports"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
