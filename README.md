# Spec Sheets -> Formulas (STF)

A closed-loop system that converts polymer spec sheets into credible candidate formulations, predicts properties, scores them for realism vs. targets, and iteratively improves formulas via Bayesian optimization. It supports sustainability guardrails for recyclable, compostable, and bio-based or mass-balance material streams.

## What this system does

1. **Generate candidates** (DOE/BO) inside a defined formulation + process search space.
2. **Predict properties** for each candidate via a bridge script and a physics/empirical hybrid model.
3. **Evaluate** each candidate with an agentic evaluator that compares predictions to targets and emits a JSON score payload.
4. **Optimize** using those scores; produce plots and a run summary.

Guardrails restrict ingredient pools up front based on sustainability goals so you do not generate chemically incompatible or non-compliant formulas (e.g., compostable mode excludes non-compostable components; recyclable mode stays in-stream).

---

## Repository structure

```
configs/                 # Processing levers, guardrails, goals
data/
  raw/                   # Vendor/supplier inputs
  processed/             # Machine-readable libraries & targets
docs/                    # Plans & design notes
results/
  formulations/          # DOE/BO candidates + metadata
  compounded/            # Predicted properties + agent artifacts
  plots/                 # Convergence & PDP plots
  summaries/             # Run summaries (JSON/CSV)
src/
  main_orchestrator.py   # End-to-end closed loop
  bridge_formulations_to_properties.py  # Property prediction bridge
  formulation_doe_generator_V1.py       # Initial DOE generator
  agent_eval_helpers.py  # Agent context + scoring helpers
  analysis/              # Tidy dataframe + plotting utilities
evaluator/matsi_property_evaluator/
  agent.py               # ADK agent definition
  root_agent.prompt      # Prompt (returns one JSON object)
```

---

## Quickstart

### 1. Environment

```bash
conda create -n stf python=3.11 -y
conda activate stf
pip install -r requirements.txt
```

The orchestrator loads `.env` from the project root and the evaluator also checks `evaluator/matsi_property_evaluator/.env`. Put your Vertex/Google credentials and ADK config in one or both locations.

### 2. Required processed data

- `data/processed/ingredient_library.json` – ingredient metadata and densities
- `data/processed/processing_levers.json` – process bounds/defaults
- `data/processed/pp_elastomer_TSE_hybrid_model_v1.json` – TSE hybrid model
- `data/processed/target_properties.json` – default targets/constraints

### 3. Run the closed loop

```bash
python src/main_orchestrator.py \
  --iterations 30 \
  --initial-points 30 \
  --focus bio-based \
  --goals configs/goals/compostable.json \
  --spec-file data/spec_sheets/example.json
```

What happens:

- **Initial DOE** candidates land under `results/formulations/`.
- **Property predictions** are generated via the bridge script and saved to `results/compounded/`.
- **Evaluator** runs per row, dropping `scores.json` and `evaluation_report.md` under `results/compounded/<run>_agent/row_xxxx`. Failures are quarantined in `results/failed_evaluations/<run_id>/` and assigned a safe low optimizer weight.
- **Optimizer** iterates (ask → predict → evaluate → tell), generating convergence and partial dependence plots plus a JSON summary.

---

## Sustainability guardrails & focus modes

Before candidate generation, the orchestrator filters ingredient pools using your chosen goals (e.g., compostable, recyclable, bio-based) via `filter_pools_by_goals`. You can also group runs by polymer family (PP, PET, biopolyesters, etc.) with the `--focus` flag.

---

## Evaluator workflow

1. Build a JSON-safe context with predictions, process settings, formulation recipe, and targets/constraints.
2. Call the ADK agent (Gemini) and extract a single JSON object, even if the model mixes text + tool calls.
3. Parse/validate the JSON; if the model omits a recommended weight, compute a fallback so the optimizer can continue.

The helper also prints pre-flight deltas versus targets to make failures explainable in the console.

---

## Outputs per run

- `results/formulations/<timestamp>.csv` – DOE/BO candidates
- `results/compounded/<timestamp>_prediction.csv` – predicted properties
- `results/compounded/<timestamp>_agent/row_XXXX/` – per-row agent artifacts
- `results/plots/convergence_<timestamp>.png`, `partial_dependence_<timestamp>.png`
- `results/summaries/summary_<timestamp>.json` – best score + parameters
- `results/summaries/tidy_results_<timestamp>.csv` – optional tidy dataset

---

## Troubleshooting

- **Matplotlib cache errors**: set `export MPLCONFIGDIR=$(pwd)/.cache/matplotlib` before running in restricted environments.
- **Missing dependencies**: ensure the conda env uses Python 3.11 and run `pip install -r requirements.txt` (required for `scikit-optimize`, Google ADK/Vertex SDKs, etc.).
- **Agent JSON issues**: the runner scans for ```json blocks or raw `{...}` payloads, normalizes quotes, and repairs trailing commas, but malformed outputs still surface in `results/failed_evaluations/`.
- **Bad rows**: failures are quarantined and assigned low optimizer weights so the loop keeps progressing.

---

## Useful commands

- Normalize a spec sheet: `python -m src.cli normalize-spec --spec path/to/spec.csv --out out.json`
- Single recommendation run: `python -m src.cli recommend --spec spec.csv --goals configs/goals/compostable.json --out-dir results/single_run`
- Batch recommend: `make test-batch` (runs `recommend-batch` on `data/test_specs/`)
- Batch optimize: `python -m src.cli optimize-batch --spec-dir data/spec_sheets --out-dir results/opt_runs --goals configs/goals/compostable.json`

---

## Roadmap (short)

1. Programmatic (non-agentic) baseline evaluator for sanity checks.
2. Automated gap-filling agent that pulls missing property ranges from literature/databases.
3. Expanded ingredient curation with redundant sources per stream/vendor.
