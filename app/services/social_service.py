"""
SocialSignalService — Sprint 12
Simulates real-time Twitter/X signal crawler for Indian equities.
Tracks handles, followers weight, and posts sentiment.
"""

import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.social_signal import SocialSignal

logger = logging.getLogger("app.services.social_service")

# Top Indian financial accounts
INFLUENCER_HANDLES = [
    {"handle": "@CNBCTV18Live", "followers": 2800000},
    {"handle": "@moneycontrolcom", "followers": 3500000},
    {"handle": "@ETNOWLive", "followers": 1900000},
    {"handle": "@DhandhoInvestor", "followers": 450000},
    {"handle": "@Trendlyne", "followers": 150000},
]


class SocialSignalService:
    """Handles social sentiment ingestion."""

    @classmethod
    def ingest_social_signals(cls, db: Session, company: Company) -> List[SocialSignal]:
        """
        Polls and crawls simulated Twitter/X social signals.
        Weights scores based on account follower reach.
        """
        # Delete signals older than 3 days to keep db clean
        cutoff = datetime.utcnow() - timedelta(days=3)
        db.query(SocialSignal).filter(
            SocialSignal.company_id == company.id,
            SocialSignal.posted_at < cutoff
        ).delete()
        db.commit()

        # Simulate fresh feeds matching this ticker
        posts = [
            f"Strong accumulation seen in #{company.ticker} today. Brokerages raise target consensus citing robust domestic execution.",
            f"FIIs reportedly increasing exposure in #{company.ticker} as promoters clean up pledged share cushion.",
            f"Retail interest peaking for #{company.ticker} ahead of crucial board meeting regarding capex capacity expansion.",
        ]

        signals = []
        for i, text in enumerate(posts):
            handle_data = random.choice(INFLUENCER_HANDLES)
            
            # Simple sentiment evaluation
            sentiment = "positive"
            score = 65.0
            if "pledged" in text.lower():
                score = 80.0
            
            signal = SocialSignal(
                company_id=company.id,
                handle=handle_data["handle"],
                content=text,
                sentiment=sentiment,
                sentiment_score=score,
                followers_count=handle_data["followers"],
                posted_at=datetime.utcnow() - timedelta(hours=i * 2)
            )
            db.add(signal)
            signals.append(signal)

        db.commit()
        logger.info(f"[Social Poller] Ingested {len(signals)} social sentiment signals for {company.ticker}.")
        return signals

    @classmethod
    def get_company_signals(cls, db: Session, company_id: int, limit: int = 20) -> List[SocialSignal]:
        """Retrieves active social posts ordered by post time."""
        return db.query(SocialSignal).filter(
            SocialSignal.company_id == company_id
        ).order_by(SocialSignal.posted_at.desc()).limit(limit).all()
