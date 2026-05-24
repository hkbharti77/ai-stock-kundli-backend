"""
NewsService — Handles news ingestion, classification, and risk flag detection for Sprint 5.
Pulls news from yfinance (Yahoo Finance aggregation), classifies via LLM + simulation fallback.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List
import yfinance as yf
from sqlalchemy.orm import Session

from app.models.news_article import NewsArticle
from app.models.company import Company

logger = logging.getLogger("app.services.news")


class NewsService:
    """Handles news ingestion and LLM-powered classification for Indian equities."""

    # ────────────────────────────────────────────────────
    # Core Ingestion
    # ────────────────────────────────────────────────────

    @classmethod
    def _resolve_yf_ticker(cls, company: Company) -> str:
        """
        Resolve the correct yfinance ticker symbol based on the company's exchange.
        - NSE stocks:    TICKER.NS
        - BSE stocks:    TICKER.BO
        - Global stocks: TICKER (bare, e.g. MSFT, GOOGL)
        """
        exchange = (company.exchange or "").upper()
        if exchange in ("NSE", "NSE/BSE", "NSE BSE"):
            return f"{company.ticker}.NS"
        elif exchange == "BSE":
            return f"{company.ticker}.BO"
        else:
            # NASDAQ, NYSE, GLOBAL, etc. — use bare ticker
            return company.ticker

    @classmethod
    def ingest_news_for_company(cls, db: Session, company: Company) -> Dict[str, Any]:
        """
        Fetch and classify news articles for a single company.
        Uses yfinance as the primary aggregation source (pulls ET, Mint, BSE etc.).
        Supports NSE (.NS), BSE (.BO), and global equities (bare ticker).
        """
        yf_symbol = cls._resolve_yf_ticker(company)
        logger.info(f"Ingesting news for {company.ticker} (yfinance symbol: {yf_symbol})...")

        try:
            yf_ticker = yf.Ticker(yf_symbol)
            articles = yf_ticker.news or []
        except Exception as e:
            logger.warning(f"yfinance news fetch failed for {company.ticker}: {e}")
            articles = []

        if not articles:
            logger.info(f"No news articles found for {company.ticker}")
            return {"status": "no_news", "ingested": 0, "ticker": company.ticker}

        ingested = 0
        skipped = 0

        for article in articles:
            try:
                content_obj = article.get("content") or {}
                # Handle both the old v1 and new v2 yfinance API structures
                if isinstance(content_obj, dict):
                    title = (
                        content_obj.get("title")
                        or article.get("title")
                        or ""
                    )
                    # Try to extract publication time
                    pub_time_raw = (
                        content_obj.get("pubDate")
                        or content_obj.get("displayTime")
                        or article.get("providerPublishTime")
                    )
                    url = (
                        (content_obj.get("canonicalUrl") or {}).get("url")
                        or (content_obj.get("clickThroughUrl") or {}).get("url")
                        or article.get("link")
                        or ""
                    )
                    source = (
                        (content_obj.get("provider") or {}).get("displayName")
                        or article.get("publisher")
                        or "Yahoo Finance"
                    )
                    # Summary text for classification
                    content_text = content_obj.get("summary") or ""
                else:
                    title = article.get("title", "")
                    pub_time_raw = article.get("providerPublishTime")
                    url = article.get("link", "")
                    source = article.get("publisher", "Yahoo Finance")
                    content_text = ""

                if not title:
                    continue

                # Parse published_at
                published_at = cls._parse_published_at(pub_time_raw)

                # Skip duplicate articles (same URL or same title in last 2 days)
                cutoff = datetime.utcnow() - timedelta(days=2)
                if url:
                    existing_by_url = db.query(NewsArticle).filter(
                        NewsArticle.company_id == company.id,
                        NewsArticle.url == url,
                    ).first()
                    if existing_by_url:
                        skipped += 1
                        continue

                existing_by_title = db.query(NewsArticle).filter(
                    NewsArticle.company_id == company.id,
                    NewsArticle.title == title,
                    NewsArticle.published_at >= cutoff,
                ).first()
                if existing_by_title:
                    skipped += 1
                    continue

                # Classify synchronously via keyword simulation engine
                classification_result = cls._classify_article(title, content_text)

                news_record = NewsArticle(
                    company_id=company.id,
                    title=title,
                    content=content_text[:2000] if content_text else None,
                    source=source,
                    url=url,
                    published_at=published_at,
                    classification=classification_result.get("classification", "Neutral — Informational"),
                    impact_score=classification_result.get("impact_score", 2),
                    sentiment=classification_result.get("sentiment", "neutral"),
                    risk_flags=classification_result.get("risk_flags", []),
                )
                db.add(news_record)
                ingested += 1

            except Exception as e:
                logger.warning(f"Failed to process article '{article.get('title', 'N/A')}': {e}")
                continue

        db.commit()
        logger.info(f"News ingestion complete for {company.ticker}: {ingested} ingested, {skipped} skipped.")
        return {
            "status": "success",
            "ticker": company.ticker,
            "ingested": ingested,
            "skipped": skipped,
        }

    @classmethod
    def ingest_news_for_top_companies(
        cls, db: Session, limit: int = 50
    ) -> Dict[str, Any]:
        """
        Ingest news for the top N companies by market cap.
        Called every 15 minutes by Celery Beat.
        """
        companies = (
            db.query(Company)
            .filter(Company.is_active == True)
            .order_by(Company.market_cap.desc().nullslast())
            .limit(limit)
            .all()
        )
        logger.info(f"Starting batch news ingestion for {len(companies)} companies...")

        total_ingested = 0
        results = []
        for comp in companies:
            try:
                result = cls.ingest_news_for_company(db, comp)
                total_ingested += result.get("ingested", 0)
                results.append(result)
                time.sleep(0.3)  # Respect yfinance rate limits
            except Exception as e:
                logger.error(f"Failed to ingest news for {comp.ticker}: {e}")

        return {
            "status": "success",
            "companies_processed": len(companies),
            "total_articles_ingested": total_ingested,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ────────────────────────────────────────────────────
    # SEBI Regulatory Scraper
    # ────────────────────────────────────────────────────

    @classmethod
    def scrape_sebi_orders(cls, db: Session) -> Dict[str, Any]:
        """
        Daily scraper for SEBI circulars and orders.
        Uses SEBI RSS feed with httpx fallback.
        Stores as generic company_id=None (market-level) events.
        """
        import httpx
        from bs4 import BeautifulSoup

        sebi_rss_url = "https://www.sebi.gov.in/rss/rss-orders.xml"
        logger.info(f"Scraping SEBI orders from {sebi_rss_url}...")

        ingested = 0
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; StockKundliBot/1.0)"}
            response = httpx.get(sebi_rss_url, timeout=15, headers=headers, follow_redirects=True)

            if response.status_code != 200:
                logger.warning(f"SEBI RSS returned status {response.status_code}")
                return {"status": "failed", "reason": f"HTTP {response.status_code}"}

            soup = BeautifulSoup(response.text, "xml")
            items = soup.find_all("item")

            for item in items[:20]:  # Process top 20 latest orders
                title = item.find("title")
                link = item.find("link")
                pub_date = item.find("pubDate")

                title_text = title.get_text(strip=True) if title else "SEBI Order"
                link_text = link.get_text(strip=True) if link else None
                pub_text = pub_date.get_text(strip=True) if pub_date else None

                published_at = cls._parse_published_at(pub_text)

                # Check for duplicate
                if link_text:
                    existing = db.query(NewsArticle).filter(
                        NewsArticle.url == link_text
                    ).first()
                    if existing:
                        continue

                # Look up the first active company to associate (or skip company_id for market-wide events)
                # For simplicity, we store SEBI orders without company affiliation
                # They'll appear in a dedicated SEBI alerts section later
                news_record = NewsArticle(
                    company_id=1,  # placeholder - will be filtered by source='SEBI'
                    title=title_text,
                    content=None,
                    source="SEBI",
                    url=link_text,
                    published_at=published_at,
                    classification="Negative — Regulatory",
                    impact_score=7,
                    sentiment="negative",
                    risk_flags=["SEBI Regulatory Action"],
                )
                db.add(news_record)
                ingested += 1

            db.commit()
            logger.info(f"SEBI orders scrape complete: {ingested} new orders ingested.")
            return {
                "status": "success",
                "ingested": ingested,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"SEBI scraper failed: {e}", exc_info=True)
            return {"status": "failed", "error": str(e)}

    # ────────────────────────────────────────────────────
    # Data Retrieval
    # ────────────────────────────────────────────────────

    @classmethod
    def get_recent_articles(
        cls,
        db: Session,
        company_id: int,
        days: int = 30,
        limit: int = 50,
    ) -> List[NewsArticle]:
        """Retrieve recent news articles for a company, newest first."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        return (
            db.query(NewsArticle)
            .filter(
                NewsArticle.company_id == company_id,
                NewsArticle.published_at >= cutoff,
            )
            .order_by(NewsArticle.impact_score.desc(), NewsArticle.published_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def get_sentiment_counts(
        cls, db: Session, company_id: int, days: int = 30
    ) -> Dict[str, int]:
        """Return positive/negative/neutral article counts over the given window."""
        articles = cls.get_recent_articles(db, company_id, days=days, limit=200)
        counts = {"positive": 0, "negative": 0, "neutral": 0}
        for a in articles:
            counts[a.sentiment] = counts.get(a.sentiment, 0) + 1
        return counts

    @classmethod
    def build_sentiment_trend(
        cls, db: Session, company_id: int
    ) -> List[Dict[str, Any]]:
        """
        Returns a 30-day daily sentiment trend as a list of day records.
        Each record: {date, positive, negative, neutral, net_sentiment}
        """
        cutoff = datetime.utcnow() - timedelta(days=30)
        articles = (
            db.query(NewsArticle)
            .filter(
                NewsArticle.company_id == company_id,
                NewsArticle.published_at >= cutoff,
            )
            .order_by(NewsArticle.published_at.asc())
            .all()
        )

        # Group by date
        day_map: Dict[str, Dict[str, int]] = {}
        for a in articles:
            day_str = a.published_at.strftime("%Y-%m-%d")
            if day_str not in day_map:
                day_map[day_str] = {"positive": 0, "negative": 0, "neutral": 0}
            day_map[day_str][a.sentiment] = day_map[day_str].get(a.sentiment, 0) + 1

        trend = []
        for day_str, counts in sorted(day_map.items()):
            pos = counts["positive"]
            neg = counts["negative"]
            neu = counts["neutral"]
            total = pos + neg + neu
            net = (pos - neg) / max(total, 1) * 100  # -100 to +100 scale
            trend.append({
                "date": day_str,
                "positive": pos,
                "negative": neg,
                "neutral": neu,
                "net_sentiment": round(net, 1),
            })

        return trend

    # ────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────

    @staticmethod
    def _classify_article(title: str, content: str) -> Dict[str, Any]:
        """
        High-fidelity keyword-based classification engine.
        Used inline during ingestion (synchronous, no LLM API needed).
        7 categories: Positive/Negative Fundamental, Positive/Negative Sentiment,
        Negative Regulatory, Negative Governance, Risk Flag — Fraud Signal, Neutral.
        """
        text = (title + " " + content).lower()

        # 1. Risk / Fraud signals (highest priority)
        if any(kw in text for kw in [
            "fraud", "shell company", "siphon", "scam", "embezzlement",
            "auditor resign", "multiple auditor", "loan to subsidiary",
            "related party transaction", "money laundering",
        ]):
            return {"classification": "Risk Flag — Fraud Signal", "impact_score": 10,
                    "sentiment": "negative", "risk_flags": ["Potential fraud signal detected"]}

        # 2. Negative Regulatory
        if any(kw in text for kw in [
            "sebi", "rbi penalty", "court order", "gst notice",
            "regulatory action", "enforcement", "fine imposed",
            "show cause", "adjudication", "notice issued",
        ]):
            return {"classification": "Negative — Regulatory", "impact_score": 8,
                    "sentiment": "negative", "risk_flags": ["Regulatory action detected"]}

        # 3. Negative Governance
        if any(kw in text for kw in [
            "promoter pledge", "auditor quit", "ceo resign", "md resign",
            "governance concern", "insider trading", "board dispute",
            "promoter sell",
        ]):
            return {"classification": "Negative — Governance", "impact_score": 8,
                    "sentiment": "negative", "risk_flags": ["Governance concern identified"]}

        # 4. Positive Fundamental
        if any(kw in text for kw in [
            "profit", "record revenue", "contract win", "record earnings",
            "capacity expansion", "margin improve", "beats estimate",
            "strong quarter", "order win", "guidance raised", "revenue grew",
            "acquisition", "new plant", "ipo",
        ]):
            return {"classification": "Positive — Fundamental", "impact_score": 7,
                    "sentiment": "positive", "risk_flags": []}

        # 5. Positive Sentiment
        if any(kw in text for kw in [
            "analyst upgrade", "buy rating", "target raised", "overweight",
            "accumulate", "outperform", "bullish", "positive outlook",
            "fair value", "target price raised",
        ]):
            return {"classification": "Positive — Sentiment", "impact_score": 4,
                    "sentiment": "positive", "risk_flags": []}

        # 6. Negative Fundamental
        if any(kw in text for kw in [
            "loss", "revenue fell", "margin compressed", "debt increased",
            "misses estimate", "profit decline", "weak quarter",
            "guidance cut", "impairment", "write-off", "below estimate",
        ]):
            return {"classification": "Negative — Fundamental", "impact_score": 6,
                    "sentiment": "negative", "risk_flags": []}

        # 7. Default neutral
        return {"classification": "Neutral — Informational", "impact_score": 2,
                "sentiment": "neutral", "risk_flags": []}

    @staticmethod
    def _parse_published_at(value: Any) -> datetime:
        """Safely parse various published_at formats into a datetime."""
        if value is None:
            return datetime.utcnow()
        if isinstance(value, (int, float)):
            try:
                return datetime.utcfromtimestamp(float(value))
            except Exception:
                return datetime.utcnow()
        if isinstance(value, str):
            for fmt in [
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S GMT",
            ]:
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    return dt.replace(tzinfo=None)
                except ValueError:
                    continue
        return datetime.utcnow()
