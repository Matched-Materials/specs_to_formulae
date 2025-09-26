### Tooling Available to You

You may request the following helper functions. The orchestrator will execute them and return results.
Use them to support sections (1–6). If unavailable, reason qualitatively.

- pearson_corr(x, y) → {n, r, r2, x_mean, y_mean, x_std, y_std}
- simple_linear_regression(x, y) → {n, slope, intercept, r2, rmse}
- zscore_outliers(x, z_thresh=2.0) → {mean, std, outlier_idx, outlier_vals}
- iqr_outliers(x, k=1.5) → {q1, q3, iqr, low, high, outlier_idx, outlier_vals}
- range_check(value, lo, hi) → {ok, value, lo, hi}
- trend_sign_check(slope, expected_sign) → {expected, observed, ok}
- advisory_weight(consistency, realism, confidence) → {recommended_weight, ...}

When you need a function executed, emit a fenced block tagged as `TOOLCALL` with a single JSON object:

```TOOLCALL
{"fn":"pearson_corr","args":{"x":[...],"y":[...]}}
