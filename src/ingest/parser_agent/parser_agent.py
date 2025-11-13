# src/ingest/parser_agent/parser_agent.py
import os
from google.adk.agents import Agent
from google.genai.types import GenerateContentConfig
from .schemas import ParserInput, ExtractResponse

# --- Load the Prompt ---
# The prompt file is located in the same directory as this agent module.
_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_prompt_path = os.path.join(_PROMPTS_DIR, "root.prompt.md")
with open(_prompt_path, "r", encoding="utf-8") as f:
    _PROMPT_INSTRUCTION = f.read()

parser_agent = Agent(
    name="pdf_parser_agent",
    model="gemini-2.5-pro",
    description="Parses raw text from a PDF to extract structured material properties.",
    instruction=_PROMPT_INSTRUCTION,

    # structured input and output schemas
    input_schema=ParserInput,
    output_schema=ExtractResponse,

    generate_content_config=GenerateContentConfig(
        temperature=0.0,
        top_p=0.0,
        top_k=1,
        candidate_count=1,
        max_output_tokens=2048,         # 2048 is plenty for 1–6 pages
        # response_mime_type="application/json", # Let output_schema handle the mode.
    ),

    # silence the transfer warning up-front
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
