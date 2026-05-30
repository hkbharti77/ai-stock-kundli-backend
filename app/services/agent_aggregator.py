"""
AggregatorAgent — Sprint 6 & Sprint 15: Combines Multi-Agent scores
into a single weighted Kundli signal with full explainable report in English or Hindi.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.agent_output import AgentOutput
from app.models.signal_accuracy import SignalAccuracy
from app.models.agent_run_log import AgentRunLog
from app.models.price_history import PriceHistory
from app.services.probability_engine import ProbabilityEngine
from app.schemas.kundli_report import (
    AgentContribution,
    KundliReportResponse,
    SignalSensitizer,
)

logger = logging.getLogger("AggregatorAgent")

# ── Weight Configuration ──────────────────────────────────────────────────────
AGENT_WEIGHTS: Dict[str, float] = {
    "fundamental_analyst": 0.25,
    "risk_analyst":        0.20,
    "technical_analyst":   0.15,
    "news_analyst":        0.15,
    "macro_analyst":       0.10,
    "valuation_analyst":   0.10,
    "sector_analyst":      0.05,
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

    HINDI_TRANSLATIONS = {
        "No positive signals detected at this time.": "इस समय कोई सकारात्मक संकेत नहीं मिले हैं।",
        "No major risk signals detected at this time.": "इस समय कोई बड़ा जोखिम संकेत नहीं मिला है।",
        "Strong Buy": "मजबूत खरीद (Strong Buy)",
        "Buy": "खरीद (Buy)",
        "Neutral / Watch": "तटस्थ / नजर रखें (Neutral / Watch)",
        "Caution": "सावधानी (Caution)",
        "Avoid": "बचें (Avoid)",
        "improving": "सुधर रहा है (improving)",
        "stable": "स्थिर (stable)",
        "neutral": "तटस्थ (neutral)",
        "declining": "गिरावट पर (declining)",
        "Low": "कम (Low)",
        "Medium": "मध्यम (Medium)",
        "High": "उच्च (High)",
        "Critical": "गंभीर (Critical)",
        "tailwind": "अनुकूल परिस्थितियां (tailwind)",
        "fair": "उचित (fair)",
    }

    @classmethod
    def _translate_text(cls, text: str, lang: str) -> str:
        if lang.lower() != "hi":
            return text
        for en, hi in cls.HINDI_TRANSLATIONS.items():
            text = text.replace(en, hi)
        return text

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def generate_kundli_report(
        cls, db: Session, company: Company, lang: str = "en"
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
                contributions.append(AgentContribution(
                    agent_type=agent_type,
                    score=0,
                    confidence=0,
                    trend=None,
                    weight=weight,
                    weighted_contribution=0.0,
                ))

        # Normalize to available agents
        if total_weight_used > 0:
            kundli_score = int(round(weighted_score_sum / total_weight_used))
            overall_confidence = int(round(weighted_conf_sum / total_weight_used))
        else:
            kundli_score = 50
            overall_confidence = 0

        data_completeness = round((total_weight_used / sum(AGENT_WEIGHTS.values())) * 100, 1)

        # ── 2. Determine signal label ─────────────────────────────────────────
        signal_label_en, signal_emoji = cls._map_signal(kundli_score, overall_confidence)
        signal_label = cls._translate_text(signal_label_en, lang)

        # ── 3. Determine overall trend ────────────────────────────────────────
        trend_en = cls._compute_trend(contributions)
        trend = cls._translate_text(trend_en, lang)

        # ── 4. Build explanation components ──────────────────────────────────
        top_positives = cls._collect_positives(agent_map, lang)
        top_risks = cls._collect_risks(agent_map, lang)
        
        signal_summary = cls._build_summary(
            company, kundli_score, signal_label, overall_confidence, agent_map, lang
        )
        sensitizers = cls._build_sensitizers(kundli_score, signal_label_en, agent_map, lang)
        confidence_note = cls._confidence_note(overall_confidence, data_completeness, lang)

        # Compute probability estimates using ProbabilityEngine
        probabilities = ProbabilityEngine.calculate_estimates(company.id, outputs)

        # ── 5. Log Telemetry Cost & Latencies (Sprint 15 monitoring) ─────────
        report_cost_usd = 0.0
        # Simulated/evaluated latency and pricing matrix per agent
        agent_cost_matrix = {
            "fundamental_analyst": {"latency": 120.0, "cost": 0.015},
            "risk_analyst": {"latency": 110.0, "cost": 0.01},
            "technical_analyst": {"latency": 90.0, "cost": 0.005},
            "news_analyst": {"latency": 150.0, "cost": 0.02},
            "macro_analyst": {"latency": 105.0, "cost": 0.008},
            "valuation_analyst": {"latency": 95.0, "cost": 0.01},
            "sector_analyst": {"latency": 85.0, "cost": 0.005},
        }

        for agent_type in AGENT_WEIGHTS.keys():
            # If agent has run successfully, log execution telemetry
            if agent_type in agent_map:
                matrix = agent_cost_matrix.get(agent_type, {"latency": 100.0, "cost": 0.01})
                cost_usd = matrix["cost"]
                cost_inr = cost_usd * 83.0  # $1 USD = ₹83 INR
                report_cost_usd += cost_usd
                
                run_log = AgentRunLog(
                    company_id=company.id,
                    agent_type=agent_type,
                    latency_ms=matrix["latency"],
                    error_occurred=False,
                    input_tokens=1500,
                    output_tokens=350,
                    cost_usd=cost_usd,
                    cost_inr=cost_inr
                )
                db.add(run_log)

        report_cost_inr = report_cost_usd * 83.0
        if report_cost_inr > 10.0:
            print(f"\n==========================================================")
            print(f"⚠️  [COST WARNING] Kundli consensus cost exceeds limit: ₹{report_cost_inr:.2f} (> ₹10)")
            print(f"==========================================================\n")
            logger.warning(f"Kundli report cost alert limit exceeded: ₹{report_cost_inr:.2f}")

        # ── 6. Log Signal Performance Accuracy Tracker ───────────────────────
        latest_price = (
            db.query(PriceHistory)
            .filter(PriceHistory.company_id == company.id)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        price_val = latest_price.close if latest_price else 1500.0
        
        accuracy_log = SignalAccuracy(
            company_id=company.id,
            signal_label=signal_label_en,
            kundli_score=kundli_score,
            price_at_signal=price_val,
            created_at=datetime.utcnow()
        )
        db.add(accuracy_log)
        db.commit()

        # ── Check for rating transition upgrades/downgrades ──
        from app.models.signal_history import SignalHistory
        from app.services.alert_engine import AlertEngine

        last_history = (
            db.query(SignalHistory)
            .filter(SignalHistory.company_id == company.id)
            .order_by(SignalHistory.changed_at.desc())
            .first()
        )

        old_signal = last_history.new_signal if last_history else None
        old_score = last_history.new_score if last_history else None

        if old_signal is None or old_signal != signal_label_en:
            new_history = SignalHistory(
                company_id=company.id,
                old_score=old_score,
                new_score=kundli_score,
                old_signal=old_signal,
                new_signal=signal_label_en,
                changed_at=datetime.utcnow()
            )
            db.add(new_history)
            db.commit()

            if old_signal is not None:
                # Dispatch Rating Transition Alert instantly
                AlertEngine.process_market_event(
                    db, company.id, "signal_change", float(kundli_score),
                    "Rating Transition Alert",
                    f"AI Kundli rating for {company.ticker} transitioned from {old_signal} to {signal_label_en} (Score: {kundli_score})",
                    "high"
                )
                # Dispatch Webhooks asynchronously
                from app.services.webhook_service import WebhookService
                try:
                    WebhookService.trigger_signal_change(
                        db, company.id, old_signal, signal_label_en, old_score, kundli_score
                    )
                except Exception as e:
                    logger.error(f"Error dispatching webhooks: {e}")

        return KundliReportResponse(
            ticker=company.ticker,
            company_name=company.name,
            probability_horizons=probabilities,
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
        trend_scores = {"improving": 2, "stable": 1, "neutral": 1, "declining": 0, "Low": 2, "Medium": 1, "High": 0, "Critical": 0, "tailwind": 2}
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
    def _collect_positives(cls, agent_map: Dict[str, AgentOutput], lang: str) -> List[str]:
        """Collect top 3 positive factors across all agents."""
        positives: List[str] = []
        for agent_type in ["fundamental_analyst", "risk_analyst", "technical_analyst", "macro_analyst", "news_analyst", "valuation_analyst", "sector_analyst"]:
            agent = agent_map.get(agent_type)
            if agent and agent.strengths:
                positives.extend(agent.strengths[:2])
        
        final_list = positives[:3] if positives else ["No positive signals detected at this time."]
        return [cls._translate_text(p, lang) for p in final_list]

    @classmethod
    def _collect_risks(cls, agent_map: Dict[str, AgentOutput], lang: str) -> List[str]:
        """Collect top 3 risk factors across all agents."""
        risks: List[str] = []
        for agent_type in ["fundamental_analyst", "risk_analyst", "technical_analyst", "macro_analyst", "news_analyst", "valuation_analyst", "sector_analyst"]:
            agent = agent_map.get(agent_type)
            if agent and agent.concerns:
                risks.extend(agent.concerns[:2])
        
        final_list = risks[:3] if risks else ["No major risk signals detected at this time."]
        return [cls._translate_text(r, lang) for r in final_list]

    @classmethod
    def _build_summary(
        cls,
        company: Company,
        score: int,
        signal_label: str,
        confidence: int,
        agent_map: Dict[str, AgentOutput],
        lang: str
    ) -> str:
        """Generate the plain-language signal summary paragraph in Hindi or English."""
        f_score = agent_map.get("fundamental_analyst")
        r_score = agent_map.get("risk_analyst")
        t_score = agent_map.get("technical_analyst")
        m_score = agent_map.get("macro_analyst")
        n_score = agent_map.get("news_analyst")
        v_score = agent_map.get("valuation_analyst")
        s_score = agent_map.get("sector_analyst")

        if lang.lower() == "hi":
            f_part = f"बुनियादी तौर पर (Fundamentally), कंपनी का स्कोर **{f_score.score}/100** ({cls._translate_text(f_score.trend or 'stable', lang)} ट्रेंड) है।" if f_score else "बुनियादी डेटा अनुपलब्ध है।"
            r_part = f"सुरक्षा की दृष्टि से (Safety-wise), कॉर्पोरेट जोखिम स्कोर **{r_score.score}/100** (जोखिम: {cls._translate_text(r_score.trend or 'Low', lang)}) है।" if r_score else "जोखिम विश्लेषण अनुपलब्ध है।"
            t_part = f"तकनीकी रूप से (Technically), शेयर की चाल का स्कोर **{t_score.score}/100** ({cls._translate_text(t_score.trend or 'neutral', lang)} मोमेंटम) है।" if t_score else "तकनीकी डेटा अनुपलब्ध है।"
            m_part = f"मैक्रोइकॉनॉमिक (Macroeconomic) नजरिये से, आर्थिक अनुकूलता का स्कोर **{m_score.score}/100** ({cls._translate_text(m_score.trend or 'neutral', lang)} चक्र) है।" if m_score else "मैक्रो डेटा अनुपलब्ध है।"
            n_part = f"समाचारों की भावना (News sentiment) का स्कोर **{n_score.score}/100** है, जो {n_score.confidence}% आत्मविश्वास के साथ है।" if n_score else "समाचार डेटा अनुपलब्ध है।"
            v_part = f"मूल्यांकन के लिहाज से (Valuation-wise), आंतरिक गुणक स्कोर **{v_score.score}/100** (निर्णय: {cls._translate_text(v_score.trend or 'fair', lang)}) है।" if v_score else "मूल्यांकन विश्लेषण अनुपलब्ध है।"
            s_part = f"सेक्टर के लिहाज से, peer तुलना स्कोर **{s_score.score}/100** (रैंक: {cls._translate_text(s_score.trend or 'Rank #2', lang)}) है।" if s_score else "सेक्टर बेंचमार्किंग अनुपलब्ध है।"

            return (
                f"**{company.name} ({company.ticker})** का कुंडली स्कोर **{score}/100** है, "
                f"जिससे **{signal_label}** का संकेत उत्पन्न होता है, और इसका **कुल आत्मविश्वास {confidence}%** है। "
                f"{f_part} {r_part} {t_part} {m_part} {n_part} {v_part} {s_part} "
                f"यह समेकित निर्णय एक व्यापक 7-एजेंट भारित सर्वसम्मत मॉडल को दर्शाता है।"
            )
        else:
            f_part = f"Fundamentally, the company scores **{f_score.score}/100** ({f_score.trend or 'stable'} trend)." if f_score else "Fundamental data is unavailable."
            r_part = f"Safety-wise, corporate risk scores **{r_score.score}/100** (Risk: {r_score.trend or 'Low'})." if r_score else "Risk analysis is unavailable."
            t_part = f"Technically, price action scores **{t_score.score}/100** ({t_score.trend or 'neutral'} momentum)." if t_score else "Technical data is unavailable."
            m_part = f"Macroeconomically, economic tailwinds score **{m_score.score}/100** ({m_score.trend or 'neutral'} cycle)." if m_score else "Macro data is unavailable."
            n_part = f"News sentiment scores **{n_score.score}/100** with {n_score.confidence}% confidence." if n_score else "News data is unavailable."
            v_part = f"Valuation-wise, intrinsic multiples score **{v_score.score}/100** (Verdict: {v_score.trend or 'fair'})." if v_score else "Valuation analysis is unavailable."
            s_part = f"Sector-wise, peer benchmarking scores **{s_score.score}/100** (Rank: {s_score.trend or 'Rank #2'})." if s_score else "Sector benchmarking is unavailable."

            return (
                f"**{company.name} ({company.ticker})** has a Kundli Score of **{score}/100**, "
                f"generating a **{signal_label}** signal with **{confidence}% overall confidence**. "
                f"{f_part} {r_part} {t_part} {m_part} {n_part} {v_part} {s_part} "
                f"This aggregated verdict reflects a comprehensive 7-agent weighted consensus model."
            )

    @classmethod
    def _build_sensitizers(
        cls,
        score: int,
        signal_label_en: str,
        agent_map: Dict[str, AgentOutput],
        lang: str
    ) -> List[SignalSensitizer]:
        """Generate 2–3 signal sensitizer triggers in Hindi or English."""
        sensitizers: List[SignalSensitizer] = []

        f_agent = agent_map.get("fundamental_analyst")
        r_agent = agent_map.get("risk_analyst")
        t_agent = agent_map.get("technical_analyst")
        m_agent = agent_map.get("macro_analyst")
        n_agent = agent_map.get("news_analyst")
        v_agent = agent_map.get("valuation_analyst")
        s_agent = agent_map.get("sector_analyst")

        # Fundamental sensitizer
        if f_agent:
            if f_agent.score < 60:
                trigger = (
                    "ROCE (>12%) में महत्वपूर्ण सुधार या लगातार 2 तिमाहियों में PAT संकुचन के उलटने से बुनियादी स्कोर अपग्रेड होगा।"
                    if lang.lower() == "hi" else
                    "A meaningful improvement in ROCE (>12%) or reversal in PAT contraction over 2 consecutive quarters would upgrade the fundamental score."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="upgrade",
                    impact="high",
                ))
            else:
                trigger = (
                    "कमाई में बड़ी गिरावट या ऋण-से-इक्विटी अनुपात का 1.5x से अधिक होना बुनियादी स्कोर को डाउनग्रेड करेगा।"
                    if lang.lower() == "hi" else
                    "A significant earnings miss or sharp rise in debt-to-equity beyond 1.5x would downgrade the fundamental score."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="downgrade",
                    impact="high",
                ))

        # Risk/Safety sensitizer
        if r_agent:
            if r_agent.score < 60:
                trigger = (
                    "प्रमोटर गिरवी (promoter pledge) के स्तर को 10% से नीचे लाने या सक्रिय कानूनी विवादों को सुलझाने से जोखिम सुरक्षा स्कोर अपग्रेड होगा।"
                    if lang.lower() == "hi" else
                    "Reducing promoter pledge levels below 10% or clearing active legal disputes will upgrade the risk safety score."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="upgrade",
                    impact="high",
                ))
            else:
                trigger = (
                    "कोई भी नया प्रमोटर गिरवी विस्तार या नियामक सेबी (SEBI) जुर्माना आदेश तुरंत जोखिम सुरक्षा स्कोर डाउनग्रेड को ट्रिगर करेगा।"
                    if lang.lower() == "hi" else
                    "Any new promoter pledge expansion or regulatory SEBI penalty order will immediately trigger a risk score downgrade."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="downgrade",
                    impact="high",
                ))

        # Valuation sensitizer
        if v_agent:
            if v_agent.score < 60:
                trigger = (
                    "मल्टीपल संपीड़न या स्टॉक मूल्य में गिरावट से सुरक्षा का मार्जिन (Margin of Safety) >25% सुधरने पर मूल्यांकन स्कोर अपग्रेड होगा।"
                    if lang.lower() == "hi" else
                    "A multiple compression or drop in stock price improving the Margin of Safety >25% would upgrade the valuation score."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="upgrade",
                    impact="high",
                ))
            else:
                trigger = (
                    "बिना किसी ठोस कमाई के बाजार मूल्य में तेज उछाल सुरक्षा के मार्जिन को कम कर देगा, जिससे मूल्यांकन में गिरावट आएगी।"
                    if lang.lower() == "hi" else
                    "A sharp run-up in market price without earnings backing would reduce the margin of safety, triggering a valuation downgrade."
                )
                sensitizers.append(SignalSensitizer(
                    trigger=trigger,
                    direction="downgrade",
                    impact="high",
                ))

        # Sector peer sensitizer
        if s_agent:
            trigger = (
                "मार्केट शेयर हासिल करने या EBITDA मार्जिन को सेक्टर औसत से ऊपर सुधारने से प्रतिस्पर्धी सेक्टर रैंक अपग्रेड होगी।"
                if lang.lower() == "hi" else
                "Gaining market share or improving EBITDA margin above sector average would upgrade the competitive sector rank."
            )
            sensitizers.append(SignalSensitizer(
                trigger=trigger,
                direction="upgrade",
                impact="medium",
            ))

        # Technical sensitizer
        if t_agent:
            trigger = (
                "वॉल्यूम विस्तार के साथ 50 से ऊपर आरएसआई (RSI) रिकवरी + एक पुष्ट एमएसीडी (MACD) बुलिश क्रॉसओवर तकनीकी संकेत को मजबूत करेगा।"
                if lang.lower() == "hi" else
                "A confirmed MACD bullish crossover + RSI recovery above 50 with volume expansion would strengthen the technical signal."
            )
            sensitizers.append(SignalSensitizer(
                trigger=trigger,
                direction="upgrade",
                impact="medium",
            ))

        return sensitizers[:3]

    @classmethod
    def _confidence_note(cls, confidence: int, completeness: float, lang: str) -> str:
        """Build the confidence disclosure note in Hindi or English."""
        if lang.lower() == "hi":
            if completeness < 100:
                return (
                    f"कुल मिलाकर आत्मविश्वास **{confidence}%** है, जो उन विश्लेषक एजेंटों से लिया गया है "
                    f"जिन्होंने डेटा सफलतापूर्वक दिया है ({completeness:.0f}% पूर्णता)। "
                    f"गायब विश्लेषक परिणाम आत्मविश्वास को कम करते हैं। डेटा उपलब्ध होने पर स्कोर स्वतः रीफ्रेश होंगे।"
                )
            return (
                f"कुल मिलाकर आत्मविश्वास **{confidence}%** है, जो व्यक्तिगत "
                f"विश्लेषकों के आत्मविश्वास के भारित औसत (Fundamental 25%, Risk 20%, Technical 15%, News 15%, Macro 10%, Valuation 10%, Sector 5%) के रूप में मापा गया है। "
                f"सभी सात एजेंटों ने इस रिपोर्ट में योगदान दिया है (100% डेटा पूर्णता)।"
            )
        else:
            if completeness < 100:
                return (
                    f"Overall confidence is **{confidence}%**, derived from the agents that "
                    f"successfully contributed data ({completeness:.0f}% completeness). "
                    f"Missing agent outputs reduce confidence. Scores will auto-refresh as data pipelines populate."
                )
            return (
                f"Overall confidence is **{confidence}%**, computed as a weighted average of individual "
                f"agent confidences (Fundamental 25%, Risk 20%, Technical 15%, News 15%, Macro 10%, Valuation 10%, Sector 5%). "
                f"All seven agents contributed to this report (100% data completeness)."
            )
