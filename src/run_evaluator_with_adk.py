# src/run_evaluator_with_adk.py
from __future__ import annotations
import asyncio, json, logging, os, queue, re, threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
from uuid import uuid4
from pydantic import ValidationError

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

# Prefer new google_genai; fall back to older google.genai
try:
    from google_genai.types import Content, Part
    _GENAI_FLAVOR = "google_genai"
except ModuleNotFoundError:
    from google.genai.types import Content, Part
    _GENAI_FLAVOR = "google.genai"

from evaluator.matsi_property_evaluator.agent import root_agent
from evaluator.matsi_property_evaluator.eval_schema import EvalInput, EvalScores

logger = logging.getLogger(__name__)
logger.debug("Using GenAI flavor: %s", _GENAI_FLAVOR)

# -------- JSON detection/repair --------
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*({.*?})\s*```", re.DOTALL)
SMART_QUOTES = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
}
TRAILING_COMMA_RE = re.compile(r",\s*(?=[}\]])")

def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.lstrip("`")
        if s[:4].lower() == "json":
            s = s[4:].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s

def _normalize_quotes(s: str) -> str:
    for k, v in SMART_QUOTES.items(): s = s.replace(k, v)
    return s

def _extract_braced(s: str) -> Optional[str]:
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1 or b <= a: return None
    return s[a:b+1]

def _escape_inside_strings(s: str) -> str:
    out, in_str, esc = [], False, False
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if not in_str:
            if ch == '"':
                in_str = True; out.append(ch)
            else:
                out.append(ch)
        else:
            if esc:
                out.append(ch); esc = False
            else:
                if ch == '\\':
                    out.append(ch); esc = True
                elif ch == '\r':
                    pass
                elif ch == '\n':
                    out.append('\\n')
                elif ch == '"':
                    j = i + 1
                    while j < n and s[j].isspace(): j += 1
                    if j < n and s[j] not in (',', '}', ']', ':') and s[j] != '\n':
                        out.append('\\"')  # interior quote
                    else:
                        out.append('"'); in_str = False
                else:
                    out.append(ch)
        i += 1
    return "".join(out)

def _salvage_json(raw: str) -> Optional[str]:
    s = _strip_fences(_normalize_quotes(raw))
    if not s.strip().startswith("{"):
        m = JSON_FENCE_RE.search(s)
        if m: s = m.group(1)
        else:
            maybe = _extract_braced(s)
            if maybe: s = maybe
    s = TRAILING_COMMA_RE.sub("", s)
    if not (s.strip().startswith("{") and s.strip().endswith("}")):
        return None
    return _escape_inside_strings(s)

# -------- Response extraction helpers --------
def _looks_like_genai_response(x: Any) -> bool:
    if x is None: return False
    if isinstance(getattr(x, "text", None), str): return True
    if hasattr(x, "candidates"): return True
    c = getattr(x, "content", None)
    return bool(c and getattr(c, "parts", None))

def _content_parts(resp: Any) -> list:
    cands = getattr(resp, "candidates", None) or []
    if cands:
        cont = getattr(cands[0], "content", None)
        return getattr(cont, "parts", None) or []
    cont = getattr(resp, "content", None)
    if cont and getattr(cont, "parts", None):
        return cont.parts
    return []

def _first_function_args_json(resp: Any) -> Optional[str]:
    for p in _content_parts(resp):
        fc = getattr(p, "function_call", None)
        if fc and getattr(fc, "args", None):
            try:
                return json.dumps(fc.args)
            except Exception:
                continue
    return None

def _first_inline_json(resp: Any) -> Optional[str]:
    for p in _content_parts(resp):
        inline = getattr(p, "inline_data", None)
        if inline and getattr(inline, "mime_type", "") == "application/json":
            data = getattr(inline, "data", None)
            if data:
                try: return data.decode("utf-8")
                except Exception: pass
    return None

def _concat_text_parts(resp: Any) -> Optional[str]:
    texts = [getattr(p, "text", "") for p in _content_parts(resp) if getattr(p, "text", "")]
    joined = "\n".join(s for s in texts if s.strip())
    return joined.strip() or None

def _extract_payload(resp: Any) -> Tuple[str, str]:
    """Return (payload, source_tag)."""
    s = _first_function_args_json(resp)
    if s: return s, "function_args"
    s = _first_inline_json(resp)
    if s: return s, "inline_json"
    s = _concat_text_parts(resp)
    if s: return s, "parts_text"
    t = getattr(resp, "text", None)
    if isinstance(t, str) and t.strip(): return t.strip(), "resp_text"
    raise ValueError("No text/JSON could be extracted from model response.")

# -------- Runner plumbing --------
DUMP_EVENTS = os.getenv("EVAL_DUMP_EVENTS", "0") not in ("0", "false", "False", "")

def _dump_event(out: Path, ev: Any) -> None:
    try:
        rec = {"class": ev.__class__.__name__}
        for name in ("type","name","kind","event_type"):
            if hasattr(ev, name): rec[name] = getattr(ev, name)
        # Shallow payload peek
        for p in ("data","payload","response","message","result","output"):
            if hasattr(ev, p):
                obj = getattr(ev, p)
                rec[p] = obj.__class__.__name__
                break
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass

def _drain_runner(
    runner: Runner,
    user_id: str,
    session_id: str,
    user_message: Content,
    out_events: Optional[Path],
    q: queue.Queue,
) -> None:
    """
    Collect the **last usable** object from the event stream:
      - event.data.response
      - event.response
      - event.message
      - the event itself (if it looks like a response)
    As a final fallback, collect any concatenated text we see and return that as a string.
    """
    try:
        final: Union[Any, str, None] = None
        text_fallback: Optional[str] = None

        for ev in runner.run(user_id=user_id, session_id=session_id, new_message=user_message):
            if out_events and DUMP_EVENTS: _dump_event(out_events, ev) # type: ignore

            # 1) Deep paths first
            data = getattr(ev, "data", None)
            if data is not None:
                resp = getattr(data, "response", None)
                if _looks_like_genai_response(resp):
                    final = resp
                else:
                    # sometimes `data` is already the response
                    if _looks_like_genai_response(data):
                        final = data

            # 2) Common shallow fields
            for attr in ("response", "message", "result", "output", "output_message"):
                if final is None and hasattr(ev, attr):
                    obj = getattr(ev, attr)
                    if _looks_like_genai_response(obj):
                        final = obj

            # 3) The event itself
            if final is None and _looks_like_genai_response(ev):
                final = ev

            # 4) Opportunistic text scrape (for last-ditch rescue)
            try:
                if final is None:
                    txt = None
                    if hasattr(ev, "text") and isinstance(ev.text, str):
                        txt = ev.text
                    elif hasattr(ev, "message") and hasattr(ev.message, "text"):
                        txt = getattr(ev.message, "text")
                    if txt and txt.strip():
                        text_fallback = txt.strip()
            except Exception:
                pass

        # Return priority: real response object > string fallback
        if final is not None:
            q.put(("ok", final))
        elif text_fallback:
            q.put(("ok", text_fallback))  # deliberately a string
        else:
            q.put(("err", "No usable response object or text found in event stream."))
    except Exception as e:
        q.put(("err", f"{e.__class__.__name__}: {e}"))

def evaluate_context(
    payload_dict: Dict[str, Any],
    out_dir: Union[str, Path],
    timeout_s: int = 120,
    app_name: str = "matsi_evaluator",
    user_id: str = "default_user",
) -> Dict[str, Any]:
    """Runs the ADK agent for a given context payload and writes artifacts."""
    out_path = Path(out_dir); out_path.mkdir(parents=True, exist_ok=True)

    # Validate input early
    EvalInput.model_validate(payload_dict)

    # Build Content for ADK
    user_message = Content(role="user", parts=[Part(text=json.dumps(payload_dict))])

    # Session + Runner
    session_id = f"eval-{uuid4().hex}"
    session_service = InMemorySessionService()
    asyncio.run(session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id))
    runner = Runner(agent=root_agent, app_name=app_name, session_service=session_service)

    events_dump = out_path / "debug_events.ndjson" if DUMP_EVENTS else None

    # Threaded run with timeout
    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=_drain_runner, args=(runner, user_id, session_id, user_message, events_dump, q), daemon=True) # type: ignore
    t.start()
    try:
        status, info = q.get(timeout=timeout_s)
    except queue.Empty:
        status, info = ("err", f"Timeout after {timeout_s}s")
    finally:
        t.join(timeout=1)

    # Try to fetch session (for debugging only; do not rely on it)
    try:
        updated_session = asyncio.run(session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id))
    except Exception:
        updated_session = None

    # --- Centralized JSON Parsing and Validation ---
    def _validate_scores(raw_payload: str) -> Dict[str, Any]:
        """Parses, validates, and returns a score dictionary."""
        payload_to_parse = raw_payload
        try:
            # First attempt: parse directly
            data = json.loads(payload_to_parse)
        except json.JSONDecodeError:
            # Second attempt: salvage and parse
            print("      -> Direct JSON parse failed. Attempting to salvage...")
            salvaged_json = _salvage_json(raw_payload)
            if not salvaged_json:
                raise ValueError("Could not salvage a valid JSON object from the response.")
            data = json.loads(salvaged_json)

        return EvalScores.model_validate(data).model_dump()

    scores: Dict[str, Any] = {}
    report = "Agent returned invalid or empty output."
    extracted_source = "N/A"
    extracted_payload = None

    try:
        if status != "ok" or info is None:
            raise ValueError(f"Agent runner failed to produce a response. run_status={status}, info={info}")

        # If _drain_runner gave us a string (fallback), use it directly.
        if isinstance(info, str):
            raw_text = info
            extracted_source = "string_fallback"
        else:
            raw_text, extracted_source = _extract_payload(info)

        if not raw_text:
            raise ValueError("Agent did not produce any text output.")

        extracted_payload = raw_text.strip()
        # Strip code fences if present
        if extracted_payload.startswith("```"):
            extracted_payload = _strip_fences(extracted_payload)

        # If we still don't see a JSON object, try regex as a fallback
        if not extracted_payload.startswith("{"):
            m = JSON_FENCE_RE.search(raw_text)
            if m:
                extracted_payload = m.group(1)

        scores = _validate_scores(extracted_payload)

        if scores.get("recommended_bo_weight") is None:
            conf = scores.get("confidence", "Medium")
            cf = {"High": 1.0, "Medium": 0.65, "Low": 0.35}.get(conf, 0.65)
            lc = scores.get("literature_consistency_score") or 0.0
            rp = scores.get("realism_penalty") or 0.0
            scores["recommended_bo_weight"] = lc * rp * cf

        report = scores.get("notes") or "No qualitative notes provided in the evaluation."

    except (ValidationError, ValueError, AttributeError) as e:
        msg = f"Agent returned invalid output. Error: {e}"
        report = msg
        scores = {"error": msg, "run_status": status, "run_info": None if info is None else str(info), "source": extracted_source}

        # Save extraction attempt for debugging
        try:
            if extracted_payload:
                (out_path / "debug_extracted_payload.txt").write_text(extracted_payload, encoding="utf-8")
        except Exception:
            pass

        # Save raw response (works for both object and string)
        try:
            if info is not None:
                if hasattr(info, "model_dump_json"):
                    raw = info.model_dump_json(indent=2)
                elif isinstance(info, str):
                    raw = info
                else:
                    raw = json.dumps(info, default=str, indent=2)
                (out_path / "debug_raw_response.json").write_text(raw, encoding="utf-8")
                report += f"\n\nRaw response object saved to {out_path/'debug_raw_response.json'}."
        except Exception as dump_err:
            report += f"\n\nFailed to write raw response object: {dump_err}"

        # Save session for context
        try:
            if updated_session and hasattr(updated_session, "model_dump_json"):
                (out_path / "debug_session.json").write_text(updated_session.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

    # Write artifacts
    (out_path / "evaluation_report.md").write_text(report, encoding="utf-8")
    (out_path / "scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")

    return {
        "score": scores,
        "report_path": str(out_path / "evaluation_report.md"),
        "scores_path": str(out_path / "scores.json"),
    }

def main():
    """Main function to run the script from the command line."""
    import argparse
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Smoke-run the evaluator with a JSON payload.")
    p.add_argument("--in-json", type=str, help="Path to an EvalInput JSON file.")
    p.add_argument("--out-dir", type=str, required=True, help="Output directory.")
    args = p.parse_args()

    if args.in_json:
        payload = json.loads(Path(args.in_json).read_text("utf-8"))
    else:
        # Use a default payload for smoke testing if no file is provided
        payload = {
            "cycle_id": uuid4().hex,
            "sample_id": "smoke-0",
            "predictions": {"MFI_g10min": 8.5, "E_GPa": 2.1, "eps_y_pct": 4.2},
            "process": {"Tm_C": 220, "Q_kgh": 5},
            "targets_constraints": {
                "MFI_g10min": {"value": 10.0, "tol": 2.0},
                "E_GPa": {"min": 1.8},
                "eps_y_pct": {"min": 3.5},
            },
            "meta": {"debug": True},
        }
    print(json.dumps(evaluate_context(payload, out_dir=args.out_dir), indent=2))

if __name__ == "__main__":
    main()
