# evaluator/matsi_property_evaluator/agent.py
import os
from google.adk.agents import Agent
from dotenv import load_dotenv
from google.genai import types
from .eval_schema import EvalScores, EvalInput

# Load environment variables from .env file located next to this agent file.
# This makes the agent's configuration self-contained.
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_CURRENT_DIR, ".env")
if os.path.exists(_ENV_PATH):
    load_dotenv(dotenv_path=_ENV_PATH)

# Conditionally import the search tool to avoid errors if dependencies are missing.
try:
    from .tools import evaluator_search_tool
except Exception:
    pass
TOOLS = [] # Per dev guide, this agent should not use tools.

# --- Load the Prompt ---
_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_prompt_path = os.path.join(_PROMPTS_DIR, "root_agent.prompt")
with open(_prompt_path, "r", encoding="utf-8") as f:
    _PROMPT_INSTRUCTION = f.read()

root_agent = Agent(
    model="gemini-2.5-pro",
    name="root_agent",
    description="Evaluates PP elastomer formulations; returns a single JSON object only.",
    instruction=_PROMPT_INSTRUCTION,
    # By setting input_schema to None, we configure the agent as a conversational
    # agent that accepts a simple text message. This aligns with how the runner
    # is invoking it (via `new_message`) and resolves the "no history" error.
    input_schema=None,
    # The agent's output schema is removed to prevent pydantic validation errors
    # from halting the agent's run if the LLM returns slightly malformed JSON.
    # Parsing is now handled robustly in `run_evaluator_with_adk.py`.
    output_schema=None,
    tools=TOOLS,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        # Increased token limit to prevent truncation of the detailed 'notes' field.
        max_output_tokens=8192,
        # Force JSON output and disable tool calling to ensure a predictable response.
        response_mime_type="application/json",
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="NONE")
        ),
    ),
)