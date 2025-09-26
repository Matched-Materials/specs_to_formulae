Development Guide — Spec Sheets → Targets → Formulations (w/ ADK)

Goal you described
Batch a folder of spec sheets → normalize & gap-fill → generate candidates constrained by “compostable” or “recyclable” (and allow “bio-based/mass-balance” sourcing) → predict properties → evaluate with ADK agent → write per-spec results folders (formulas, analysis, logs) → move on to next spec.

0) End-to-end dataflow (what the code does today)

CLI (src/cli.py)

recommend-batch dispatches to src.batch.run_batch with --spec-dir, --out-dir, --goals, and --workers.

For each spec, it runs pipeline.run_single.

Single run (src/pipeline.py)

Loads goals (e.g., configs/goals/compostable.json).

Ingests the spec: ingest/normalize_spec.py → normalized JSON {material, properties[], diagnostics} using configs/property_ontology.json.

Gap-fills: gapfill/merger.py → pulls catalog defaults (gapfill/retriever.py) and simple estimators (gapfill/estimators.py).

Prefilters: prefilter.filter_candidates(...) based on family/compatibility rules (today this is resin-centric).

Formulation DOE/BO → properties bridge (bridge_formulations_to_properties.py) → predictions DataFrame.

Evaluates with ADK (run_evaluator_with_adk.py + evaluator/matsi_property_evaluator/* via agent_eval_helpers.evaluate_with_agent).

Writes artifacts under results/<spec_stem>/... (CSV, JSON, md, logs).

Batch (src/batch.py)

Orchestrates parallel runs with safe temp dirs → atomic move to out_dir/<spec_stem>; resilient logging.

Evaluator agent (ADK)

evaluator/matsi_property_evaluator/agent.py defines the agent; eval_schema.py defines strict input/output Pydantic models; root_agent.prompt + optional tools.

Compatibility (src/compatibility.py)

Chemistry blocks + process scores (MFR ratio, melt overlap). Meant for pre-generation blocking and post-generation scoring.

1) Why you saw those warnings/errors — and how to fix
A. “MCP requires Python 3.10 or above”

You’re importing modules (e.g., ADK or tool deps) that transitively import MCP on Python < 3.10.
Fix: enforce Python >= 3.10 and lazy-import optional bits.

Actions

In pyproject.toml (or setup.cfg), set:

[project]
requires-python = ">=3.10"


In files that import ADK/tools, guard imports:

import sys
if sys.version_info < (3, 10):
    raise RuntimeError("Python 3.10+ required for ADK/MCP features")


Make non-ADK paths not import ADK unless needed (move imports inside functions).

B. “Skipping blocked formulation … PP ↔ PLA/PHA-PHB”

You’re generating pairs that cannot ever pass compatibility, then spamming block logs. This is both noisy and wastes attempts (e.g., “Reached max attempts (400) … Generated 0/20”).

Fix: push stream & chemistry constraints upstream into pool selection and DOE sampling so you never attempt impossible pairs.

Actions (snippets below in §3 & §5):

Filter pools by goal (compostable vs recyclable) and by recycling_stream per ingredient in data/processed/ingredient_library.json.

Pre-compute a compatibility allowlist by family (e.g., Polyolefin only with Polyolefin elastomers and PP-g-MAH compatibilizer; never polyester with PP unless a compat route explicitly allowed).

In DOE generator, abandon attempts early if pool is unsatisfiable; don’t loop 400×.

C. Evaluator “Agent context is missing required keys: ['predictions', 'targets_constraints']”

This comes from src/agent_eval_helpers._validate_agent_context. The function flags empty dicts as missing. When the mapping from targets → internal keys doesn’t hit, targets_constraints becomes empty → every row fails.

Fixes:

Ensure build_targets_constraints(...) always maps to the actual columns in your predictions DataFrame (PROPERTY_MAP must match; your predictions columns include MFI_g10min, sigma_y_MPa, E_GPa, Izod_m20_kJm2, HDT_C).

Fail once with a helpful error if zero constraints mapped.

# src/agent_eval_helpers.py (inside build_targets_constraints)
if not constraints:
    raise ValueError(
        "No targets matched PROPERTY_MAP; check target names/conditions and predictions columns."
    )


Also improve the validation message:

def _validate_agent_context(ctx: dict):
    miss = [k for k in ("predictions","targets_constraints")
            if not isinstance(ctx.get(k), dict) or len(ctx.get(k)) == 0]
    if miss:
        raise ValueError(
            f"Agent context missing/empty: {miss}. "
            f"predictions_keys={list(ctx.get('predictions',{}).keys())[:8]} "
            f"targets_keys={list(ctx.get('targets_constraints',{}).keys())[:8]}"
        )

2) Stream guardrails you asked for (recyclable / compostable / bio-based | mass-balance)
Design

Two orthogonal axes:

End-of-life stream (recycling_stream): e.g., PP, PET, Compost, Any, Mixed-Polyolefin.

Sourcing (sustainability.origin): fossil, bio-based, mass-balance (+ optional certs: ISCC+, RSB, etc).

Goals file governs EoL constraints; sourcing is optional weighting/filters:

configs/goals/recyclable.json → one stream lock (e.g., PP).

configs/goals/compostable.json → all ingredients must be Compost or Any and families that are actually compostable (PLA, PHA/PHB, PBAT blends).

New: configs/goals/bio_based.json (or embed in recyclable) for sourcing preference, while EoL remains recyclable.

Schema changes (lightweight)

Your ingredient_library.json already has recycling_stream and sustainability.origin. Add optional certifications and clarify “bio-based & recyclable”:

{
  "name": "Bio-PP ICP",
  "type": "PP-ICP",
  "family": "Polypropylene",
  "recycling_stream": "PP",
  "sustainability": {
    "origin": "bio-based",            // or "mass-balance" | "fossil"
    "bio_content_pct": 85,
    "recycled_content_pct": 0,
    "certs": ["ISCC+"]
  }
}

3) Prefilter & pool selection (make the guardrails bite before DOE)

File: src/prefilter.py — it currently filters mainly by resin family. Extend it to filter all ingredient pools (elastomers, compatibilizers, fillers, stabilizers, etc.) by stream and family.

🔧 Add a new function and call it from pipeline.run_single before DOE:

# src/prefilter.py
from typing import Dict, Any, List, Tuple

ALLOWED_COMPOST_FAMILIES = {"polyester/PLA", "polyester/PHA-PHB", "polyester/PBAT"}  # tune as needed

def filter_pools_by_goals(
    goals: Dict[str, Any],
    lib: Dict[str, Any],
    target_stream: str | None = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Return filtered pools by EoL stream & chemistry."""
    compostable = goals.get("sustainability", {}).get("compostable", False)
    if compostable:
        def ok(item):
            rs = (item.get("recycling_stream") or "").lower()
            fam = (item.get("chem_family") or "").lower()
            return ("compost" in rs) and any(fam.startswith(f) for f in ALLOWED_COMPOST_FAMILIES)
    else:
        # recyclable: lock the stream to the base resin stream (e.g., PP)
        target_stream = target_stream or goals.get("recycling_stream", None)
        def ok(item):
            rs = (item.get("recycling_stream") or "").lower()
            return (target_stream or "").lower() in rs or rs == "any"

    pools = {}
    for k, items in lib.items():
        if not isinstance(items, list): 
            continue
        pools[k] = [it for it in items if ok(it)]
    return pools


Plumb it: in pipeline.run_single (right after loading ingredient_library.json), detect the base resin stream from the selected base or from the spec (PP vs PET), then call filter_pools_by_goals(...) and pass those filtered pools to DOE generator.

4) DOE generator — stop producing impossible pairs

File: src/formulation_doe_generator_V1.py
Add early blocking by chemistry and stream (use compatibility.chemistry_score + stream match). Also bail out if pool is empty.

🔧 Inside DOE selection loop (before sampling):

from src.compatibility import chemistry_score

def _chem_ok(fa: str, fb: str, rules: dict) -> bool:
    score, blocked, _ = chemistry_score(fa, fb, rules, has_compat=False)
    return (not blocked) and score > 0.66  # tune threshold

# Example when choosing base + elastomer
cand_els = [e for e in pools["elastomers"]
            if _chem_ok(base["chem_family"], e["chem_family"], rules)]
if not cand_els:
    # no valid elastomers for this base under current goal → skip or switch base
    continue


🔧 Abandon impossible categories cleanly:

if not pools["compatibilizers"] and needs_compat:   # detected by low chemistry score
    logger.info("No compatibilizers available under goal; skipping compat paths.")
    continue


🔧 Cap attempts per (base, elastomer, filler_type) key and back-off if blocked >N times to prevent “Reached max attempts (400) … 0/20”:

attempt_budget = defaultdict(int)
key = (base["type"], elastomer["type"], filler_family)
attempt_budget[key] += 1
if attempt_budget[key] > 20:
    continue  # try a different family instead of hammering the same dead end

5) Hard guardrails (contamination prevention)

File: configs/guardrails.yaml — expand to include stream policy and blocked families.

streams:
  compostable:
    allowed_families:
      - polyester/PLA
      - polyester/PHA-PHB
      - polyester/PBAT
    disallow:
      - polyolefin/PP
      - polyolefin/PE
  recyclable:
    require_same_stream: true   # all ingredients must be 'PP' or 'Any' for PP stream
    # optional: allow compatibilizers tagged PP even if MAH
    notes: "Compatibilizer must not break sort/regrind quality."

doe_hard_block_threshold: 0.40
needs_compat_threshold: 0.70


File: src/compatibility.py — it already returns blocked + reasons. Make sure pairwise_compatibility_score(...) reads the thresholds above (it already does for doe_hard_block_threshold).

File: src/pipeline.py — enforce before generation:

from src.prefilter import filter_pools_by_goals

pools = filter_pools_by_goals(goals, ingredient_library, target_stream=base_stream)
if any(len(v)==0 for v in (pools.get("base_resins",[]), pools.get("elastomers",[]))):
    raise RuntimeError("Unsatisfiable pools under selected goal/stream; no candidates will be generated.")

6) Gap-filling agent (your #1 request)

Today gapfill/merger.py uses a local catalog and trivial estimators. Replace/extend with an ADK agent that can (optionally) query literature/registries (Crossref, PubChem/ChemSpider, MatWeb, etc.) and codify conservative estimates with provenance.

New package: src/gapfill_agent/

src/gapfill_agent/
  agent.py           # ADK agent: takes normalized spec, returns enriched properties[]
  tools.py           # search_* tools (stubs or real) + unit harmonization
  schema.py          # Pydantic input/output
  prompt.md          # Principled, conservative, cite sources


Pydantic schema (example)

# src/gapfill_agent/schema.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class Prop(BaseModel):
    name: str
    value: float
    unit: str
    provenance: str
    confidence: float = Field(ge=0, le=1)

class GapfillInput(BaseModel):
    material: Dict[str, Any]
    known_properties: List[Prop]

class GapfillOutput(BaseModel):
    properties: List[Prop]
    notes: Optional[str] = None


Agent (ADK)

# src/gapfill_agent/agent.py
from google.adk.agents import Agent
from google.genai import types
from .schema import GapfillInput, GapfillOutput
from .tools import search_literature, lookup_pubchem, normalize_units

root_gapfill_agent = Agent(
    name="gapfill_agent",
    instructions=open(Path(__file__).with_name("prompt.md")).read(),
    input_schema=GapfillInput,
    output_schema=GapfillOutput,
    tools=[search_literature, lookup_pubchem, normalize_units],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json"
    )
)


Merger hook

# src/gapfill/merger.py
from src.gapfill_agent.run import enrich_with_agent  # wrap ADK call

def gapfill(normalized: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(normalized)
    props = list(enriched.get("properties", []))
    agent_out = enrich_with_agent(enriched["material"], props)
    # Keep priority: spec > literature > estimator
    seen = {p["name"] for p in props}
    for p in agent_out.get("properties", []):
        if p["name"] not in seen:
            props.append(p)
            seen.add(p["name"])
    enriched["properties"] = props
    return enriched


Note: Keep the agent optional (env flag) so the pipeline works offline.

7) Bio-based / Mass-balance stream you asked for

Interpretation
“Bio-based” and “Mass-balance” are sourcing attributes that don’t change EoL stream; a bio-PP still goes to PP recycling. So:

Keep the recyclable goal unchanged for EoL.

Add source constraints/preferences in goals (soft or hard):

// configs/goals/recyclable_biopp.json
{
  "sustainability": { "compostable": false },
  "sourcing": {
    "origin_allow": ["bio-based", "mass-balance"],
    "min_bio_content_pct": 50,
    "prefer_certs": ["ISCC+"]
  }
}


Selection logic (in prefilter.filter_pools_by_goals):

src_goal = goals.get("sourcing", {})
allowed_origins = set(src_goal.get("origin_allow", []))

def ok_source(item):
    s = item.get("sustainability", {})
    origin = s.get("origin")
    bio_pct = s.get("bio_content_pct", 0)
    certs = set(s.get("certs", []))
    if allowed_origins and origin not in allowed_origins:
        return False
    if src_goal.get("min_bio_content_pct") and bio_pct < src_goal["min_bio_content_pct"]:
        return False
    if src_goal.get("prefer_certs") and not (certs & set(src_goal["prefer_certs"])):
        # not hard fail; instead annotate as lower priority
        item["_source_priority_penalty"] = 0.1
    return True


Then bias DOE sampling weights inversely to _source_priority_penalty.

8) Make the evaluator contract rock-solid

Input mapping: Confirm predictions DataFrame has the keys used in PROPERTY_MAP (MFI_g10min, sigma_y_MPa, E_GPa, Izod_m20_kJm2, HDT_C) or extend PROPERTY_MAP accordingly.

Guardrail deltas (configs/guardrails.yaml) are applied vs baseline model if present. Keep these conservative to avoid over-penalizing.

Agent JSON enforcement: Already set response_mime_type="application/json" in agent and re-validated in run_evaluator_with_adk.py. Good.

9) Batch autonomy & foldering

Already supported by src/batch.py + CLI. Two small upgrades:

Resume-safe: Skip any spec whose final dir already contains a success marker; write a DONE file on success.

if (final_dir / "DONE").exists():
    logger.info(f"Skipping already-completed: {sp.name}")
    continue
# after move:
(final_dir / "DONE").write_text("ok")


Spec grouping (PP vs PET) — use directory structure you described:

data/specs/
  PP/
  PET/
  Compostable/


Run recommend-batch per group; the base stream can be inferred from group or spec’s normalized family.

10) Tests you’ll want (fast & decisive)

test_stream_guardrails.py

Compostable goal rejects PP elastomers/compatibilizers/fillers.

Recyclable PP goal rejects PLA/PHA items.

test_doe_no_block_spam.py

DOE never attempts a known-blocked pair; attempts per key < 5; no “Reached max attempts … 0/20” spam.

test_targets_mapping.py

build_targets_constraints maps to existing prediction columns; empty mapping raises helpful error.

test_gapfill_agent_optional.py

With GAPFILL_AGENT_DISABLED=1, pipeline completes using catalog/estimators.

test_batch_resume.py

Existing DONE folders are skipped.

(You already have good tests for atomic write, resume, end-to-end batch.)

11) Concrete code snippets (drop-in)
A) Wire goals into pools in pipeline.run_single
# src/pipeline.py (near start of run_single)
from src.prefilter import filter_pools_by_goals
from pathlib import Path
import json

lib = json.loads(Path("data/processed/ingredient_library.json").read_text("utf-8"))

# determine base stream: from spec or group (fallback to PP)
base_stream = (normalized["material"].get("family") or "PP")
if "propyl" in base_stream.upper(): base_stream = "PP"
elif "PET" in base_stream.upper(): base_stream = "PET"

pools = filter_pools_by_goals(goals, lib, target_stream=base_stream)
# pass 'pools' to DOE generator instead of raw 'lib'

B) Abort early if unsatisfiable pools
def _require_nonempty(pools, keys):
    for k in keys:
        if not pools.get(k):
            raise RuntimeError(f"No candidates available for '{k}' under current goal.")

_require_nonempty(pools, ["base_resins", "elastomers"])

C) Improve validator error clarity (already shown in §1C)
12) Ops/Config hygiene

README: add explicit Python 3.10+ note and a quickstart that runs one spec, one group, and batch.

Logging: src/utils/io.setup_logging is solid. Add a per-run summary.json with counts: generated, blocked, evaluated, top-k exported.

Configs: keep all thresholds in configs/guardrails.yaml and all targets in a single data/target_properties.json. Your configs/targets.py already normalizes to canonical keys—good.

13) What “credible formulas” means here (practical checklist)

✅ All ingredients in a candidate share the goal’s EoL stream (or Any) — verified before generation.

✅ No hard-blocked chemistry pairs pass into DOE.

✅ Compatibilizer mandatory if chemistry score < needs_compat_threshold.

✅ Process realism (MFR ratio, melt overlap) above your thresholds.

✅ Evaluator agent sanity-checks predicted properties vs targets and flags unrealistic combos (already in agent_eval_helpers).

✅ Bio-based/mass-balance sourcing recognized and preferred/required per goal without changing EoL stream.

14) Runbook (what you’ll actually do)

Put spec sheets by stream/family groups (as you planned).

Choose the goal:

Recyclable PP → configs/goals/recyclable.json (+ optional sourcing goal)

Compostable → configs/goals/compostable.json

Run one spec first:

python -m src.cli recommend-single \
  --spec data/specs/PP/Acme_PP_ICP_2040.csv \
  --out-dir results/pp_run_01 \
  --goals configs/goals/recyclable.json


Batch the folder:

python -m src.cli recommend-batch \
  --spec-dir data/specs/PP \
  --out-dir results/pp_run_01 \
  --goals configs/goals/recyclable.json \
  --workers 4


Inspect per-spec folders: formulations.csv, props.csv, evaluation_report.md, scores.json, logs.

15) Open items you may want next

Add unit harmonization assertions in ingest/normalize_spec.py (the ontology already has units; enforce them).

Expand estimators (Fox equation for Tg of blends, rule-of-mixtures for modulus/density, Gordon–Taylor for miscibility hints).

Add vendor dedup & confidence scoring (multiple sources) using simple tie-break rules (latest date, closest grade name, identical InChIKey for additives where applicable).

(Optional) Lightweight graph of allowed transitions (family → compat → filler) to drive DOE with fewer dead ends.

TL;DR — Top 6 changes (in order)

Filter pools by stream + family upfront (prefilter.filter_pools_by_goals) and pass to DOE.

Early chemistry blocking in DOE, cap attempts per key (stop the spam).

Fix evaluator target mapping to avoid empty targets_constraints.

Add gap-fill ADK agent (optional) for literature-derived conservative fills.

Add sourcing preferences (bio-based/mass-balance) orthogonal to EoL.

Enforce Python 3.10+ and lazy-import ADK bits.

If you want, I can turn the snippets above into exact patches for each file, but this should be enough for Gemini Code Assist to implement confidently.