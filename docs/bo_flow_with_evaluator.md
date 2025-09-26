```flowchart TD
  %% ====== CLUSTERS ======
  subgraph A["Inputs & Targets"]
    A1[ingredient_library.json];
    A2[target_properties.json];
    A3[pp_elastomer_TSE_hybrid_model_v1.json];
  end

  subgraph B["1. DOE Generation"]
    B1[formulation_doe_generator_V1.py];
    B_out["results/formulations/....csv"];
  end

  subgraph C["2. Property Prediction"]
    C1[bridge_formulations_to_properties.py];
    C_in["results/formulations/....csv"];
    C_out["results/compounded/....csv"];
  end

  subgraph D["3. Agent-based Evaluation"]
    D_entry["main_orchestrator.py<br/>(drives evaluation)"];
    D_helper[agent_eval_helpers.py];
    D_runner[run_evaluator_with_adk.py];
    D_agent["(evaluator package)<br/>root_agent.py"];
    D_llm([LLM: Gemini 2.5 Pro]);
    D_tool_loop[[TOOLCALL <-> TOOLRESULT]];
    D_rep[[".../row_.../evaluation_report.md"]];
    D_score[[".../row_.../scores.json"]];
  end

  subgraph E["4. Optimization (in main_orchestrator.py)"]
    E1[skopt.Optimizer];
    E2{Objective Function};
    E3[(Suggest Next Candidate)];
  end

  %% ====== FLOWS ======
  A1 --> B1;
  B1 --> B_out;

  B_out --> C_in;
  A1 & A3 --> C1;
  C1 --> C_out;

  %% Context assembly for evaluator
  C_out -->|DataFrame| D_entry;
  D_entry -->|calls helper with row| D_helper;
  D_helper -->|builds JSON payload| D_runner;
  D_runner -->|runs agent| D_agent;
  D_agent --> D_llm;
  D_llm <--> D_tool_loop;
  D_runner --> D_rep & D_score;

  %% Optimization loop
  A2 --> E2;
  D_score -->|recommended_bo_weight| E1;
  C_out -->|predicted props| E2;
  E2 -->|objective score| E1;
  E1 -->|proposes new point| E3;
  E3 -->|feeds back to| B1;
```