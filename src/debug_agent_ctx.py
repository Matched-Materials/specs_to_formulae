# scripts/debug_agent_ctx.py
from __future__ import annotations
import json, math
from typing import Any
import importlib

def to_plain_json(obj: Any) -> Any:
    def default(o):
        # numpy scalar → native python
        if hasattr(o, "item"): 
            try: return o.item()
            except Exception: pass
        # pandas Timestamp / NA / etc → str/None
        try:
            import pandas as pd
            if isinstance(o, (pd.Timestamp, )):
                return o.isoformat()
            if o is pd.NaT:
                return None
        except Exception:
            pass
        return str(o)
    return json.loads(json.dumps(obj, default=default))

def coerce_nan_none(d: dict) -> dict:
    out = {}
    for k,v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        else:
            out[k] = v
    return out

def main():
    # 1) Import your agent input model
    # This path points to where the EvalInput class is defined.
    module_path = "evaluator.matsi_property_evaluator.eval_schema"
    class_name  = "EvalInput"

    mod = importlib.import_module(module_path)
    EvalInput = getattr(mod, class_name)

    # 2) Print the schema the agent ACTUALLY expects
    try:
        schema = EvalInput.model_json_schema()  # Pydantic v2
        version = "v2"
    except Exception:
        schema = EvalInput.schema()             # Pydantic v1 fallback
        version = "v1"
    print(f"[Pydantic {version}] EvalInput schema:")
    print(json.dumps(schema, indent=2)[:2000], "...\n")

    # 3) Build a minimal fake row to test the contract
    # The agent schema expects 'process' and 'predictions' dicts.
    predictions = {
        "MFI_g10min": 12.3,
        "sigma_y_MPa": 32.1,
        "E_GPa": 1.5,
    }
    process = {
        "baseA_wtpct": 85.0, 
        "elastomer_wtpct": 15.0,
        "Tm_C": 220.0,
        "N_rps": 5.0,
    }
    targets_constraints = {
        "MFI_g10min": {"value": 10.0},
        "sigma_y_MPa": {"value": 30.0},
        "E_GPa": {"value": 1.2},
    }
    
    ctx = {
        "cycle_id": "debug_run",
        "sample_id": "debug_sample_01",
        "predictions": to_plain_json(coerce_nan_none(predictions)),
        "process": to_plain_json(coerce_nan_none(process)),
        "targets_constraints": targets_constraints,
        "meta": {"source": "debug-script"},
    }

    print("Context keys being validated:", list(ctx.keys()))
    # 4) Validate and print granular errors
    try:
        inst = EvalInput.model_validate(ctx)
        print("\nOK: EvalInput accepted the context.")
    except Exception as e:
        print("\nVALIDATION ERROR:")
        try:
            from pydantic import ValidationError
            if isinstance(e, ValidationError):
                print(json.dumps(e.errors(), indent=2))
            else:
                print(repr(e))
        except Exception:
            print(repr(e))

if __name__ == "__main__":
    main()