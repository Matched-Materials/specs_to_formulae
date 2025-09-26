# spec_sheets_to_formulas/src/batch.py
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import logging
import os
import shutil

from .pipeline import run_single
from .utils.io import setup_logging

# Set up a global logger for the batch process itself
logger = logging.getLogger(__name__)

def run_batch(spec_dir: Path,
              goals: Dict[str, Any],
              out_dir: Path,
              n_candidates: int = 20,
              topk: int = 10,
              workers: int = 4,
              resume: bool = False,
              assume_json: bool = False,
              weights: Optional[Dict[str, float]] = None,
              log_file: Optional[Path] = None) -> Dict[str, int]:
    """
    Drives the batch processing of a directory of spec sheets.
    This function follows the logic outlined in the dev_guide.md (lines 95-115).
    """
    spec_dir, out_dir = Path(spec_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if log_file:
        setup_logging(str(log_file), name=__name__)

    # Robust file discovery: ignore hidden files (like .DS_Store)
    specs = sorted([p for p in spec_dir.iterdir()
                    if p.suffix.lower() in {".csv", ".json", ".pdf"} and not p.name.startswith('.')],
                   key=lambda p: p.name)

    skipped_specs: List[str] = []
    # A task is now a tuple of (input_spec_path, final_output_dir)
    tasks_with_paths: List[Tuple[Path, Path]] = []
    for spec_path in specs:
        # Handle duplicate stems by appending the suffix (e.g., spec.csv -> spec_csv/)
        # This prevents spec.csv and spec.json from overwriting each other's results.
        spec_out_name = f"{spec_path.stem}{spec_path.suffix.replace('.', '_')}"
        spec_out_dir = out_dir / spec_out_name
        if resume and (spec_out_dir / "recommendations.json").exists():
            skipped_specs.append(spec_path.name)
            logger.info(f"Skipping already processed spec: {spec_path.name}")
            continue
        tasks_with_paths.append((spec_path, spec_out_dir))


    logger.info(f"Found {len(specs)} total specs. Processing {len(tasks_with_paths)}, skipping {len(skipped_specs)}.")

    results = {"processed": 0, "skipped": len(skipped_specs), "failed": 0}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        # Create a temporary directory for each task to ensure atomic writes.
        tmp_root = out_dir / f".tmp_{os.getpid()}"
        tmp_root.mkdir(exist_ok=True)

        futs = {
            ex.submit(run_single, sp, tmp_root / final_dir.name, goals, topk, n_candidates, assume_json, weights): (sp, final_dir)
            for sp, final_dir in tasks_with_paths
        }
        for fut in as_completed(futs):
            sp, final_dir = futs[fut]
            tmp_dir = tmp_root / final_dir.name
            try:
                fut.result()
                # On success, atomically move the temp dir to the final destination
                if tmp_dir.exists():
                    if final_dir.exists():
                        shutil.rmtree(final_dir)
                    shutil.move(str(tmp_dir), str(final_dir))                
                logger.info(f"Successfully processed: {sp.name}")
                results["processed"] += 1
            except Exception as e:
                logger.exception(f"Failed to process: {sp.name}. Error: {e}")
                results["failed"] += 1
    
    if tmp_root.exists() and not any(tmp_root.iterdir()):
        shutil.rmtree(tmp_root)

    return results
