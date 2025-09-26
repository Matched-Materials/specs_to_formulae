# spec_sheets_to_formulas/src/gapfill/retriever.py
from __future__ import annotations
from typing import Dict, Any, List
from pathlib import Path
import json

def _load_catalog() -> Dict[str, Any]:
    """Loads the material catalog."""
    # Point to the canonical ingredient library instead of a separate catalog
    catalog_path = Path(__file__).parent.parent.parent / "data/processed/ingredient_library.json"
    if not catalog_path.exists():
        return {}
    return json.loads(catalog_path.read_text(encoding="utf-8"))

CATALOG = _load_catalog()

def _extract_properties_from_entry(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Helper to extract known properties from a library entry."""
    props = []
    # This can be expanded to find more properties from the library entries
    if "density_gcc" in entry:
        props.append({
            "name": "density_g_cc",
            "value": entry["density_gcc"],
            "unit": "g/cm^3",
            "provenance": "catalog_family_default",
            "confidence": 0.7
        })
    return props

def retrieve_candidates(material: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Looks up properties for a material family from a local catalog.
    Return a list of property dicts with name/value/unit/provenance.
    """
    # The family name might be 'Polypropylene' or 'PP'. We need to handle both.
    target_family = material.get("family")
    if not target_family or not CATALOG:
        return []

    # Search through all ingredients to find a representative for the family.
    # A good heuristic is to find a "general-purpose" or virgin grade.
    for category in CATALOG.values():
        if isinstance(category, list):
            for entry in category:
                entry_family = entry.get("family")
                entry_name = entry.get("name", "").lower()
                
                # Check if the entry's family matches the target
                if entry_family and target_family.lower() in entry_family.lower():
                    # Prefer a "general-purpose" or "virgin" entry as the default
                    if "general-purpose" in entry_name or "virgin" in entry_name:
                        return _extract_properties_from_entry(entry)

    return []
