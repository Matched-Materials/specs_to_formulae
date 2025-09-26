# spec_sheets_to_formulas/src/pipeline.py
from __future__ import annotations
import os
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional
import time
import math
import json

from .utils.io import write_json, sha256_file, safe_mkdirs, setup_logging, read_json
from .ingest.normalize_spec import normalize_spec
from .gapfill.merger import gapfill
from .prefilter import prefilter

# --- Import the core physics model from the bridge script ---
# This allows us to predict properties for in-memory candidates without calling a subprocess.
from .bridge_formulations_to_properties import compute_row_properties, load_ingredient_catalog
from .formulation_doe_generator_V1 import generate_formulation_doe
from .agent_eval_helpers import evaluate_with_agent, build_targets_constraints


def generate_candidates(pf: Dict[str, Any], n_candidates: int = 20) -> List[Dict[str, Any]]:
    """
    Generates a set of candidate formulations based on the prefilter allowlist.
    This now uses the more advanced DOE generator.
    """
    allowlist = pf.get("allowlist", [])
    if not allowlist:
        return []

    # The DOE generator returns a DataFrame, we need to convert it to the list-of-dicts format.
    # We'll use the first allowed family as the focus for generation.
    # This logic maps the `allowlist` from the prefilter (e.g., ['biopolymer'])
    # to a specific `focus` mode that the DOE generator understands (e.g., 'biopolyester').
    # This prevents the generator from attempting to create chemically incompatible
    # formulations (like PP/PLA) when a compostable goal is active.
    if "biopolymer" in allowlist:
        focus_family = "biopolyester"
    else:
        focus_family = allowlist[0] if allowlist else "none"
    seed = int(time.time()) # Use a simple time-based seed for variety
    
    candidates_df = generate_formulation_doe(n=n_candidates, seed=seed, focus=focus_family)
    
    # Convert DataFrame to the expected list of {"formulation": {...}} dicts
    formulation_dicts = candidates_df.to_dict(orient='records')
    return [{"formulation": record} for record in formulation_dicts]

def _calculate_similarity_penalty(
    predicted_props: Dict[str, Any],
    target_props: List[Dict[str, Any]],
    weights: Dict[str, float]
) -> float:
    """Calculates a penalty based on deviation from target properties."""
    penalty = 0.0
    target_map = {p["name"]: p for p in target_props if "name" in p}

    for name, target_data in target_map.items():
        pred_val = predicted_props.get(name)
        target_val = target_data.get("value")
        weight = weights.get(name, 0.1) # Default weight if not specified

        if isinstance(pred_val, (int, float)) and isinstance(target_val, (int, float)) and target_val != 0:
            # Normalized squared error
            normalized_error = (pred_val - target_val) / target_val
            penalty += weight * (normalized_error ** 2)

    return math.sqrt(penalty) # Return root mean squared weighted error

def evaluate_and_rank(candidates: List[Dict[str, Any]],
                      enriched: Dict[str, Any],
                      goals: Dict[str, Any],
                      weights: Optional[Dict[str, float]],
                      topk: int) -> Dict[str, Any]:
    """
    Scores candidates based on similarity to spec, goals, and other metrics.
    This version now uses the AI agent for evaluation.
    """
    if not candidates:
        return {"summary": {"candidates_considered": 0, "error": "No candidates to evaluate."}, "topk": []}

    # --- 1. Convert candidates to a DataFrame and predict properties ---
    formulations_df = pd.DataFrame([c.get("formulation", {}) for c in candidates])
    
    try:
        model_path = Path(__file__).parent.parent / "data/processed/pp_elastomer_TSE_hybrid_model_v1.json"
        tse_model = read_json(model_path)
        lib_path = Path(__file__).parent.parent / "data/processed/ingredient_library.json"
        ing_lib = read_json(lib_path)
        catalog = load_ingredient_catalog(ing_lib)
        process_cfg = {"Torque_Nm": 100.0, "N_rps": 5.0, "Q_kgh": 5.0, "Tm_C": 220.0}

        predicted_props_list = [compute_row_properties(row, tse_model, catalog, process_cfg) for _, row in formulations_df.iterrows()]
        predictions_df = pd.concat([formulations_df, pd.DataFrame(predicted_props_list)], axis=1)

    except FileNotFoundError:
        return {"summary": {"error": "Required model or library files not found."}, "topk": []}

    # --- 2. Build target constraints directly from the enriched spec dictionary ---
    # This avoids creating temporary files and is more robust.
    targets_constraints = build_targets_constraints(enriched)

    # --- 3. Evaluate the predictions with the AI agent ---
    # Note: The agent evaluation writes its own detailed artifacts.
    # The `out_dir` for the agent is relative to where the script is run.
    agent_out_dir = Path(f"agent_eval_artifacts_{time.time_ns()}")
    evaluated_df = evaluate_with_agent(predictions_df, process_cfg, targets_constraints, out_dir=str(agent_out_dir), run_identifier="pipeline_run")
    evaluated_df = evaluated_df.sort_values(by="recommended_bo_weight", ascending=False)

    # --- 4. Format the topk results for the final recommendations.json ---
    # This now follows the structured schema from the dev guide.
    top_k_df = evaluated_df.head(topk)
    top_k_results = []
    for i, (idx, row) in enumerate(top_k_df.iterrows()):
        # Formulation from wt% columns
        formulation = {k: v for k, v in row.items() if k.endswith(('_wtpct', '_ppm')) and v > 0}
        
        # Collect predicted properties and scores into nested objects
        predicted_properties = {
            "MFI_g10min": row.get("MFI_g10min"), "sigma_y_MPa": row.get("sigma_y_MPa"),
            "E_GPa": row.get("E_GPa"), "Izod_m20_kJm2": row.get("Izod_m20_kJm2"),
            "HDT_C": row.get("HDT_C"), "rho_gcc": row.get("rho_gcc"),
            "eps_y_pct": row.get("eps_y_pct"), "Gardner_J": row.get("Gardner_J"),
        }
        # Filter out None values from properties
        predicted_properties = {k: v for k, v in predicted_properties.items() if v is not None}

        scores = {
            "recommended_bo_weight": row.get("recommended_bo_weight"),
            "literature_consistency_score": row.get("literature_consistency_score"),
            "realism_penalty": row.get("realism_penalty"),
            "confidence": row.get("confidence"),
        }
        
        top_k_results.append({
            "rank": i + 1,
            "formulation": formulation,
            "predicted_properties": predicted_properties,
            "scores": scores
        })

    return {"summary": {"candidates_considered": len(evaluated_df)}, "topk": top_k_results}


def run_single(spec_path: Path,
            out_dir: Path,
            goals: Dict[str, Any],
            topk: int = 10,
            n_candidates: int = 20,
            assume_json: bool = False,
            weights: Optional[Dict[str, float]] = None) -> None:
    # Setup per-spec logging
    log_path = out_dir / "run.log"
    logger = setup_logging(str(log_path), name=spec_path.stem)

    start_time = time.time()
    meta = {"spec_file": spec_path.name, "spec_sha256": sha256_file(spec_path), "started_at": time.time()}

    try:
        # Step 1: Normalize
        logger.info("Starting normalization...")
        norm = normalize_spec(spec_path, assume_json=assume_json)
        write_json(out_dir / "00_normalized.json", norm)

        # Step 2: Gap-fill
        logger.info("Starting gap-filling...")
        enriched = gapfill(norm)
        write_json(out_dir / "01_gapfilled.json", enriched)

        # Step 3: Prefilter
        logger.info("Starting pre-filtering...")
        pf = prefilter(enriched, goals)
        # --- Test hook to simulate failure ---
        if os.environ.get("SIMULATE_FAILURE") == "prefilter":
            raise RuntimeError("Simulated failure after prefilter step.")
        # --- End test hook ---
        write_json(out_dir / "02_prefilter.json", pf)

        # Step 4: Generate Candidates and Score (using stubs for now)
        logger.info("Generating candidates and scoring...")
        cands = generate_candidates(pf, n_candidates=n_candidates)
        write_json(out_dir / "03_candidates.json", {"count": len(cands), "examples": cands[:3]})
        results = evaluate_and_rank(cands, enriched, goals, weights, topk)
        write_json(out_dir / "recommendations.json", results)

        meta["status"] = "success"
        logger.info("Pipeline completed successfully.")

    except Exception as e:
        meta["status"] = "failed"
        meta["error"] = f"{type(e).__name__}: {e}"
        logger.exception(f"Pipeline failed for {spec_path.name}")

    meta["finished_at"] = time.time()
    meta["elapsed_s"] = round(meta["finished_at"] - start_time, 2)
    write_json(out_dir / "meta.json", meta)
