# src/ingest/run_parser_direct.py
from __future__ import annotations
import asyncio
import json
import logging
import os
import threading, sys
from io import BytesIO
try:
    import PIL.Image
except ImportError:
    PIL = None
from pathlib import Path
import re, unicodedata
from typing import Any, Dict, Optional, Tuple, Union

import google.generativeai as genai
from google.generativeai.types import Content, Part, Blob, GenerationConfig

logger = logging.getLogger(__name__)

# --- JSON Salvage and Repair Logic (reused from previous runner) ---

def _iter_balanced_objects(s: str, start_idx: int) -> list[str]:
    objs = []
    i = start_idx
    n = len(s)
    in_str = False
    esc = False
    depth = 0
    obj_start = None
    while i < n:
        ch = s[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == "{":
                depth += 1
                if depth == 1:
                    obj_start = i
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and obj_start is not None:
                        objs.append(s[obj_start:i+1])
                        obj_start = None
            elif ch == "]" and depth == 0:
                break
        i += 1
    return objs

def _salvage_properties_from_truncated_json(s: str) -> dict | None:
    m = re.search(r'"properties"\s*:\s*\[', s)
    if not m:
        return None
    i = m.end()
    objs = []
    for obj_txt in _iter_balanced_objects(s, i):
        objs.append(obj_txt)

    if not objs:
        return None
    try:
        return {"properties": json.loads("[" + ",".join(objs) + "]")}
    except Exception:
        return None

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.I | re.M)

def _strip_code_fences(s: str) -> str:
    return _CODE_FENCE_RE.sub("", s.strip())

def _slice_first_balanced_json(s: str) -> Optional[str]:
    if not s: return None
    start = s.find("{")
    if start < 0: return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
    return None

def safe_parse_model_json(text_or_obj: Any) -> dict:
    if isinstance(text_or_obj, dict):
        return text_or_obj
    if isinstance(text_or_obj, list):
        return {"properties": text_or_obj}
    if not isinstance(text_or_obj, str) or not text_or_obj.strip():
        return {"properties": []}

    s = _strip_code_fences(text_or_obj)
    s_try = _slice_first_balanced_json(s) or s
    try:
        return json.loads(s_try)
    except Exception as e:
        logging.debug("Primary JSON parse failed: %s. Attempting repair.", e)
        s2 = s_try.replace("\r", "").replace("\t", " ")
        s2 = re.sub(r",\s*([}\]])", r"\1", s2)
        s2 = s2.replace("\u2018", "'").replace("\u2019", "'")
        s2 = s2.replace("\u201c", '"').replace("\u201d", '"')
        s2 = re.sub(r"(?<![A-Za-z0-9_])None(?![A-Za-z0-9_])", "null", s2)
        s2 = re.sub(r"(?<![A-Za-z0-9_])True(?![A-Za-z0-9_])", "true", s2)
        s2 = re.sub(r"(?<![A-Za-z0-9_])False(?![A-Za-z0-9_])", "false", s2)
        try:
            return json.loads(s2)
        except Exception as e2:
            logging.debug("Repair failed: %s. Trying key/quote normalization.", e2)
            s3 = re.sub(r"(?<!\\)\'", '"', s2)
            s3 = re.sub(r'(?P<prefix>[{,\s])(?P<key>[A-Za-z_][A-Za-z0-9_]*)(\s*):', r'\g<prefix>"\g<key>":', s3)
            s3 = re.sub(r",\s*([}\]])", r"\1", s3)
            try:
                return json.loads(s3)
            except Exception as e3:
                logging.debug("Key/quote normalization failed: %s. Attempting salvage.", e3)
                salvaged = _salvage_properties_from_truncated_json(s)
                if salvaged:
                    return salvaged
                
                logging.error("All JSON parsing and salvage attempts failed.")
                # Save the failed payload for debugging
                try:
                    dbg_dir = Path("results/ingestion_tests/__debug_payloads")
                    dbg_dir.mkdir(parents=True, exist_ok=True)
                    ts = asyncio.get_event_loop().time()
                    (dbg_dir / f"failed_payload_{ts:.0f}.txt").write_text(str(text_or_obj), encoding="utf-8")
                except Exception as dump_err:
                    logging.error(f"Failed to write debug payload: {dump_err}")
                return {"properties": []}

def _load_prompt_text() -> str:
    # Keep the filename in one place
    here = Path(__file__).parent
    for fname in ("root.prompt.md", "root.prompt"):  # prefer .md if present
        path = here / "parser_agent" / fname
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("Parser prompt not found (root.prompt.md or root.prompt).")

def _extract_json_from_response(resp) -> str | None:
    # Prefer inline JSON parts if present; else fallback to concatenated text
    try:
        for cand in getattr(resp, "candidates", []) or []:
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            for p in parts:
                # New SDK uses dict-like parts
                if isinstance(p, dict):
                    if p.get("inline_data", {}).get("mime_type") == "application/json":
                        data = p["inline_data"]["data"]
                        return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
                    if "text" in p and p["text"]:
                        # Keep first non-empty text part; JSON salvage will handle it
                        return p["text"]
                # Older objects expose attributes
                mime = getattr(getattr(p, "inline_data", None), "mime_type", "")
                if mime == "application/json":
                    data = getattr(getattr(p, "inline_data", None), "data", b"")
                    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
                t = getattr(p, "text", None)
                if isinstance(t, str) and t.strip():
                    return t
    except Exception:
        pass
    # Fallback
    return getattr(resp, "text", None)

# --- Unit Normalization (reused) ---

_SUPERSCRIPT_MAP = str.maketrans({"²":"2","³":"3","¹":"1","⁻":"-","⁺":"+"})
_MICRO_FORMS = ("µ","μ")
UNIT_CANON_MAP = {
    "°c": "degC", "°f": "degF", "kj/m2": "kJ/m^2", "j/m2": "J/m^2",
    "g/10min": "g/10min", "g/10 min": "g/10min", "g/cm3": "g/cm^3", "kg/m3": "kg/m^3",
    "mpa": "MPa", "gpa": "GPa", "pa": "Pa", "j/m": "J/m",
}

def normalize_unit_text(u: str|None) -> str|None:
    if not u: return u
    s = unicodedata.normalize("NFKD", u).translate(_SUPERSCRIPT_MAP)
    for m in _MICRO_FORMS: s = s.replace(m, "u")
    s = s.replace("°", "deg")
    s = s.replace("·", "/").replace("∙", "/").replace("／", "/").replace("÷", "/")
    s = re.sub(r"\s+", "", s)
    s = s.replace("/m2", "/m^2").replace("/cm2", "/cm^2")
    low = s.lower()
    low = re.sub(r"\bdeg\s*c\b", "degc", low, flags=re.I)
    if low in UNIT_CANON_MAP: return UNIT_CANON_MAP[low]
    s = re.sub(r"\bmpa\b", "MPa", s, flags=re.I)
    s = re.sub(r"\bgpa\b", "GPa", s, flags=re.I)
    s = re.sub(r"\bpa\b", "Pa", s, flags=re.I)
    return s

def normalize_units_inplace(parsed: dict) -> dict:
    props = parsed.get("properties") or []
    for p in props:
        p["unit"] = normalize_unit_text(p.get("unit"))
        name = (p.get("name") or "").lower()
        val = p.get("value")
        if isinstance(val, (int, float)) and "modulus" in name and name.endswith("_gpa") and p.get("unit") == "MPa":
            p["value"] = val / 1000.0
            p["unit"] = "GPa"
        if name.startswith("mfi_") and p.get("unit") in {"g/10 min", "g/10min"}:
            p["unit"] = "g/10min"
        if p.get("unit") in {"C", "degC"}:
            p["unit"] = "degC"
    return parsed

# --- Evidence Clamping (reused) ---
def clamp_evidence_inplace(parsed: dict, max_chars: int = 160) -> dict:
    props = parsed.get("properties") or []
    for p in props:
        ev = p.get("provenance")
        if isinstance(ev, dict) and isinstance(ev.get("text"), str):
            s = re.sub(r"\s+", " ", ev["text"]).strip()
            if len(s) > max_chars:
                s = s[: max_chars - 1] + "…"
            ev["text"] = s
    return parsed


def run_parser(
    file_name: str,
    pdf_text: str,
    tables_text: Optional[str] = None,
    page_image: Optional["PIL.Image.Image"] = None,
    timeout_s: int = 180,
) -> Dict[str, Any]:
    try:
        import google.generativeai as genai
        # 1) Configure API key path (API) OR rely on ADC for Vertex. We’ll use API key if provided.
        if os.getenv("GOOGLE_API_KEY"):
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

        system_instruction = _load_prompt_text()

        # 2) Correct generation config for google.generativeai
        gen_config = GenerationConfig(
            temperature=0.0,
            top_p=0.0,
            top_k=1,
            candidate_count=1,
            max_output_tokens=1536,            # per-page JSON; safe upper bound
            response_mime_type="application/json",
        )

        model_name = os.getenv("PARSER_MODEL", "gemini-2.0-flash")  # or "gemini-2.5-flash"
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_instruction,
        )

        # 3) Build multimodal message parts: image first (helps vision models), then compact JSON string
        parts: list[Any] = []
        if page_image is not None and PIL is not None:
            buf = BytesIO()
            page_image.save(buf, format="JPEG", quality=85, optimize=True)
            parts.append({"mime_type": "image/jpeg", "data": buf.getvalue()})

        # Keep the “user JSON” concise; tables first per your prompt
        payload = {
            "file_name": file_name,
            "pdf_text": (pdf_text or "")[:15000],          # hard clamp
            "tables_text": (tables_text or None)
        }
        parts.append(json.dumps(payload, ensure_ascii=False))

        # 4) Call the model
        resp = model.generate_content(
            parts,
            generation_config=gen_config,
            request_options={"timeout": timeout_s},
        )

        # 5) Robustly capture JSON
        raw = _extract_json_from_response(resp) or ""
        parsed = safe_parse_model_json(raw)

        # Make sure we carry the filename through
        parsed.setdefault("file_name", file_name)

        # Clamp breadcrumbs and units
        parsed = clamp_evidence_inplace(parsed, max_chars=160)
        parsed = normalize_units_inplace(parsed)

        # Final validation against the Pydantic schema
        from .parser_agent.schemas import ExtractResponse
        return ExtractResponse.model_validate(parsed).model_dump()

    except Exception as e:
        logger.error(f"Error running direct parser for {file_name}: {e}", exc_info=True)
        # On any failure, return a valid empty structure to avoid crashing the pipeline.
        return {
            "file_name": file_name,
            "properties": [],
            "error": str(e)
        }