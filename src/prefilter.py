# spec_sheets_to_formulas/src/prefilter.py
from __future__ import annotations
from typing import Dict, Any, List, Set
from pathlib import Path

from .utils.io import read_json

# It's good practice to load this once at module level if it's static.
# --- Refactoring to use the canonical ingredient library ---
# This consolidates our data sources and aligns with the main project architecture.
INGREDIENT_LIBRARY_PATH = Path(__file__).parent.parent / "data/processed/ingredient_library.json"

def _load_material_capabilities_from_library(path: Path) -> Dict[str, Any]:
    """Extracts pre-filtering capabilities from the main ingredient library."""
    if not path.exists():
        return {}
    library = read_json(path)
    capabilities = {}
    # This is a simplified extraction; it can be made more sophisticated.
    # For now, we'll just check for compostability based on recycling stream.
    for category in library.values():
        if isinstance(category, list):
            for ingredient in category:
                family = ingredient.get("family")
                if family and family not in capabilities:
                    # A material is considered compostable if its stream is 'Compostable'
                    # or if it's a biopolymer like PLA that is industrially compostable.
                    # This logic is more robust than a simple string match.
                    stream = ingredient.get("recycling_stream")
                    is_compostable = (stream == "Compostable") or (family == "biopolymer" and stream == "PLA")
                    capabilities[family] = {"compostable": is_compostable}
    return capabilities

MATERIAL_CAPABILITIES = _load_material_capabilities_from_library(INGREDIENT_LIBRARY_PATH)

def prefilter(enriched: Dict[str, Any], goals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given enriched properties + goals, return an allowlist of material families
    and any property bounds implied by the process/sustainability goals.
    """
    if not MATERIAL_CAPABILITIES:
        # Graceful failure if the library is missing or empty
        return {"allowlist": [], "bounds": {}, "notes": ["Error: ingredient_library.json not found or capabilities could not be extracted."]}

    sustain = goals.get("sustainability", {})
    proc = goals.get("process", {})
    notes: List[str] = []
    initial_allowlist = list(MATERIAL_CAPABILITIES.keys())
    allowlist = list(initial_allowlist) # Make a copy to modify

    # --- Determine Allowlist based on Sustainability ---
    compostable_goal = sustain.get("compostable")
    if compostable_goal is True:
        allowlist = [mat for mat in allowlist if MATERIAL_CAPABILITIES.get(mat, {}).get("compostable")]
        excluded = sorted(list(set(initial_allowlist) - set(allowlist)))
        if excluded:
            notes.append(f"Goal 'compostable=True' excluded: {', '.join(excluded)}")
    elif compostable_goal is False:
        allowlist = [mat for mat in allowlist if not MATERIAL_CAPABILITIES.get(mat, {}).get("compostable")]
        excluded = sorted(list(set(initial_allowlist) - set(allowlist)))
        if excluded:
            notes.append(f"Goal 'compostable=False' excluded: {', '.join(excluded)}")

    # --- Further filter Allowlist based on Process (STUBBED) ---
    process_method = proc.get("method")
    if process_method:
        # TODO: Enhance _load_material_capabilities_from_library to extract process_methods
        notes.append(f"Process method filtering for '{process_method}' is not yet fully implemented with ingredient_library.json.")

    # --- Determine Property Bounds (STUBBED) ---
    bounds = {}

    return {"allowlist": sorted(allowlist), "bounds": bounds, "notes": notes}

ALLOWED_COMPOST_FAMILIES = {"polyester/pla", "polyester/pha-phb", "polyester/pbat", "filler/", "additive/"}

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