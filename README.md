Awesome—let’s merge those two docs into one clean, practical README. Below is a single-file replacement you can drop in at the repo root.

---

# Spec Sheets → Formulas (STF)

A closed-loop system that converts polymer **spec sheets** into **credible candidate formulations**, predicts properties, scores them for realism vs. targets, and iteratively improves formulas via **Bayesian optimization**. It supports sustainability guardrails for **recyclable**, **compostable**, and **bio-based / mass-balance** material streams.

## What this system does

1. **Generate candidates** (DOE/BO) within a defined search space of formulation and process levers.&#x20;
2. **Predict properties** for each candidate via a bridge script and a physics/empirical hybrid model.&#x20;
3. **Evaluate** each candidate with an agentic evaluator that compares predictions to targets, checks plausibility, and emits a JSON score payload.&#x20;
4. **Optimize** using scores as the objective; generate plots and a run summary.&#x20;

Guardrails restrict ingredient pools up-front based on sustainability goals so you **don’t** generate chemically incompatible or non-compliant formulas (e.g., compostable stream excludes non-compostable components; recyclable stream stays “clean”).&#x20;

---

## Repo structure

```
configs/                 # Processing lever configs, thresholds, etc.
data/
  raw/                   # Vendor/supplier raw info
  processed/             # Machine-readable libs & targets (e.g., ingredient_library.json, target_properties.json)
docs/                    # Plans & notes
results/
  formulations/          # DOE/BO candidates + metadata
  compounded/            # Predicted properties + per-row agent artifacts
  plots/                 # Convergence & PDP plots
  summaries/             # Run summaries (JSON)
src/
  main_orchestrator.py   # End-to-end loop controller
  formulation_doe_generator_V1.py  # Initial DOE generation
  bridge_formulations_to_properties.py  # Property prediction bridge
  analysis/              # Tidy/plots of results
  prefilter.py           # Goal-based ingredient filtering
  agent_eval_helpers.py  # Agent I/O + row evaluation
evaluator/matsi_property_evaluator/
  agent.py               # ADK agent definition + prompt config
  root_agent.prompt      # Instructions (returns one JSON object)
```

Key references: orchestrator entrypoint & structure, DOE generator, and evaluation tooling. &#x20;

---

## Quickstart

### 1) Environment

```bash
# Use the repo's preferred Python
conda create -n stf python=3.11 -y
conda activate stf

# Install dependencies
pip install -r requirements.txt
```

> The orchestrator loads `.env` from the **project root**, and the evaluator agent also looks for a `.env` inside `evaluator/matsi_property_evaluator/`. Put your Vertex/Google credentials and model config in one (or both) as needed. &#x20;

### 2) Data you need

* `data/processed/ingredient_library.json` – inputs & metadata
* `data/processed/processing_levers.json` – process bounds & defaults
* `data/processed/pp_elastomer_TSE_hybrid_model_v1.json` – bridge model
* `data/processed/target_properties.json` – “spec sheet” targets the evaluator will score against

The orchestrator builds the **targets & constraints** dictionary directly from `target_properties.json`. &#x20;

### 3) Run the full loop

```bash
python src/main_orchestrator.py
```

What happens:

* **Initial DOE** candidates are generated and saved under `results/formulations`.&#x20;
* **Predictions** are produced by the bridge script and written back under `results/compounded`.&#x20;
* **Evaluator** runs per row and drops artifacts:

  * `row_XXXX/scores.json` and `row_XXXX/evaluation_report.md`
  * Failures are moved into `results/failed_evaluations/<run_id>/row_XXXX_<run_id>/` so they don’t block progress. &#x20;
* **Optimizer** updates with these scores and then iterates (ask → predict → evaluate → tell), producing **convergence** and **partial dependence** plots plus a **JSON summary**. &#x20;

---

## Sustainability guardrails & focus modes

Before generating candidates, the orchestrator **filters ingredient pools** using your chosen goals (e.g., `compostable`, `recyclable`, `bio-based`). This ensures the generator only samples compliant inputs. You can also group runs by polymer family (PP, PET, etc.).&#x20;

---

## How scoring works (evaluator)

For each candidate row:

1. We build a **JSON context** with predictions, process settings + recipe, and target constraints. Everything is coerced to JSON-safe primitives to avoid type/NaN issues.&#x20;
2. We call the **ADK agent** (Gemini) and extract a single JSON object from its last message, even if the model mixes text/function parts. &#x20;
3. We parse/validate the JSON; if the model omitted a recommended weight, we compute a reasonable fallback so the optimizer can keep going. &#x20;

We also emit quick **pre-flight deltas** vs targets (largest normalized errors) to make failures explainable in the console.&#x20;

---

## Outputs (per run)

* `results/formulations/<timestamp>.csv` – DOE/BO candidates
* `results/compounded/<timestamp>_prediction.csv` – predicted properties
* `results/compounded/<timestamp>_agent/row_XXXX/` – evaluator artifacts
* `results/plots/convergence_<timestamp>.png`, `partial_dependence_<timestamp>.png` – optimizer diagnostics &#x20;
* `results/summaries/summary_<timestamp>.json` – best score and params&#x20;

---

## Troubleshooting

* **Agent “session has no history” / “no text output” / “non-text parts”**
  The runner now pulls the **last agent message** from ADK session history and can serialize function-call args when needed. This recovers valid JSON in cases where the model returns tool-ish structures.&#x20;
* **Invalid JSON**
  We search for a fenced \`\`\`json block in the agent response and fall back to raw leading “{…}” when fences are missing, then validate with Pydantic. &#x20;
* **Bad rows shouldn’t derail the run**
  Failures are **quarantined** to `results/failed_evaluations/<run_id>/…`, and the optimizer receives a safe low weight so the loop can continue.&#x20;

---

## Useful standalone commands

Generate larger DOE batches or alternative additive suites (biofiber, biochar, compatibilizer) as needed; examples are included in the original generator docs.&#x20;

---

## Roadmap (short)

* Programmatic (non-agentic) baseline evaluator for sanity-checks (thresholds, physics filters)
* Automated **gap-filling** agent to pull missing property ranges from literature/DBs
* Expanded ingredient curation: redundant sources per stream/vendor

---

If you want, I can drop this into your repo as `README.md` and retire the other file, or add a short `docs/CHANGELOG.md` to track cleanup steps next.
