# spec_sheets_to_formulas/src/cli.py
from __future__ import annotations
import argparse, json
import os, sys, logging
from pathlib import Path
from typing import Dict, Any, Optional

from .ingest.normalize_spec import normalize_spec
from .pipeline import run_single
from .batch import run_batch
from .optimize_batch import run_optimize_batch

# --- Load environment variables from .env file at the project root ---
# This ensures that credentials and other configurations are available to all modules.
from dotenv import load_dotenv
project_root = Path(__file__).parent.parent
# Use override=True to ensure that the .env file takes precedence over any
# existing environment variables, which is crucial for preventing stale paths.
load_dotenv(dotenv_path=project_root / ".env", override=True)

def _load_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main():
    # Get the project root directory (the parent of 'src')
    # This makes path resolution robust, regardless of where the script is called from.

    ap = argparse.ArgumentParser(prog="spec-sheets-to-formulas")
    ap.add_argument(
        "--log-level",
        default=os.getenv("LOGLEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set the logging level for the run."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s_norm = sub.add_parser("normalize-spec")
    s_norm.add_argument("--spec", required=True)
    s_norm.add_argument("--out", required=True)
    s_norm.add_argument("--assume-json", action="store_true")

    s_rec = sub.add_parser("recommend")
    s_rec.add_argument("--spec", required=True)
    s_rec.add_argument("--goals", required=True)
    s_rec.add_argument("--out-dir", required=True)
    s_rec.add_argument("--topk", type=int, default=10)
    s_rec.add_argument("--weights")
    s_rec.add_argument("--assume-json", action="store_true")

    s_batch = sub.add_parser("recommend-batch")
    s_batch.add_argument("--spec-dir", required=True)
    s_batch.add_argument("--goals", required=True)
    s_batch.add_argument("--out-dir", required=True)
    s_batch.add_argument("--topk", type=int, default=10)
    s_batch.add_argument("--num-candidates", type=int, default=20, help="Number of candidates to generate per spec.")
    s_batch.add_argument("--weights")
    s_batch.add_argument("--assume-json", action="store_true")
    s_batch.add_argument("--workers", type=int, default=4)
    s_batch.add_argument("--resume", action="store_true")
    s_batch.add_argument("--log-file", help="Path to a central log file for the batch run.")

    # --- New: optimize-batch command ---
    s_opt_batch = sub.add_parser("optimize-batch", help="Run closed-loop optimization for a batch of spec sheets.")
    s_opt_batch.add_argument("--spec-dir", required=True, help="Directory containing spec sheets to optimize.")
    s_opt_batch.add_argument("--out-dir", required=True, help="Base directory to save all optimization run artifacts.")
    s_opt_batch.add_argument("--iterations", type=int, default=25, help="Number of optimization iterations per spec.")
    s_opt_batch.add_argument("--initial-points", type=int, default=30, help="Number of initial DOE points per spec.")
    s_opt_batch.add_argument("--goals", type=str, default=None, help="Path to a goals JSON file (e.g., for compostable constraints). Overrides --focus.")
    s_opt_batch.add_argument("--focus", type=str, default="none", choices=["none", "recycled", "bio-based", "biopolyester"], help="Focus for the initial DOE generation.")
    s_opt_batch.add_argument("--explore-ratio", type=float, default=0.25, help="Fraction of candidates for random exploration.")
    s_opt_batch.add_argument("--workers", type=int, default=2, help="Number of spec sheets to process in parallel.")
    s_opt_batch.add_argument("--log-file", help="Path to a central log file for the batch run.")

    args = ap.parse_args()

    # --- Configure Logging ---
    # Set up basic logging based on the provided log level.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )

    if args.cmd == "normalize-spec":
        norm = normalize_spec(Path(args.spec), assume_json=args.assume_json)
        # Note: This uses a different IO pattern than the main pipeline.
        # It's a simple utility, so direct write is acceptable.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(norm, indent=2, sort_keys=True), encoding="utf-8")
        return

    if args.cmd == "recommend-batch":
        # Resolve the goals path to an absolute path before passing it to workers.
        # This prevents FileNotFoundError if the worker process changes directory.
        # It now correctly joins the project root with the relative path from the command line.
        goals_path = project_root / args.goals
        if not goals_path.exists():
            raise FileNotFoundError(f"Goals file not found at resolved path: {goals_path}")
        goals = _load_json(str(goals_path)) or {}
        weights = _load_json(args.weights) if args.weights else None
        log_path = Path(args.log_file) if args.log_file else None
        res = run_batch(Path(args.spec_dir), goals, Path(args.out_dir),
                        topk=args.topk, n_candidates=args.num_candidates, workers=args.workers,
                        resume=args.resume, assume_json=args.assume_json, weights=weights,
                        log_file=log_path)
        print(json.dumps(res, indent=2, sort_keys=True))
        return

    if args.cmd == "optimize-batch":
        log_path = Path(args.log_file) if args.log_file else None
        goals = {}
        if args.goals:
            goals_path = project_root / args.goals
            if not goals_path.exists():
                raise FileNotFoundError(f"Goals file not found at resolved path: {goals_path}")
            goals = _load_json(str(goals_path)) or {}
        res = run_optimize_batch(
            spec_dir=Path(args.spec_dir),
            out_dir=Path(args.out_dir),
            iterations=args.iterations,
            initial_points=args.initial_points,
            goals=goals,
            focus=args.focus,
            explore_ratio=args.explore_ratio,
            workers=args.workers,
            log_file=log_path
        )
        print(json.dumps(res, indent=2, sort_keys=True))

    if args.cmd == "recommend":
        # Note: The single 'recommend' command does not yet support --num-candidates.
        goals = _load_json(args.goals) or {}
        weights = _load_json(args.weights) if args.weights else None
        out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        run_single(Path(args.spec), out_dir, goals, topk=args.topk, assume_json=args.assume_json, weights=weights)
        return

if __name__ == "__main__":
    main()
