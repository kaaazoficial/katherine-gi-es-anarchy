"""Regression tests for Katherine's cold-start import footprint."""

import subprocess
import sys


def test_main_import_defers_rare_optional_dependencies():
    """Normal boot must not import packages used only by diagnostics or rare tools."""
    script = r"""
import sys
import main

blocked = {
    "prometheus_client",
    "rapidfuzz",
    "defusedxml",
}
loaded = sorted(name for name in blocked if name in sys.modules)
assert not loaded, f"Optional modules loaded during normal import: {loaded}"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        "Katherine's normal import path loaded optional dependencies or failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
