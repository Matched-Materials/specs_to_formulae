# src/prefilter.py
from __future__ import annotations
from typing import Dict, Any, List, Set

# Define which chemical families are allowed for compostable goals.
# This is a critical guardrail to prevent mixing incompatible polymer types.
ALLOWED_COMPOST_FAMILIES = {"polyester/pla", "polyester/pha-phb", "polyester/pbat", "filler/", "additive/"}

def prefilter(enriched: Dict[str, Any], goals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyzes the spec and goals to produce an allowlist of material families.
    This is a placeholder for a more sophisticated pre-filtering logic.
    """
    # For now, if the goal is compostable, we only allow biopolymers.
    if goals.get("sustainability", {}).get("compostable"):
        allowlist = ["biopolymer"]
    else:
        # Default to allowing the family of the base material from the spec sheet.
        base_family = enriched.get("material", {}).get("family", "Polypropylene")
        allowlist = [base_family]

    return {
        "allowlist": allowlist,
        "blocklist": [],
        "notes": "Prefilter based on sustainability goals."
    }

def filter_pools_by_goals(
    goals: Dict[str, Any],
    lib: Dict[str, Any],
    target_stream: str | None = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns filtered ingredient pools based on the end-of-life stream and chemistry.
    This function implements the logic from the dev_guide.md.
    """
    compostable = goals.get("sustainability", {}).get("compostable", False)
    if compostable:
        def is_allowed(item: Dict[str, Any]) -> bool:
            # For compostable goals, check if the item's chemical family is in the allowlist.
            chem_family = (item.get("chem_family") or "none").lower()
            return any(chem_family.startswith(f) for f in ALLOWED_COMPOST_FAMILIES)
    else:
        # For recyclable goals, lock the stream to the base resin's stream (e.g., 'PP').
        target_stream = (target_stream or goals.get("recycling_stream", "pp")).lower()
        def is_allowed(item: Dict[str, Any]) -> bool:
            stream = (item.get("recycling_stream") or "none").lower()
            return target_stream in stream or stream == "any"

    filtered_pools = {k: [it for it in items if isinstance(it, dict) and is_allowed(it)] for k, items in lib.items() if isinstance(items, list)}
    return filtered_pools