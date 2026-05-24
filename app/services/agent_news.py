"""
NewsAnalystAgent — Sprint 5 AI agent that analyzes classified news for a company,
produces a structured investment sentiment score, top material events, and risk flags.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.agent_output import AgentOutput
from app.models.news_article import NewsArticle
from app.services.news import NewsService

logger = logging.getLogger("app.services.agent_news")


class NewsAnalystAgent:
    """
    Third AI Agent — News Intelligence.
    Orchestrates news ingestion, LLM-powered analysis, and persists output
    into agent_outputs table with agent_type='news_analyst'.
    """

    @classmethod
    async def analyze_company(cls, db: Session, company: Company) -> AgentOutput:
        """
        Main entry point.
        1. Ensure news is ingested (ingest if stale or empty)
        2. Pull 30-day articles
        3. Run LLM news analysis chain
        4. Persist & return AgentOutput
        """
        logger.info(f"[NewsAnalyst] Starting analysis for {company.ticker}")

        # ── Step 1: Ensure fresh news is available ──────────────────────────
        try:
            ingestion_result = NewsService.ingest_news_for_company(db, company)
            logger.info(f"[NewsAnalyst] Ingestion result: {ingestion_result}")
        except Exception as e:
            logger.warning(f"[NewsAnalyst] News ingestion failed for {company.ticker}: {e}")

        # ── Step 2: Pull classified articles ────────────────────────────────
        articles = NewsService.get_recent_articles(db, company.id, days=30, limit=50)
        sentiment_counts = NewsService.get_sentiment_counts(db, company.id, days=30)
        article_count = len(articles)

        logger.info(
            f"[NewsAnalyst] {company.ticker}: {article_count} articles | "
            f"pos={sentiment_counts['positive']}, neg={sentiment_counts['negative']}, neu={sentiment_counts['neutral']}"
        )

        # Build articles summary for LLM
        articles_summary: List[Dict[str, Any]] = [
            {
                "title": a.title,
                "classification": a.classification,
                "impact_score": a.impact_score,
                "sentiment": a.sentiment,
                "source": a.source,
                "published_at": a.published_at.strftime("%Y-%m-%d") if a.published_at else "N/A",
                "risk_flags": a.risk_flags or [],
            }
            for a in articles
        ]

        # ── Step 3: Run analysis ─────────────────────────────────────────────
        analysis = cls._run_analysis(
            ticker=company.ticker,
            company_name=company.name,
            articles=articles,
            sentiment_counts=sentiment_counts,
            article_count=article_count,
        )

        # ── Step 4: Build sentiment trend ────────────────────────────────────
        sentiment_trend = NewsService.build_sentiment_trend(db, company.id)

        # Collect all unique risk_flags from articles
        all_risk_flags: List[str] = []
        for a in articles:
            if a.risk_flags:
                all_risk_flags.extend(a.risk_flags)
        unique_risk_flags = list(set(all_risk_flags))[:10]

        # Merge LLM-generated risk_flags with article-level ones
        llm_risk_flags = analysis.get("risk_flags") or []
        combined_risk_flags = list(set(llm_risk_flags + unique_risk_flags))[:10]

        # ── Step 5: Persist to agent_outputs ────────────────────────────────
        existing = db.query(AgentOutput).filter(
            AgentOutput.company_id == company.id,
            AgentOutput.agent_type == "news_analyst",
        ).first()

        agent_metadata = {
            "article_count_analyzed": article_count,
            "sentiment_breakdown": sentiment_counts,
            "top_material_events": analysis.get("top_material_events", [])[:5],
            "risk_flags": combined_risk_flags,
            "news_sentiment": analysis.get("news_sentiment", "neutral"),
            "sentiment_trend_30d": analysis.get("sentiment_trend_30d", "stable"),
            "sentiment_trend_data": sentiment_trend,
        }

        if existing:
            existing.score = analysis.get("score", 50)
            existing.confidence = analysis.get("confidence", 70)
            existing.trend = analysis.get("trend", "stable")
            existing.strengths = analysis.get("strengths", [])
            existing.concerns = analysis.get("concerns", [])
            existing.reasoning = analysis.get("reasoning", "")
            existing.agent_metadata = agent_metadata
            existing.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            logger.info(f"[NewsAnalyst] Updated existing report for {company.ticker} (score={existing.score})")
            return existing
        else:
            output = AgentOutput(
                company_id=company.id,
                agent_type="news_analyst",
                score=analysis.get("score", 50),
                confidence=analysis.get("confidence", 70),
                trend=analysis.get("trend", "stable"),
                strengths=analysis.get("strengths", []),
                concerns=analysis.get("concerns", []),
                reasoning=analysis.get("reasoning", ""),
                agent_metadata=agent_metadata,
            )
            db.add(output)
            db.commit()
            db.refresh(output)
            logger.info(f"[NewsAnalyst] Created new report for {company.ticker} (score={output.score})")
            return output

    @classmethod
    def _run_analysis(
        cls,
        ticker: str,
        company_name: str,
        articles: list,
        sentiment_counts: Dict[str, int],
        article_count: int,
    ) -> Dict[str, Any]:
        """Compute news analysis entirely from classified articles (no LLM needed)."""
        pos = sentiment_counts.get("positive", 0)
        neg = sentiment_counts.get("negative", 0)
        neu = sentiment_counts.get("neutral", 0)
        total = max(pos + neg + neu, 1)

        pos_ratio = pos / total
        neg_ratio = neg / total

        # Score: 50 baseline + adjusted by sentiment ratio
        score = int(50 + (pos_ratio - neg_ratio) * 50)
        score = min(max(score, 5), 95)

        if pos_ratio > 0.6:
            news_sentiment, trend = "positive", "improving"
        elif neg_ratio > 0.5:
            news_sentiment, trend = "negative", "deteriorating"
        else:
            news_sentiment, trend = "neutral", "stable"

        # Top 5 material events (highest impact_score)
        sorted_arts = sorted(
            [a for a in articles if isinstance(a.impact_score, int)],
            key=lambda x: x.impact_score, reverse=True
        )[:5]
        top_events = [
            {
                "headline": a.title,
                "classification": a.classification,
                "impact_score": a.impact_score,
                "date": a.published_at.strftime("%Y-%m-%d") if a.published_at else "N/A",
                "source": a.source,
            }
            for a in sorted_arts
        ]

        # Risk flags
        risk_flags = list(set(
            flag
            for a in articles
            for flag in (a.risk_flags or [])
        ))[:5]

        strengths = []
        concerns = []
        if pos > 0:
            strengths.append(f"{pos} positive news articles detected in the last 30 days.")
        if pos_ratio > 0.4:
            strengths.append(f"Media and analyst coverage of {company_name} is predominantly optimistic.")
        if neg > 0:
            concerns.append(f"{neg} negative articles may exert short-term selling pressure.")
        if risk_flags:
            concerns.append("Critical risk events detected — regulatory or governance concerns need attention.")
        if not strengths:
            strengths.append(f"News flow for {company_name} is relatively balanced with no major red flags.")
        if not concerns:
            concerns.append("Continue monitoring news pipeline for emerging risks.")

        reasoning = (
            f"### **News Analyst Report: {company_name} ({ticker})**\n\n"
            f"Humne {company_name} ke liye pichle 30 din mein **{article_count} news articles** "
            f"ka comprehensive analysis kiya hai.\n\n"
            f"#### **Sentiment Overview**\n"
            f"Overall news sentiment score **{score}/100** hai — "
            f"{pos} positive, {neg} negative, {neu} neutral articles hain. "
            f"Current outlook: **{news_sentiment.upper()}** trend.\n\n"
            f"#### **Risk Assessment**\n"
            + ("\u26a0\ufe0f Critical risk flags detected — exercise caution." if risk_flags
               else "No major fraud or regulatory risk signals detected.")
        )

        return {
            "score": score,
            "confidence": min(60 + article_count * 2, 90),
            "trend": trend,
            "news_sentiment": news_sentiment,
            "sentiment_trend_30d": trend,
            "strengths": strengths[:4],
            "concerns": concerns[:4],
            "reasoning": reasoning,
            "top_material_events": top_events,
            "risk_flags": risk_flags,
        }
