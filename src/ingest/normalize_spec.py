from typing import Dict, Any, List
import csv, json
from pathlib import Path

def _load_ontology() -> Dict[str, Any]:
    """Loads and flattens the property ontology for quick lookups."""
    ontology_path = Path(__file__).parent.parent.parent / "configs/property_ontology.json"
    if not ontology_path.exists():
        return {"aliases": {}, "units": {}}

    raw_ontology = json.loads(ontology_path.read_text(encoding="utf-8"))
    alias_map: Dict[str, str] = {}
    unit_map: Dict[str, str] = {}
    for canonical_name, details in raw_ontology.get("properties", {}).items():
        unit_map[canonical_name] = details.get("unit")
        for alias in details.get("aliases", []):
            alias_map[alias.lower().strip()] = canonical_name
    return {"aliases": alias_map, "units": unit_map}

ONTOLOGY = _load_ontology()

def _normalize_record(k: str, v: str, unmapped: List[str]) -> Dict[str, Any]:
    """Converts a single key-value pair into a canonical property dictionary."""
    k_norm = k.lower().strip()
    canonical_name = ONTOLOGY["aliases"].get(k_norm)

    if not canonical_name:
        unmapped.append(k)
        canonical_name = k.strip() # Use original name if no mapping found

    try:
        val = float(v)
        conf = 0.9
    except Exception:
        val, conf = v, 0.6

    unit = ONTOLOGY["units"].get(canonical_name)

    return {
        "name": canonical_name,
        "value": val,
        "unit": unit,
        "method": None,
        "conditions": {},
        "provenance": "spec_sheet",
        "confidence": conf,
    }

def normalize_spec(spec_path: Path, assume_json: bool = False) -> Dict[str, Any]:
    """
    Reads a spec sheet from a path (CSV or JSON) and converts it into the
    canonical format, including diagnostics.
    """
    p = Path(spec_path)
    properties: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {"unmapped_fields": []}
    unmapped: List[str] = []

    if p.suffix.lower() == ".json" or assume_json:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            items = raw.items() if isinstance(raw, dict) else []
            for k, v in items:
                properties.append(_normalize_record(str(k), str(v), unmapped))
        except Exception as e:
            diagnostics["error"] = f"json_parse_error: {e}"

    elif p.suffix.lower() == ".csv":
        try:
            with open(p, newline="", encoding="utf-8-sig") as f: # Use utf-8-sig for BOM
                header = f.readline().strip()
                # Sniff to see if it's a simple 2-column key-value file
                is_kv_style = len(header.split(',')) == 2
                f.seek(0)
                
                if is_kv_style: # Handles "property,value" format
                    reader = csv.reader(f)
                    # Skip header if it looks like one (e.g., "property", "value")
                    first_row = next(reader)
                    if 'property' not in first_row[0].lower() and 'name' not in first_row[0].lower():
                        f.seek(0) # It wasn't a header, so rewind to process all rows
                    for row in reader:
                        if len(row) == 2 and row[0].strip() != "":
                            properties.append(_normalize_record(row[0], row[1], unmapped))
                else: # Handles wide format with header
                    reader = csv.DictReader(f)
                    for row_dict in reader:
                        for k, v in (row_dict or {}).items():
                            if v is not None and str(v).strip() != "":
                                properties.append(_normalize_record(k, v, unmapped))
        except Exception as e:
            diagnostics["error"] = f"csv_parse_error: {e}"
    else:
        diagnostics["warning"] = "pdf_parser_not_enabled"

    return {
        "material": {"family": None, "grade": p.stem, "vendor": None},
        "properties": properties,
        "diagnostics": {"unmapped_fields": unmapped, **diagnostics},
    }
