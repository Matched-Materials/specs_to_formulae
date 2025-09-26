from __future__ import annotations
from pathlib import Path
import json
import pandas as pd

def load_processing_levers(path: str | Path = "data/processed/processing_levers.json") -> dict:
    """
    Loads and parses the processing levers JSON into a flat dictionary
    keyed by internal variable names, with 'min' and 'max' values.
    This is designed to match the variables used in the Bayesian Optimizer.
    """
    with open(path, "r") as f:
        raw_config = json.load(f)

    levers = {}
    
    # This mapping is crucial to bridge the human-readable JSON with the code's variable names.
    variable_map = {
        "Screw speed": "N_rps",
        "Melt temp": "Tm_C",
        "Melt temp profile": "Tm_C",
        "Feed rate": "Q_kgh",
        "Specific energy": "Torque_Nm", # This is a proxy; ranges are defined in orchestrator
    }

    for section_data in raw_config.values():
        if "levers" in section_data and isinstance(section_data["levers"], list):
            for lever_spec in section_data["levers"]:
                var_name_human = lever_spec.get("variable")
                internal_name = variable_map.get(var_name_human)
                
                if internal_name and "range" in lever_spec and isinstance(lever_spec["range"], list) and len(lever_spec["range"]) == 2:
                    min_val, max_val = lever_spec["range"]
                    
                    if internal_name == "N_rps":
                        min_val /= 60.0
                        max_val /= 60.0
                    
                    # For Tm_C, we might have two definitions. Let's take the wider range.
                    if internal_name in levers:
                        existing = levers[internal_name]
                        min_val = min(min_val, existing['min'])
                        max_val = max(max_val, existing['max'])

                    levers[internal_name] = {"min": min_val, "max": max_val}
    return levers

def clamp_process_row(row: dict, levers: dict) -> dict:
    """
    Clamp each process field to [min, max] from config.
    """
    out = dict(row)
    for k, spec in levers.items():
        lo, hi = spec["min"], spec["max"]
        if k in out and out[k] is not None:
            out[k] = max(lo, min(hi, out[k]))
    return out

def compute_process_features(df: pd.DataFrame, levers: dict) -> pd.DataFrame:
    """
    Adds normalized features per lever + a simple 'proc_intensity' aggregate.
    """
    out = df.copy()
    zcols = []
    for k, spec in levers.items():
        if k in out.columns:
            lo, hi = spec["min"], spec["max"]
            # min-max normalized feature
            out[f"{k}_norm"] = (out[k] - lo) / max(1e-9, (hi - lo))
            zcols.append(f"{k}_norm")
    if zcols:
        out["proc_intensity"] = out[zcols].mean(axis=1)
    return out