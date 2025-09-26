# Action Plan (Updated with Evaluator Agent)
_Last updated: 2025-08-29_
Action Plan: Implementing the Closed-Loop Optimization Workflow
This plan details the steps to create a fully autonomous system that can generate, simulate, evaluate, and optimize material formulations based on a defined target.

Phase 1: Formalize the Target Profile
The first step is to create a structured, machine-readable definition of the target material.

Action: Develop a new script, src/target_profile_generator.py.
Functionality:
This script will take key performance indicators (KPIs) and target values as input (e.g., from a technical datasheet for a material like Exceed™ Tough PP8285E1).
It will generate a target_profile.json file. This file will serve as the "ground truth" for the evaluator agent, containing properties like Melt Flow Index, Flexural Modulus, Izod Impact Strength, etc., along with their desired values and acceptable ranges.
Outcome: A standardized target profile that can be programmatically referenced by the evaluation and optimization steps.
Phase 2: Enhance Formulation Generation
The existing formulation generator needs to be adapted to create focused design spaces.

Action: Modify the existing src/formulation_doe_generator_V1.py script.
Functionality:
Introduce new command-line flags like --focus recycled and --focus bio-based to guide the Design of Experiments (DOE).
When a focus is selected, the script should constrain the ingredient selection to only include materials of that type (e.g., rPP for recycled, or bio-fibers/bio-char for bio-based).
This requires ensuring the data/processed/ingredient_library.json is populated with the necessary recycled and bio-based materials and their metadata (cost, CO2e, etc.).
Outcome: The ability to generate initial formulation sets that are specifically tailored to either a recycled or bio-based material strategy.
Phase 3: Implement the Main Orchestrator Logic
The src/main_orchestrator.py script will be the central controller that executes the entire closed-loop process. Its logic will manage the sequence of operations from initialization to convergence.

Action: Define and implement the workflow logic within src/main_orchestrator.py.

Functionality:

Initialization (Cycle 0):

The orchestrator starts by calling the target_profile_generator.py to establish the goal.
It then calls the formulation_doe_generator.py (with a specified focus) to generate an initial set of diverse candidate formulations using a space-filling design (e.g., Latin Hypercube Sampling). This serves as the initial data for the optimizer.
Main Optimization Loop (Cycles 1 to N):

The orchestrator will loop for a specified number of cycles (--num-cycles) or until a convergence criterion is met. For each cycle:
A. Predict Properties: It calls src/bridge_formulations_to_properties.py, feeding it the current cycle's candidate formulations. The script uses the physics-informed models to predict the material properties for each candidate.
B. Evaluate Formulations: It calls src/evaluator_orchestrator.py. This agent receives the predicted properties and the target_profile.json. It produces quantitative scores (how well properties match the target) and qualitative feedback (scientific plausibility, comparison to known material behaviors).
C. Optimize & Suggest Next Candidates:
The orchestrator feeds the full history of formulations and their corresponding evaluation scores into a Bayesian Optimizer module.
This optimizer, configured with a 25%:75% explore-exploit ratio, learns the relationship between formulation and performance.
It then suggests the next batch of promising formulations to be evaluated in the subsequent cycle. These new formulations become the input for step A in the next loop.
Termination:

The loop terminates when the maximum number of cycles is reached or when the optimizer's suggestions no longer yield significant improvement in scores, indicating convergence.
Outcome: A fully automated, iterative workflow managed by a single script that intelligently explores the formulation space to find optimal solutions.

Phase 4: Configuration and Data Management
A robust configuration and data handling strategy is crucial for managing the complexity of the loop.

Action: Centralize workflow configuration and ensure systematic data handling.

Functionality:

Master Configuration: Create a primary configuration file (e.g., configs/orchestrator_config.yaml) that defines all key parameters for a run, including:
Formulation focus (recycled or bio-based).
Number of optimization cycles.
Bayesian Optimizer settings (e.g., explore/exploit ratio).
Paths to all models and libraries.
Data Flow Management: The main_orchestrator.py will be responsible for all file I/O between steps. It will create a dedicated subdirectory for each iteration (e.g., results/run_XYZ/iter_001/, results/run_XYZ/iter_002/) to store the formulations, predicted properties, and evaluation reports for that cycle, preventing data overwrites and ensuring traceability.
Outcome: A reproducible and easily configurable system where each optimization run is self-contained and its results are clearly organized.


_________________________
Chat GPT action plan: 
High-level architecture (modules & data flow)

Orchestrator
→ Target Profiler (0)
→ Formula Generator (1)
→ Process/TSE Surrogate (2)
→ Property Aggregator
→ LLM Evaluator (3)
→ Bayes Optimizer (4–5)
→ loop until Convergence (6)

All modules read/write typed JSON blobs (see “IO contracts”). Use a single run folder per experiment with ML-style tracking (artifacts + metadata).

Global conventions & defaults

Composition simplex: all fractions sum to 1.0 (100%), step size defaults to 0.5–2.0 wt% per component (set per component).

Focus modes: "bio_based" or "recycled" toggle allowed component sets and min content constraints (e.g., ≥60% bio-based; ≥50% recycled).

Process levers (per batch): screw speed, barrel zone temps, die temp, feed rate, throughput, L/D, venting, and optional compatibilizer dosing rate.

Properties of record (extendable): tensile modulus/strength/elongation, impact, HDT, Tg, OTR/WVTR, density, haze, melt flow, biodegradation % at N days, compostability pass/fail, SME, torque window, predicted degradation index.

Scoring scales: normalize all properties to [0,1] against target ranges; LLM returns calibrated scores in [0,1].

IO contracts (no code — target shapes to implement)
0) Target profile

Input: freeform user goals (numbers/ranges) + focus mode
Output: TargetProfile

{
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
    "process_bounds": {"screw_rpm":[150,500], "zone_temps_C":[150,230], "die_temp_C":[150,220], "throughput_kg_h":[5,25]}
  },
  "weights_policy": "auto_from_ranges"  // or "manual"
}

1) Candidate formulas

Output: CandidateBatch

{
  "batch_id": "cand_0001",
  "focus_mode": "bio_based",
  "candidates": [
    {
      "recipe": {"PLA":0.65,"PBAT":0.15,"PHA":0.10,"talc":0.09,"compatibilizer":0.01},
      "process": {"screw_rpm":320,"zone_temps_C":[170,175,180,185,190,195,200],"die_temp_C":190,"throughput_kg_h":12.0},
      "origin": "LHC_init"  // or "optimizer_suggest"
    }
  ]
}

2) Process/TSE surrogate predictions

Output: PredictedPropertiesBatch

{
  "batch_id":"cand_0001",
  "predictions":[
    {
      "id":"cand_0001_00",
      "recipe":{...},
      "process":{...},
      "properties": {
        "tensile_strength_MPa":{"mean":46.2,"std":2.1},
        "elongation_%":{"mean":4.7,"std":0.8},
        "HDT_C":{"mean":61.5,"std":1.7},
        "OTR_ccm_m2_day":{"mean":1300,"std":120},
        "melt_flow_index":{"mean":6.2,"std":0.5},
        "SME_kWh_kg":{"mean":0.19,"std":0.03},
        "degradation_index":{"mean":0.12,"std":0.04}
      }
    }
  ]
}

3) LLM evaluator outputs

Output: EvaluatorScoresBatch

{
  "batch_id":"cand_0001",
  "evaluations":[
    {
      "id":"cand_0001_00",
      "plausibility_score": 0.78,            // literature/physics consistency
      "target_alignment_score": 0.64,        // distance-to-target w/ domain context
      "feasibility_flag": true,              // hard veto if false
      "risk_flags": ["PLA_over_degradation_risk"],
      "notes": "Strength plausible; OTR borderline vs target; torque ok.",
      "citations": ["doi:...","datasheet:..."]
    }
  ]
}

4–5) Optimizer inputs & suggestions

Input: merged predictions + evaluator scores
Output: OptimizerSuggestionBatch

{
  "iteration": 3,
  "acquisition_mix": {"explore":0.25,"exploit":0.75},
  "suggestions":[
    {"recipe":{"PLA":0.60,"PBAT":0.20,"PHA":0.08,"talc":0.10,"compatibilizer":0.02},
     "process":{"screw_rpm":340,"zone_temps_C":[...],"die_temp_C":192,"throughput_kg_h":13.0},
     "rationale":"Increase PBAT for toughness; raise die temp slightly for flow; penalize OTR via filler tuning."}
  ]
}

Step-by-step implementation plan
Step 0 — Target Profiler

Ingest targets (numbers or ranges). If only a single target value is provided, auto-derive min/max using domain heuristics (e.g., ±10–20% band).

Normalize & weight: create a distance-to-target function that:

Scales each property to [0,1] vs target band (direction aware).

Applies weights from the profile.

Returns (overall_target_distance, per_property_distances).

Hard constraints: cost, compostability standard(s), bio- vs recycled content. Prepare feasibility checks used later by the optimizer and evaluator.

Step 1 — Formula Generator (bio-based / recycled)

Component library: declare allowed components per focus with min/max and granularity (e.g., compatibilizer max 2 wt%).

Initial design: generate a Latin Hypercube (LHC) or Sobol set covering:

Composition simplex under bounds.

Process levers within guardrails.

Diversity gating: reject near-duplicates (L2 distance in recipe+process space below ε).

Batch size: rule of thumb = 10–20 × (num_free_parameters) for the first LHC batch (you can tune down later).

Step 2 — Process/Twin-Screw Surrogate

Feature vector: concatenation of recipe fractions + process levers + derived features (SME estimate, shear index, PLA residence-time degradation proxy).

Outputs: predicted property distribution (mean, std). Include flags if any process violations (e.g., torque > limit).

Uncertainty: ensure the surrogate yields a usable σ (via ensembles, MC dropout, or GP head). This feeds exploration.

Step 3 — LLM Evaluator (“evaluator_agent”)

RAG context: index spec sheets + literature you trust; retrieve top-k passages per candidate.

Prompt contract: ask for JSON exactly as in EvaluatorScoresBatch; require:

Plausibility (physics/literature consistency),

Target alignment (use TargetProfile weights),

Feasibility flag (hard veto if something violates obvious constraints),

Short notes and citations.

Guardrails: low temperature, “do not speculate beyond provided sources,” and require feasibility_flag=false if it finds contradictions (“This material doesn’t behave like that.”).

Calibration pass: on a small labeled set, fit an affine map so LLM scores align with real/validated expectations (optional but valuable).

Step 4 — Use LLM scores for initialization & priors

Filter & weight initial LHC/random points:

Drop candidates with feasibility_flag=false.

Compute composite seed score = 0.6*target_alignment + 0.4*plausibility and pick top-M for optimizer warm-start.

Feasibility prior: train a lightweight classifier (plausible vs implausible) on accumulated LLM labels to create a soft feasibility constraint for BO (probability-of-feasibility multiplier).

Step 5 — Bayesian Optimizer (25% explore / 75% exploit)

Surrogate(s):

Primary: GP/ensemble over the objective = - overall_target_distance (maximize).

Secondary: feasibility classifier (from Step 4) as a constraint.

Acquisition policy (batched):

Exploit (75%): q-Expected Improvement (q-EI) or q-NEHVI (if multi-objective), multiplied by feasibility probability.

Explore (25%): Thompson sampling or q-Upper Confidence Bound with high κ, also feasibility-weighted.

Joint optimization of process + recipe: constrain moves with a trust region:

Composition: max Δ per component per iteration (e.g., ≤5 wt%).

Process: bounded per-lever step (e.g., rpm ±30, die temp ±5 °C).

Diversity in batch: penalize intra-batch similarity to avoid redundant suggestions.

Step 6 — Stopping / Convergence

Stop when any holds:

EI (or NEHVI hypervolume gain) < ε for K consecutive iterations.

overall_target_distance < τ (meets spec).

Max iterations n reached.

Emit best-so-far candidate set with full trace.

How each step is validated (quick checks)

0 Target Profiler: Sanity check: weights sum ≈ 1; directionality correct (e.g., “lower_better” for OTR).

1 Generator: Verify sums to 1.0, bounds held, focus constraints satisfied.

2 Surrogate: Reject if predicted torque/SME outside machine limits.

3 Evaluator: Must return JSON with required keys; fail closed if missing, then set feasibility_flag=false.

4 Seeds/Priors: Confirm at least M warm-start points after filtering; if not, relax filters iteratively.

5 Optimizer: Enforce trust region; log acquisition values and acceptance.

6 Convergence: Persist artifacts and a human-readable summary.

Orchestration loop (what the controller does each iteration)

Load TargetProfile.

If iteration==0: produce CandidateBatch via LHC/Sobol; else use OptimizerSuggestionBatch.

Run Process/TSE Surrogate → PredictedPropertiesBatch.

Aggregate predicted properties + target distances → feed to evaluator_agent → EvaluatorScoresBatch.

Merge predictions + LLM scores → update dataset (for BO and the feasibility classifier).

Bayes step: compute acquisition mix (25% explore/75% exploit), select q new candidates → OptimizerSuggestionBatch.

Check Convergence; continue or finalize.

Tracking, reproducibility, and data hygiene

Run metadata: target profile, focus mode, component catalog versions, surrogate model hash, LLM prompt/template hash, seeds.

Per-iteration log: candidate JSON, predictions, evaluator output, acquisition values, convergence metrics.

Artifacts: CSV/JSONL snapshots of all candidates; optional plots for distances and acquisition landscape.

Reproducibility: fixed seeds for LHC, surrogate ensembling, and BO sampling per run.

Risk controls & gotchas

LLM hallucination: require citations + set feasibility_flag=false if no supporting evidence; random spot-check citations.

Surrogate drift: retrain/refresh when residuals (vs any real test data) exceed threshold; monitor with rolling error estimates.

Constraint bleed: trust region plus a projection step back to simplex and bounds.

Over-exploitation: enforce the 25/75 mix strictly; if stagnation detected, temporarily raise explore share to 40–50% for one iteration.

Multi-objective tension: if you must hard-meet compostability/cost, treat them as constraints; otherwise include in weighted distance.

Minimal acceptance checklist (what “done” looks like)

Can load a human-readable TargetProfile and compute normalized distances.

Can emit initial CandidateBatch in both bio_based and recycled focus modes under constraints.

Surrogate returns PredictedPropertiesBatch with mean/std and flags.

evaluator_agent reliably returns EvaluatorScoresBatch JSON with scores, veto, notes, citations.

Optimizer consumes merged data, honors 25% explore / 75% exploit, and outputs OptimizerSuggestionBatch.

Orchestrator loops until Convergence, persisting full audit trail and best candidates.

If you want, I can turn this into a short “wiring checklist” doc (or a canvas) with the exact field names and validation rules you can paste next to your code while you hook it up—still without changing any code.