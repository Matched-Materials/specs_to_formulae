# tests/test_duplicate_stems.py
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

def test_duplicate_stems_are_handled(tmp_path: Path, project_root: Path):
    """
    Verifies that files with the same stem but different extensions (e.g., spec.csv, spec.json)
    are processed into unique output directories.
    """
    # 1. --- Setup test environment ---
    spec_dir = tmp_path / "test_specs"
    spec_dir.mkdir()
    out_dir = tmp_path / "test_results"

    # Create two spec files with the same stem
    (spec_dir / "duplicate_spec.csv").write_text("property,value\n\"MFI (230/2.16)\",10.0")
    (spec_dir / "duplicate_spec.json").write_text('{"MFI (230/2.16)": 20.0}')

    goals_file = "configs/goals/compostable.json"

    # 2. --- Run the batch process ---
    cmd = [
        sys.executable, "-m", "src.cli", "recommend-batch",
        "--spec-dir", str(spec_dir),
        "--out-dir", str(out_dir),
        "--goals", goals_file,
        "--workers", "1"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)

    assert result.returncode == 0, f"CLI command failed with error:\n{result.stderr}"

    # 3. --- Verify unique output directories were created ---
    output_summary = json.loads(result.stdout)
    assert output_summary.get("processed") == 2, "Expected both specs to be processed."

    assert (out_dir / "duplicate_spec_csv").is_dir(), "Output directory for .csv file was not created."
    assert (out_dir / "duplicate_spec_json").is_dir(), "Output directory for .json file was not created."
    assert (out_dir / "duplicate_spec_csv" / "recommendations.json").exists()
    assert (out_dir / "duplicate_spec_json" / "recommendations.json").exists()