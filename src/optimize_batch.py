# src/optimize_batch.py
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Tuple
import logging
import os
import shutil

from src.main_orchestrator import run_optimization_loop
from src.utils.io import setup_logging

logger = logging.getLogger(__name__)

def _run_single_optimization_wrapper(
    spec_file: Path,
    out_dir: Path,
    iterations: int,
    initial_points: int,
    goals: Dict[str, Any],
    focus: str,
    explore_ratio: float
):
    """
    A wrapper to call the main optimization loop and redirect its output
    to a spec-specific subdirectory.
    """
    # Override the default 'results' directory to be inside the spec's output folder
    os.environ["RESULTS_DIR"] = str(out_dir)
    run_optimization_loop(
        max_iterations=iterations,
        n_initial_points=initial_points,
        focus_mode=focus,
        goals=goals,
        explore_ratio=explore_ratio,
        spec_file=str(spec_file)
    )

def run_optimize_batch(
    spec_dir: Path,
    out_dir: Path,
    iterations: int,
    initial_points: int,
    goals: Dict[str, Any],
    focus: str,
    explore_ratio: float,
    workers: int,
    log_file: Path | None = None
) -> Dict[str, int]:
    """
    Drives the batch optimization process for a directory of spec sheets.
    """
    spec_dir, out_dir = Path(spec_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if log_file:
        setup_logging(str(log_file), name=__name__)

    # Robust file discovery: include PDFs and ignore hidden files (like .DS_Store)
    specs = sorted([p for p in spec_dir.iterdir()
                    if p.suffix.lower() in {".csv", ".json", ".pdf"} and not p.name.startswith('.')],
                   key=lambda p: p.name)
    tasks: List[Tuple[Path, Path]] = [(spec, out_dir / spec.stem) for spec in specs]

    logger.info(f"Found {len(specs)} specs to optimize.")

    results = {"processed": 0, "failed": 0}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futs = {
            executor.submit(_run_single_optimization_wrapper, spec_file, spec_out_dir, iterations, initial_points, goals, focus, explore_ratio): spec_file
            for spec_file, spec_out_dir in tasks
        }

        for fut in as_completed(futs):
            spec_file = futs[fut]
            try:
                fut.result()  # Check for exceptions
                logger.info(f"Successfully optimized: {spec_file.name}")
                results["processed"] += 1
            except Exception as e:
                logger.exception(f"Failed to optimize: {spec_file.name}. Error: {e}")
                results["failed"] += 1

    return results