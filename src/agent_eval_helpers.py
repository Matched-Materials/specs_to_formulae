# src/agent_eval_helpers.py
from __future__ import annotations
import os
import json
import shutil
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np
import logging
import yaml

try:
    from src.analysis.baseline import load_baseline_model, baseline_predict_row
    BASELINE_MODEL = load_baseline_model()
except (ImportError, FileNotFoundError):
    print("Warning: Baseline model not found or cannot be loaded. Skipping baseline deviation checks.")
    BASELINE_MODEL = None
    baseline_predict_row = None

try:
    with open("configs/guardrails.yaml", 'r') as f:
        GUARDRAILS = yaml.safe_load(f)
    THRESHOLDS = GUARDRAILS.get("thresholds", {})
except (FileNotFoundError, ImportError):
    print("Warning: guardrails.yaml not found or PyYAML not installed. Skipping guardrail checks.")
    THRESHOLDS = {}

logger = logging.getLogger(__name__)

# Adapter that actually runs the agent:
from src.run_evaluator_with_adk import evaluate_context
from evaluator.matsi_property_evaluator.eval_schema import EvalInput

PROPERTY_MAP = {
    # Map human names (in your target_properties.json) to prediction column names
    "Melt Mass-Flow Rate (MFR)": {"key": "MFI_g10min"},
    "Tensile Strength at Yield": {"key": "sigma_y_MPa"},
    # For properties with multiple conditions, use a list of condition-to-key maps
    "Flexural Modulus": [
        {"conditions_must_contain": "ISO 178", "key": "E_GPa"}
    ],
    "Notched Izod Impact Strength": [
        {"conditions_must_contain": "-20", "key": "Izod_m20_kJm2"},
        {"conditions_must_contain": "23", "key": "Izod_23_kJm2"}
    ],
    "Heat Deflection Temperature": {"key": "HDT_C"},
    # --- Add remaining properties from the model ---
    "Density": {"key": "rho_gcc"},
    "Elongation at Yield": {"key": "eps_y_pct"},
    "Gardner Impact": {"key": "Gardner_J"},
}


def _coerce_number(x):
    try:
        return float(x)
    except Exception:
        return None


def build_targets_constraints(targets_input: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Load targets from a file path or use a dictionary directly to build a constraints dictionary for the agent.
    This function now handles properties that are distinguished by their 'conditions'
    or 'test_method' to select the correct target value.
    """
    if isinstance(targets_input, (str, Path)):
        with open(targets_input, "r") as f:
            targets_src = json.load(f)
    elif isinstance(targets_input, dict):
        targets_src = targets_input
    else:
        raise TypeError("targets_input must be a file path or a dictionary.")

    constraints: Dict[str, Any] = {}
    for prop in targets_src.get("properties", []):
        name = prop.get("name")
        if not name:
            continue

        # --- Robust lookup: find the correct mapping for the property name ---
        found_mapping = None
        if name in PROPERTY_MAP:
            found_mapping = PROPERTY_MAP[name]
        else:
            # Check if the name matches a 'key' value inside the map
            for map_value in PROPERTY_MAP.values():
                check_maps = map_value if isinstance(map_value, list) else [map_value]
                for m in check_maps:
                    if m.get("key") == name:
                        found_mapping = map_value
                        break
                if found_mapping:
                    break
        
        if not found_mapping:
            continue

        # Normalize to a list to handle both single and multiple mappings
        mappings = found_mapping if isinstance(found_mapping, list) else [found_mapping]

        for condition_map in mappings:
            key = condition_map.get("key")
            if not key: continue

            # Check if a condition is required for this mapping
            condition_str = condition_map.get("conditions_must_contain")
            if condition_str and condition_str not in prop.get("conditions", "") and condition_str not in prop.get("test_method", ""):
                continue # This mapping doesn't apply, try the next one
            
            constraints[key] = {
                "value": _coerce_number(prop.get("value")),
                "tol": _coerce_number(prop.get("tol")),
                "weight": _coerce_number(prop.get("weight", 1.0)),
                "notes": prop.get("notes"),
            }
    return constraints


def _row_formulation_dict(row: pd.Series) -> Dict[str, Any]:
    """
    Pull *_wtpct columns from a row to form the 'Formulation' payload.
    """
    d = {}
    for c, v in row.items():
        if isinstance(c, str) and c.endswith("_wtpct"):
            d[c] = _coerce_number(v)
    return d


def _validate_agent_context(ctx: dict):
    """Checks for required keys and content before calling the agent."""
    missing = [k for k in ("predictions", "targets_constraints")
               if not ctx.get(k) or not isinstance(ctx.get(k), dict)]
    if missing:
        raise ValueError(
            f"Agent context is missing required keys: {missing}. Context keys available: {list(ctx.keys())}"
        )

    if not ctx["predictions"]:
        raise ValueError(
            "Agent context 'predictions' dictionary is empty. "
            "Check if prediction columns from 'targets_constraints' exist in the input DataFrame."
        )


def _to_plain_json(obj: Any) -> Any:
    """Converts an object to a JSON-serializable format, handling numpy/pandas types."""
    def default(o):
        if hasattr(o, "item"):
            try: return o.item()
            except Exception: pass
        if isinstance(o, pd.Timestamp):
            return o.isoformat()
        # pandas NA / numpy nan → None
        try:
            import numpy as np
            if isinstance(o, (np.floating, )) and (math.isnan(float(o)) or math.isinf(float(o))):
                return None
        except Exception:
            pass
        return str(o)
    return json.loads(json.dumps(obj, default=default))

def _build_agent_context_dict(
    row: pd.Series, process_vars: Dict[str, Any], targets_constraints: Dict[str, Any], run_identifier: str
) -> Dict[str, Any]:
    """Builds a clean, JSON-serializable context dictionary for the agent."""
    
    # 1. Predictions: Extract all numeric properties from the row
    all_preds = {k: v for k, v in row.to_dict().items() if isinstance(v, (int, float))}
    
    # 2. Process: Combine process variables and the formulation recipe
    process = dict(process_vars)
    formulation = {k: v for k, v in row.to_dict().items() if k.endswith(('_wtpct', '_ppm')) and isinstance(v, (int, float))}
    process.update(formulation)

    # 3. Clean and Coerce all values to be JSON-safe
    # This is the critical step to prevent Pydantic validation errors.
    final_predictions = _to_plain_json({k: v for k, v in all_preds.items() if pd.notna(v)})
    final_process = _to_plain_json({k: v for k, v in process.items() if pd.notna(v)})
    final_targets = _to_plain_json(targets_constraints)

    payload = EvalInput(
        cycle_id=run_identifier,
        sample_id=str(getattr(row, "name", -1)),
        predictions=final_predictions, # The cleaned predictions dictionary
        process=final_process,         # The cleaned process dictionary
        targets_constraints=final_targets, # The cleaned targets dictionary
        meta={"source": "pipeline", "row_index": int(getattr(row, "name", -1))}
    )
    return payload.model_dump(mode="json")

def _pre_flight_check(row: pd.Series, targets: Dict[str, Any], idx: int):
    """Calculates and prints the deviation of predictions from targets to help debug agent failures."""
    errors = {}
    for prop, target_data in targets.items():
        if prop in row and target_data and isinstance(target_data, dict):
            pred_val = row.get(prop)
            target_val = target_data.get('value')
            if pred_val is not None and target_val is not None and abs(target_val) > 1e-9:
                error = pred_val - target_val
                norm_error = error / target_val
                errors[prop] = norm_error
    
    if errors:
        # Sort by absolute error to see the biggest problems first
        sorted_errors = sorted(errors.items(), key=lambda item: abs(item[1]), reverse=True)
        print(f"\n    - Pre-flight check for row {idx}:")
        for prop, norm_err in sorted_errors[:3]: # Print top 3 offenders
            print(f"      - Property '{prop}' has normalized error: {norm_err:+.1%}")


def evaluate_with_agent(
    predictions_df: pd.DataFrame,
    process_vars: Dict[str, Any],
    targets_constraints: Dict[str, Any],
    out_dir: str,
    run_identifier: str,
) -> pd.DataFrame:
    """
    For each row in predictions_df, call the evaluator agent and attach scores as columns.
    Returns a copy of predictions_df with added columns:
        literature_consistency_score, realism_penalty, recommended_bo_weight, confidence
    Also leaves per-row artifacts in out_dir/<row_idx>/{evaluation_report.md,scores.json}.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    # Create a dedicated directory for failed evaluation artifacts for easier debugging
    # The directory is named with the run_identifier to keep failed runs organized.
    failed_eval_dir = os.path.join(os.path.dirname(out_dir), "failed_evaluations", run_identifier)
    os.makedirs(failed_eval_dir, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    total_rows = len(predictions_df)
    print(f"\nStarting agent evaluation for {total_rows} candidates...")

    for i, (idx, row) in enumerate(predictions_df.iterrows()):
        row_dir = os.path.join(out_dir, f"row_{idx:04d}")
        print(f"\n  - Evaluating row {i+1}/{total_rows} (index: {idx})...", end="", flush=True)

        # Add a pre-flight check to see which properties are furthest from target.
        # This helps diagnose agent failures caused by token limits from long explanations.
        _pre_flight_check(row, targets_constraints, idx)

        try:
            # Build the evaluator context payload
            ctx = _build_agent_context_dict(row, process_vars, targets_constraints, run_identifier)
        except (ValueError, TypeError) as e:
            print(" ERROR (validation)")
            logger.error(f"Validation error for row {idx}: {e}")
            continue
        except Exception as e:
            print(" ERROR (payload build)")
            print(f"Skipping row {idx} due to payload build error: {e}")
            continue

        out = evaluate_context(ctx, row_dir)
        print(" done.")

        # --- Handle evaluation result (success or failure) ---
        score = out.get("score") or {}
        if "error" in score:
            print(f"      -> Evaluation failed for row {idx}. Reason: {score.get('error', 'Unknown')}. Moving artifacts.")
            # Move the failed evaluation directory for later inspection
            try:
                # Use a unique name in the failed directory to avoid collisions from different runs
                failed_row_dir_name = f"row_{idx:04d}_{run_identifier}"
                shutil.move(row_dir, os.path.join(failed_eval_dir, failed_row_dir_name))
            except Exception as e:
                print(f"      -> Warning: Could not move failed evaluation directory: {e}")

            # Append a row with fallback scores to keep the data point but penalize it
            failed_row = row.to_dict()
            failed_row.update({
                "literature_consistency_score": 0.1,
                "realism_penalty": 0.1,
                "recommended_bo_weight": 0.0, # Give it a very low score for the optimizer
                "confidence": "Error",
                "evaluation_status": "failed"
            })
            # Add nulls for property scores
            for prop in targets_constraints.keys():
                failed_row[f"{prop}_score"] = None
            
            rows.append(failed_row)
            continue # Move to the next row

        # --- Process successful evaluation ---
        flags = []
        # --- Flatten property-level scores for easier analysis ---
        property_scores = score.get("property_scores") or {}
        flat_prop_scores = {}
        for prop_name, prop_data in property_scores.items():
            if isinstance(prop_data, dict):
                flat_prop_scores[f"{prop_name}_score"] = prop_data.get("score")
        # 1. Unrealistic physics check
        if row.get('Izod_m20_kJm2', 0) > 25 and row.get('elastomer_wtpct', 0) < 5:
            flags.append("unrealistic_izod_without_elastomer")

        # 2. Deviation from baseline check
        if BASELINE_MODEL and THRESHOLDS and baseline_predict_row:
            base_preds = baseline_predict_row(row, BASELINE_MODEL)
            for prop, thresh in THRESHOLDS.items():
                if prop in row and f"{prop}_base" in base_preds:
                    pred_val = row.get(prop)
                    base_val = base_preds.get(f"{prop}_base")
                    if pred_val is not None and base_val is not None and abs(pred_val - base_val) > thresh:
                        flags.append(f"large_delta_from_baseline_for_{prop}")
        
        if flags:
            if "extras" not in score: score["extras"] = {}
            score["extras"]["flags"] = list(set(score.get("extras", {}).get("flags", []) + flags))
            if "recommended_bo_weight" in score and score["recommended_bo_weight"] is not None:
                score["recommended_bo_weight"] *= 0.5 # Penalize by 50%

        # Pull scores and add fallbacks to prevent errors in the optimizer
        lc_score = score.get("literature_consistency_score")
        penalty = score.get("realism_penalty")
        confidence = score.get("confidence")
        rec_weight = score.get("recommended_bo_weight")

        if rec_weight is None:
            # Fallback calculation if agent fails to provide a valid weight.
            # Use pessimistic defaults if other scores are also missing.
            lc_score = lc_score if lc_score is not None else 0.5
            penalty = penalty if penalty is not None else 0.5
            confidence = confidence if confidence is not None else "Low"
            
            # Align fallback logic with the agent's prompt instructions.
            conf_scale = {"High": 1.0, "Medium": 0.65, "Low": 0.35}.get(confidence, 0.35)
            rec_weight = max(0.0, min(1.0, (lc_score * penalty) * conf_scale))

        rows.append({
            **row.to_dict(),
            **flat_prop_scores,
            "literature_consistency_score": lc_score,
            "realism_penalty": penalty,
            "recommended_bo_weight": rec_weight,
            "confidence": confidence,
            "evaluation_status": "success" # Add status for successful runs
        })

    return pd.DataFrame(rows)
