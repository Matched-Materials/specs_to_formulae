from typing import Dict, Any, List, Optional, Tuple
import csv, json, os, logging
from pathlib import Path
from io import BytesIO
try:
    import PIL.Image
    from pdf2image import convert_from_path
except ImportError:
    PIL = None
import re

# Optional deps (we degrade gracefully)
try:
    import camelot
except Exception:
    camelot = None

try:
    import pdfplumber
    _PDFPLUMBER_IMPORT_ERR = None
except Exception as e:
    pdfplumber = None
    _PDFPLUMBER_IMPORT_ERR = repr(e)

try:
    import pandas as pd
except Exception:
    pd = None

# --- New: Import the agent-based parser ---
try:
    from .run_text_extractor import run_text_extraction as run_parser
    _RUN_PARSER_IMPORT_ERR = None
except Exception as e:  # catch ANY import-time failure, not just ImportError
    run_parser = None
    _RUN_PARSER_IMPORT_ERR = repr(e)

# --- Regex for parsing values, conditions, and methods ---
NUM_RE = re.compile(r"(?<![A-Za-z])(-?\d+(?:[.,]\d+)?)(?:\s*(?:–|-|to)\s*(-?\d+(?:[.,]\d+)?))?")
COND_TEMP_RE = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)?)\s*°?\s*C", re.I)
COND_LOAD_RE = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)?)\s*(MPa|psi|kg)", re.I)
METHOD_RE = re.compile(r"(ASTM\s+D?\d+[A-Z]?(?:/\w+)?|ISO\s+\d+(?:-\d+)?(?:/[A-Z0-9]+)?)")
THICK_RE = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)?)\s*mm", re.I)
RATE_RE  = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)?)\s*(?:°?\s*C/?h|C/?h|mm/min|mm/s|°C/h)", re.I)
FREQ_RE  = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)?)\s*(Hz|kHz|MHz)", re.I)

# unicode/superscripts / NBSP unit tokens
UNIT_TOKEN_RE = re.compile(
    r"(g/10\s*min|cm(?:\u00B3|3)/10\s*min|g/cm(?:\u00B3|3)|kg/m(?:\u00B3|3)|"
    r"kJ/m(?:\u00B2|2)|J/m|MPa|GPa|°C|C|kV/mm|ohm(?:-|\s*)m|ohm/sq|ppm/K|W/m-?K)",
    re.I
)

NBSP_RE = re.compile(u"\u00A0")  # non-breaking space
SUP3 = "\u00B3"                  # superscript 3
SUP2 = "\u00B2"                  # superscript 2

def _clean_text(s: str) -> str:
    # normalize NBSP and common unicode quirks
    return NBSP_RE.sub(" ", s).replace("°", "°").replace(SUP3, "3").replace(SUP2, "2")

# --- Unit families & conversions (manual, robust) ---
UNIT_FAMILY = {
    "MPa": "stress", "psi": "stress", "Pa": "stress",
    "GPa": "modulus",
    "J/m": "impact_len", "kJ/m2": "impact_area",
    "g/cm3": "density", "kg/m3": "density",
    "C": "temperature", "F": "temperature",
    "kV/mm": "dielectric",
    "ohm-m": "resistivity_vol", "ohm/sq": "resistivity_surf",
    "ppm/K": "cte",
    "W/m-K": "thermal_cond",
    "cm3/m2-day-atm": "permeation_otr",
    "g/m2-day": "permeation_wvtr",
    "Pa-s": "viscosity",
    "g/10min": "mfr", "cm3/10min": "mvr"
}

CONVERT = {
    ("psi","MPa"): 0.00689476,
    ("MPa","GPa"): 0.001,
    ("GPa","MPa"): 1000.0,
    ("kg/m3","g/cm3"): 0.001,
    ("ohm-cm","ohm-m"): 0.01,
}

def _same_family(u1: Optional[str], u2: Optional[str]) -> bool:
    return u1 in UNIT_FAMILY and u2 in UNIT_FAMILY and UNIT_FAMILY[u1] == UNIT_FAMILY[u2]

def _normalize_unit_symbol(u: Optional[str]) -> Optional[str]:
    if not u: return u
    u = _clean_text(u)
    u = u.replace("°C", "C").replace(" ", "")
    u = u.replace("cm3/10min", "cm3/10min").replace("g/10min", "g/10min")
    u = u.replace("g/cm3", "g/cm3").replace("kg/m3", "kg/m3")
    u = u.replace("kJ/m2", "kJ/m2").replace("J/m", "J/m")
    return u

def _convert_value(val: float, u_from: Optional[str], u_to: Optional[str]) -> Optional[float]:
    u_from = _normalize_unit_symbol(u_from)
    u_to = _normalize_unit_symbol(u_to)
    if not u_from or not u_to or u_from == u_to:
        return val if u_from == u_to else None
    if not _same_family(u_from, u_to):
        return None
    if (u_from, u_to) in CONVERT:
        factor = CONVERT[(u_from, u_to)]
        return val * factor
    # special case °F->°C
    if u_from == "F" and u_to == "C":
        return (val - 32.0) * 5.0 / 9.0
    # DO NOT convert MFR<->MVR; Izod<->Charpy; different families
    return None

# --- Ontology loading & alias index ---
def _load_ontology(ontology_path: Optional[Path] = None) -> Dict[str, Any]:
    # precedence: explicit arg -> ENV -> legacy relative -> fallback
    if ontology_path is None:
        env_path = os.getenv("PROPERTY_ONTOLOGY_PATH")
        if env_path:
            ontology_path = Path(env_path)
        else:
            ontology_path = Path(__file__).parent.parent / "configs/property_ontology.json"
    if not ontology_path.exists():
        return {"alias_index": {}, "units": {}}

    raw = json.loads(ontology_path.read_text(encoding="utf-8"))
    alias_index: Dict[str, str] = {}
    unit_map: Dict[str, str] = {}
    for canonical_name, details in (raw.get("properties") or {}).items():
        unit_map[canonical_name] = details.get("unit")
        # direct canonical
        alias_index[canonical_name.lower()] = canonical_name
        # aliases
        for alias in (details.get("aliases") or []):
            alias_index[alias.lower().strip()] = canonical_name
    return {"alias_index": alias_index, "units": unit_map}

ONTOLOGY = _load_ontology()

# --- New helpers for 2-column stitching logic ---
SECTION_TITLES = {
    "rheological properties","mechanical properties","impact","thermal",
    "thermal properties","other properties","processing","injection molding",
    "other text information","processing extrusion","moulding"
}

PROPERTY_KEYWORDS = {
    "mfr": r"(melt\s+(mass|volume)[-\s]?flow|mfr|mvr)",
    "density": r"\bdensity\b",
    "tensile": r"\btensile\b",
    "flexural": r"\bflexural\b",
    "izod": r"\bizod\b",
    "charpy": r"\bcharpy\b",
    "gardner": r"\bgardner\b",
    "hdt": r"(hdt|dtul|deflection temperature|heat deflection)",
    "vicat": r"\bvicat\b",
}

# --- New helpers for 2-column stitching logic ---
SECTION_TITLES = {
    "general","features","uses","appearance","forms","processing method","revision date",
    "physical","mechanical","impact","thermal","other properties","product texts",
    "rheological properties","mechanical properties","thermal properties"
}

def _looks_like_section(title: str) -> bool:
    t = (title or "").strip().lower().rstrip(":")
    return t in SECTION_TITLES

def _looks_like_placeholder(title: str) -> bool:
    # titles that should NOT start a new property
    t = (title or "").strip().lower()
    return not t or re.search(r"^(typical value|method|test based on|product datasheet)$", t)

def _looks_like_condition(label: str) -> bool:
    """Right-cell strings that should attach to the previous property."""
    s = (label or "").strip().lower()
    # temps like "0°F (-18°C)" or "73°F (23°C)" or "Temperature"
    if re.search(r"(-?\d+(\.\d+)?)\s*°\s*(c|f)|\btemperature\b|\btemp\b", s):
        return True
    # load / speed / geometry style rows
    if re.search(r"\bload\b|\bspeed\b|\bgeometry\b|\bthickness\b|\bmm\b|\bin/min\b|\bmm/min\b|\bmm/s\b", s):
        return True
    return s in {"temperature", "load"}

def _unit_hint_by_property(name: str) -> Optional[str]:
    n = (name or "").lower()
    if re.search(PROPERTY_KEYWORDS["density"], n): return "g/cm3"
    if re.search(PROPERTY_KEYWORDS["mfr"], n):     return "g/10min"  # default; MVR often cm3/10min
    if re.search(PROPERTY_KEYWORDS["izod"], n):    return "J/m"
    if re.search(PROPERTY_KEYWORDS["charpy"], n):  return "kJ/m2"
    return None

def _should_treat_as_load(text: str, prop_name: str, section: str) -> bool:
    t = (text or "") + " " + (prop_name or "") + " " + (section or "")
    return bool(re.search(r"(load|dtul|hdt|deflection temperature|vicat|iso\s*75|astm\s*d648|iso\s*306)", t, re.I))

def _same_topic(prop_name: str, cond_text: str, section: str) -> bool:
    pn = (prop_name or "").lower()
    ct = (cond_text or "").lower()
    sec = (section or "").lower()
    if re.search(PROPERTY_KEYWORDS["hdt"], ct) or "astm d648" in ct or "iso 75" in ct:
        return re.search(PROPERTY_KEYWORDS["hdt"], pn) or "thermal" in sec
    if "vicat" in ct: return re.search(PROPERTY_KEYWORDS["vicat"], pn) or "thermal" in sec
    if "gardner" in pn or "gardner" in ct: return re.search(PROPERTY_KEYWORDS["gardner"], pn) or "impact" in sec
    if re.search(PROPERTY_KEYWORDS["izod"], pn) or re.search(PROPERTY_KEYWORDS["izod"], ct): return re.search(PROPERTY_KEYWORDS["izod"], pn) or "impact" in sec
    if re.search(PROPERTY_KEYWORDS["charpy"], pn) or re.search(PROPERTY_KEYWORDS["charpy"], ct): return re.search(PROPERTY_KEYWORDS["charpy"], pn) or "impact" in sec
    return True

def _match_alias(name: str) -> Optional[Dict[str, str]]:
    """Exact (lowercased) match, then substring fallback."""
    if not name: return None
    idx = ONTOLOGY.get("alias_index", {})
    n = re.sub(r"\s+", " ", str(name).lower()).strip()
    if n in idx:
        return {"canonical": idx[n], "unit": ONTOLOGY["units"].get(idx[n])}
    # soft contains: prefer longest alias
    best = None
    best_len = 0
    for alias, canon in idx.items():
        if alias and alias in n and len(alias) > best_len:
            best = {"canonical": canon, "unit": ONTOLOGY["units"].get(canon)}
            best_len = len(alias)
    return best

# --- Parsing helpers ---
def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",","."))  # handle decimal commas
    except Exception:
        return None

def _parse_numeric(cell: str) -> Optional[Dict[str, Any]]:
    if not cell: return None
    m = NUM_RE.search(cell.replace("\u2212", "-").replace("\u2013","-"))
    if not m: return None
    a = _to_float(m.group(1)) if m.group(1) else None
    b = _to_float(m.group(2)) if m.group(2) else None
    if a is not None and b is not None:
        lo, hi = (a, b) if a <= b else (b, a)
        return {"value_min": lo, "value_max": hi}
    if a is not None:
        return {"value": a}
    return None

def _parse_conditions(text: str, prop_name: str = "", section: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not text:
        return out
    S = _clean_text(text)

    # Temperature
    t = COND_TEMP_RE.search(S)
    if t:
        out["temp_C"] = _to_float(t.group(1))

    # Only treat MPa/psi as LOAD if explicit context is present
    if _should_treat_as_load(S, prop_name, section):
        l_load = COND_LOAD_RE.search(S)
        if l_load:
            val = _to_float(l_load.group(1))
            unit = l_load.group(2).lower()
            if val is not None:
                if unit == "mpa": out["load_MPa"] = val
                elif unit == "psi": out["load_MPa"] = val * 0.00689476
                elif unit == "kg": out["load_kg"] = val

    # Thickness / speed / freq (same as before)
    th = THICK_RE.search(S); r = RATE_RE.search(S); f = FREQ_RE.search(S)
    if th: out["specimen_thickness_mm"] = _to_float(th.group(1))
    if r:
        rv = _to_float(r.group(1))
        rt = r.group(2) if len(r.groups()) >= 2 else r.group(0)
        if rv is not None:
            if "mm/min" in rt: out["speed_mm_min"] = rv
            elif "mm/s" in rt: out["speed_mm_s"] = rv
            else: out["heating_rate_C_per_h"] = rv
    if f:
        fv = _to_float(f.group(1)); fu = f.group(2).lower()
        if fv is not None:
            out["frequency_Hz"] = fv * (1000.0 if fu == "khz" else (1_000_000.0 if fu == "mhz" else 1.0))

    m = METHOD_RE.search(S)
    if m: out["method"] = m.group(1).strip()
    return out

def _parse_value_block(text: str, prop_name_for_hint: str = "") -> tuple[dict, dict]:
    """
    Parse the left-cell stacked text into (value_dict, conditions_dict),
    ignoring method lines when extracting numbers/units.
    """
    value = {}
    conds: dict = {}
    if not text:
        return value, conds

    parts = [p.strip() for p in re.split(r"[\r\n]+", str(text)) if p and str(p).strip()]

    # Extract method (keep, but don't use those lines when parsing value)
    method_lines = set()
    for p in parts:
        m = METHOD_RE.search(p)
        if m:
            conds["method"] = m.group(1).strip()
            method_lines.add(p)

    # Special values
    joined_all = " ".join(parts)
    if re.search(r"\bno\s*break\b", joined_all, flags=re.I):
        value["special_value"] = "no_break"
        return value, conds

    # Build candidates WITHOUT method lines
    payload_parts = [p for p in parts if p not in method_lines]
    joined = " ".join(payload_parts)
    candidates = [joined] + payload_parts + ([" ".join(payload_parts[:2])] if len(payload_parts) >= 2 else [])

    for c in candidates:
        # Skip obvious placeholders
        if re.search(r"^(typical value|method|test based on)\b", c, re.I):
            continue
        num = _parse_numeric(c)
        if num:
            value.update(num)
            m_unit = UNIT_TOKEN_RE.search(c)
            if m_unit:
                value["unit"] = _normalize_unit_symbol(m_unit.group(1))
            else:
                # fall back to property-based hint
                hint = _unit_hint_by_property(prop_name_for_hint)
                if hint:
                    value.setdefault("unit", hint)
            break
    return value, conds

UNIT_TOKENS = {
    "C","MPa","GPa","J/m","kJ/m2","g/10min","cm3/10min","g/cm3","kg/m3",
    "kV/mm","ohm-m","ohm/sq","ppm/K","W/m-K","cm3/m2-day-atm","g/m2-day","Pa-s"
}

def _normalize_pdf_row(row: Dict[str, str], page_idx: int, table_idx: int, row_idx: int) -> Tuple[Dict[str, Any], bool]:
    # Try common header keys with fallbacks
    prop_raw = str(row.get("property") or row.get("Property") or row.get("parameter") or row.get("Parameter") or next(iter(row.values()), ""))
    unit_raw = str(row.get("unit") or row.get("Unit") or "")
    value_raw = str(row.get("value") or row.get("Value") or "")
    method_raw = str(row.get("method") or row.get("standard") or row.get("test") or "")
    conds_raw = str(row.get("conditions") or row.get("notes") or "")

    # Stronger swap heuristic
    val_num = _parse_numeric(value_raw)
    unit_num = _parse_numeric(unit_raw)
    if (not val_num and unit_num) or (value_raw.strip() in UNIT_TOKENS and unit_num):
        value_raw, unit_raw = unit_raw, value_raw
        val_num = unit_num

    # Conditions from method/notes only (avoid parsing property label text)
    conditions = _parse_conditions(" ".join([method_raw, conds_raw]), prop_raw)
    alias = _match_alias(prop_raw)

    # Special values
    numeric = _parse_numeric(value_raw)
    if not numeric and value_raw and "no break" in value_raw.lower():
        numeric = {"special_value": "no_break"}

    # Build record
    rec: Dict[str, Any] = {
        "name": (alias or {}).get("canonical") or prop_raw.strip(),
        **(numeric if numeric else ({"raw_value": value_raw.strip()} if value_raw.strip() else {})),
        "unit": _normalize_unit_symbol(((alias or {}).get("unit")) or (unit_raw.strip() or None)),
        "conditions": conditions,
        "method": conditions.get("method") or (method_raw.strip() or None),
        "provenance": {"page": page_idx + 1, "table_idx": table_idx, "row_idx": row_idx, "source": "spec_sheet"},
        "confidence": 1.0 if numeric else 0.6,
    }

    # Unit normalization if we have a scalar value and a target unit
    target_unit = ((alias or {}).get("unit")) or ONTOLOGY.get("units", {}).get(rec["name"])
    if target_unit and numeric and "value" in numeric and rec.get("unit"):
        new_val = _convert_value(rec["value"], rec["unit"], target_unit)
        if new_val is not None:
            rec["value"] = new_val
            rec["unit"] = target_unit
            rec["confidence"] = min(1.0, rec["confidence"] + 0.1)

    return rec, bool(alias)

def _stitch_two_column_rows(rows: list[dict]) -> list[dict]:
    """
    For tables shaped like:
        left: value/unit/method/notes
        right: property/section/condition labels
    Emit canonical property records by stitching sequences.
    """
    out = []
    current: Optional[Dict[str, Any]] = None
    current_section = ""

    for r_idx, r in enumerate(rows):
        keys = list(r.keys())
        if len(keys) < 1: continue
        # Heuristic: the *right* column header is the non-empty, longer header
        right_key = max(keys, key=lambda k: len(str(k).strip()))
        left_key = min(keys, key=lambda k: len(str(k).strip()))
        right_txt = _clean_text(str(r.get(right_key) or ""))
        left_txt  = _clean_text(str(r.get(left_key)  or ""))

        rt_norm = right_txt.strip().lower().rstrip(":")
        if rt_norm in SECTION_TITLES:
            current_section = rt_norm
            continue

        is_propertyish = bool(_match_alias(right_txt)) or re.search(
            r"(density|melt\s+(mass|volume)-?flow|mfr|mvr|tensile|elongation|strain|flexural|izod|charpy|gardner|heat\s+deflection|dtul|vicat|hardness|modulus|impact)",
            right_txt, re.I)

        if is_propertyish:
            if current and current.get("name"):
                out.append(current)
            current = {"name": right_txt, "conditions": {}, "provenance": {"row_idx": r_idx}}
            vblock, vconds = _parse_value_block(left_txt, prop_name_for_hint=right_txt)
            for k in ("value","value_min","value_max","special_value"):
                if k in vblock: current[k] = vblock[k]
            if vblock.get("unit"): current["unit"] = vblock["unit"]
            conds = {}
            conds.update(_parse_conditions(right_txt, right_txt, current_section))
            conds.update(_parse_conditions(left_txt, right_txt, current_section))
            current["conditions"] = conds
            current["method"] = conds.get("method")
            if current.get("unit") in {"C","c"} and not re.search(r"(temp|temperature|hdt|dtul|vicat)", right_txt, re.I):
                current["unit"] = _unit_hint_by_property(right_txt)
            continue

        if current:
            if _same_topic(current["name"], right_txt + " " + left_txt, current_section):
                vblock, vconds = _parse_value_block(left_txt, prop_name_for_hint=current["name"])
                if vblock and not any(k in current for k in ("value","value_min","value_max","special_value")):
                    for k in ("value","value_min","value_max","special_value"):
                        if k in vblock: current[k] = vblock[k]
                    if vblock.get("unit") and not current.get("unit"):
                        current["unit"] = vblock["unit"]

                conds = _parse_conditions(right_txt + " " + left_txt, current["name"], current_section)
                if conds:
                    curc = current.setdefault("conditions", {})
                    curc.update(conds)
                    current["method"] = curc.get("method", current.get("method"))
                _maybe_parse_processing_line(current, right_txt, left_txt)
                continue

    # Flush tail
    if current is not None and current.get("name"):
        out.append(current)
    return out

PROC_TEMP_RE = re.compile(r"(melt|mould|mold|pre[-\s]?dry(?:ing)?|extrusion)\s+temperature\s+(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*C", re.I)
ONE_TEMP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*C")
HOURS_RE    = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*h", re.I)
MOIST_RE    = re.compile(r"moisture.*?<\s*0[.,](\d+)\s*%", re.I)

def _maybe_parse_processing_line(current: Dict[str,Any], right_txt: str, left_txt: str):
    label = (right_txt or "").lower()
    text  = (left_txt or "")
    if "processing injection" in label or "moulding" in label or "processing extrusion" in label or "pre-drying" in label:
        m = PROC_TEMP_RE.search(text)
        if m:
            lo, hi = float(m.group(2)), float(m.group(3))
            current["value_min"], current["value_max"] = lo, hi
            current["unit"] = "C"
            return
        t = ONE_TEMP_RE.search(text)
        if t and "pre-dry" in label:
            current["value"] = float(t.group(1)); current["unit"] = "C"
        h = HOURS_RE.search(text)
        if h:
            curc = current.setdefault("conditions", {})
            curc["time_h_min"] = float(h.group(1)); curc["time_h_max"] = float(h.group(2))
        q = MOIST_RE.search(text)
        if q:
            curc = current.setdefault("conditions", {})
            curc["moisture_max_pct"] = float(f"0.{q.group(1)}")

def _extract_pdf_tables(path: Path) -> List[Tuple[int, int, List[Dict[str, str]]]]:
    tables = []
    # Camelot first
    if camelot is not None:
        for flavor in ("lattice", "stream"):
            try:
                tabs = camelot.read_pdf(str(path), pages="all", flavor=flavor)
                for t_idx, t in enumerate(tabs):
                    df = t.df
                    if df is not None and len(df) > 1:
                        headers = list(df.iloc[0])
                        rows = [dict(zip(headers, list(r))) for _, r in df.iloc[1:].iterrows()]
                        tables.append((t.page - 1, t_idx, rows))
                if tables:
                    return tables
            except Exception:
                pass
    # pdfplumber fallback
    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                for p_idx, page in enumerate(pdf.pages):
                    try:
                        extracted = page.extract_table()
                        if extracted and len(extracted) > 1:
                            headers = extracted[0]
                            rows = [dict(zip(headers, r)) for r in extracted[1:] if r and len(r)==len(headers)]
                            tables.append((p_idx, 0, rows))
                    except Exception:
                        continue
        except Exception:
            pass
    return tables

def _normalize_record(k: str, v: str, unmapped: List[str]) -> Dict[str, Any]:
    alias_meta = _match_alias(k)
    canonical_name = alias_meta.get("canonical") if alias_meta else None
    if not canonical_name:
        unmapped.append(k)
        canonical_name = k.strip()

    # parse numeric or range
    num = _parse_numeric(str(v))
    unit = (alias_meta.get("unit") if alias_meta else None) or ONTOLOGY.get("units", {}).get(canonical_name)

    rec: Dict[str, Any] = {
        "name": canonical_name,
        **(num if num else {"raw_value": str(v)}),
        "unit": unit,
        "method": None,
        "conditions": {},
        "provenance": "spec_sheet",
        "confidence": 0.9 if num else 0.6,
    }
    return rec

def normalize_spec(spec_path: Path, assume_json: bool = False, ontology_path: Optional[Path] = None) -> Dict[str, Any]:
    # Reload ontology if a path is provided
    global ONTOLOGY
    if ontology_path:
        ONTOLOGY = _load_ontology(ontology_path)

    """
    Reads a spec sheet from a path (CSV or JSON) and converts it into the
    canonical format, including diagnostics.
    """
    p = Path(spec_path)
    properties: List[Dict[str, Any]] = []
    extras: List[Dict[str, Any]] = []
    verbatim_tables: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {}
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
    
    elif p.suffix.lower() == ".pdf":
        missing = []
        try:
            from pdf2image import convert_from_path as _cfp
        except Exception as e:
            _cfp = None
            missing.append(f"pdf2image import failed: {e!r}")

        if run_parser is None or pdfplumber is None:
            diagnostics["error"] = "agent_parser_dependency_missing"
            if _RUN_PARSER_IMPORT_ERR:
                missing.append(f"run_parser failed: {_RUN_PARSER_IMPORT_ERR}")
            if _PDFPLUMBER_IMPORT_ERR:
                missing.append(f"pdfplumber failed: {_PDFPLUMBER_IMPORT_ERR}")
            diagnostics["warning"] = "; ".join(missing) or "Unknown import failure"
        else:
            try:
                print(f"  -> Using agent-based parser for: {p.name}")
                
                # Call the text-based extractor
                agent_result = run_parser(str(p))

                print("-" * 80)
                print(f"START AGENT RESPONSE for {p.name}")
                print(agent_result)
                print(f"END AGENT RESPONSE for {p.name}")
                print("-" * 80)

            except Exception as e:
                diagnostics["error"] = f"pdf_agent_pipeline_error: {e}"

    else:
        diagnostics["warning"] = f"unsupported_file_type: {p.suffix}"

    if not properties and not extras and "error" not in diagnostics:
        diagnostics.setdefault("warning", "no_properties_extracted")

    return {
        "material": {"family": None, "grade": p.stem, "vendor": None},
        "properties": properties,
        "extras": {"properties": extras},
        "verbatim": {"tables": verbatim_tables},
        "diagnostics": {"unmapped_fields": unmapped, **diagnostics, "file": p.name},
    }
