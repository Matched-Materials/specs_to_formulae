"""
evaluator_tools.py
Lightweight statistics helpers the evaluator agent can request via your orchestrator.
No heavy dependencies: standard library only.

All functions return small JSON-serializable dicts for easy logging and parsing.
"""

from typing import Sequence, Dict, Any, List
import math

# ---------- Helpers ----------

def _as_float_list(x: Sequence[float]) -> List[float]:
    return [float(v) for v in x]

def _mean(x: Sequence[float]) -> float:
    n = len(x)
    return sum(x) / n if n else float('nan')

def _var(x: Sequence[float]) -> float:
    n = len(x)
    mu = _mean(x)
    return sum((v - mu) ** 2 for v in x) / (n - 1) if n > 1 else float('nan')

def _std(x: Sequence[float]) -> float:
    v = _var(x)
    return math.sqrt(v) if v == v else float('nan')

# ---------- Correlation ----------

def pearson_corr(x: Sequence[float], y: Sequence[float]) -> Dict[str, Any]:
    """
    Returns Pearson correlation r, r^2, means, stds, and n.
    """
    x_f = _as_float_list(x)
    y_f = _as_float_list(y)
    n = min(len(x_f), len(y_f))

    # Truncate to common length for paired data analysis
    if len(x_f) > n: x_f = x_f[:n]
    if len(y_f) > n: y_f = y_f[:n]

    if n < 2:
        return {"n": n, "r": float('nan'), "r2": float('nan'), "x_mean": _mean(x_f), "y_mean": _mean(y_f), "x_std": _std(x_f), "y_std": _std(y_f)}
    xm = _mean(x_f); ym = _mean(y_f)
    num = sum((xi - xm) * (yi - ym) for xi, yi in zip(x_f, y_f))
    denx = math.sqrt(sum((xi - xm) ** 2 for xi in x_f))
    deny = math.sqrt(sum((yi - ym) ** 2 for yi in y_f))
    r = num / (denx * deny) if denx and deny else float('nan')
    return {"n": n, "r": r, "r2": (r*r if r==r else float('nan')), "x_mean": xm, "y_mean": ym, "x_std": _std(x_f), "y_std": _std(y_f)}

# ---------- Linear regression ----------

def simple_linear_regression(x: Sequence[float], y: Sequence[float]) -> Dict[str, Any]:
    """
    y = a + b*x  (intercept a, slope b)
    Returns slope, intercept, R^2, RMSE, and n.
    """
    x_f = _as_float_list(x)
    y_f = _as_float_list(y)
    n = min(len(x_f), len(y_f))

    # Truncate to common length for paired data analysis
    if len(x_f) > n: x_f = x_f[:n]
    if len(y_f) > n: y_f = y_f[:n]

    if n < 2:
        return {"n": n, "slope": float('nan'), "intercept": float('nan'), "r2": float('nan'), "rmse": float('nan')}
    xm = _mean(x_f); ym = _mean(y_f)
    Sxx = sum((xi - xm) ** 2 for xi in x_f)
    Sxy = sum((xi - xm) * (yi - ym) for xi, yi in zip(x_f, y_f))
    if Sxx == 0:
        return {"n": n, "slope": float('nan'), "intercept": float('nan'), "r2": float('nan'), "rmse": float('nan')}
    slope = Sxy / Sxx
    intercept = ym - slope * xm
    # Fit diagnostics
    yhat = [intercept + slope * xi for xi in x_f]
    ss_res = sum((yi - yhi) ** 2 for yi, yhi in zip(y_f, yhat))
    ss_tot = sum((yi - ym) ** 2 for yi in y_f)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot else float('nan')
    rmse = math.sqrt(ss_res / n) if n > 0 else float('nan')
    return {"n": n, "slope": slope, "intercept": intercept, "r2": r2, "rmse": rmse}

# ---------- Outliers ----------

def zscore_outliers(x: Sequence[float], z_thresh: float = 2.0) -> Dict[str, Any]:
    """
    Returns indices and values with |z| > z_thresh, along with mean/std.
    """
    x = _as_float_list(x)
    n = len(x)
    mu = _mean(x)
    sd = _std(x)
    if not (sd == sd) or sd == 0 or n < 2:
        return {"n": n, "mean": mu, "std": sd, "z_thresh": z_thresh, "outlier_idx": [], "outlier_vals": []}
    z = [(xi - mu) / sd for xi in x]
    idx = [i for i, zi in enumerate(z) if abs(zi) > z_thresh]
    vals = [x[i] for i in idx]
    return {"n": n, "mean": mu, "std": sd, "z_thresh": z_thresh, "outlier_idx": idx, "outlier_vals": vals}

def iqr_outliers(x: Sequence[float], k: float = 1.5) -> Dict[str, Any]:
    """
    Tukey fences with multiplier k (1.5 default). Returns indices outside [Q1-k*IQR, Q3+k*IQR].
    """
    x_f = _as_float_list(x)
    n = len(x_f)
    if n < 4:
        return {"n": n, "q1": float('nan'), "q3": float('nan'), "iqr": float('nan'), "k": k, "low": float('nan'), "high": float('nan'), "outlier_idx": [], "outlier_vals": []}

    xs = sorted(x_f)
    def _pct(sorted_x, p):
        i = (len(sorted_x) - 1) * p
        lo = int(math.floor(i)); hi = int(math.ceil(i))
        if lo == hi: return sorted_x[lo]
        return sorted_x[lo] * (hi - i) + sorted_x[hi] * (i - lo)

    q1 = _pct(xs, 0.25); q3 = _pct(xs, 0.75)
    iqr = q3 - q1
    low = q1 - k * iqr; high = q3 + k * iqr
    # Find outliers in the original unsorted list to return correct indices
    idx = [i for i, v in enumerate(x_f) if (v < low or v > high)]
    vals = [x_f[i] for i in idx]
    return {"n": n, "q1": q1, "q3": q3, "iqr": iqr, "k": k, "low": low, "high": high, "outlier_idx": idx, "outlier_vals": vals}

# ---------- Range / plausibility checks ----------

def range_check(value: float, lo: float, hi: float) -> Dict[str, Any]:
    ok = (lo <= value <= hi)
    return {"ok": ok, "value": float(value), "lo": float(lo), "hi": float(hi)}

def trend_sign_check(slope: float, expected_sign: str) -> Dict[str, Any]:
    """
    expected_sign: 'neg' | 'pos'
    """
    sign = 'pos' if slope > 0 else 'neg' if slope < 0 else 'zero'
    return {"expected": expected_sign, "observed": sign, "ok": (sign == expected_sign)}

# ---------- Composite advisory weight ----------

def advisory_score(literature_consistency: float, realism_penalty: float, confidence: str, notes: str = "") -> Dict[str, Any]:
    """
    Calculates a composite score for a Bayesian Optimizer based on evaluation metrics.
    This aligns with the `evaluator_advisory_scores.schema.json`.

    - literature_consistency: (0-1) How well does the result align with known science.
    - realism_penalty: (0-1) A penalty for results that seem physically unrealistic.
    - confidence: (High, Medium, Low) Confidence in the evaluation.
    High=1.0, Medium=0.8, Low=0.6
    - notes: Optional explanatory notes.
    """
    conf_scale = {"high": 1.0, "medium": 0.8, "low": 0.6}.get(str(confidence).lower(), 0.8)
    # The weight is reduced by the realism penalty
    weight = max(0.0, min(1.0, (literature_consistency * (1.0 - realism_penalty)) * conf_scale))
    return {
        "literature_consistency": float(literature_consistency),
        "realism_penalty": float(realism_penalty),
        "confidence": confidence,
        "recommended_bo_weight": weight,
        "notes": notes
    }

if __name__ == '__main__':
    # Example Usage
    print("--- Example Usage of Evaluator Tools ---")

    # Sample data
    sample_x = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 50] # includes an outlier
    sample_y = [2.1, 3.9, 6.2, 7.8, 10.3, 12.1, 13.9, 16.2, 17.8, 20.3, 25] # y = 2*x, with some noise and one odd point

    print("\n--- Correlation ---")
    corr_result = pearson_corr(sample_x, sample_y)
    print(f"Pearson Correlation: r={corr_result.get('r', 'N/A'):.3f}, r^2={corr_result.get('r2', 'N/A'):.3f}, n={corr_result.get('n')}")

    # Demonstrate unequal length handling
    corr_unequal = pearson_corr(sample_x, sample_y[:-1])
    print(f"Pearson Correlation (unequal lengths): n={corr_unequal.get('n')}")


    print("\n--- Linear Regression ---")
    reg_result = simple_linear_regression(sample_x, sample_y)
    print(f"Simple Linear Regression: y = {reg_result.get('intercept', 'N/A'):.2f} + {reg_result.get('slope', 'N/A'):.2f}*x, R^2={reg_result.get('r2', 'N/A'):.3f}, RMSE={reg_result.get('rmse', 'N/A'):.2f}")

    print("\n--- Outlier Detection ---")
    z_outliers = zscore_outliers(sample_x, z_thresh=2.0)
    print(f"Z-Score Outliers (|z|>2.0): Indices={z_outliers['outlier_idx']}, Values={z_outliers['outlier_vals']}")

    iqr_outliers_res = iqr_outliers(sample_x, k=1.5)
    print(f"IQR Outliers (k=1.5): Indices={iqr_outliers_res['outlier_idx']}, Values={iqr_outliers_res['outlier_vals']}")

    print("\n--- Range and Trend Checks ---")
    range_res = range_check(value=5.5, lo=0.0, hi=10.0)
    print(f"Range Check (5.5 in [0, 10]): OK = {range_res['ok']}")
    range_res_fail = range_check(value=11.0, lo=0.0, hi=10.0)
    print(f"Range Check (11.0 in [0, 10]): OK = {range_res_fail['ok']}")

    trend_res = trend_sign_check(reg_result.get('slope', 0), expected_sign='pos')
    print(f"Trend Sign Check (slope={reg_result.get('slope', 0):.2f}): OK = {trend_res['ok']} (Expected: 'pos', Observed: '{trend_res['observed']}')")

    print("\n--- Advisory Weight ---")
    score_res = advisory_score(literature_consistency=0.9, realism_penalty=0.1, confidence="High", notes="Looks good.")
    print(f"Advisory Score (High confidence): {score_res['recommended_bo_weight']:.2f} | Notes: '{score_res['notes']}'")
    score_res_low = advisory_score(literature_consistency=0.9, realism_penalty=0.1, confidence="Low")
    print(f"Advisory Score (Low confidence): {score_res_low['recommended_bo_weight']:.2f}")
