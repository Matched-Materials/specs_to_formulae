# Project Rivian: Low-Carbon PP Compound Development

This project aims to develop and optimize a low-carbon polypropylene (PP) impact copolymer compound that meets the performance targets of a material like Exceed™ Tough PP8285E1.

We use a data-driven approach, combining machine-readable material libraries, Design of Experiments (DOE), dimensionality reduction, and physics-informed modeling to accelerate the formulation process.

## Overview

This project is built around a closed-loop optimization workflow:

1.  **Formulation Generation**: A set of candidate material formulations is generated, either via a Design of Experiments (DOE) for an initial run, or suggested by a Bayesian Optimizer in subsequent loops.
2.  **Simulation & Prediction**: The properties of these candidates are predicted using physics-informed models (e.g., processing simulation, performance prediction).
3.  **Evaluation**: An "evaluator agent" scores the predicted outcomes against desired targets and scientific plausibility.
4.  **Optimization**: The scores are fed to a Bayesian Optimizer, which learns from the results and suggests the next most promising formulation to evaluate.
5.  This loop continues until the performance of the suggested formulations converges or a set number of iterations is reached.

The main entrypoint for running this workflow is `src/main_orchestrator.py`.

## Running the Full Workflow

This section describes how to set up the environment and run the optimization loop.

### 1. Setup

It is recommended to use a virtual environment to manage project dependencies.

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install required packages from the requirements file
pip install -r requirements.txt
```

### 2. Running the Optimization

The main script `src/main_orchestrator.py` drives the entire closed-loop process. To start the optimization, run the script from the project's root directory.

```bash
# Run from the project root directory
python src/main_orchestrator.py
```

## Directory Structure

- **configs/**: Configuration files for agents and models.
- **data/**: All project data.
  - `raw/`: Original, immutable data files (e.g., supplier cost sheets).
  - `processed/`: Cleaned, machine-readable data libraries (e.g., `ingredient_library.json`).

- **docs/**: Project documentation, plans, and reports.

- **results/**: All generated outputs from the scripts.
  - `datasets/`: Generated CSV files, like the DOE candidates.
  - `plots/`: Generated visualizations, like PCA plots.
  - `models/`: (Future) Saved machine learning models.

- **src/**: All Python source code.
  - `main_orchestrator.py`: The main script for running the closed-loop optimization workflow.
  - `formulation_doe_generator_V1.py`: Script to generate initial experimental designs.
  - `evaluator/tools.py`: A module containing statistical and scoring helper functions used by the main orchestrator.

- **notebooks/**: (Future) Jupyter notebooks for exploratory data analysis and visualization.

#To get formulas with different suites of additives you'll need to call for them: 

python src/formulation_doe_generatorV1.py \
  --ingredient_library ../data/processed/ingredient_library.json \
  --cycle iter_001 --include-biofiber --use-intune -n 200
# -> ../results/datasets/formulations/iter_001/doe_iter_001.csv
# -> ../results/datasets/formulations/iter_001/doe_run_metadata.json

 
Other examples
------------------
This generator broadens the search to include biofiber, biochar, and optional INTUNE compatibilizer.
It also allows categorical elastomer choice (PBE, POE, OBC) via split runs.

Examples:
  python formulation_doe_generator_v2.py -n 60 --include-biofiber --use-intune -o doe_biofiber_intune.csv
  python formulation_doe_generator_v2.py -n 60 --include-biochar -o doe_biochar.csv
  python formulation_doe_generator_v2.py -n 80 --elastomer_family POE --include-biofiber --include-biochar -o doe_POE_bio.csv

The output includes estimated cost, CO2e, and biogenic mass fraction based on ingredient_library.json metadata.

Then bridge:

bash
Copy code
python src/bridge_formulations_to_properties.py \
  --cycle iter_001 \
  --ingredient-library ../data/processed/ingredient_library.json \
  --model ../data/processed/pp_elastomer_TSE_hybrid_model_v1_gpt.json
# -> ../results/datasets/properties/iter_001/props_iter_001.csv
# -> ../results/datasets/properties/iter_001/bridge_run_metadata.json
Then orchestrate:

bash
Copy code
python src/evaluator_orchestrator.py \
  --provider dummy --model-name placeholder \
  --context ../results/datasets/properties/iter_001/context_iter_001.json \
  --cycle iter_001
# -> ../results/evaluations/iter_001/evaluator_report_iter_001.md
# -> ../results/evaluations/iter_001/advisory_scores_iter_001.json
# -> ../results/evaluations/iter_001/evaluator_run_metadata.json