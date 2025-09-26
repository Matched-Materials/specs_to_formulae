# spec_sheets_to_formulas/src/gapfill/merger.py
from __future__ import annotations
from typing import Dict, Any, List, Set

from .retriever import retrieve_candidates
from .estimators import estimate_property

def gapfill(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merges properties from the spec sheet with retrieved and estimated values.
    Priority: spec_sheet > catalog_retrieved > estimator.
    This function follows the logic from dev_guide.md (lines 76-80).
    """
    # Start with a copy of the normalized data
    enriched = dict(normalized)
    props: List[Dict[str, Any]] = list(enriched.get("properties", []))
    existing_prop_names: Set[str] = {p.get("name") for p in props if "name" in p}

    # --- 1. Attempt to retrieve from catalog ---
    retrieved_props = retrieve_candidates(enriched.get("material", {}))
    for prop in retrieved_props:
        if prop.get("name") not in existing_prop_names:
            props.append(prop)
            existing_prop_names.add(prop["name"])

    # --- 2. Attempt to estimate remaining gaps (e.g., density) ---
    if "density_g_cc" not in existing_prop_names:
        est = estimate_property("density_g_cc", {"properties": props})
        if est:
            props.append(est)

    enriched["properties"] = props
    return enriched