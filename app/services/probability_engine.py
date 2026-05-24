from typing import List, Dict, Any
from app.models.company import Company
from app.models.agent_output import AgentOutput

class ProbabilityHorizon:
    def __init__(self, horizon: str, output: str, probability: int):
        self.horizon = horizon
        self.output = output
        self.probability = probability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon": self.horizon,
            "output": self.output,
            "probability": self.probability
        }

class ProbabilityEngine:
    @classmethod
    def calculate_estimates(cls, company_id: int, agent_outputs: List[AgentOutput]) -> List[Dict[str, Any]]:
        """
        Calibrates probabilistic forecasts for 1w, 1m, 3m, and 6m horizons.
        Merges fundamental quality, technical breakouts, and social sentiment into math estimates.
        """
        # Map agent scores
        agent_scores = {o.agent_type: o.score for o in agent_outputs}
        agent_trends = {o.agent_type: o.trend for o in agent_outputs}

        fundamental_score = agent_scores.get("fundamental_analyst", 60)
        technical_score = agent_scores.get("technical_analyst", 60)
        sentiment_score = agent_scores.get("sentiment_analyst", 55)
        risk_score = agent_scores.get("risk_analyst", 50)
        valuation_score = agent_scores.get("valuation_analyst", 50)

        # Technical trend direction factor (-5 to +5)
        tech_trend = agent_trends.get("technical_analyst", "stable")
        tech_modifier = 0
        if tech_trend == "improving" or technical_score > 70:
            tech_modifier = 6
        elif tech_trend == "declining" or technical_score < 40:
            tech_modifier = -6

        # Sentiment modifier (-5 to +5)
        sentiment_modifier = int((sentiment_score - 50) * 0.2)

        # ── 1. Horizon: 1 Week ──────────────────────────────────────────
        # Probability that price is above current levels in 1 week
        w1_prob = 50 + tech_modifier + sentiment_modifier + int((fundamental_score - 50) * 0.1)
        w1_prob = max(15, min(95, w1_prob))
        w1_output = f"Price likely above current level: {w1_prob}% probability"

        # ── 2. Horizon: 1 Month ─────────────────────────────────────────
        # Probability that price yields >= 10% upside in 1 month
        m1_base = 35
        m1_prob = m1_base + int((technical_score - 50) * 0.3) + int((fundamental_score - 50) * 0.2) + sentiment_modifier
        m1_prob = max(10, min(85, m1_prob))
        m1_output = f"Upside potential >= 10%: {m1_prob}% probability"

        # ── 3. Horizon: 3 Months ────────────────────────────────────────
        # Probability that fundamental thesis remains intact
        # Governed by fundamental stability and sector tailwinds
        m3_prob = int(fundamental_score * 0.7 + (100 - risk_score) * 0.2)
        m3_prob = max(20, min(98, m3_prob))
        m3_output = f"Fundamental thesis intact: {m3_prob}% probability"

        # ── 4. Horizon: 6 Months ────────────────────────────────────────
        # Probability of a major (20%+) drawdown/correction
        # Increased by high risk score or excessive valuation multiples
        m6_base = 25
        val_risk = int((100 - valuation_score) * 0.3) if valuation_score < 50 else -int((valuation_score - 50) * 0.1)
        m6_prob = m6_base + int(risk_score * 0.4) + val_risk - int(fundamental_score * 0.2)
        m6_prob = max(5, min(90, m6_prob))
        m6_output = f"Risk of 20%+ drawdown: {m6_prob}% probability"

        return [
            ProbabilityHorizon("1 week", w1_output, w1_prob).to_dict(),
            ProbabilityHorizon("1 month", m1_output, m1_prob).to_dict(),
            ProbabilityHorizon("3 months", m3_output, m3_prob).to_dict(),
            ProbabilityHorizon("6 months", m6_output, m6_prob).to_dict()
        ]
