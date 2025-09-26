# tests/test_resume_logic.py
from __future__ import annotations
import pytest
from pathlib import Path
import json
import subprocess
import sys

@pytest.fixture(scope="module")
def project_root() -> Path:
    """Fixture to get the project root directory."""
    return Path(__file__).parent.parent

def test_resume_logic(tmp_path: Path, project_root: Path):
    """
    Verifies that the --resume flag correctly skips already-processed specs.
    This follows the test plan in dev_guide.md.
    """
    # 1. --- Setup test environment ---
    spec_dir = tmp_path / "test_specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "test_results"
    out_dir.mkdir()

    # Create two dummy spec sheets
    (spec_dir / "spec_A.csv").write_text("property,value\n\"MFI (230/2.16)\",10.0")
    (spec_dir / "spec_B.csv").write_text("property,value\n\"MFI (230/2.16)\",20.0")

    # Pre-create a result for spec_A to simulate a completed run
    completed_spec_dir = out_dir / "spec_A_csv"
    completed_spec_dir.mkdir()
    (completed_spec_dir / "recommendations.json").write_text('{"topk": []}')

    # Define the path to the pre-existing goals file
    goals_file = "configs/goals/compostable.json"

    # 2. --- Run the batch process with the --resume flag ---
    cmd = [
        sys.executable, "-m", "src.cli", "recommend-batch",
        "--spec-dir", str(spec_dir),
        "--out-dir", str(out_dir),
        "--goals", goals_file,
        "--workers", "1",
        "--resume" # The key flag for this test
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)

    # Assert that the command ran successfully
    assert result.returncode == 0, f"CLI command failed with error:\n{result.stderr}"

    # 3. --- Verify the output summary ---
    # The CLI prints a JSON summary to stdout. We parse it to check the counts.
    summary = json.loads(result.stdout)
    assert summary.get("processed") == 1, "Expected exactly one spec to be processed."
    assert summary.get("skipped") == 1, "Expected exactly one spec to be skipped."
    assert (out_dir / "spec_B_csv" / "recommendations.json").exists(), "The non-skipped spec was not processed."