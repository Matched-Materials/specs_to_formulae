# tests/test_weights_logic.py
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

def test_weights_logic(tmp_path: Path, project_root: Path):
    """
    Verifies that the --weights flag correctly influences the scoring.
    """
    # 1. --- Setup test environment ---
    spec_dir = tmp_path / "test_specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "test_results"

    # Create a dummy spec sheet
    (spec_dir / "spec_C.csv").write_text("property,value\n\"MFI (230/2.16)\",10.0")

    # Create a custom weights file that heavily penalizes MFI deviation
    weights_file = tmp_path / "weights.json"
    weights_file.write_text('{"MFI_g_10min_230C_2p16kg": 100.0}')

    # Define the path to the pre-existing goals file
    goals_file = "configs/goals/compostable.json"

    # 2. --- Run the batch process with the --weights flag ---
    cmd = [
        sys.executable, "-m", "src.cli", "recommend-batch",
        "--spec-dir", str(spec_dir),
        "--out-dir", str(out_dir),
        "--goals", goals_file,
        "--weights", str(weights_file), # The key flag for this test
        "--workers", "1"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)

    # Assert that the command ran successfully
    assert result.returncode == 0, f"CLI command failed with error:\n{result.stderr}"

    # 3. --- Verify the output reflects the custom weights ---
    result_subdir = out_dir / "spec_C_csv"
    recommendations_path = result_subdir / "recommendations.json"
    assert recommendations_path.exists(), "recommendations.json was not created."

    recs = json.loads(recommendations_path.read_text())
    
    # This is a simple check. A more advanced test could run with and without
    # weights and assert that the ranking changes as expected.
    # For now, we just confirm the pipeline ran to completion.
    assert "topk" in recs, "The recommendations file is missing the 'topk' key."
    assert len(recs["topk"]) > 0, "The pipeline did not produce any recommendations."