# spec_sheets_to_formulas/src/gapfill/estimators.py
from __future__ import annotations
from typing import Dict, Any, Optional

def estimate_property(name: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Stub estimators. Add Fox equation, rule-of-mixtures, etc.
    Return a dict like: {"name": name, "value": ..., "unit": "...", "provenance": "estimator", "confidence": 0.4}
    """
    # This is a very simple placeholder. A real implementation would use
    # context from other properties to make a more informed guess.
    if name == "density_g_cc":
        return {"name": name, "value": 1.24, "unit": "g/cm^3", "provenance": "estimator_default", "confidence": 0.4}
    return None