from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import sys

# Add project root to path to allow absolute imports
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.bridge_formulations_to_properties import compute_row_properties, load_ingredient_catalog

# Load catalog once at module level for efficiency
try:
    catalog_path = project_root / "data/processed/ingredient_library.json"
    lib = json.loads(catalog_path.read_text())
    CATALOG = load_ingredient_catalog(lib)
except FileNotFoundError:
    print("Warning: Ingredient library not found for baseline model. Predictions will fail.")
    CATALOG = {}

def load_baseline_model(path: str | Path = "data/processed/pp_elastomer_TSE_hybrid_model_v1.json") -> dict:
    """Loads the baseline model JSON, resolving path relative to project root."""
    # Make path absolute if it's relative
    if not Path(path).is_absolute():
        path = project_root / path
    with open(path, "r") as f:
        return json.load(f)

def baseline_predict_row(row: pd.Series, model: dict) -> dict:
    """
    Runs the baseline physics-based model for a given formulation row.
    This is a proxy to the main property prediction logic.
    """
    if not CATALOG:
        return {}
        
    row_dict = row.to_dict()
    preds = compute_row_properties(row_dict, model, CATALOG, row_dict)
    
    return {
        f"{k}_base": v
        for k, v in preds.items()
        if k in ["MFI_g10min", "sigma_y_MPa", "E_GPa", "Izod_m20_kJm2", "HDT_C"]
    }

def attach_baseline(df: pd.DataFrame, model: dict) -> pd.DataFrame:
    """Applies baseline_predict_row to each row of a DataFrame."""
    if df.empty:
        return df
    preds = df.apply(lambda r: baseline_predict_row(r, model), axis=1, result_type="expand")
    return pd.concat([df, preds], axis=1)
