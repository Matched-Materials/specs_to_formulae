#!/usr/bin/env python3
"""
Evaluator Orchestrator (iteration-aware)
- Runs the evaluator agent with TOOLCALL/TOOLRESULT loop
- Executes evaluator_tools.* when the agent requests them
- Extracts Quantitative Advisory Score JSON from the agent's markdown
- Writes a markdown report and a scores JSON
- Iteration-aware path handling (cycle folders)
- Loads the long evaluator prompt from a --prompt-file

Usage examples:
  # Dummy provider (no API keys needed), iteration-aware outputs:
  python src/evaluator_orchestrator.py \
    --provider dummy --model-name placeholder \
    --prompt-file ../configs/evaluator_prompt.md \
    --context ../results/datasets/properties/iter_001/context_iter_001.json \
    --cycle iter_001

  # OpenAI:
  OPENAI_API_KEY=... python src/evaluator_orchestrator.py \
    --provider openai --model-name gpt-4o \
    --prompt-file ../configs/evaluator_prompt.md \
    --context ../results/datasets/properties/iter_001/context_iter_001.json \
    --cycle iter_001
"""

import os, re, json, argparse, textwrap
from typing import Dict, Any, List, Optional

# ---- import tools used by TOOLCALL execution ----
try:
    import evaluator_tools as et
except Exception:
    et = None  # OK if tools run elsewhere; we'll return an error TOOLRESULT.

# ====== Provider shims ======
def call_model_dummy(messages: List[Dict[str, str]], model_name: str) -> str:
    # Minimal content to exercise TOOLCALL flow.
    return textwrap.dedent("""\
    ### Evaluation Report

    **1. Literature Alignment**
    - Dummy alignment.

    **2. Deviations / Anomalies**
    - Dummy deviations.

    **3. Risk Flags**
    - None.

    **4. Hypotheses / Next Steps**
    - Try more data.

    **5. Confidence Rating**
    - Low (dummy).

    **6. Statistical Reasoning**
    ```TOOLCALL
    {"fn":"pearson_corr","args":{"x":[0.1,0.12,0.2,0.22],"y":[5.1,5.4,6.0,6.3]}}
    ```

    """)

def call_model_openai(messages: List[Dict[str, str]], model_name: str) -> str:
    import openai
    client = openai.OpenAI()  # needs OPENAI_API_KEY
    resp = client.chat.completions.create(model=model_name, messages=messages, temperature=0.2)
    return resp.choices[0].message.content

def call_model_gemini(messages: List[Dict[str, str]], model_name: str) -> str:
    import google.generativeai as genai, os as _os
    api_key = _os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GOOGLE_API_KEY for Gemini provider.")
    genai.configure(api_key=api_key)
    # Flatten to a single prompt string for simplicity:
    prompt = ""
    for m in messages:
        role = m.get("role", "user").upper()
        prompt += f"[{role}]\n{m.get('content','')}\n\n"
    resp = genai.GenerativeModel(model_name).generate_content(prompt)
    return resp.text or ""

# ====== TOOLCALL protocol ======
TOOLCALL_RE = re.compile(r"```TOOLCALL\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

def find_toolcalls(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in TOOLCALL_RE.finditer(text or ""):
        try:
            payload = json.loads(m.group(1))
            if isinstance(payload, dict) and "fn" in payload:
                out.append({"full_match": m.group(0), "payload": payload})
        except Exception:
            pass
    return out

def make_toolresult_block(fn: str, result: Dict[str, Any]) -> str:
    return "```TOOLRESULT\n" + json.dumps({"fn": fn, "result": result}, indent=2) + "\n```"

def execute_toolcall(payload: Dict[str, Any]) -> str:
    fn = payload.get("fn")
    args = payload.get("args", {}) or {}
    if et is None or not hasattr(et, fn):
        return make_toolresult_block(fn, {"error": "Tool not available"})
    try:
        fn_ref = getattr(et, fn)
        out = fn_ref(**args)
    except Exception as e:
        out = {"error": str(e)}
    return make_toolresult_block(fn, out)

# ====== Advisory extraction/validation ======
REQUIRED_FIELDS = ["literature_consistency", "realism_penalty", "confidence", "recommended_bo_weight"]

def extract_advisory_scores(markdown: str) -> Optional[Dict[str, Any]]:
    for m in JSON_BLOCK_RE.finditer(markdown or ""):
        try:
            obj = json.loads(m.group(1))
            if all(k in obj for k in REQUIRED_FIELDS):
                return obj
        except Exception:
            pass
    return None

def validate_scores(obj: Dict[str, Any]) -> List[str]:
    errs = []
    def num01(name):
        v = obj.get(name)
        if not isinstance(v, (int, float)):
            errs.append(f"{name} must be number")
        elif not (0.0 <= float(v) <= 1.0):
            errs.append(f"{name} must be within [0,1]")
    num01("literature_consistency")
    num01("realism_penalty")
    num01("recommended_bo_weight")
    conf = str(obj.get("confidence","")).lower()
    if conf not in {"high","medium","low"}:
        errs.append("confidence must be High/Medium/Low")
    return errs

# ====== Context rendering ======
def render_context_md(ctx: Dict[str, Any]) -> str:
    def fmt(d): return json.dumps(d, indent=2, ensure_ascii=False)
    return f"""
---
## CONTEXT (for this evaluation)

**Formulation**
```json
{fmt(ctx.get("formulation", {}))}
