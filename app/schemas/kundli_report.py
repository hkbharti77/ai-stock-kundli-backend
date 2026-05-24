"""
KundliReport — Pydantic schema for the aggregated multi-agent Kundli Report.
Sprint 6: Aggregator Signal Engine.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class AgentContribution(BaseModel):
    """Score contribution from a single agent."""
    agent_type: str        # "fundamental_analyst" | "technical_analyst" | "news_analyst"
    score: int             # Raw agent score 0-100
    confidence: int        # Agent confidence 0-100
    trend: Optional[str]   # improving | stable | declining | neutral
    weight: float          # Weight used in aggregation (0.0 to 1.0)
    weighted_contribution: float  # score * weight


class SignalSensitizer(BaseModel):
    """A 'what would change this signal' trigger."""
    trigger: str           # Plain-language trigger description
    direction: str         # "upgrade" | "downgrade"
    impact: str            # "high" | "medium" | "low"


class ProbabilityHorizon(BaseModel):
    """Calibrated probability estimate for a time horizon."""
    horizon: str        # "1 week" | "1 month" | "3 months" | "6 months"
    output: str         # Text description
    probability: int    # 0 to 100 percentage


class KundliReportResponse(BaseModel):
    """Full aggregated Kundli Report — the top-level output of Sprint 6."""

    # ── Identity ─────────────────────────────────────────
    ticker: str
    company_name: str
    probability_horizons: Optional[List[ProbabilityHorizon]] = None

    # ── Aggregated Score ─────────────────────────────────
    kundli_score: int          # Final weighted score 0-100
    signal_label: str          # "Strong Buy" | "Buy" | "Neutral / Watch" | "Caution" | "Avoid"
    signal_emoji: str          # Visual indicator: 🟢🟡🟠🔴
    overall_confidence: int    # Weighted average confidence 0-100
    trend: str                 # "improving" | "stable" | "declining"

    # ── Agent Contributions ──────────────────────────────
    agents: List[AgentContribution]
    data_completeness: float   # % of agents that contributed (0-100)

    # ── 7 Mandatory Explanation Components ───────────────
    # 1. Signal Summary
    signal_summary: str

    # 2. Top Positive Factors
    top_positives: List[str]   # 3 specific data points

    # 3. Top Risk Factors
    top_risks: List[str]       # 3 specific caution points

    # 4. Signal Sensitizers
    sensitizers: List[SignalSensitizer]  # 2-3 triggers

    # 5. Data Completeness (agents field covers this)

    # 6. Confidence Disclosure
    confidence_note: str

    # 7. Methodology
    methodology_url: str

    # ── Metadata ─────────────────────────────────────────
    generated_at: datetime
    cached: bool = False

    class Config:
        from_attributes = True
