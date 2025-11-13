# src/ingest/parser_agent/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union

class ParserInput(BaseModel):
    """
    Input for the PDF parsing agent, containing the raw text extracted from a PDF
    and the filename for context.
    """
    file_name: str = Field(description="The original filename of the PDF.")
    pdf_text: str = Field(description="The full text content extracted from the PDF document.")
    tables_text: Optional[str] = Field(default=None, description="Optional pre-extracted and flattened text from tables within the document.")

NumberOrStr = Union[float, int, str, None]

class Property(BaseModel):
    """
    Represents a single property extracted from a datasheet. This structure is
    designed to capture not just the value, but also the context (conditions, method).
    """
    # allow harmless extras if the model includes them
    model_config = {"extra": "allow"}

    name: Optional[str] = Field(default=None, description="Canonical name if mapped.")
    # ↓ make raw_name optional so the generator isn't forced to drop an otherwise good record
    raw_name: Optional[str] = Field(default=None, description="The original property label from the document.")
    value: NumberOrStr = Field(default=None, description="Primary numeric value, or string like 'No Break'.")
    value_min: Optional[float] = Field(default=None, description="The minimum value if a range was specified.")
    value_max: Optional[float] = Field(default=None, description="The maximum value if a range was specified.")
    unit: Optional[str] = Field(default=None, description="The unit of measurement for the value.")
    conditions: Dict[str, Any] = Field(default_factory=dict, description="A dictionary of test conditions, like temperature or load.")
    method: Optional[str] = Field(default=None, description="The test method used, e.g., 'ASTM D638'.")
    special_value: Optional[str] = Field(default=None, description="e.g., 'no_break' if the text says 'No Break'")
    # keep 'provenance' (matches the prompt after patch #1)
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Traceability information, like source text.")
    confidence: Optional[float] = Field(default=None, description="Confidence score for the extraction.")

class ExtractResponse(BaseModel):
    """The structured output from the parsing agent."""
    # tolerate unexpected top-level keys if the model adds any
    model_config = {"extra": "allow"}
    properties: List[Property] = Field(default_factory=list)
