# Development Plan — Spec Sheets → Formulas (STF)

**Purpose**
A reliable, mostly autonomous pipeline that converts **spec sheets** into **credible polymer formulations**, evaluates them against **targets & constraints**, and iteratively improves via **Bayesian optimization (BO)**. The system must enforce **sustainability guardrails** (recyclable, compostable, bio\_based/mass\_balance) and remain scientifically grounded.

---

## 1) Goals & Non‑Negotiables

* **Autonomy:** Given a folder of spec sheets and a chosen goal (e.g., `compostable`, `recyclable`), the system processes **each spec** → writes a **results folder** → moves on to the next.
* **Stream purity:** Ingredient pools must obey the selected **end‑of‑life stream**. Compostable excludes non‑compostable items; recyclable keeps streams clean (e.g., no compost‑only additives).
* **Bio-based / mass-balance:** Allow materials that are chemically identical to petro-based but sourced **bio‑based** or via **mass balance**. They join the same recycling stream; provenance is tracked.
* **Robustness first:** If the agent hiccups, the **programmatic evaluator** keeps the loop moving with deterministic scoring.
* **Domain fidelity:** Targets and generated formulas must stay within physically plausible bounds and documented compatibilities.

---

## 2) Architecture & Run Layout (Final)

**Data flow per spec sheet**

1. **Parse spec → TargetProfile** (targets & constraints)
2. **Prefilter** ingredient pools based on focus (e.g., `compostable`, `recyclable`, `bio_based`) and polymer family grouping (PP, PET, PLA, etc.)
3. **Generate candidates** (initial DOE → BO propose)
4. **Predict properties** (bridge surrogate)
5. **Evaluate** (programmatic scorer + ADK agent)
6. **Optimize** (update BO with scores)
7. **Emit artifacts** (scores, notes, plots, logs) and advance to next spec

**Key modules**

* `src/main_orchestrator.py` — end‑to‑end controller
* `src/prefilter.py` — goal/focus guardrails & pool selection
* `src/formulation_doe_generator_V1.py` — initial DOE candidates
* `src/bridge_formulations_to_properties.py` — property prediction bridge
* `src/agent_eval_helpers.py` — per-row evaluation orchestration
* `src/run_evaluator_with_adk.py` — ADK runner/bridge (prefers function\_call.args)
* `evaluator/matsi_property_evaluator/agent.py` — ADK agent config & tools
* `evaluator/matsi_property_evaluator/eval_schema.py` — Pydantic schemas
* `src/evaluator/scorer.py` — **programmatic evaluator** (baseline)

**Artifacts**

* `results/formulations/*.csv` — DOE/BO candidates
* `results/compounded/*_predictions*.csv` — predicted properties
* `results/compounded/<run_id>/row_xxxx/` — evaluator artifacts (`scores.json`, `evaluation_report.md`, `debug_*`)
* `results/plots/*.png` — optimizer diagnostics
* `results/summaries/*.json` — run summaries
* `results/failed_evaluations/<run_id>/...` — quarantined failures

---

## 3) Data Contracts (Schemas)

> All JSON written under `results/` MUST include `schema_version` (e.g., `"1.0"`).

### 3.1 TargetProfile

```json
{
  "schema_version": "1.0",
  "meta": {"spec_id": "<file or uuid>", "stream": "compostable|recyclable|bio_based", "base_polymer_family": "PP|PET|PLA|..."},
  "targets_constraints": {
    "E_GPa":   {"min": 1.5, "max": 3.0},
    "MFI_g10min": {"range": [10, 25]},
    "eps_y_pct":  {"min": 4},
    "HDT_C":      {"min": 60}
  }
}
```

### 3.2 Ingredient Library (subset, required fields)

```json
{
  "schema_version": "1.0",
  "ingredients": [
    {
      "id": "INGR-PLA-123",
      "name": "PLA 3052D",
      "family": "polyester/PLA",
      "supplier_id": "NatureWorks",
      "recycling_stream": "PLA|PP|PET|Any",
      "compostable": true,
      "is_bio_based": true,
      "mass_balance": false,
      "wtpct_bounds": [0, 100],
      "compatibility_tags": ["PLA", "PHA-PHB"],
      "blocked_pairs": ["polyolefin/PP"],
      "notes": "",
      "properties": {"rho_gcc": 1.24}
    }
  ]
}
```

### 3.3 CandidateBatch (DOE / BO propose)

```json
{
  "schema_version": "1.0",
  "batch_id": "2025-09-24_160428",
  "focus": "compostable|recyclable|bio_based",
  "candidates": [
    {
      "id": "row_0000",
      "recipe": {"PLA": 70.0, "PBAT": 20.0, "Compatibilizer": 1.5, "Fiber": 8.5},
      "process": {"N_rps": 5, "Tm_C": 220, "Q_kgh": 5}
    }
  ]
}
```

### 3.4 Predictions

* Adopt `*_mean` and optional `*_std` columns.

```json
{
  "schema_version": "1.0",
  "batch_id": "2025-09-24_160428",
  "predictions": [
    {
      "id": "row_0000",
      "props": {
        "E_GPa_mean": 2.2, "E_GPa_std": 0.2,
        "MFI_g10min_mean": 12.0,
        "eps_y_pct_mean": 6.0,
        "rho_gcc_mean": 1.05
      }
    }
  ]
}
```

### 3.5 Evaluations (per row)

```json
{
  "schema_version": "1.0",
  "row_id": "row_0000",
  "scores": {
    "literature_consistency_score": 0.72,
    "realism_penalty": 0.90,
    "confidence": "Medium",
    "recommended_bo_weight": 0.468,
    "notes": "Text only, <=1000 chars",
    "extras": {"per_prop": {"E_GPa": "+20% vs min"}}
  }
}
```

> If the agent omits `recommended_bo_weight`, compute as: `lc * rp * cf`, where `cf = 1.0|0.65|0.35` for High|Medium|Low.

### 3.6 Optimizer Suggestion

```json
{
  "schema_version": "1.0",
  "batch_id": "2025-09-24_160428",
  "suggestions": [
    {"id": "row_0101", "recipe": {"PLA": 68.0, "PBAT": 22.0, "Compatibilizer": 1.5, "Fiber": 8.5}, "process": {"N_rps": 5, "Tm_C": 220, "Q_kgh": 5}}
  ]
}
```

---

## 4) Guardrails & Prefiltering

Centralize in `configs/guardrails.yaml`:

```yaml
schema_version: "1.0"
focus_modes:
  compostable:
    require_fields: ["compostable"]
    filters:
      compostable: true
  recyclable:
    filters:
      recycling_stream: ["PP", "PET", "Any"]
  bio_based:
    filters:
      is_bio_based: true
      # joins same recycling stream as its petrochemically identical counterpart
blocked_pairs:
  - ["polyester/PLA", "polyolefin/PP"]
compatibility:
  PLA:
    miscible_with: ["PHA-PHB"]
    immiscible_with: ["PP"]
```

**Rules**

* Filtering happens **before** candidate generation.
* Polymer‑family grouping (PP/PET/PLA/…) narrows pools further.
* DOE enforces `blocked_pairs` and `compatibility` to avoid impossible blends.

---

## 5) Evaluator (Programmatic + Agent)

### 5.1 Programmatic baseline (`src/evaluator/scorer.py`)

* Compute **target deltas** per property (normalized by tolerance or target magnitude).
* Combine into a base score `S_prog ∈ [0,1]` (e.g., soft penalties for violations, bonus for within‑band).
* Apply **realism penalty** from simple physics/heuristics (density bounds, known miscibilities, process ranges).
* Output Pydantic `EvalScores` payload.

**Pseudocode**

```python
S_target = Π_i softband(pred_i, target_i)
realism = Π_j rule_ok_j ? 1.0 : 0.7
S_prog = S_target * realism
```

### 5.2 Agentic evaluator (ADK, `emit_eval_scores`)

* Agent ALWAYS returns a **function call** `emit_eval_scores(args=dict)`.
* Bridge (`run_evaluator_with_adk.py`) prefers **function\_call.args**; falls back to text by extracting the last balanced `{...}`.
* `notes` = plain text (no markdown/citations), ≤1000 chars.
* If agent fails → use programmatic score to keep BO running.

**Failure handling**

* Quarantine row under `results/failed_evaluations/<run_id>/...` with `debug_raw_response.json` and session snapshot.
* Sentinel values from tool calls should be strings (e.g., `"TOOL_UNAVAILABLE"`), never `null`.

**Composite score (optional)**

* Blend programmatic & agent: `S_final = α * S_prog + (1-α) * S_agent` (configurable `α`, default 0.7).

---

## 6) Gap‑Fill Agent (Optional)

Purpose: fetch missing ranges from literature/databases to narrow constraints or flag risk.

**Inputs**: property name, polymer family, temperature/conditioning, candidate recipe context.

**Tools**: literature search (RAG), ChemSpider/API wrappers (respect TOS), internal curated tables.

**Outputs**: `(value | range, provenance, confidence, note)` → appended to `extras` and optionally narrows `targets_constraints`.

**Provenance‑first**: every gap fill must include `source_url|doi`, `source_confidence`, and a brief excerpt.

---

## 7) Optimizer Policy

* **Initial DOE:** LHS/sobol within filtered bounds; dedupe; respect `blocked_pairs`.
* **Explore/Exploit:** start \~**75/25**; anneal toward exploit when variance shrinks.
* **Trust regions:** limit per‑iteration recipe deltas (e.g., ±5 wt% per additive; ±10 °C for `Tm_C`).
* **Back‑off:** if a focus × family yields repeated infeasibility (e.g., >50% blocked), shrink or skip that subspace and log.
* **Seeding:** prefer known compatibilized systems first (domain priors).

---

## 8) Orchestration & CLI

**Single spec run**

```bash
python -m src.cli recommend --spec data/specs/PLA/part_a.json \
  --out-dir results/run_pla_a --goals configs/goals/compostable.json --workers 1
```

**Batch folder run**

```bash
python -m src.cli recommend-batch \
  --spec-dir data/specs/PLA_batch \
  --out-dir results/pla_batch_run_1 \
  --goals configs/goals/compostable.json \
  --group-by base_polymer_family --workers 1
```

* Each spec gets its own subfolder under `--out-dir`.
* Logs stream pre‑flight normalized errors and evaluator status per row.

---

## 9) Environment & Dependencies

* **Python:** 3.11 (`conda create -n stf python=3.11`)
* **ADK / Google clients:** ensure imports match installed packages (`google-genai` vs `google-generativeai`; use the one your code imports, plus ADK package).
* **Auth:** Application Default Credentials (ADC) or service account for Vertex; set model, region, and project in `.env`.
* **Pin versions:** maintain `requirements.txt` and a lock snapshot in `docs/requirements-locked.txt` after successful runs.

Example `.env` keys:

```
GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_REGION=us-central1
GENAI_MODEL=gemini-2.5-pro
ADK_BACKEND=vertex_ai
```

---

## 10) Milestones & Acceptance Criteria

**v0.1 — Stable Baseline**

* Programmatic evaluator live; agent optional.
* Folder‑of‑specs runs end‑to‑end; artifacts stable; guardrails enforced.
* DOE respects blocked pairs; back‑off prevents thrash.

**v0.2 — Agent First‑Class**

* Agent emits function\_call reliably; bridge consumes `function_call.args`.
* Composite scoring enabled; notes capped & plain text.
* Uncertainty columns (`*_mean/_std`) propagated from bridge → evaluator.

**v0.3 — Gap Fill & Provenance**

* Gap‑fill agent augments missing ranges with citations and confidence.
* Analytics: per‑family leaderboards; convergence dashboards.

---

## 11) PR Checklist (per change)

* [ ] Schema version bumped if contract changes; dual‑write in place for 1 iter
* [ ] Guardrails updated + tests on pool filtering
* [ ] DOE/BO respects bounds & blocked pairs; dedupe verified
* [ ] Bridge writes `*_mean/_std` as configured
* [ ] Evaluator: programmatic path passes unit tests; agent path E2E smoke
* [ ] Results folders reproducible; plots generated
* [ ] README + DEV\_PLAN updated

---

## 12) Troubleshooting Matrix

* **Agent returned non‑text / function\_call only** → Bridge prefers `function_call.args`.
* **Invalid JSON / EOF** → Bridge extracts last balanced `{...}`; else quarantine row and continue with programmatic.
* **Session has no history** → Use session snapshot; ensure ADK runner emits final llm response; bridge now tolerant.
* **`Event` has no `type`** → Bridge checks `.type|.kind|class`.
* **MCP requires Python ≥ 3.10** → Use `python=3.11` env.
* **Reached max attempts 0/N** → Tighten prefilter, reduce impossible families, enable back‑off.

---

## 13) Conventions

* **Focus names (canonical):** `compostable`, `recyclable`, `bio_based`
* **Keys/filenames:** `snake_case`; timestamps `YYYYMMDD_HHMMSS`
* **Logging:** INFO by default; DEBUG for per‑row payloads under row folder

---

*End of plan*
