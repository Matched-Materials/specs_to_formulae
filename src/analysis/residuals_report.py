import pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt
import argparse

def main(parquet_path: str, outdir: str = "results/summaries"):
    print(f"Loading tidy data from: {parquet_path}")
    try:
        df = pd.read_parquet(parquet_path)
    except FileNotFoundError:
        print(f"Error: Parquet file not found at {parquet_path}")
        return

    props = ["MFI_g10min","sigma_y_MPa","E_GPa","Izod_m20_kJm2","HDT_C"]
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Check if any error columns exist
    err_cols_exist = any(f"{p}_err" in df.columns for p in props)
    if not err_cols_exist:
        print("\nWarning: No residual columns ('*_err') found in the Parquet file.")
        print("This script requires residuals to be calculated by 'tidy_results.py'.")
        print("Please ensure 'tidy_results.py' was run with target properties defined.")
        return

    # Save Spearman correlations vs key knobs
    print("\nCalculating Spearman correlations...")
    knobs = [c for c in df.columns if c.endswith("_wtpct")] + ["proc_intensity","N_rps","Tm_C"]
    corrs = []
    for p in props:
        err_col = f"{p}_err"
        if err_col not in df.columns: continue
        for k in knobs:
            if k in df and df[k].notna().sum() > 1:
                # Ensure there are at least 2 valid pairs for correlation
                valid_pairs = df[[err_col, k]].dropna()
                if len(valid_pairs) > 1:
                    corrs.append({"property": p, "knob": k, "spearman": valid_pairs.corr(method="spearman").iloc[0,1]})
    if corrs:
        corr_df = pd.DataFrame(corrs)
        corr_path = outdir / "spearman_residuals_vs_knobs.csv"
        corr_df.to_csv(corr_path, index=False)
        print(f"[ok] Wrote correlations to: {corr_path}")
    else:
        print("Skipping correlation report (not enough data).")

    # Quick plots (no seaborn)
    print("\nGenerating residual plots...")
    plot_outdir = Path("results/plots")
    plot_outdir.mkdir(parents=True, exist_ok=True)
    plots_generated = 0
    for p in props:
        err_col = f"{p}_err"
        if err_col not in df.columns: continue
        for k in ["elastomer_wtpct","filler_wtpct","proc_intensity"]:
            if k in df and df[k].notna().any():
                plt.figure(figsize=(8, 5))
                # Use the DataFrame plot method directly on a non-NaN subset
                plot_data = df[[k, err_col]].dropna()
                if not plot_data.empty:
                    plot_data.plot(x=k, y=err_col, kind="scatter", title=f"{p} residual vs {k}", grid=True, alpha=0.6)
                    plt.axhline(0, color='r', linestyle='--')
                    plot_path = plot_outdir / f"{p}_residual_vs_{k}.png"
                    plt.savefig(plot_path, bbox_inches="tight")
                    plt.close()
                    plots_generated += 1

    if plots_generated > 0:
        print(f"[ok] Wrote {plots_generated} plots to: {plot_outdir}")
    else:
        print("Skipping plots (no relevant data found).")
    
    print("\nResiduals report script finished.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate a report on model residuals from a tidy data file.")
    ap.add_argument("--parquet", required=True, help="Path to the tidy parquet file from tidy_results.py")
    ap.add_argument("--outdir", default="results/summaries", help="Directory to save summary CSVs")
    args = ap.parse_args()
    main(args.parquet, args.outdir)