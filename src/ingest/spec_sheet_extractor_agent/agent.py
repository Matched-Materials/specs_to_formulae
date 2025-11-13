# Copyright 2025 Matched Materials LLC

"""Spec sheet extractor sub-agent definition."""

from google.adk.agents import Agent
from .sseprompt import SPEC_SHEET_EXTRACTOR_PROMPT

spec_sheet_extractor_agent = Agent(
    name="spec_sheet_extractor_agent",
    model="gemini-2.5-flash",
    description="Extracts material properties from a spec sheet text and formats them for the dashboard.",
    instruction=SPEC_SHEET_EXTRACTOR_PROMPT,
)