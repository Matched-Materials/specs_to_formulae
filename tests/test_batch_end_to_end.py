# tests/test_batch_end_to_end.py
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

def test_batch_end_to_end(tmp_path: Path, project_root: Path):
    """
    Runs the `recommend-batch` command on a small, temporary dataset and
    verifies that the expected output artifacts are created.
    This follows the test plan in dev_guide.md.
    """
    # 1. --- Setup test environment ---
    spec_dir = tmp_path / "test_specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "test_results"

    # Create a dummy CSV spec sheet
    spec_file = spec_dir / "dummy_spec.csv"
    spec_file.write_text("property,value\n\"MFI (230/2.16)\",10.0\n\"HDT 66 psi\",85.0")

    # Define the path to the pre-existing goals file
    goals_file = "configs/goals/compostable.json"

    # 2. --- Run the batch process via CLI ---
    cmd = [
        sys.executable, "-m", "src.cli", "recommend-batch",
        "--spec-dir", str(spec_dir),
        "--out-dir", str(out_dir),
        "--goals", goals_file,
        "--workers", "1" # Use a single worker for deterministic testing
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)

    # Assert that the command ran successfully
    assert result.returncode == 0, f"CLI command failed with error:\n{result.stderr}"

    # 3. --- Verify output artifacts ---
    result_subdir = out_dir / "dummy_spec_csv"
    assert result_subdir.is_dir(), "The per-spec output directory was not created."

    expected_artifacts = ["00_normalized.json", "01_gapfilled.json", "02_prefilter.json", "03_candidates.json", "recommendations.json", "meta.json", "run.log"]
    for artifact in expected_artifacts:
        assert (result_subdir / artifact).exists(), f"Expected artifact '{artifact}' is missing."

    # Sanity check the meta.json file
    meta = json.loads((result_subdir / "meta.json").read_text())
    assert "spec_sha256" in meta
    assert meta.get("status") == "success"