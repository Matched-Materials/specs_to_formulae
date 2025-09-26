# scripts/tidy_results.py
from __future__ import annotations
import argparse, json, glob, re
from pathlib import Path
from typing import Dict, Optional
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Assuming config files are in a sibling `src` directory
import sys
from pathlib import Path
# Add project root to path to allow absolute imports
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from configs.targets import get_targets
from configs.processing import compute_process_features, load_processing_levers

RUN_ID_RE = re.compile(r"(20\d{6}_\d{6})")
ITER_RE   = re.compile(r"iter_(\d{1,3})")

def _extract_cycle_id(p: Path) -> str:
    m = RUN_ID_RE.search(p.as_posix())
    return m.group(1) if m else "unknown"

def _extract_iter(p: Path) -> str:
    if "initial_" in p.as_posix():
        return "init"
    m = ITER_RE.search(p.as_posix())
    return str(m.group(1)) if m else "init"

def _extract_row_index(row_dir: Path) -> int:
    return int(row_dir.name.replace("row_", ""))

def load_predictions(pred_glob: str) -> pd.DataFrame:
    rows = []
    for fp in sorted(glob.glob(pred_glob)):
        p = Path(fp)
        df = pd.read_csv(p)
        if "row_index" not in df.columns:
            df = df.copy()
            df["row_index"] = np.arange(len(df))
        df["cycle_id"] = _extract_cycle_id(p)
        df["iter"]     = _extract_iter(p)
        rows.append(df)
    if not rows:
        raise FileNotFoundError(f"No prediction CSVs matched: {pred_glob}")
    return pd.concat(rows, ignore_index=True)

def load_scores(scores_glob: str) -> pd.DataFrame:
    recs = []
    for fp in sorted(glob.glob(scores_glob)):
        p = Path(fp)
        d = json.loads(Path(fp).read_text())

        # --- Flatten property-level scores for easier analysis ---
        property_scores = d.get("property_scores") or {}
        flat_prop_scores = {}
        for prop_name, prop_data in property_scores.items():
            if isinstance(prop_data, dict):
                flat_prop_scores[f"{prop_name}_score"] = prop_data.get("score")
        recs.append({
            "cycle_id": _extract_cycle_id(p),
            "iter": _extract_iter(p),
            "row_index": _extract_row_index(p.parent),
            **flat_prop_scores,
            "literature_consistency_score": d.get("literature_consistency_score"),
            "realism_penalty": d.get("realism_penalty"),
            "recommended_bo_weight": d.get("recommended_bo_weight"),
            "confidence": d.get("confidence"),
            "confidence_factor": d.get("confidence_factor"),
            "r2_izod_vs_elastomer": d.get("r2_izod_vs_elastomer"),
            "r2_modulus_vs_elastomer": d.get("r2_modulus_vs_elastomer"),
            "outlier_fraction": d.get("outlier_fraction"),
            "has_error": "error" in d,
            "notes_len": len((d.get("notes") or "")),
        })
    if not recs:
        # This is not an error, just might not have scores yet
        print(f"Warning: No scores.json matched: {scores_glob}")
        return pd.DataFrame()
    return pd.DataFrame.from_records(recs)

def derive_modes(df: pd.DataFrame) -> pd.DataFrame:
    def flag(col): return (df.get(col, 0).fillna(0) > 0).astype(int)
    df = df.copy()
    df["has_elastomer"] = flag("elastomer_wtpct")
    df["has_talc"]      = flag("talc_wtpct")
    df["has_filler"]    = flag("filler_wtpct")
    df["has_compat"]    = flag("compat_wtpct")

    # Add a column to distinguish between initial DOE and optimization iterations
    df['phase'] = np.where(df['iter'] == 'init', 'Initial DOE', 'Optimized')
    
    # The 'mode' column from the DOE generator is the primary mode.
    # We will create a secondary 'analysis_mode' for simplified grouping.
    df["analysis_mode"] = np.select(
        [
            (df["has_elastomer"]==1) & (df["has_talc"]==1),
            (df["has_elastomer"]==1) & (df["has_talc"]==0),
            (df["has_elastomer"]==0) & (df["has_talc"]==1),
        ],
        ["tough+stiff", "tough", "stiff"],
        default="base"
    )
    # simple process bins (tweak as needed)
    def bin_col(col, bins):
        if col not in df or df[col].isnull().all(): return np.nan
        # Convert intervals to strings for Parquet compatibility
        return pd.cut(df[col], bins=bins, include_lowest=True).astype(str)
    df["Tm_bin"]   = bin_col("Tm_C",  [170, 200, 230, 260])
    df["N_rps_bin"]= bin_col("N_rps",[0, 5, 10, 20, 60])
    df["Q_bin"]    = bin_col("Q_kgh",[0, 3, 6, 12])
    return df

def add_targets_and_residuals(df: pd.DataFrame, targets: dict[str,float]|None) -> pd.DataFrame:
    if not targets: 
        return df
    df = df.copy()
    # Ensure all key properties are included for residual calculation
    props = [
        "MFI_g10min", "sigma_y_MPa", "E_GPa", "Izod_m20_kJm2", "Izod_23_kJm2",
        "HDT_C", "rho_gcc", "eps_y_pct", "Gardner_J"
    ]
    for p in props:
        t = targets.get(p)
        if t is None: 
            continue
        df[f"{p}_target"] = t
        if p in df.columns:
            err = df[p] - t
            df[f"{p}_err"] = err
            df[f"{p}_ape"] = (err.abs() / (abs(t) + 1e-9))
    return df

def load_process_jsons(process_glob: str) -> pd.DataFrame:
    """Loads process conditions from a glob of JSON files."""
    if not process_glob:
        return pd.DataFrame()
    
    recs = []
    for fp in sorted(glob.glob(process_glob)):
        p = Path(fp)
        d = json.loads(p.read_text())
        rec = {
            "cycle_id": _extract_cycle_id(p),
            "iter": _extract_iter(p),
            **d
        }
        recs.append(rec)
    if not recs:
        print(f"Warning: No process JSONs matched: {process_glob}")
        return pd.DataFrame()
    return pd.DataFrame.from_records(recs)

def attach_baseline(df: pd.DataFrame, baseline_model_path: str) -> pd.DataFrame:
    """Placeholder for attaching baseline model predictions."""
    print(f"Attaching baseline predictions from: {baseline_model_path} (placeholder)")
    # In a real implementation, this would load a model and predict.
    # For now, we'll just add dummy columns.
    out = df.copy()
    for col in ["MFI_g10min", "sigma_y_MPa", "E_GPa", "Izod_m20_kJm2", "HDT_C"]:
        if col in out.columns:
            # Simulate a baseline that is systematically off
            out[f"{col}_base"] = out[col] * (1 + np.random.randn(len(out)) * 0.1 - 0.05)
            out[f"{col}_delta"] = out[col] - out[f"{col}_base"]
    return out

def make_plots(df: pd.DataFrame, outdir: Path):
    """Generates and saves summary plots."""
    print("Generating plots...")
    outdir.mkdir(parents=True, exist_ok=True)
    
    # --- 1. Plot Agent's Property-Level Plausibility Scores ---
    score_cols = sorted([c for c in df.columns if c.endswith("_score") and c != "literature_consistency_score"])
    if score_cols:
        plt.figure(figsize=(12, 7))
        # Melt the dataframe to have property names on x-axis and scores on y-axis
        melted_scores = df.melt(value_vars=score_cols, var_name='Property', value_name='Plausibility Score')
        sns.boxplot(data=melted_scores, x='Property', y='Plausibility Score')
        plt.title("Agent's Plausibility Scores per Property")
        plt.ylabel("Plausibility Score (0.0 - 1.0)")
        plt.xlabel("Property")
        plt.xticks(rotation=45, ha='right')
        plt.ylim(0, 1.05)
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()
        plot_path = outdir / "agent_property_scores_distribution.png"
        plt.savefig(plot_path)
        plt.close()

    # --- 2. Plot Predicted vs. Agent Score, colored by phase ---
    # This helps visualize if the agent is penalizing certain value ranges.
    props_with_scores = [c.replace("_score", "") for c in df.columns if c.endswith("_score")]

    for prop in props_with_scores:
        pred_col = prop
        score_col = f"{prop}_score"
        if pred_col not in df.columns or score_col not in df.columns:
            continue

        plt.figure(figsize=(10, 6))
        sns.scatterplot(data=df, x=pred_col, y=score_col, hue='phase', alpha=0.7, style='phase')
        plt.title(f"Agent Score vs. Predicted Value for {prop}")
        plt.xlabel(f"Predicted {prop}")
        plt.ylabel("Agent Plausibility Score")
        plt.ylim(0, 1.05)
        plt.grid(True, which='both', linestyle='--', linewidth=0.5)
        plt.legend(title="Optimization Phase")
        plot_path = outdir / f"agent_score_vs_predicted_{prop}.png"
        plt.savefig(plot_path)
        plt.close()
    
    # Define properties to plot residuals for
    props_to_plot = [
        "MFI_g10min", "sigma_y_MPa", "E_GPa", "Izod_m20_kJm2", "Izod_23_kJm2",
        "HDT_C", "rho_gcc", "eps_y_pct", "Gardner_J"
    ]
    features_to_plot = ["elastomer_wtpct", "filler_wtpct", "proc_intensity"]

    for prop in props_to_plot:
        err_col = f"{prop}_err"
        if err_col not in df.columns:
            continue
        
        for feat in features_to_plot:
            if feat not in df.columns:
                continue
            
            plt.figure(figsize=(8, 6))
            sns.scatterplot(data=df, x=feat, y=err_col, alpha=0.6, hue="mode")
            plt.axhline(0, color='r', linestyle='--')
            plt.title(f"Residual Plot: {err_col} vs. {feat}")
            plt.grid(True, which='both', linestyle='--', linewidth=0.5)
            plot_path = outdir / f"residual_{prop}_vs_{feat}.png"
            plt.savefig(plot_path)
            plt.close()
    print(f"Saved plots to {outdir}")

def build_tidy(
    pred_glob: str, 
    scores_glob: str, 
    process_glob: Optional[str],
    targets: Optional[Dict[str,float]],
    baseline_model: Optional[str],
) -> pd.DataFrame:
    pred = load_predictions(pred_glob)
    scr  = load_scores(scores_glob)
    proc = load_process_jsons(process_glob)

    # Merge predictions with scores
    if not scr.empty:
        df = pred.merge(scr, on=["cycle_id","iter","row_index"], how="left")
    else:
        df = pred

    # Merge process conditions if they weren't in the prediction files
    if not proc.empty:
        # Get list of process columns that are not already in df
        proc_cols_to_merge = [c for c in proc.columns if c not in df.columns or c in ["cycle_id", "iter"]]
        if len(proc_cols_to_merge) > 2: # more than just keys
             df = df.merge(proc[proc_cols_to_merge], on=["cycle_id", "iter"], how="left")

    # Add features and residuals
    df = derive_modes(df)
    df = add_targets_and_residuals(df, targets)
    
    # Add normalized process features
    try:
        levers = load_processing_levers("data/processed/processing_levers.json")
        df = compute_process_features(df, levers)
    except FileNotFoundError:
        print("Warning: Processing levers not found at data/processed/processing_levers.json. Skipping process feature generation.")

    # Attach baseline if requested
    if baseline_model:
        df = attach_baseline(df, baseline_model)

    return df

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="results/compounded/*prediction*.csv")
    ap.add_argument("--scores", default="results/compounded/*_agent/row_*/scores.json")
    ap.add_argument("--process_file_glob", default="", help="Optional glob for process condition JSONs (e.g., 'results/compounded/*_process.json')")
    ap.add_argument("--outdir", default="results/summaries")
    ap.add_argument("--targets_file", default="data/processed/target_properties.json", help="Path to target properties JSON file.")
    ap.add_argument("--izod_temp", default="neg20C", choices=["neg20C", "pos23C"], help="Izod temperature to use for targets. Use 'neg20C' or 'pos23C'.")
    ap.add_argument("--baseline_model", default="", help="Path to a baseline model for comparison.")
    ap.add_argument("--top-n-threshold", type=float, default=0.75, help="Score threshold for top candidates list.")
    args = ap.parse_args()

    # Map the CLI-friendly izod_temp argument to the value expected by the config loader.
    # This avoids argparse misinterpreting "-20C" as a flag.
    izod_temp_map = {"neg20C": "-20C", "pos23C": "23C"}
    izod_temp_val = izod_temp_map.get(args.izod_temp, "-20C")

    # Load targets using the config helper
    try:
        targets = get_targets(path=args.targets_file, izod_temp=izod_temp_val)
    except (FileNotFoundError, KeyError) as e:
        print(f"Error loading targets: {e}. Proceeding without targets.")
        targets = None

    tidy = build_tidy(args.pred, args.scores, args.process_file_glob, targets, args.baseline_model)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    cid = tidy["cycle_id"].mode()[0] if "cycle_id" in tidy and not tidy["cycle_id"].empty else "run"
    
    # Main tidy output
    tidy.to_parquet(outdir / f"tidy_{cid}.parquet", index=False)
    tidy.to_csv(outdir / f"tidy_{cid}.csv", index=False)

    # tiny console summary
    print(f"[ok] wrote: {outdir}/tidy_{cid}.parquet and .csv")

    # Filter for and save top candidates
    if "recommended_bo_weight" in tidy.columns:
        top_candidates = tidy[tidy["recommended_bo_weight"] >= args.top_n_threshold].copy()
        if not top_candidates.empty:
            top_candidates.sort_values("recommended_bo_weight", ascending=False, inplace=True)
            top_candidates_path = outdir / f"top_candidates_{cid}.csv"
            top_candidates.to_csv(top_candidates_path, index=False)
            print(f"[ok] wrote: {len(top_candidates)} top candidates (score >= {args.top_n_threshold}) to {top_candidates_path}")
        else:
            print(f"Info: No candidates found with score >= {args.top_n_threshold}.")

    # Bias summaries
    if targets:
        prop_err_cols = [c for c in tidy.columns if c.endswith("_err")]
        if prop_err_cols:
            # Bias by formulation mode
            group_cols = ["mode", "analysis_mode"]
            bias_by_mode = tidy.groupby([c for c in group_cols if c in tidy.columns])[prop_err_cols].mean().reset_index()
            bias_by_mode.to_csv(outdir / "bias_by_mode.csv", index=False)
            print(f"[ok] wrote: {outdir}/bias_by_mode.csv")

            # Bias by process intensity
            if "proc_intensity" in tidy.columns:
                tidy["proc_intensity_bin"] = pd.cut(tidy["proc_intensity"], bins=5).astype(str)
                bias_by_intensity = tidy.groupby("proc_intensity_bin")[prop_err_cols].mean().reset_index()
                bias_by_intensity.to_csv(outdir / "bias_by_intensity.csv", index=False)
                print(f"[ok] wrote: {outdir}/bias_by_intensity.csv")
    
    # Make plots (moved outside of `if targets` to ensure they always run)
    plot_dir = Path("results/plots")
    make_plots(tidy, plot_dir)

if __name__ == "__main__":
    main()

'''Usage: 
python src/analysis/tidy_results.py \
  --pred "results/compounded/*prediction*.csv" \
  --scores "results/compounded/*_agent/row_*/scores.json" \
  --outdir "results/summaries" \
  --targets_file "data/processed/target_properties.json" \
  --izod_temp "neg20C" \
  --process_file_glob "results/compounded/run_*_process.json" \
  --baseline_model "data/pp_elastomer_TSE_hybrid_model_v1.json"
'''