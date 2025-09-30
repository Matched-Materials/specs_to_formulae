"""
main_orchestrator.py

This script orchestrates the closed-loop formulation optimization process.
It connects the different stages:
1. Formulation Generation (DOE or Bayesian Optimizer)
2. Simulation (e.g., processing models)
3. Property Prediction (e.g., performance models)
4. Evaluation (using evaluator agent tools)
5. Optimization (feeding scores back to a Bayesian Optimizer)
"""
import subprocess
import sys
import os
import pandas as pd
import numpy as np
import datetime
import logging
import json
import argparse
import matplotlib.pyplot as plt
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from pathlib import Path
from collections import Counter


# Add the project root to the Python path to allow absolute imports from `src`.
# This makes the script runnable from any directory.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Load environment variables from .env file at the project root ---
# This ensures that credentials and other configurations are available to all modules.
from dotenv import load_dotenv
# Use override=True to ensure that the .env file takes precedence over any
# existing environment variables, which is crucial for preventing stale paths.
load_dotenv(dotenv_path=os.path.join(project_root, ".env"), override=True)

from src.ingest.normalize_spec import normalize_spec
from src.gapfill.merger import gapfill
from src.formulation_doe_generator_V1 import generate_formulation_doe as generate_doe_candidates
from src.prefilter import filter_pools_by_goals

from skopt import Optimizer
from skopt.space import Real
from skopt.plots import plot_objective

from src.analysis.tidy_results import build_tidy, make_plots
from configs.processing import load_processing_levers, clamp_process_row
from src.agent_eval_helpers import build_targets_constraints, evaluate_with_agent

# --- Configure Logging ---
# Set up basic logging. Increase verbosity for the ADK components to DEBUG.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logging.getLogger('google.adk').setLevel(logging.DEBUG)

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def run_property_prediction_bridge(formulations_df: pd.DataFrame, input_path: str, output_path: str, process_path: str = None):
    """
    Runs the property prediction by calling the bridge script as a subprocess.
    This creates a clear, traceable artifact for each prediction step.
    """
    print("Running property prediction bridge script...")
    # 1. Save the input formulations to the specified path
    formulations_df.to_csv(input_path, index=False)

    # 2. Prepare arguments for the bridge script
    model_path = os.path.join(project_root, "data/processed/pp_elastomer_TSE_hybrid_model_v1.json")
    library_path = os.path.join(project_root, "data/processed/ingredient_library.json")
    
    cmd = [
        "python", "src/bridge_formulations_to_properties.py",
        "--doe", input_path,
        "--out", output_path,
        "--model", model_path,
        "--ingredient-library", library_path
    ]

    # Add process conditions file if provided
    if process_path:
        cmd.extend(["--process", process_path])

    # 3. Run the subprocess
    subprocess.run(cmd, check=True)

    # 4. Load and return the results
    print("...property prediction bridge complete.")
    return pd.read_csv(output_path)

def initialize_optimizer(search_space):
    """
    Initializes a Bayesian Optimizer.
    """
    print("Initializing Bayesian Optimizer...")
    # We use a Gaussian Process (GP) model with Expected Improvement (EI) as the acquisition function.
    optimizer = Optimizer(
        dimensions=search_space,
        base_estimator="GP",
        acq_func="EI"
    )
    print("...optimizer initialized.")
    return optimizer

def run_optimization_loop(
    max_iterations: int,
    n_initial_points: int,
    focus_mode: str = "none",
    goals: Optional[Dict[str, Any]] = None,
    explore_ratio: float = 0.25,
    spec_file: Optional[str] = None
):
    """
    Main orchestration loop.
    """
    base_results_dir = os.environ.get("RESULTS_DIR", "results")
    # Define output directories and create them if they don't exist.
    formulations_dir = os.path.join(base_results_dir, "formulations")
    compounded_dir = os.path.join(base_results_dir, "compounded")
    summaries_dir = os.path.join(base_results_dir, "summaries")
    plots_dir = os.path.join(base_results_dir, "plots")
    os.makedirs(formulations_dir, exist_ok=True)
    os.makedirs(compounded_dir, exist_ok=True)
    os.makedirs(summaries_dir, exist_ok=True)
    os.makedirs(plots_dir, exist_ok=True)

    # Use a timestamp to ensure files from this run are unique.
    run_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"Using run identifier: {run_timestamp}")

    # Load configs
    try:
        processing_levers = load_processing_levers(os.path.join(project_root, "data/processed/processing_levers.json"))
    except FileNotFoundError:
        print("Warning: data/processed/processing_levers.json not found. Cannot clamp process variables.")
        processing_levers = None

    # --- Build Target Constraints ---
    if spec_file:
        print(f"Building targets from spec file: {spec_file}")
        # Ingest and enrich the spec file to build dynamic targets
        normalized_spec = normalize_spec(Path(spec_file))
        # --- Save the normalized spec for debugging ---
        normalized_spec_path = Path(base_results_dir) / f"normalized_spec_{run_timestamp}.json"
        with open(normalized_spec_path, 'w') as f:
            json.dump(normalized_spec, f, indent=2)
        print(f"Saved normalized spec to: {normalized_spec_path}")
        enriched_spec = gapfill(normalized_spec)
        targets_constraints = build_targets_constraints(enriched_spec)
    else:
        print("No spec file provided, using default targets.")
        target_properties_path = os.path.join(project_root, "data", "processed", "target_properties.json")
        targets_constraints = build_targets_constraints(target_properties_path)

    # 1. Define search space for the optimizer.
    # This includes key formulation variables and the processing levers from processing_levers.json.
    bo_search_space = [
        # Formulation Levers
        # For biopolyesters, a wider range is needed for effective toughening.
        Real(10.0, 30.0, name='elastomer_wtpct') if "bio" in focus_mode 
            else Real(8.0, 18.0, name='elastomer_wtpct'),
        Real(0.0, 20.0, name='filler_wtpct'),
        Real(0.5, 3.0, name='compat_wtpct'),
        # Processing Levers (from processing_levers.json and pp_elastomer_TSE_hybrid_model_v1.json)
        Real(2.5, 8.33, name='N_rps'),      # Screw Speed (rps), converted from [150, 500] rpm
        Real(200.0, 240.0, name='Tm_C'),    # Melt Temperature (°C)
        Real(1.0, 10.0, name='Q_kgh'),      # Feed Rate (kg/h)
        Real(50.0, 250.0, name='Torque_Nm') # Motor Torque (Nm), a proxy for specific energy
    ]
    optimizer = initialize_optimizer(bo_search_space)

    # 2. Generate initial data (cold start)
    # This now uses the --focus modes from the generator, replacing the old hardcoded modes.
    # Change 'focus' to "recycled" or "bio-based" to align with P1 of the action plan.
    initial_doe_focus = focus_mode

    # --- Filter ingredient pools based on goals before generating candidates ---
    # This prevents the DOE generator from creating chemically incompatible formulations.
    print("Filtering ingredient pools based on goals...")
    full_ingredient_lib = json.loads(Path(project_root, "data/processed/ingredient_library.json").read_text())
    
    # Use the explicit goals file if provided, otherwise simulate from focus_mode.
    active_goals = goals if goals else {}
    if not active_goals and "bio" in focus_mode:
        print(f"No goals file provided. Simulating 'compostable' goal from focus: '{focus_mode}'")
        active_goals = {"sustainability": {"compostable": True}}
    filtered_pools = filter_pools_by_goals(active_goals, full_ingredient_lib)

    n_initial_candidates = n_initial_points
    # Create a deterministic seed from the timestamp for reproducibility
    run_seed = int(run_timestamp.replace("_", "")) % (2**32 - 1)
    print(f"Generating {n_initial_candidates} initial candidates with focus='{initial_doe_focus}'...")
    formulations_df = generate_doe_candidates(
        n=n_initial_candidates,
        # Pass the filtered ingredient pools to the generator
        ingredient_pools=filtered_pools,
        seed=run_seed,
        focus=initial_doe_focus
    )

    initial_candidates_path = os.path.join(formulations_dir, f"initial_doe_{run_timestamp}.csv")
    formulations_df.to_csv(initial_candidates_path, index=False)

    # Write metadata for the initial DOE run
    meta = {
        "run_id": run_timestamp,
        "initial_doe_focus": initial_doe_focus,
        "n_initial_candidates": n_initial_candidates,
        "total": int(len(formulations_df)),
    }
    (Path(formulations_dir) / "doe_run_metadata.json").write_text(json.dumps(meta, indent=2))
    print(f"Wrote {len(formulations_df)} candidates to {initial_candidates_path}")
    print(f"Wrote metadata to {Path(formulations_dir) / 'doe_run_metadata.json'}")
    # Since the initial DOE may not vary all levers, we assign default values.
    # This makes the initial data compatible with the expanded search space.
    default_optimization_levers = {
        'compat_wtpct': 1.5, # Default compatibilizer wt%
        'N_rps': 5.0,      # A reasonable mid-point for screw speed
        'Tm_C': 220.0,     # Typical melt temp
        'Q_kgh': 5.0,      # Typical lab feed rate
        'Torque_Nm': 50.0 # Align with the lower bound of the optimizer's search space
    }
    for lever, value in default_optimization_levers.items():
        formulations_df[lever] = value

    # Add a 'mode' column for downstream compatibility, even though we generate from a single focus now.
    formulations_df['mode'] = f"focus_{initial_doe_focus}"

    # 3. Run first simulation and evaluation
    # Define output paths for the bridge script to create traceable artifacts
    initial_predictions_path = os.path.join(compounded_dir, f"initial_predictions_{run_timestamp}.csv")
    # The bridge script needs a process file, even for defaults.
    default_process_levers = {k: v for k, v in default_optimization_levers.items() if not k.endswith('_wtpct')}
    default_process_path = os.path.join(compounded_dir, f"default_process_conditions_{run_timestamp}.json")
    with open(default_process_path, 'w') as f:
        json.dump(default_process_levers, f, indent=2)

    # Call the bridge script to perform property prediction
    predictions_df = run_property_prediction_bridge(
        formulations_df, initial_candidates_path, initial_predictions_path, default_process_path
    )
    evaluated_df = evaluate_with_agent(
        predictions_df=predictions_df,
        process_vars=default_process_levers,
        targets_constraints=targets_constraints,
        out_dir=os.path.join(compounded_dir, f"initial_agent_{run_timestamp}"),
        run_identifier=run_timestamp
    )

    initial_evaluated_path = os.path.join(compounded_dir, f"initial_evaluated_{run_timestamp}.csv")
    evaluated_df.to_csv(initial_evaluated_path, index=False)

    print("\n--- Initial Evaluation Results ---")
    display_cols = [
        'elastomer_wtpct', 'filler_wtpct', 'compat_wtpct', 'N_rps', 'Tm_C',
        'Q_kgh', 'sigma_y_MPa', 'MFI_g10min', 'E_GPa', 'HDT_C',
        'Izod_m20_kJm2', 'Izod_23_kJm2', 'rho_gcc', 'eps_y_pct', 'Gardner_J',
        'recommended_bo_weight',
    ]
    # Filter to only show columns that actually exist in the dataframe
    print(evaluated_df[[c for c in display_cols if c in evaluated_df.columns]].head())

    # 4. "Tell" the optimizer about the initial results.
    # scikit-optimize minimizes, so we pass the *negative* of our score.
    # Ensure the columns for the search space are numeric before passing to the optimizer.
    # This prevents errors if the CSV contains non-numeric values or empty strings.
    for dim in bo_search_space:
        evaluated_df[dim.name] = pd.to_numeric(evaluated_df[dim.name], errors='coerce')
    evaluated_df.dropna(subset=[dim.name for dim in bo_search_space], inplace=True)

    # Filter the DataFrame to include only points that are strictly within the defined search space.
    # This is a robust way to handle any edge cases from the generator that fall outside the optimizer's bounds.
    initial_rows = len(evaluated_df)
    for dim in bo_search_space:
        evaluated_df = evaluated_df[evaluated_df[dim.name] >= dim.low]
        evaluated_df = evaluated_df[evaluated_df[dim.name] <= dim.high]

    if len(evaluated_df) < initial_rows:
        print(f"Warning: Filtered out {initial_rows - len(evaluated_df)} initial points that were outside the optimizer's search space.")

    X_initial = evaluated_df[[dim.name for dim in bo_search_space]].values.tolist()
    y_initial = (-evaluated_df['recommended_bo_weight']).values.tolist()
    if not X_initial:
        raise ValueError("No valid initial points found within the defined search space. Check the DOE generator and search space definitions.")
    optimizer.tell(X_initial, y_initial)
    print(f"\nOptimizer updated with {len(X_initial)} initial DOE results.")

    # 5. Main optimization loop
    # --- P2: Implement Optimizer Explore/Exploit Strategy ---
    # For each iteration, we generate a batch of candidates based on the 25% explore / 75% exploit ratio.
    n_candidates_per_iteration = 4  # As per plan, e.g., 4 candidates per batch
    explore_fraction = clamp(explore_ratio, 0.0, 1.0) # Use the new parameter, clamped for safety
    n_explore = int(n_candidates_per_iteration * explore_fraction)
    n_exploit = n_candidates_per_iteration - n_explore

    for i in range(max_iterations):
        print(f"\n--- Optimization Iteration {i+1}/{max_iterations} ---")
        print(f"Generating {n_candidates_per_iteration} candidates ({n_exploit} exploit, {n_explore} explore)...")

        # "Ask" optimizer for the next best points to try (exploit)
        exploit_points = optimizer.ask(n_points=n_exploit)

        # Generate random points for exploration
        explore_points = optimizer.space.rvs(n_samples=n_explore)

        # Combine points for this iteration's batch
        next_points = exploit_points + explore_points
        X_batch, y_batch = [], [] # Store results for this batch

        # Process each point in the batch sequentially
        for point_idx, next_point in enumerate(next_points):
            print(f"\n-- Processing candidate {point_idx + 1}/{n_candidates_per_iteration} for iteration {i+1} --")
            search_space_names = [dim.name for dim in bo_search_space]
            suggested_values = dict(zip(search_space_names, next_point))
            print(f"Optimizer suggests point: {suggested_values}")

            # Separate formulation variables from process variables
            formulation_vars = {k: v for k, v in suggested_values.items() if k.endswith('_wtpct')}
            process_vars = {k: v for k, v in suggested_values.items() if not k.endswith('_wtpct')}

            # Clamp process variables
            if processing_levers:
                process_vars = clamp_process_row(process_vars, processing_levers)
                print(f"Clamped process vars: {process_vars}")

            # Create a valid new formulation from the optimizer's suggestion.
            # This re-balances the other formulation components to ensure the total sums to 100 wt%.
            template_df = formulations_df.head(1)
            next_formulation_df = pd.DataFrame([suggested_values])

            # --- Refined Re-balancing Logic ---
            # Instead of scaling all other components, we'll primarily adjust the base resin
            # to make up the difference to 100%. This is more realistic.
            sum_of_new_optimized_values = sum(formulation_vars.values())
            if sum_of_new_optimized_values > 100.0:
                print(f"Warning: Sum of optimized formulation values ({sum_of_new_optimized_values:.2f}) > 100. This point will be invalid.")
                # Let the invalid point proceed; the evaluator should penalize it heavily.

            # Identify all wt% columns from the original DOE to create a full recipe
            all_wtpct_cols = [col for col in formulations_df.columns if col.endswith('_wtpct')]
            unoptimized_wtpct_cols = [col for col in all_wtpct_cols if col not in formulation_vars]

            # Copy non-optimized values from the template, assuming they are minor additives
            for col in unoptimized_wtpct_cols:
                next_formulation_df[col] = template_df[col].iloc[0]

            # Re-calculate the sum and adjust the primary base resin (if one exists)
            current_sum = next_formulation_df[all_wtpct_cols].sum(axis=1).iloc[0]
            base_resin_cols = [c for c in formulations_df.columns if 'base' in c.lower() and c.endswith('_wtpct')]
            if base_resin_cols:
                primary_base = base_resin_cols[0]
                adjustment = 100.0 - current_sum
                next_formulation_df[primary_base] += adjustment
                print(f"Re-balancing: Adjusted '{primary_base}' by {adjustment:.2f} to sum to 100%.")

            next_formulation_df['mode'] = 'bo_suggested'

            # Run simulation and evaluation for this single candidate
            iter_id = f"iter_{i+1:02d}_cand_{point_idx+1:02d}"
            iter_formulation_path = os.path.join(formulations_dir, f"run_{run_timestamp}_{iter_id}.csv")
            iter_prediction_path = os.path.join(compounded_dir, f"run_{run_timestamp}_{iter_id}_prediction.csv")
            iter_process_path = os.path.join(compounded_dir, f"run_{run_timestamp}_{iter_id}_process.json")
            with open(iter_process_path, 'w') as f:
                json.dump(process_vars, f, indent=2)

            prediction_df = run_property_prediction_bridge(
                next_formulation_df, iter_formulation_path, iter_prediction_path, iter_process_path
            )
            evaluated_df = evaluate_with_agent(
                predictions_df=prediction_df,
                process_vars=process_vars,
                targets_constraints=targets_constraints,
                out_dir=os.path.join(compounded_dir, f"run_{run_timestamp}_{iter_id}_agent"),
                run_identifier=f"{run_timestamp}_{iter_id}"
            )

            iteration_path = os.path.join(compounded_dir, f"run_{run_timestamp}_{iter_id}_evaluated.csv")
            evaluated_df.to_csv(iteration_path, index=False)

            # Get the result and append to the batch lists
            result_score = evaluated_df['recommended_bo_weight'].iloc[0]
            X_batch.append(next_point)
            y_batch.append(-result_score) # skopt minimizes, so we pass the negative score

            print(f"Candidate {point_idx+1} evaluation complete. Score: {result_score:.4f}")
            log_cols = [
                'elastomer_wtpct', 'filler_wtpct', 'N_rps', 'Tm_C', 'sigma_y_MPa',
                'MFI_g10min', 'E_GPa', 'HDT_C', 'Izod_m20_kJm2', 'Izod_23_kJm2',
                'rho_gcc', 'eps_y_pct', 'Gardner_J', 'recommended_bo_weight'
            ]
            print(evaluated_df[[c for c in log_cols if c in evaluated_df.columns]].to_string())

        # After processing all points in the batch, "tell" the optimizer all results at once
        optimizer.tell(X_batch, y_batch)
        print(f"\nOptimizer updated with {len(X_batch)} new results from iteration {i+1}.")

    # Find the best result from the optimization history
    best_score_index = np.argmin(optimizer.yi)
    best_score = optimizer.yi[best_score_index]
    best_params = optimizer.Xi[best_score_index]
    best_params_dict = dict(zip([d.name for d in bo_search_space], best_params))

    # --- Generate and save plots ---
    print("\nGenerating optimization plots...")

    # 1. Convergence Plot: Shows the best score found over time.
    # The first N points are from the initial DOE, the rest are from active optimization.
    n_initial = len(X_initial)
    initial_best_score = np.min(optimizer.yi[:n_initial])
    optimization_scores = optimizer.yi[n_initial:]
    
    # We plot the negative of the internal score (since skopt minimizes), so higher is better.
    # Start the plot with the best score from the initial DOE points.
    convergence_data = -np.minimum.accumulate(np.insert(optimization_scores, 0, initial_best_score))

    plt.figure(figsize=(10, 6))
    plt.plot(range(0, len(convergence_data)), convergence_data, marker='o', linestyle='-')
    plt.title(f"Convergence Plot - Run {run_timestamp}")
    plt.xlabel("Active Optimization Iteration (0 = Best of Initial DOE)")
    plt.ylabel("Best Score Found So Far")
    plt.grid(True)
    convergence_plot_path = os.path.join(plots_dir, f"convergence_{run_timestamp}.png")
    plt.savefig(convergence_plot_path)
    plt.close()
    print(f"Saved convergence plot to {convergence_plot_path}")

    # 2. Partial Dependence Plots (shows objective function and uncertainty)
    _ = plot_objective(optimizer.get_result(), dimensions=search_space_names)
    pdp_plot_path = os.path.join(plots_dir, f"partial_dependence_{run_timestamp}.png")
    plt.savefig(pdp_plot_path)
    plt.close()
    print(f"Saved partial dependence plot to {pdp_plot_path}")

    print("\nOptimization loop finished.")
    print(f"Best score found: {-best_score:.4f}")
    print(f"Best formulation parameters: {best_params_dict}")

    # Save a summary of the optimization run
    summary = {
        "best_score": -best_score,
        "best_parameters": best_params_dict,
        "search_space": [str(d) for d in bo_search_space],
        "max_iterations": max_iterations
    }
    summary_path = os.path.join(summaries_dir, f"summary_{run_timestamp}.json")
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved optimization summary to {summary_path}")

    # --- Run final analysis and plotting ---
    print("\n--- Running Final Analysis ---")
    try:
        # Construct glob patterns for all artifacts from this run
        pred_glob = os.path.join(compounded_dir, f"*{run_timestamp}*prediction.csv")
        scores_glob = os.path.join(compounded_dir, f"*{run_timestamp}*_agent/row_*/scores.json")
        process_glob = os.path.join(compounded_dir, f"*{run_timestamp}*process*.json")

        # The analysis script expects a flat dictionary of targets
        targets_flat = {k: v.get('value') for k, v in targets_constraints.items() if isinstance(v, dict)}

        tidy_df = build_tidy(
            pred_glob=pred_glob,
            scores_glob=scores_glob,
            process_glob=process_glob,
            targets=targets_flat,
            baseline_model=None # Not using baseline model in this automated run
        )

        if not tidy_df.empty:
            tidy_out_path = os.path.join(summaries_dir, f"tidy_results_{run_timestamp}.csv")
            tidy_df.to_csv(tidy_out_path, index=False)
            print(f"Saved tidy results to {tidy_out_path}")
            make_plots(tidy_df, Path(plots_dir))
            print(f"Saved analysis plots to {plots_dir}")
    except Exception as e:
        print(f"An error occurred during final analysis: {e}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run the main optimization loop.")
    parser.add_argument("--iterations", type=int, default=30, help="Number of optimization iterations to run.")
    parser.add_argument("--initial-points", type=int, default=30, help="Number of initial DOE points to generate.")
    parser.add_argument("--focus", type=str, default="none", choices=["none", "recycled", "bio-based", "biopolyester"],
                        help="Focus for the initial DOE generation.")
    parser.add_argument("--goals", type=str, default=None, help="Path to a goals JSON file (e.g., for compostable constraints). Overrides --focus.")
    parser.add_argument("--explore-ratio", type=float, default=0.25, help="Fraction of candidates to generate via random exploration (0.0 to 1.0).")
    parser.add_argument("--spec-file", type=str, default=None, help="Path to a single spec sheet to define the optimization target.")
    args = parser.parse_args()
    
    goals_dict = json.loads(Path(args.goals).read_text()) if args.goals else None
    
    run_optimization_loop(max_iterations=args.iterations, n_initial_points=args.initial_points, focus_mode=args.focus, goals=goals_dict, explore_ratio=args.explore_ratio, spec_file=args.spec_file)