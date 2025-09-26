# tests/test_atomic_write.py
from __future__ import annotations
import pytest
from pathlib import Path
import subprocess
import sys
import os

@pytest.fixture(scope="module")
def project_root() -> Path:
    """Fixture to get the project root directory."""
    return Path(__file__).parent.parent

def test_atomic_write_on_failure(tmp_path: Path, project_root: Path, monkeypatch):
    """
    Verifies that a failing pipeline run does not create a partial output directory.
    This follows the test plan in dev_guide.md.
    """
    # 1. --- Setup test environment ---
    spec_dir = tmp_path / "test_specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "test_results"

    # Create a dummy spec sheet
    (spec_dir / "spec_fails.csv").write_text("property,value\n\"MFI (230/2.16)\",10.0")

    # 2. --- Simulate a failure in the pipeline ---
    # We use monkeypatch to make one of the core functions raise an exception.
    # This simulates a real-world failure during the 'prefilter' step.
    monkeypatch.setenv("SIMULATE_FAILURE", "prefilter")

    # Define paths and command
    goals_file = "configs/goals/compostable.json"
    cmd = [
        sys.executable, "-m", "src.cli", "recommend-batch",
        "--spec-dir", str(spec_dir),
        "--out-dir", str(out_dir),
        "--goals", goals_file,
        "--workers", "1"
    ]
    
    # 3. --- Run the batch process ---
    # We expect it to fail for the one spec, but the overall command should succeed.
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)
    assert result.returncode == 0, f"CLI command should not crash on worker failure. Stderr:\n{result.stderr}"

    # 4. --- Verify atomic behavior ---
    # The final output directory for the failed spec should NOT exist.
    final_spec_dir = out_dir / "spec_fails"
    assert not final_spec_dir.exists(), "A partial output directory was created on failure."

    # A temporary directory might still exist for debugging, which is acceptable.
    tmp_dirs = list(out_dir.glob(".tmp_*/spec_fails"))
    if tmp_dirs:
        print(f"Found temporary directory as expected: {tmp_dirs[0]}")