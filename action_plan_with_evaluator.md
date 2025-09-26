# Unified Development Bible: Closed‑Loop Formulation Optimizer + LLM Evaluator

*Last updated: 2025‑08‑29*

---

## Priority Implementation Checklist

This checklist outlines the highest-priority tasks to evolve the existing codebase into the fully-functional, closed-loop system described in this document.

### P1: Implement Formulation "Focus" Modes

*   **Goal**: Enable the generation of initial formulations that are specifically tailored to either a `recycled` or `bio-based` strategy, as required by the plan.
*   **Action**:
    1.  **Modify `formulation_doe_generator_V1.py`**:
        *   Add a `--focus` command-line argument that accepts `recycled` or `bio-based`.
        *   In `generate_formulation_doe`, add logic to filter the ingredient pools (e.g., `base_resins`, `fibrous_fillers`) based on the focus. For example:
            *   If `focus == 'recycled'`, only include base resins with "rPP" in their `type` or name.
            *   If `focus == 'bio-based'`, only include fillers/extenders where `sustainability.origin == 'bio-based'`.
    2.  **Update `main_orchestrator.py`**:
        *   Instead of using the hardcoded `FORMULATION_MODES`, adapt the `build_initial_multimode_batch` function or its caller to use the new `--focus` capability of the DOE generator. This will likely involve passing a new `focus` kwarg.

### P2: Implement Optimizer Explore/Exploit Strategy

*   **Goal**: Align the Bayesian Optimizer's behavior with the plan's required 25% explore / 75% exploit ratio.
*   **Action**:
    1.  **Modify `main_orchestrator.py`**:
        *   Inside the main optimization loop, instead of calling `optimizer.ask()` for every new candidate, implement a mixed strategy.
        *   For a given batch size (e.g., 4 candidates), call `optimizer.ask()` 3 times (exploit) and generate 1 candidate with random values within the search space (explore). This is a pragmatic way to achieve the 75/25 split.

### P3: Refine Formulation Re-balancing Logic

*   **Goal**: Improve the realism of how new formulations are created from the optimizer's suggestions.
*   **Action**:
    1.  **Modify `main_orchestrator.py`**:
        *   Review the section in the optimization loop that creates `next_formulation_df`. Currently, it scales all unoptimized components to make the total sum to 100%.
        *   Refine this logic to primarily adjust the `baseA_wtpct` and `baseB_wtpct` to accommodate changes in additives (elastomer, filler, etc.). This more closely mimics how a formulator would adjust a recipe.

### P4: Align Data Contracts for Uncertainty

*   **Goal**: Prepare the data pipeline for handling property uncertainty, a key input for advanced optimization, without needing to change the underlying physics model yet.
*   **Action**:
    1.  **Modify `bridge_formulations_to_properties.py`**:
        *   In `compute_row_properties`, when creating the final `props` dictionary, change property names to include a `_mean` suffix (e.g., `"MFI_g10min_mean": mfi`).
        *   For each property, also add a corresponding `_std` column (e.g., `"MFI_g10min_std": 0.0`). For now, the standard deviation will be zero, but this makes the data schema compliant with the plan's future intent.
    2.  **Update `main_orchestrator.py`**:
        *   Adjust the code where it reads the predictions to look for the new `_mean` column names. The `evaluate_with_agent` helper will also need to be aware of this change.

---

> **Read me first**: This is an **action plan only**. It specifies architecture, contracts, and checklists so you can implement without ambiguity. **No code changes are included** here.

---

## 0) Consistency check & terminology map

This section confirms alignment between your coding assistant’s plan and the prior ChatGPT plan and unifies names.

**Shared intent & flow** (consistent across both):

1. Formalize a machine‑readable **Target Profile** from user KPIs.
2. Generate focus‑specific **candidate formulations** (recycled vs bio‑based).
3. Predict properties via a **twin‑screw extrusion (TSE) / process surrogate**.
4. Pass candidates + predictions to an **LLM Evaluator (evaluator\_agent)** for quantitative/qualitative scoring.
5. Feed scores into a **Bayesian Optimizer** (use as LHC/random seed and iterative guidance).
6. Run closed‑loop with **25% explore / 75% exploit** until **convergence** or **n** cycles.

**Terminology & file map**

* **Target Profiler** ↔ `src/target_profile_generator.py`
* **Formula Generator (DOE)** ↔ `src/formulation_doe_generator_V1.py` (adds `--focus`)
* **Process/TSE Surrogate** ↔ `src/bridge_formulations_to_properties.py`
* **LLM Evaluator** ↔ `src/evaluator_orchestrator.py`
* **Bayesian Optimizer** ↔ module invoked from `src/main_orchestrator.py` (can be `src/optimizer.py` or internal class)
* **Orchestrator** ↔ `src/main_orchestrator.py`

**Focus modes (shared semantics)**

* `bio_based`: enforce minimum bio‑based content and include only bio‑based ingredients from the library.
* `recycled`: enforce minimum recycled content and include only recycled sources (e.g., rPP, rPE, PCR streams).

---

## 1) System architecture (top‑down)

```
main_orchestrator
  ├─ target_profile_generator          (Step 0)
  ├─ formulation_doe_generator_V1      (Step 1)
  ├─ bridge_formulations_to_properties (Step 2)
  ├─ evaluator_orchestrator            (Step 3)
  ├─ bayesian_optimizer                (Steps 4–5)
  └─ loop until convergence            (Step 6)
```

**Data exchange principle**: Modules communicate via **typed JSON** artifacts under a run directory; no in‑memory coupling required. This enables reproducibility and simple debugging.

---

## 2) Directory, config, and run layout

**Repo‑level structure (add/confirm)**

```
configs/
  orchestrator_config.yaml
  evaluator_prompt.yaml              # system/user templates, schema validation rules
  component_policies.yaml            # focus rules, min content, bounds

data/
  processed/
    ingredient_library.json          # components with metadata (cost, CO2e, bio/recycled flags, bounds)
models/
  process_surrogate/                 # saved model, scaler hashes, meta
  feasibility_classifier/            # optional; trained from LLM labels
results/
  run_YYYYMMDD_HHMMSS/
    target_profile.json
    iter_000/
      candidates.json
      predictions.json
      evaluations.json
      optimizer_suggestions.json
      logs/
    iter_001/
      ...
logs/
  orchestrator.log
```

**`configs/orchestrator_config.yaml` (schema)**

* `run_name`: string
* `focus_mode`: `bio_based | recycled`
* `num_cycles`: int
* `batch_size`: int (`q`)
* `explore_exploit`: `{explore: 0.25, exploit: 0.75}`
* `random_seed`: int
* `component_policies_path`: str
* `ingredient_library_path`: str
* `surrogate_model_path`: str
* `evaluator_prompt_path`: str
* `optimizer`: {`type`: `gp|ensemble`, `acq`: `qEI|qNEHVI|TS|qUCB`, `trust_region`: {`recipe_max_delta_wt%`: 5, `rpm_step`: 30, `die_temp_step_C`: 5}}
* `stopping`: {`max_cycles`: int, `ei_tol`: float, `ei_patience`: int, `target_distance_tol`: float}
* `process_bounds`: {`screw_rpm`: \[min,max], `zone_temps_C`: \[min,max], `die_temp_C`: \[min,max], `throughput_kg_h`: \[min,max]}

**Seeds & hashes**: log DOE seed, optimizer seeds, surrogate/evaluator template hashes per run.

---

## 3) Data contracts (IO artifacts)

All JSON artifacts are versioned with a `"schema_version"` key (start at `1`).

### 3.1 TargetProfile (Step 0 output)

```json
{
  "schema_version": 1,
  "id": "target_YYYYMMDD_HHMM",
  "focus_mode": "bio_based",
  "properties": {
    "tensile_strength_MPa": {"target": 45, "min": 40, "max": 55, "weight": 0.9},
    "elongation_%": {"target": 5, "min": 3, "max": 8, "weight": 0.6},
    "HDT_C": {"target": 60, "min": 55, "max": 80, "weight": 0.7},
    "OTR_ccm_m2_day": {"target": 1200, "max": 1500, "direction": "lower_better", "weight": 0.5},
    "cost_$Kg": {"max": 6.0, "weight": 0.5},
    "compostability": {"standard": "EN13432", "must_pass": true, "weight": 1.0}
  },
  "constraints": {
    "sum_to_1": true,
    "component_bounds": {"PLA": [0.3, 0.9], "PBAT": [0.0, 0.4], "PHA": [0.0, 0.3], "talc": [0.0, 0.1], "compatibilizer": [0.0, 0.05]},
    "process_bounds": {"screw_rpm": [150, 500], "zone_temps_C": [150, 230], "die_temp_C": [150, 220], "throughput_kg_h": [5, 25]}
  },
  "weights_policy": "auto_from_ranges"
}
```

### 3.2 CandidateBatch (Step 1 output)

```json
{
  "schema_version": 1,
  "batch_id": "cand_0001",
  "focus_mode": "bio_based",
  "candidates": [
    {
      "id": "cand_0001_00",
      "origin": "LHC_init",
      "recipe": {"PLA": 0.65, "PBAT": 0.15, "PHA": 0.10, "talc": 0.09, "compatibilizer": 0.01},
      "process": {"screw_rpm": 320, "zone_temps_C": [170, 175, 180, 185, 190, 195, 200], "die_temp_C": 190, "throughput_kg_h": 12.0}
    }
  ]
}
```

### 3.3 PredictedPropertiesBatch (Step 2 output)

```json
{
  "schema_version": 1,
  "batch_id": "cand_0001",
  "predictions": [
    {
      "id": "cand_0001_00",
      "recipe": {"PLA": 0.65, "PBAT": 0.15, "PHA": 0.10, "talc": 0.09, "compatibilizer": 0.01},
      "process": {"screw_rpm": 320, "zone_temps_C": [170, 175, 180, 185, 190, 195, 200], "die_temp_C": 190, "throughput_kg_h": 12.0},
      "properties": {
        "tensile_strength_MPa": {"mean": 46.2, "std": 2.1},
        "elongation_%": {"mean": 4.7, "std": 0.8},
        "HDT_C": {"mean": 61.5, "std": 1.7},
        "OTR_ccm_m2_day": {"mean": 1300, "std": 120},
        "melt_flow_index": {"mean": 6.2, "std": 0.5},
        "SME_kWh_kg": {"mean": 0.19, "std": 0.03},
        "degradation_index": {"mean": 0.12, "std": 0.04}
      },
      "flags": []
    }
  ]
}
```

### 3.4 EvaluatorScoresBatch (Step 3 output)

```json
{
  "schema_version": 1,
  "batch_id": "cand_0001",
  "evaluations": [
    {
      "id": "cand_0001_00",
      "plausibility_score": 0.78,
      "target_alignment_score": 0.64,
      "feasibility_flag": true,
      "risk_flags": ["PLA_over_degradation_risk"],
      "notes": "Strength plausible; OTR borderline vs target; torque ok.",
      "citations": ["doi:...", "datasheet:..."]
    }
  ]
}
```

### 3.5 OptimizerSuggestionBatch (Steps 4–5 output)

```json
{
  "schema_version": 1,
  "iteration": 3,
  "acquisition_mix": {"explore": 0.25, "exploit": 0.75},
  "suggestions": [
    {
      "id": "sugg_0003_00",
      "recipe": {"PLA": 0.60, "PBAT": 0.20, "PHA": 0.08, "talc": 0.10, "compatibilizer": 0.02},
      "process": {"screw_rpm": 340, "zone_temps_C": [170, 175, 180, 185, 190, 195, 200], "die_temp_C": 192, "throughput_kg_h": 13.0},
      "rationale": "Increase PBAT for toughness; raise die temp slightly for flow; penalize OTR via filler tuning."
    }
  ]
}
```

---

## 4) Module‑by‑module implementation plan

### 4.0 Target Profiler (`src/target_profile_generator.py`)

**Goal**: Convert KPIs (from datasheets or user input) into a consistent `TargetProfile` JSON.

**Inputs**

* KPIs (CSV/JSON or CLI) with desired values/ranges.
* `focus_mode`.

**Logic**

* For scalar KPIs, auto‑derive bands (±10–20%) unless ranges provided.
* Encode property **directionality** (`higher_better`, `lower_better`, or `target_value`).
* Create a **weighted, normalized distance function** definition to be reused downstream (document formula in Appendix A).
* Validate **hard constraints**: compostability standard(s), cost cap, min bio/recycled content.

**Outputs**

* `results/run_x/target_profile.json` (see 3.1).

**Validation**

* Weights non‑negative; recommend sum≈1 (normalize if needed).
* Component/process bounds are present; `sum_to_1=true` if working on composition simplex.

---

### 4.1 Formula Generator (`src/formulation_doe_generator_V1.py`)

**Goal**: Generate initial diverse candidates honoring focus constraints.

**Inputs**

* `ingredient_library.json` (with flags: `is_bio_based`, `is_recycled`, bounds, granularity, cost, CO2e).
* `component_policies.yaml` (min content rules per focus).
* `TargetProfile` (for bounds sanity and optional cost/compliance prechecks).

**Logic**

* Add `--focus {bio-based|recycled}`.
* Filter allowed components per focus + enforce **min content** (e.g., ≥60% bio or ≥50% recycled; parameterized in policy file).
* Generate DOE via **Latin Hypercube** or **Sobol** across:

  * Composition under bounds with `sum=1.0` projection.
  * Process levers within `process_bounds`.
* **Diversity gating**: reject near‑duplicates using L2 distance in concatenated recipe+process space.
* **Batch size**: `10–20 × (#free_parameters)` for cycle 0.

**Outputs**

* `iter_000/candidates.json` (3.2 format with origin=`LHC_init`).

**Validation**

* Each recipe sums to 1.0 ± 1e‑6; all bounds satisfied; focus min content satisfied.

---

### 4.2 Process/TSE Surrogate (`src/bridge_formulations_to_properties.py`)

**Goal**: Predict property distributions for each candidate and flag infeasible process windows.

**Inputs**

* `candidates.json`.
* Trained surrogate model + scalers in `models/process_surrogate/`.

**Logic**

* Features = recipe fractions + process levers + derived features (e.g., SME estimate, shear index, degradation proxy for PLA/PBAT/PHA).
* Output **mean/std** per property; set `flags` when predicted torque/SME exceeds limits or temps out of spec.
* Provide **uncertainty** estimates (ensemble variance/MC‑dropout/GP σ) for optimizer exploration.

**Outputs**

* `iter_k/predictions.json` (3.3).

**Validation**

* No NaNs; std≥0; any limit breach adds a descriptive flag.

---

### 4.3 LLM Evaluator (`src/evaluator_orchestrator.py`)

**Goal**: Provide **plausibility** and **target alignment** scores + short notes and citations.

**Inputs**

* `predictions.json` + `target_profile.json`.
* `evaluator_prompt.yaml` (system/user templates; schema for required JSON keys).
* Access to literature/spec sheets (RAG index paths).

**Prompt contract**

* Require JSON matching `EvaluatorScoresBatch` (3.4).
* Definitions:

  * `plausibility_score` = physics/literature consistency in \[0,1].
  * `target_alignment_score` = normalized closeness to targets, weighted by `TargetProfile`.
  * `feasibility_flag` = **hard veto** if contradictions or missing support.
* Instruct: "Cite sources; if no support, set feasibility=false."
* Temperature low; length constrained; forbid speculation.

**Calibration** (optional but recommended)

* On a seed set, fit an affine map to calibrate LLM scores to internal scales; log calibration parameters in run folder.

**Outputs**

* `iter_k/evaluations.json` (3.4).

**Validation**

* Strict schema check; missing keys → evaluator returns `feasibility_flag=false` with note.

---

### 4.4 Bayesian Optimizer (module/class)

**Goal**: Use all historical data to suggest the next batch under **25% explore / 75% exploit**.

**Inputs**

* History: candidates + predictions + evaluations up to iteration `k`.
* Trust‑region & bounds from config.

**Objective**

* Maximize `- overall_target_distance` (see Appendix A) or, if multi‑objective, use **q‑NEHVI** with constraints.

**Constraints**

* Probability‑of‑feasibility multiplier based on a **feasibility classifier** trained from `feasibility_flag` labels (optional but valuable).

**Acquisition policy**

* **Exploit (75%)**: q‑Expected Improvement (q‑EI) or q‑NEHVI; feasibility‑weighted.
* **Explore (25%)**: Thompson Sampling or high‑κ q‑UCB; feasibility‑weighted.
* Enforce **trust region**: max Δ per component (e.g., ≤5 wt%/iter) and bounded steps for RPM/temperatures.
* Encourage **batch diversity** via repulsion penalty in acquisition optimization.

**Outputs**

* `iter_k/optimizer_suggestions.json` (3.5). Next cycle’s `candidates.json` can be this file renamed or re‑emitted by the orchestrator with `origin="optimizer_suggest"`.

**Validation**

* Suggestions respect all bounds, sum‑to‑one, focus minima, and trust‑region steps.

---

### 4.5 Orchestrator (`src/main_orchestrator.py`)

**Goal**: Deterministically drive the loop; log everything.

**Cycle 0 (initialization)**

1. Emit `target_profile.json` via Target Profiler.
2. Emit `iter_000/candidates.json` via DOE generator (with `--focus`).

**Cycles 1..N**
A. Predict → call Process Surrogate → `predictions.json`.
B. Evaluate → call LLM Evaluator → `evaluations.json`.
C. Optimize → call Bayesian Optimizer with full history → `optimizer_suggestions.json`.
D. Prepare next iteration’s `candidates.json` from suggestions.
E. Check **stopping**: `(EI < ε for K iters) OR (target_distance < τ) OR (iter == max)`.

**Logging & artifacts**

* Write per‑iteration logs, acquisition values, convergence metrics.
* Persist seeds, hashes, and config snapshot under the run folder.

**Failure modes**

* If evaluator returns many `feasibility=false`, fall back to more exploration in next iter (temporarily raise explore to 0.4–0.5).
* If optimizer yields duplicates, increase diversity penalty or enlarge trust region slightly; log decision.

---

## 5) Validation & testing strategy

**Unit tests (by module)**

* **Target Profiler**: directionality math; weight normalization; bound serialization.
* **DOE Generator**: simplex projection; min content constraints; diversity filter.
* **Surrogate**: no‑nan guarantees; monotonicity sanity for obvious trends; uncertainty non‑negative.
* **Evaluator**: schema conformance; forbidden free‑text leaks; citation presence.
* **Optimizer**: trust‑region enforcement; acquisition mix accounting exactly 25/75; constraint adherence.

**Integration tests**

* Dry run with 5–10 tiny candidates, stub surrogate (deterministic), stub evaluator (fixed scores), confirm loop mechanics and stopping work.

**Acceptance tests (definition of “works”)**

* One end‑to‑end run produces: `target_profile.json`, `iter_000/..`, `iter_001/..`, … until stop, with no missing artifacts, and final best candidate reported with rationale.

---

## 6) Runbook (operational checklist)

1. **Prepare inputs**: verify `ingredient_library.json` flags, bounds, and `component_policies.yaml` min content rules.
2. **Select focus**: `bio_based` or `recycled`; set in `orchestrator_config.yaml`.
3. **Set bounds**: confirm `process_bounds` reflect the current machine capability.
4. **Set optimizer**: choose acquisition (`qEI` default) and trust‑region steps.
5. **Seed DOE**: record `random_seed`.
6. **Launch orchestrator**: ensure a new `results/run_*` is created with a config snapshot.
7. **Monitor**: per‑iter logs for EI, target distance, feasibility rate.
8. **Conclude**: archive run folder; export `best_candidates.json` and a CSV summary.

---

## 7) Risk controls & mitigations

* **LLM hallucination** → require citations; `feasibility=false` if unsupported; sample audit citations.
* **Constraint bleed** → projection to simplex + trust region; explicit checkers before writing suggestions.
* **Mode collapse (over‑exploit)** → keep strict 25/75; auto‑bump explore temporarily when EI stagnates.
* **Surrogate mis‑specification** → track residuals (when real data available); retrain on drift triggers.
* **Library inconsistency** → lock ingredient library version per run (hash + path recorded).

---

## 8) Definition of done (DoD)

* CLI or config‑only launch produces a complete, reproducible run folder.
* JSON contracts validated at every hop; no schema errors.
* Optimizer batches respect bounds and trust region with 25/75 mix.
* Evaluator outputs include notes + citations; zero missing keys.
* Final output: ranked best candidates with per‑property deltas vs targets and a concise rationale.

---

## Appendix A: Scoring & normalization

**Per‑property normalized distance**

* If `higher_better`:
  `score = clip((x - min) / (target - min), 0, 1)`
* If `lower_better`:
  `score = clip((max - x) / (max - target), 0, 1)`
* If `target_value`:
  `score = exp(-|x - target| / s)` (pick `s` from range width or domain heuristic)

**Overall target distance**

* `overall_target_distance = 1 - Σ (w_i * score_i)`
* Minimize distance (or maximize `Σ w_i * score_i`).

**Composite seed score (for Step 4)**

* `seed = 0.6 * target_alignment_score + 0.4 * plausibility_score`.

---

## Appendix B: Ingredient library fields (suggested)

* `name`, `id`, `is_bio_based` (bool), `is_recycled` (bool)
* `bounds_wt_frac`: \[min, max], `step_wt%`
* `cost_$Kg`, `co2e_kg_per_kg`, `density_g_cc`
* `notes`, `supplier`, `spec_refs` (list)

---

## Appendix C: Process bounds & levers (suggested set)

* `screw_rpm`, `zone_temps_C` (array), `die_temp_C`, `throughput_kg_h`, `feed_rate_kg_h`, `venting` (bool), `L_over_D`

---

## Appendix D: Minimal progress metrics to log per iteration

* Best **Σ w\_i \* score\_i** so far; Δ vs previous iter.
* Batch **EI** (or hypervolume gain) mean/median.
* % candidates with `feasibility=true`.
* Acquisition mix counts (explore vs exploit).
* Average recipe/process step sizes vs trust region.
