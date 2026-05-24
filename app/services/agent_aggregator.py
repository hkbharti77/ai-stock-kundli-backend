"""
AggregatorAgent — Sprint 6: Combines Fundamental, Technical, and News agent scores
into a single weighted Kundli signal with full explainable report.

Weight distribution:
    Fundamental Analyst  : 55%  (financial health is the primary driver)
    Technical Analyst    : 25%  (market momentum and trend confirmation)
    News Analyst         : 20%  (sentiment and event risk)

Signal Labels (5 tiers):
    ≥ 80 + confidence ≥ 70%  →  Strong Buy
    65–79 + confidence ≥ 60% →  Buy
    45–64                    →  Neutral / Watch
    30–44                    →  Caution
    < 30                     →  Avoid
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.agent_output import AgentOutput
from app.schemas.kundli_report import (
    AgentContribution,
    KundliReportResponse,
    SignalSensitizer,
)

logger = logging.getLogger("AggregatorAgent")

# ── Weight Configuration ──────────────────────────────────────────────────────
AGENT_WEIGHTS: Dict[str, float] = {
    "fundamental_analyst": 0.55,
    "technical_analyst":   0.25,
    "news_analyst":        0.20,
}

SIGNAL_TIERS = [
    (80, 70, "Strong Buy",    "🟢"),
    (65, 60, "Buy",           "🔵"),
    (45,  0, "Neutral / Watch", "🟡"),
    (30,  0, "Caution",       "🟠"),
    (0,   0, "Avoid",         "🔴"),
]


class AggregatorAgent:
    """Combines multi-agent outputs into the final Kundli signal + report."""

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def generate_kundli_report(
        cls, db: Session, company: Company
    ) -> KundliReportResponse:
        """
        Main entry point. Loads agent outputs, computes weighted score,
        and returns the full KundliReportResponse with all 7 explanation components.
        """
        outputs: List[AgentOutput] = (
            db.query(AgentOutput)
            .filter(AgentOutput.company_id == company.id)
            .all()
        )

        # Index by agent type
        agent_map: Dict[str, AgentOutput] = {o.agent_type: o for o in outputs}

        # ── 1. Compute weighted score ─────────────────────────────────────────
        contributions: List[AgentContribution] = []
        total_weight_used = 0.0
        weighted_score_sum = 0.0
        weighted_conf_sum = 0.0

        for agent_type, weight in AGENT_WEIGHTS.items():
            agent_out = agent_map.get(agent_type)
            if agent_out:
                contribution = weight * agent_out.score
                contributions.append(AgentContribution(
                    agent_type=agent_type,
                    score=agent_out.score,
                    confidence=agent_out.confidence,
                    trend=agent_out.trend,
                    weight=weight,
                    weighted_contribution=round(contribution, 2),
                ))
                weighted_score_sum += contribution
                weighted_conf_sum += weight * agent_out.confidence
                total_weight_used += weight
            else:
                # Agent missing — record zero contribution
                contributions.append(AgentContribution(
                    agent_type=agent_type,
                    score=0,
                    confidence=0,
                    trend=None,
                    weight=weight,
                    weighted_contribution=0.0,
                ))

        # Normalize to available agents (so partial data still gives a fair score)
        if total_weight_used > 0:
            kundli_score = int(round(weighted_score_sum / total_weight_used))
            overall_confidence = int(round(weighted_conf_sum / total_weight_used))
        else:
            kundli_score = 50
            overall_confidence = 0

        data_completeness = round((total_weight_used / sum(AGENT_WEIGHTS.values())) * 100, 1)

        # ── 2. Determine signal label ─────────────────────────────────────────
        signal_label, signal_emoji = cls._map_signal(kundli_score, overall_confidence)

        # ── 3. Determine overall trend ────────────────────────────────────────
        trend = cls._compute_trend(contributions)

        # ── 4. Build explanation components ──────────────────────────────────
        top_positives = cls._collect_positives(agent_map)
        top_risks = cls._collect_risks(agent_map)
        signal_summary = cls._build_summary(
            company, kundli_score, signal_label, overall_confidence, agent_map
        )
        sensitizers = cls._build_sensitizers(kundli_score, signal_label, agent_map)
        confidence_note = cls._confidence_note(overall_confidence, data_completeness)

        return KundliReportResponse(
            ticker=company.ticker,
            company_name=company.name,
            kundli_score=kundli_score,
            signal_label=signal_label,
            signal_emoji=signal_emoji,
            overall_confidence=overall_confidence,
            trend=trend,
            agents=contributions,
            data_completeness=data_completeness,
            signal_summary=signal_summary,
            top_positives=top_positives,
            top_risks=top_risks,
            sensitizers=sensitizers,
            confidence_note=confidence_note,
            methodology_url="/methodology",
            generated_at=datetime.utcnow(),
            cached=False,
        )

    # ── Private Helpers ───────────────────────────────────────────────────────

    @classmethod
    def _map_signal(cls, score: int, confidence: int) -> tuple[str, str]:
        """Map (score, confidence) → (signal_label, emoji)."""
        if score >= 80 and confidence >= 70:
            return "Strong Buy", "🟢"
        if score >= 65 and confidence >= 60:
            return "Buy", "🔵"
        if score >= 45:
            return "Neutral / Watch", "🟡"
        if score >= 30:
            return "Caution", "🟠"
        return "Avoid", "🔴"

    @classmethod
    def _compute_trend(cls, contributions: List[AgentContribution]) -> str:
        """Derive overall trend from constituent agent trends."""
        trend_scores = {"improving": 2, "stable": 1, "neutral": 1, "declining": 0}
        values = [
            trend_scores.get(c.trend or "stable", 1)
            for c in contributions if c.score > 0
        ]
        if not values:
            return "stable"
        avg = sum(values) / len(values)
        if avg >= 1.5:
            return "improving"
        if avg >= 0.8:
            return "stable"
        return "declining"

    @classmethod
    def _collect_positives(cls, agent_map: Dict[str, AgentOutput]) -> List[str]:
        """Collect top 3 positive factors across all agents."""
        positives: List[str] = []
        for agent_type in ["fundamental_analyst", "technical_analyst", "news_analyst"]:
            agent = agent_map.get(agent_type)
            if agent and agent.strengths:
                positives.extend(agent.strengths[:2])
        return positives[:3] if positives else ["No positive signals detected at this time."]

    @classmethod
    def _collect_risks(cls, agent_map: Dict[str, AgentOutput]) -> List[str]:
        """Collect top 3 risk factors across all agents."""
        risks: List[str] = []
        for agent_type in ["fundamental_analyst", "technical_analyst", "news_analyst"]:
            agent = agent_map.get(agent_type)
            if agent and agent.concerns:
                risks.extend(agent.concerns[:2])
        return risks[:3] if risks else ["No major risk signals detected at this time."]

    @classmethod
    def _build_summary(
        cls,
        company: Company,
        score: int,
        signal_label: str,
        confidence: int,
        agent_map: Dict[str, AgentOutput],
    ) -> str:
        """Generate the plain-language signal summary paragraph."""
        f_score = agent_map.get("fundamental_analyst")
        t_score = agent_map.get("technical_analyst")
        n_score = agent_map.get("news_analyst")

        f_part = f"Fundamentally, the company scores **{f_score.score}/100** ({f_score.trend or 'stable'} trend)." if f_score else "Fundamental data is unavailable."
        t_part = f"Technically, price action scores **{t_score.score}/100** ({t_score.trend or 'neutral'} momentum)." if t_score else "Technical data is unavailable."
        n_part = f"News sentiment scores **{n_score.score}/100** with {n_score.confidence}% confidence." if n_score else "News data is unavailable."

        return (
            f"**{company.name} ({company.ticker})** has a Kundli Score of **{score}/100**, "
            f"generating a **{signal_label}** signal with **{confidence}% overall confidence**. "
            f"{f_part} {t_part} {n_part} "
            f"This aggregated verdict reflects a multi-dimensional analysis across fundamentals, "
            f"price momentum, and market sentiment."
        )

    @classmethod
    def _build_sensitizers(
        cls,
        score: int,
        signal_label: str,
        agent_map: Dict[str, AgentOutput],
    ) -> List[SignalSensitizer]:
        """Generate 2–3 signal sensitizer triggers."""
        sensitizers: List[SignalSensitizer] = []

        f_agent = agent_map.get("fundamental_analyst")
        t_agent = agent_map.get("technical_analyst")
        n_agent = agent_map.get("news_analyst")

        # Fundamental sensitizer
        if f_agent:
            if f_agent.score < 60:
                sensitizers.append(SignalSensitizer(
                    trigger="A meaningful improvement in ROCE (>12%) or reversal in PAT contraction over 2 consecutive quarters would upgrade the fundamental score.",
                    direction="upgrade",
                    impact="high",
                ))
            else:
                sensitizers.append(SignalSensitizer(
                    trigger="A significant earnings miss or sharp rise in debt-to-equity beyond 1.5x would downgrade the fundamental score.",
                    direction="downgrade",
                    impact="high",
                ))

        # Technical sensitizer
        if t_agent:
            sensitizers.append(SignalSensitizer(
                trigger="A confirmed MACD bullish crossover + RSI recovery above 50 with volume expansion would strengthen the technical signal.",
                direction="upgrade",
                impact="medium",
            ))

        # News sensitizer
        if n_agent:
            if n_agent.score < 60:
                sensitizers.append(SignalSensitizer(
                    trigger="A stream of positive fundamental or management guidance news over 7+ days would materially improve the news sentiment score.",
                    direction="upgrade",
                    impact="medium",
                ))
            else:
                sensitizers.append(SignalSensitizer(
                    trigger="Detection of regulatory action (SEBI notice, GST inquiry) or promoter pledge escalation would immediately downgrade the news signal.",
                    direction="downgrade",
                    impact="high",
                ))

        return sensitizers[:3]

    @classmethod
    def _confidence_note(cls, confidence: int, completeness: float) -> str:
        """Build the confidence disclosure note."""
        if completeness < 100:
            return (
                f"Overall confidence is **{confidence}%**, derived from the agents that "
                f"successfully contributed data ({completeness:.0f}% completeness). "
                f"Missing agent outputs reduce confidence. Scores will auto-refresh as data pipelines populate."
            )
        return (
            f"Overall confidence is **{confidence}%**, computed as a weighted average of individual "
            f"agent confidences (Fundamental 55%, Technical 25%, News 20%). "
            f"All three agents contributed to this report (100% data completeness)."
        )
