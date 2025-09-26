# evaluator/matsi_property_evaluator/eval_schema.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any

class EvalInput(BaseModel):
    """Structured input for the evaluator agent."""
    cycle_id: str = Field(description="Cycle/run identifier")
    sample_id: Optional[str] = Field(default=None, description="Row/sample identifier")
    predictions: Dict[str, Optional[float]] = Field(description="Predicted properties for ONE candidate")
    process: Dict[str, Optional[float]] = Field(description="Process settings for the same candidate")
    targets_constraints: Dict[str, Any] = Field(description="Target values and constraints for properties")
    previous_report_md: Optional[str] = Field(default=None, description="Last evaluator report as markdown")
    previous_scores: Optional[Dict[str, Any]] = Field(default=None, description="Last advisory scores JSON")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Any extra bookkeeping info")

class PropertyScore(BaseModel):
    """Plausibility score for a single predicted property."""
    score: float = Field(..., description="Plausibility score for this property, from 0.0 to 1.0.", ge=0, le=1)
    notes: str = Field("", description="Brief justification for the score, citing evidence if possible.")

class EvalScores(BaseModel):
    """Structured quantitative output for the evaluator agent."""
    literature_consistency_score: float = Field(ge=0.0, le=1.0)
    realism_penalty: float = Field(ge=0.0, le=1.0)
    recommended_bo_weight: float = Field(description="Composite = consistency * realism * confidence_factor")
    confidence: Literal["High", "Medium", "Low"]
    confidence_factor: float = Field(ge=0.0, le=1.0, description="Numeric factor for confidence: High=1.0, Medium=0.65, Low=0.35")
    property_scores: Optional[Dict[str, PropertyScore]] = Field(
        default=None,
        description="A dictionary mapping property names to their individual evaluation scores and notes."
    )
    # Optional extras if you want to log additional numeric checks
    r2_izod_vs_elastomer: Optional[float] = None
    r2_modulus_vs_elastomer: Optional[float] = None
    outlier_fraction: Optional[float] = None
    notes: Optional[str] = None
    extras: Optional[Dict[str, Any]] = None
