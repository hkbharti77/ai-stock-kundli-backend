"""
RAG Query Engine.
Orchestrates natural language intent classification, DB querying,
semantic FAISS retrieval, response caching, and structured answer synthesis.
"""

import os
import json
import logging
import asyncio
import httpx
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from app.core.config import get_settings
from app.models.company import Company
from app.models.financial import Financial
from app.models.news_article import NewsArticle
from app.models.price_history import PriceHistory
from app.models.intraday_price import IntradayPrice
from app.services.embedding_service import EmbeddingService

from fastapi import BackgroundTasks
import yfinance as yf

logger = logging.getLogger("QueryEngine")
settings = get_settings()

# In-Memory Cache Fallback (TTL: 10 minutes)
_memory_cache: Dict[str, Dict[str, Any]] = {}

def background_enrich_and_index(company_id: int):
    """
    Background worker function that performs live enrichment (prices, profile, financials)
    and ingest news, then builds embeddings and indices the company into FAISS.
    """
    from app.core.database import SessionLocal
    from app.services.ingestion import IngestionService
    from app.services.news import NewsService
    from app.models.company import Company
    from app.models.news_article import NewsArticle

    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            logger.warning(f"[Background Ingest] Company with ID {company_id} not found.")
            return

        logger.info(f"[Background Ingest] Starting dynamic live data enrichment for {company.ticker}...")
        # 1. Live profile, price and financial enrichment
        IngestionService.enrich_company_data_live(db, company)

        # 2. Ingest latest news articles
        logger.info(f"[Background Ingest] Fetching recent news for {company.ticker}...")
        NewsService.ingest_news_for_company(db, company)

        # 3. Build company summary and index to FAISS
        logger.info(f"[Background Ingest] Generating company text summary for FAISS indexing...")
        comp_summary = EmbeddingService.build_company_summary_text(company)
        EmbeddingService.add_to_store(
            doc_id=f"company_{company.ticker}",
            category="company",
            ticker=company.ticker,
            text=comp_summary,
            source_id=company.id
        )

        # 4. Build news summaries and index to FAISS
        news_articles = db.query(NewsArticle).filter(NewsArticle.company_id == company.id).all()
        logger.info(f"[Background Ingest] Indexing {len(news_articles)} news articles for {company.ticker} to FAISS...")
        for article in news_articles:
            news_summary = EmbeddingService.build_news_summary_text(article, company.name)
            EmbeddingService.add_to_store(
                doc_id=f"news_{article.id}",
                category="news",
                ticker=company.ticker,
                text=news_summary,
                source_id=article.id
            )

        # 5. Save the FAISS index to disk
        EmbeddingService.save_store()
        logger.info(f"[Background Ingest] Successfully enriched and indexed {company.ticker} to FAISS store.")
    except Exception as e:
        logger.error(f"[Background Ingest] Dynamic ingestion failed for company ID {company_id}: {e}", exc_info=True)
    finally:
        db.close()

def validate_and_register_ticker(db: Session, ticker: str) -> Optional[Company]:
    """
    Validates a ticker using yfinance Search/Lookup, and registers it in the DB if valid.
    Returns the created Company model instance or None.
    """
    import yfinance as yf
    ticker_upper = ticker.upper().strip()
    
    # 1. Check if already exists in DB (to prevent race conditions)
    existing = db.query(Company).filter(Company.ticker == ticker_upper).first()
    if existing:
        return existing

    # Try standard ticker, ticker.NS, and ticker.BO
    candidates = [ticker_upper]
    if not ticker_upper.endswith(".NS") and not ticker_upper.endswith(".BO"):
        candidates.append(f"{ticker_upper}.NS")
        candidates.append(f"{ticker_upper}.BO")

    for symbol in candidates:
        try:
            logger.info(f"Validating ticker symbol {symbol} via yfinance...")
            t_obj = yf.Ticker(symbol)
            hist = t_obj.history(period="1d")
            if not hist.empty:
                # Valid ticker! Try fetching company info/name
                info = {}
                try:
                    info = t_obj.info
                except Exception:
                    pass
                
                long_name = info.get("longName") or info.get("shortName") or ticker_upper
                exchange = info.get("exchange") or ("NSE" if symbol.endswith(".NS") else ("BSE" if symbol.endswith(".BO") else "NASDAQ"))
                
                # Register in database
                company = Company(
                    ticker=ticker_upper,
                    name=long_name,
                    exchange=exchange,
                    is_active=True
                )
                db.add(company)
                db.commit()
                db.refresh(company)
                logger.info(f"Successfully registered ticker {ticker_upper} (name: {long_name}) in DB.")
                return company
        except Exception as e:
            logger.warning(f"Failed to validate candidate symbol {symbol}: {e}")

    # Try yfinance Search as a fallback
    try:
        logger.info(f"Performing search fallback for '{ticker_upper}' via yfinance...")
        search = yf.Search(ticker_upper, max_results=1)
        if search.quotes:
            best_quote = search.quotes[0]
            symbol = best_quote.get("symbol")
            name = best_quote.get("shortname") or best_quote.get("longname") or symbol
            exchange = best_quote.get("exchange") or "NSE"
            
            # Check if resolved symbol already exists
            symbol_upper = symbol.upper()
            existing = db.query(Company).filter(Company.ticker == symbol_upper).first()
            if existing:
                return existing

            company = Company(
                ticker=symbol_upper,
                name=name,
                exchange=exchange,
                is_active=True
            )
            db.add(company)
            db.commit()
            db.refresh(company)
            logger.info(f"Registered fallback ticker {symbol_upper} (name: {name}) in DB.")
            return company
    except Exception as e:
        logger.warning(f"yfinance search fallback failed: {e}")

    return None

class QueryEngine:

    @classmethod
    def _is_hinglish(cls, text: str) -> bool:
        """Determines if the text contains Hinglish markers."""
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        hinglish_markers = {
            "hai", "kya", "batao", "dikhao", "kaise", "kab", "kyu", "kyun", "karna", "krna", 
            "raha", "rahe", "rahi", "rhe", "ko", "se", "ka", "ki", "ke", "aur", "pe", 
            "mein", "bhi", "toh", "ne", "kuch", "sab", "ab", "tum", "aap", "tera", 
            "apna", "apni", "apne", "hum", "hume", "humein", "jyada", "zyada", "thoda", 
            "bahut", "bohot", "acha", "accha", "sahi", "galat", "kaun", "kise", "kisko", 
            "kiske", "kin", "kis", "yeh", "ye", "woh", "wo", "ise", "use", "inhone", 
            "unhone", "karke", "krke", "lekin", "magar", "kyunki", "kyonki", "isliye", 
            "isliya", "phir", "baad", "pehle", "pahle", "aaj", "kal", "parso", "samjhao", 
            "samjh", "likho", "bolo", "kaho", "suno", "karo", "kro", "nahi", "nhi", "yaar", 
            "bhai", "shuru", "khtm", "khatam", "paise", "rupaye", "rupiya", "kamana", 
            "kaunsa", "kaunsi", "kaunse", "kaha", "kahin", "jaha", "jahan", "waha", 
            "wahan", "udhar", "idhar", "kiska", "kiski", "kiske", "kisse", "kisne",
            "bataiye", "batao", "samjhaiye", "dikhaye", "dikhao", "chahiye", "karne", 
            "hoga", "hogi", "hoge", "honge", "tha", "thi", "rha", "rhi", "rhe", 
            "rahein", "kar", "rhi", "hu", "hoon", "hege", "kr", "karne", "sakte", "sakta", 
            "sakti", "sakta", "hai", "hain", "ga", "ge", "gi", "gaya", "gayi", "gaye",
            "hua", "hui", "hue", "baat", "kuch", "koi", "mil", "milega", "milegi"
        }
        return any(w in hinglish_markers for w in words)

    @classmethod
    def _get_cache(cls, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached query response from Redis, with memory fallback."""
        # 1. Try Redis Cache
        try:
            import redis
            r = redis.from_url(settings.REDIS_URL, decode_responses=True)
            cached = r.get(f"nlq_cache:{key}")
            if cached:
                logger.info(f"Cache HIT (Redis) for key: {key}")
                return json.loads(cached)
        except Exception as e:
            logger.debug(f"Redis cache fetch failed or redis not installed: {e}")

        # 2. Try In-Memory Fallback
        if key in _memory_cache:
            entry = _memory_cache[key]
            if entry["expiry"] > datetime.utcnow():
                logger.info(f"Cache HIT (In-Memory) for key: {key}")
                return entry["data"]
            else:
                del _memory_cache[key]
        return None

    @classmethod
    def _set_cache(cls, key: str, data: Dict[str, Any]):
        """Caches query response in Redis, with memory fallback."""
        # 1. Try Redis Cache
        try:
            import redis
            r = redis.from_url(settings.REDIS_URL, decode_responses=True)
            r.setex(f"nlq_cache:{key}", 600, json.dumps(data))  # 10 min TTL
            logger.info(f"Cached in Redis for key: {key}")
        except Exception as e:
            logger.debug(f"Redis cache store failed: {e}")

        # 2. Try In-Memory Fallback
        _memory_cache[key] = {
            "expiry": datetime.utcnow() + timedelta(minutes=10),
            "data": data
        }

    @classmethod
    def _clean_json_text(cls, text: str) -> str:
        """Helper to strip markdown code fences from JSON strings."""
        text = text.strip()
        match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text

    @classmethod
    async def _call_llm_json(cls, prompt: str) -> Optional[Dict[str, Any]]:
        """Invokes Ollama local LLM directly as the primary LLM, expecting JSON output."""
        ollama_url = settings.OLLAMA_API_URL or "http://localhost:11434"
        model = settings.OLLAMA_MODEL or "gemma3:4b"

        logger.info(f"[LLM] Calling Ollama model '{model}' at {ollama_url}")
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                body = {
                    "model": model,
                    "prompt": prompt + "\n\nIMPORTANT: Return ONLY a raw valid JSON object. No markdown, no code fences, no explanation.",
                    "format": "json",
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 4096,
                    }
                }
                response = await client.post(f"{ollama_url.rstrip('/')}/api/generate", json=body)
                if response.status_code == 200:
                    content = response.json().get("response", "")
                    logger.info(f"[LLM] Ollama responded successfully ({len(content)} chars)")
                    return json.loads(cls._clean_json_text(content))
                else:
                    logger.error(f"[LLM] Ollama returned HTTP {response.status_code}: {response.text[:300]}")
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] Ollama JSON parse error: {e}")
        except Exception as e:
            logger.error(f"[LLM] Ollama call failed: {e}")

        # All LLMs failed — return None to trigger simulated fallback
        return None

    @classmethod
    async def analyze_query_intent(cls, query: str, history_str: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """Analyzes query intent, extracting tickers, metrics, sectors, and generating semantic search text."""
        prompt = f"""
Analyze the user's natural language query and context to identify search intent, tickers, sector/industries, and construct a search string for semantic retrieval.

Conversation History Context:
{history_str}

User Query: "{query}"

Return a JSON object with this format:
{{
  "tickers": ["TCS", "INFY", etc. - tickers explicitly or implicitly mentioned],
  "intent": "screening" | "comparison" | "news_semantic" | "general",
  "metrics": ["roe", "pat", "revenue", "debt_equity", etc. - financial metrics asked about],
  "timeframe": "quarterly" | "annual" | "recent" | "3 quarters" etc.,
  "sector": "banking" | "it" | "auto" etc.,
  "question_type": "text" | "table" | "chart" | "comparison",
  "semantic_query": "A refined search query suited for semantic news or profile retrieval"
}}
"""
        try:
            result = await cls._call_llm_json(prompt)
            if result:
                return result
        except Exception as e:
            logger.error(f"Error analyzing query intent: {e}")
        
        # Default smart offline fallback using regex and database lookup
        detected_tickers = []
        if db:
            try:
                # Query all active tickers
                all_tickers = [row[0] for row in db.query(Company.ticker).filter(Company.is_active == True).all()]
                for t in all_tickers:
                    # Match ticker boundaries case-sensitively for common Hindi/English stop words and small words
                    if t.lower() in {"ko", "in", "or", "at", "by", "is", "it", "up", "us", "on", "go", "be", "so", "to", "he", "me", "my", "we", "do", "am", "an", "as", "if", "no", "and", "the", "for", "but", "are", "has", "had", "was", "its", "out", "new", "all", "any", "not", "yes", "who", "how", "why"}:
                        if re.search(r'\b' + re.escape(t) + r'\b', query):
                            detected_tickers.append(t)
                    else:
                        if re.search(r'\b' + re.escape(t) + r'\b', query, re.IGNORECASE):
                            detected_tickers.append(t)
            except Exception as e:
                logger.debug(f"Offline ticker extraction failed: {e}")

        # If no DB session, try finding capital words of 2-5 letters
        if not detected_tickers:
            raw_caps = re.findall(r'\b[A-Z]{2,6}\b', query)
            if raw_caps:
                detected_tickers = list(set(raw_caps))

        # Detect intent heuristically
        intent = "general"
        query_lower = query.lower()
        if len(detected_tickers) >= 2 or "compare" in query_lower or " vs " in query_lower or "versus" in query_lower:
            intent = "comparison"
        elif any(w in query_lower for w in ["screen", "sector", "industry", "list", "top stocks", "best stocks"]):
            intent = "screening"
        elif any(w in query_lower for w in ["news", "sentiment", "update", "latest"]):
            intent = "news_semantic"

        # Detect question output format type
        question_type = "text"
        if intent == "comparison":
            question_type = "comparison"
        elif any(w in query_lower for w in ["chart", "graph", "plot", "trend"]):
            question_type = "chart"
        elif any(w in query_lower for w in ["table", "grid", "list"]):
            question_type = "table"

        # Extract sector if screening
        sector = None
        for sec_word in ["it", "technology", "banking", "finance", "energy", "automobile", "auto", "pharmaceutical", "pharma"]:
            if sec_word in query_lower:
                sector = sec_word
                break

        return {
            "tickers": detected_tickers,
            "intent": intent,
            "metrics": ["roe", "pat", "revenue"] if "financial" in query_lower or "metric" in query_lower else [],
            "timeframe": "quarterly" if "quarter" in query_lower else "annual",
            "sector": sector,
            "question_type": question_type,
            "semantic_query": query
        }

    @classmethod
    def _fetch_company_data(cls, db: Session, tickers: List[str]) -> List[Dict[str, Any]]:
        """Fetches detailed db metrics for specific company tickers, including latest prices."""
        companies = db.query(Company).filter(Company.ticker.in_(tickers), Company.is_active == True).all()
        results = []
        for c in companies:
            c_data = {
                "name": c.name,
                "ticker": c.ticker,
                "sector": c.sector,
                "sub_sector": c.sub_sector,
                "market_cap": float(c.market_cap) if c.market_cap is not None else None,
                "financials": [],
                "price_data": None,
                "price_history_10d": []
            }

            # ── Fetch latest daily close price (LTP) from price_history ──
            try:
                latest_price_row = (
                    db.query(PriceHistory)
                    .filter(PriceHistory.company_id == c.id)
                    .order_by(PriceHistory.date.desc())
                    .first()
                )
                prev_price_row = (
                    db.query(PriceHistory)
                    .filter(PriceHistory.company_id == c.id)
                    .order_by(PriceHistory.date.desc())
                    .offset(1)
                    .first()
                )

                # 52-week high/low
                from datetime import date as dt_date, timedelta
                one_year_ago = dt_date.today() - timedelta(days=365)
                high_52w_row = (
                    db.query(PriceHistory)
                    .filter(PriceHistory.company_id == c.id, PriceHistory.date >= one_year_ago)
                    .order_by(PriceHistory.high.desc())
                    .first()
                )
                low_52w_row = (
                    db.query(PriceHistory)
                    .filter(PriceHistory.company_id == c.id, PriceHistory.date >= one_year_ago)
                    .order_by(PriceHistory.low.asc())
                    .first()
                )

                # Latest intraday close (more real-time than daily)
                intraday_latest = (
                    db.query(IntradayPrice)
                    .filter(IntradayPrice.company_id == c.id)
                    .order_by(IntradayPrice.timestamp.desc())
                    .first()
                )

                ltp = None
                ltp_source = None
                if intraday_latest and intraday_latest.close:
                    ltp = float(intraday_latest.close)
                    ltp_source = f"Intraday ({intraday_latest.timestamp.strftime('%Y-%m-%d %H:%M')})"
                elif latest_price_row and latest_price_row.close:
                    ltp = float(latest_price_row.close)
                    ltp_source = f"Daily close ({latest_price_row.date})"

                prev_close = float(prev_price_row.close) if (prev_price_row and prev_price_row.close) else None
                change_pct = None
                if ltp and prev_close and prev_close > 0:
                    change_pct = round(((ltp - prev_close) / prev_close) * 100, 2)

                c_data["price_data"] = {
                    "ltp": ltp,
                    "ltp_source": ltp_source,
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "day_open": float(latest_price_row.open) if (latest_price_row and latest_price_row.open) else None,
                    "day_high": float(latest_price_row.high) if (latest_price_row and latest_price_row.high) else None,
                    "day_low": float(latest_price_row.low) if (latest_price_row and latest_price_row.low) else None,
                    "week52_high": float(high_52w_row.high) if (high_52w_row and high_52w_row.high) else None,
                    "week52_low": float(low_52w_row.low) if (low_52w_row and low_52w_row.low) else None,
                    "intraday_rsi": float(intraday_latest.rsi) if (intraday_latest and intraday_latest.rsi) else None,
                    "intraday_vwap": float(intraday_latest.vwap) if (intraday_latest and intraday_latest.vwap) else None,
                }

                # Last 10 trading days OHLCV for chart context
                recent_prices = (
                    db.query(PriceHistory)
                    .filter(PriceHistory.company_id == c.id)
                    .order_by(PriceHistory.date.desc())
                    .limit(10)
                    .all()
                )
                c_data["price_history_10d"] = [
                    {
                        "date": str(p.date),
                        "open": float(p.open) if p.open else None,
                        "high": float(p.high) if p.high else None,
                        "low": float(p.low) if p.low else None,
                        "close": float(p.close) if p.close else None,
                        "volume": int(p.volume) if p.volume else None,
                    }
                    for p in reversed(recent_prices)
                ]
            except Exception as e:
                logger.warning(f"Failed to fetch price data for {c.ticker}: {e}")

            # Fetch last 8 financials (annual & quarterly)
            financial_records = db.query(Financial).filter(Financial.company_id == c.id).order_by(Financial.period_end.desc()).limit(8).all()
            for f in financial_records:
                c_data["financials"].append({
                    "period_type": f.period_type,
                    "period_end": str(f.period_end),
                    "revenue": float(f.revenue) if f.revenue is not None else None,
                    "gross_profit": float(f.gross_profit) if f.gross_profit is not None else None,
                    "pat": float(f.pat) if f.pat is not None else None,
                    "roe": float(f.roe) if f.roe is not None else None,
                    "roce": float(f.roce) if f.roce is not None else None,
                    "debt_equity": float(f.debt_equity) if f.debt_equity is not None else None,
                    "promoter_holding": float(f.promoter_holding_pct) if f.promoter_holding_pct is not None else None
                })
            results.append(c_data)
        return results

    @classmethod
    def _fetch_sector_data(cls, db: Session, sector: str) -> List[Dict[str, Any]]:
        """Queries companies within a matching sector to facilitate screening questions."""
        # Simple wildcard query for sector
        companies = db.query(Company).filter(
            Company.sector.ilike(f"%{sector}%") | Company.sub_sector.ilike(f"%{sector}%")
        ).all()
        
        # If too few, fallback to getting top active companies
        if not companies:
            companies = db.query(Company).filter(Company.is_active == True).limit(10).all()

        results = []
        for c in companies:
            # Get latest annual ratios
            f = db.query(Financial).filter(
                Financial.company_id == c.id, Financial.period_type == "annual"
            ).order_by(Financial.period_end.desc()).first()
            
            # Fetch latest quarterly ratios (last 3 quarters) for checking improvements
            quarters = db.query(Financial).filter(
                Financial.company_id == c.id, Financial.period_type == "quarterly"
            ).order_by(Financial.period_end.desc()).limit(3).all()

            results.append({
                "ticker": c.ticker,
                "name": c.name,
                "sector": c.sector,
                "market_cap": float(c.market_cap) if c.market_cap is not None else None,
                "annual_roe": float(f.roe) if (f and f.roe is not None) else None,
                "annual_pat": float(f.pat) if (f and f.pat is not None) else None,
                "annual_debt_equity": float(f.debt_equity) if (f and f.debt_equity is not None) else None,
                "quarters": [
                    {
                        "period_end": str(q.period_end),
                        "roe": float(q.roe) if (q and q.roe is not None) else None,
                        "pat": float(q.pat) if (q and q.pat is not None) else None
                    }
                    for q in quarters
                ]
            })
        return results

    @classmethod
    async def execute_query(
        cls, 
        db: Session, 
        query: str, 
        history: List[Dict[str, str]], 
        background_tasks: Optional[BackgroundTasks] = None
    ) -> Dict[str, Any]:
        """Runs the multi-turn conversational RAG flow: caches -> analyzes intent -> fetches context -> synthesizes."""
        is_hinglish = cls._is_hinglish(query)
        # 1. Generate a Cache Key
        history_key_elements = [f"{m['role']}:{m['content']}" for m in history[-3:]]  # last 3 messages for context
        cache_key = f"{query.strip().lower()}_" + "_".join(history_key_elements)
        cache_key = "".join(c for c in cache_key if c.isalnum() or c in "_-")[:120]

        cached_res = cls._get_cache(cache_key)
        if cached_res:
            return cached_res

        # 2. Analyze intent
        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history])
        intent_info = await cls.analyze_query_intent(query, history_str, db)
        logger.info(f"Analyzed intent: {intent_info}")

        tickers = intent_info.get("tickers", [])
        intent = intent_info.get("intent", "general")
        metrics = intent_info.get("metrics", [])
        sector = intent_info.get("sector")
        semantic_query = intent_info.get("semantic_query", query)

        # 2b. Check if any ticker is missing and needs background ingestion
        ingesting_tickers = []
        if db and tickers:
            for ticker in tickers:
                t_upper = ticker.upper().strip()
                existing = db.query(Company).filter(Company.ticker == t_upper, Company.is_active == True).first()
                if not existing:
                    # Validate and register missing ticker
                    company = validate_and_register_ticker(db, t_upper)
                    if company:
                        ingesting_tickers.append(company)
                        if background_tasks:
                            background_tasks.add_task(background_enrich_and_index, company.id)
                        else:
                            asyncio.create_task(asyncio.to_thread(background_enrich_and_index, company.id))
                        logger.info(f"Dispatched background ingestion task for missing ticker {t_upper} (Company ID: {company.id})")

        if ingesting_tickers:
            # Inform user that data ingestion is running in background
            ticker_details = [f"**{c.ticker} ({c.name})**" for c in ingesting_tickers]
            ticker_list_str = ", ".join(ticker_details)
            
            if is_hinglish:
                answer = f"### 📥 Stock Data Fetching in Progress\n\n" \
                         f"Humare database mein {ticker_list_str} ki complete history aur metrics filhal available nahi hain.\n\n" \
                         f"Maine background mein in stocks ka **live data enrichment (prices, financials, and news)** aur **FAISS search indexing** start kar diya hai.\n\n" \
                         f"Is process mein lagbhag **5-10 seconds** lagenge. Kripya tab tak baaki details check kijiye aur thodi der baad is query ko dobara poochna!"
            else:
                answer = f"### 📥 Stock Data Fetching in Progress\n\n" \
                         f"Complete history and metrics for {ticker_list_str} are not currently available in our database.\n\n" \
                         f"I have started **live data enrichment (prices, financials, and news)** and **FAISS search indexing** for these stocks in the background.\n\n" \
                         f"This process will take about **5-10 seconds**. Please explore other details in the meantime and try this query again shortly!"
            
            return {
                "answer": answer,
                "type": "text",
                "data": {},
                "suggestions": [
                    f"Check status for {ingesting_tickers[0].ticker} again",
                    "List all active companies in database",
                    "Show latest market macro data"
                ],
                "links": [],
                "sources": ["yfinance Live Ingestion Service"]
            }

        context_data = {}

        # 3. Gather Context from DB and FAISS
        # Direct DB Company Lookup
        if tickers:
            context_data["companies"] = cls._fetch_company_data(db, tickers)
            # Fetch latest news from DB for these companies as well
            companies_ids = db.query(Company.id).filter(Company.ticker.in_(tickers)).all()
            if companies_ids:
                c_ids = [r[0] for r in companies_ids]
                news = db.query(NewsArticle).filter(NewsArticle.company_id.in_(c_ids)).order_by(NewsArticle.published_at.desc()).limit(5).all()
                context_data["direct_news"] = [
                    {"ticker": n.company.ticker, "title": n.title, "sentiment": n.sentiment, "date": str(n.published_at.date()), "summary": n.content[:200] if n.content else ""}
                    for n in news
                ]

        # Sector Screening
        if intent == "screening" and sector:
            context_data["screening_results"] = cls._fetch_sector_data(db, sector)
        elif not tickers and sector:
            context_data["screening_results"] = cls._fetch_sector_data(db, sector)

        # FAISS Semantic Retrieval
        # Retrieve similar records from FAISS vector store
        faiss_results = EmbeddingService.search_similar(query=semantic_query, k=5)
        context_data["semantic_retrieval"] = [
            {"ticker": r["ticker"], "category": r["category"], "text": r["text"], "score": r["score"]}
            for r in faiss_results
        ]

        # 4. Generate Final Answer Synthesis
        if is_hinglish:
            language_guideline = 'Mix English and Hindi (Hinglish) naturally (e.g. "ROE improve ho raha hai", "market cap kaafi high hai", "yahan hum compare karenge"). Speak like a friendly Indian market expert/broker, but keep it highly professional.'
        else:
            language_guideline = 'Respond strictly and completely in English. Do not use any Hindi or Hinglish words.'

        prompt = f"""
You are "AI Stock Kundli Chatbot", an advanced, friendly, and expert SEBI-registered financial research assistant.
Your goal is to answer the user's natural language query by leveraging the provided database and vector retrieval context.

Conversation History:
{history_str}

User's Query: "{query}"

Gathered Context:
{json.dumps(context_data, indent=2)}

IMPORTANT — PRICE DATA INSTRUCTIONS:
- The context above contains REAL-TIME stock price data from our live database under each company's "price_data" and "price_history_10d" fields.
- "price_data.ltp" = Latest Traded Price (LTP) — the most current stock price available.
- "price_data.change_pct" = Today's price change percentage vs previous close.
- "price_data.week52_high" and "price_data.week52_low" = 52-week high and low prices.
- "price_data.intraday_rsi" = Latest RSI technical indicator value.
- "price_data.intraday_vwap" = Latest VWAP (Volume Weighted Average Price).
- "price_history_10d" = Last 10 trading days OHLCV data for trend analysis.
- ALWAYS use this price data to answer price-related questions. NEVER say you don't have real-time prices — you DO have them in the context.
- Format prices with ₹ symbol. Example: ₹202.72

Guidelines:
1. Provide a comprehensive, professional, and detailed answer in the "answer" field. Write in Markdown.
2. {language_guideline}
3. If the query asks for price data, ALWAYS include: LTP, day change %, 52W high/low, RSI, and VWAP if available.
4. If the query asks for comparison or numerical listings, structure it as a table, comparison matrix, or chart.
5. Set the "type" field to:
   - "chart": if returning price trends, numerical series, or comparison charts. Use "price_history_10d" data for price charts.
   - "table": if listing multiple stocks, metrics, or rows.
   - "comparison": if doing a side-by-side company matrix.
   - "text": for purely conversational or text/news-related queries.
6. Populating "data" field:
   - For "table": {{ "headers": ["Header1", "Header2"], "rows": [["val1", "val2"], ["val3", "val4"]] }}
   - For "chart" with price data: {{ "chartType": "area", "xKey": "date", "dataKeys": ["close"], "chartData": [ {{ "date": "2026-06-01", "close": 202.72 }}, ... ] }}
   - For "chart" with financials: {{ "chartType": "line" | "bar" | "area", "xKey": "label", "dataKeys": ["pat", "revenue"], "chartData": [...] }}
   - For "comparison": {{ "headers": ["Metric", "Ticker1", "Ticker2"], "rows": [ ["LTP", "₹202.72", "₹1450.30"], ["ROE", "15.4%", "12.0%"] ] }}
7. Populate "links" with action URLs:
   - If ticker 'TATASTEEL' is discussed, add: {{ "text": "View TATASTEEL Kundli", "url": "/dashboard/stocks/TATASTEEL" }}
8. Generate 2-3 interactive, relevant, and short follow-up questions in "suggestions".
9. Populate "sources" with an array of financial data sources or databases leveraged for this information.

Return response in valid JSON matching this schema EXACTLY:
{{
  "answer": "Markdown text here...",
  "type": "text" | "table" | "chart" | "comparison",
  "data": {{ ... }},
  "suggestions": ["suggestion 1", "suggestion 2"],
  "links": [ {{ "text": "link text", "url": "relative dashboard url" }} ],
  "sources": ["source 1", "source 2"]
}}
"""

        try:
            answer_json = await cls._call_llm_json(prompt)
            if answer_json:
                if "sources" not in answer_json or not isinstance(answer_json["sources"], list):
                    answer_json["sources"] = ["AI Financial Analysis Engine"]
                cls._set_cache(cache_key, answer_json)
                return answer_json
        except Exception as e:
            logger.error(f"Error generating RAG answer: {e}")

        # Fallback to high-quality simulated RAG response based on DB/FAISS context
        logger.warning("Falling back to local simulated RAG response synthesis.")
        simulated_res = cls._generate_simulated_rag_response(query, context_data, is_hinglish=is_hinglish)
        cls._set_cache(cache_key, simulated_res)
        return simulated_res

    @classmethod
    def _generate_simulated_rag_response(cls, query: str, context: Dict[str, Any], is_hinglish: bool = True) -> Dict[str, Any]:
        """Generates a high-quality simulated Hinglish or English RAG response based on DB/FAISS context."""
        companies = context.get("companies", [])
        screening = context.get("screening_results", [])
        semantic = context.get("semantic_retrieval", [])

        # 1. Multi-company comparison
        if len(companies) >= 2:
            c1, c2 = companies[0], companies[1]
            t1, t2 = c1["ticker"], c2["ticker"]
            
            # Extract latest financials
            f1 = c1["financials"][0] if c1["financials"] else {}
            f2 = c2["financials"][0] if c2["financials"] else {}
            
            roe1 = f"{f1.get('roe')}%" if f1.get("roe") else "N/A"
            roe2 = f"{f2.get('roe')}%" if f2.get("roe") else "N/A"
            de1 = f"{f1.get('debt_equity')}" if f1.get("debt_equity") else "N/A"
            de2 = f"{f2.get('debt_equity')}" if f2.get("debt_equity") else "N/A"
            pat1 = f"₹{f1.get('pat'):,.1f} Cr" if f1.get("pat") else "N/A"
            pat2 = f"₹{f2.get('pat'):,.1f} Cr" if f2.get("pat") else "N/A"

            if is_hinglish:
                answer = f"### 📊 Comparison: {c1['name']} ({t1}) vs {c2['name']} ({t2})\n\n" \
                         f"Humne dono companies ke core financial metrics ko analyse kiya hai. Yahan details di gayi hain:\n\n" \
                         f"- **ROE**: {t1} ka ROE **{roe1}** hai, wahi {t2} ka ROE **{roe2}** hai.\n" \
                         f"- **Debt/Equity**: {t1} ka D/E ratio **{de1}** hai aur {t2} ka **{de2}** hai.\n" \
                         f"- **Latest Profit (PAT)**: {t1} ne **{pat1}** report kiya hai aur {t2} ne **{pat2}** report kiya hai.\n\n" \
                         f"Overall, return ratios ko dekh kar lagta hai ki dono IT majors robust hain, par relative analysis ke liye niche table check kijiye."
            else:
                answer = f"### 📊 Comparison: {c1['name']} ({t1}) vs {c2['name']} ({t2})\n\n" \
                         f"We have analyzed the core financial metrics for both companies. Here are the details:\n\n" \
                         f"- **ROE**: {t1}'s ROE is **{roe1}**, while {t2}'s is **{roe2}**.\n" \
                         f"- **Debt/Equity**: {t1}'s D/E ratio is **{de1}** and {t2}'s is **{de2}**.\n" \
                         f"- **Latest Profit (PAT)**: {t1} reported **{pat1}** and {t2} reported **{pat2}**.\n\n" \
                         f"Overall, both IT majors are robust based on return ratios, but please check the table below for side-by-side comparison."

            return {
                "answer": answer,
                "type": "comparison",
                "data": {
                    "headers": ["Metric", t1, t2],
                    "rows": [
                        ["Return on Equity (ROE)", roe1, roe2],
                        ["Debt to Equity", de1, de2],
                        ["Net Profit (PAT)", pat1, pat2],
                        ["Sector", c1["sector"], c2["sector"]]
                    ]
                },
                "suggestions": [
                    f"Show quarterly PAT growth for {t1}",
                    f"Show latest news for {t2}"
                ],
                "links": [
                    {"text": f"View {t1} Profile", "url": f"/dashboard/stocks/{t1}"},
                    {"text": f"View {t2} Profile", "url": f"/dashboard/stocks/{t2}"}
                ],
                "sources": ["NSE Database", "Company Filings", "yfinance News"]
            }

        # 2. Single company details/charts
        elif len(companies) == 1:
            c = companies[0]
            t = c["ticker"]
            
            # Construct quarterly chart data
            chart_data = []
            quarters = [f for f in c["financials"] if f["period_type"] == "quarterly"]
            quarters.reverse()  # chronologically ascending
            
            for q in quarters[:4]:  # last 4 quarters
                chart_data.append({
                    "label": q["period_end"][-5:] if len(q["period_end"]) > 5 else q["period_end"],
                    "Revenue": q["revenue"] or 0.0,
                    "PAT": q["pat"] or 0.0
                })

            latest = c["financials"][0] if c["financials"] else {}
            roe = f"{latest.get('roe')}%" if latest.get("roe") else "N/A"
            pat = f"₹{latest.get('pat'):,.1f} Cr" if latest.get("pat") else "N/A"
            rev = f"₹{latest.get('revenue'):,.1f} Cr" if latest.get("revenue") else "N/A"

            if is_hinglish:
                answer = f"### 📈 {c['name']} ({t}) Financial Overview\n\n" \
                         f"Aapne **{c['name']}** ke financial health ke baare mein poocha hai. Database metrics ke mutabik latest details yahan hain:\n\n" \
                         f"- **Revenue**: {rev} report hui hai.\n" \
                         f"- **Profit After Tax (PAT)**: {pat} raha hai.\n" \
                         f"- **Return on Equity (ROE)**: {roe} pe maintain hai.\n\n" \
                         f"Neeche quarterly revenue aur profit trends chart ke through visually layout kiye gaye hain."
            else:
                answer = f"### 📈 {c['name']} ({t}) Financial Overview\n\n" \
                         f"You asked about the financial health of **{c['name']}**. According to database metrics, the latest details are:\n\n" \
                         f"- **Revenue**: {rev} was reported.\n" \
                         f"- **Profit After Tax (PAT)**: Net profit stands at {pat}.\n" \
                         f"- **Return on Equity (ROE)**: ROE is maintained at {roe}.\n\n" \
                         f"Quarterly revenue and profit trends are visually laid out in the chart below."

            return {
                "answer": answer,
                "type": "chart",
                "data": {
                    "chartType": "bar",
                    "xKey": "label",
                    "dataKeys": ["Revenue", "PAT"],
                    "chartData": chart_data
                },
                "suggestions": [
                    f"Analyze {t} fundamental report",
                    f"Is {t} in a strong buy zone?"
                ],
                "links": [
                    {"text": f"View {t} Profile", "url": f"/dashboard/stocks/{t}"}
                ],
                "sources": ["NSE Database", "Latest Quarterly Disclosures"]
            }

        # 3. Screening / Sector screening results
        elif screening:
            rows = []
            for sc in screening[:5]:  # top 5
                roe_str = f"{sc['annual_roe']}%" if sc['annual_roe'] else "N/A"
                pat_str = f"₹{sc['annual_pat']:,.1f} Cr" if sc['annual_pat'] else "N/A"
                rows.append([sc["ticker"], sc["name"], roe_str, pat_str])

            if is_hinglish:
                answer = f"### 🔍 Sector Screening Results\n\n" \
                         f"Aapke request ke basis par active screening se top companies fetch ki gayi hain. " \
                         f"Yahan list di gayi hai:\n\n" \
                         f"Is segment mein standard return levels acche hain. Neeche tabular format compare kijiye."
            else:
                answer = f"### 🔍 Sector Screening Results\n\n" \
                         f"Top companies have been fetched from active screening based on your request. Here is the list:\n\n" \
                         f"Standard return levels are strong in this segment. Compare the details in the table below."

            return {
                "answer": answer,
                "type": "table",
                "data": {
                    "headers": ["Ticker", "Company Name", "Annual ROE", "Annual PAT"],
                    "rows": rows
                },
                "suggestions": [
                    "Compare top 2 stocks from this list",
                    "Show latest news for these stocks"
                ],
                "links": [],
                "sources": ["Exchange Screener Database", "Company Financial Database"]
            }

        # 4. Semantic / general news fallback
        else:
            news_items = []
            for s in semantic[:3]:
                news_items.append(f"- **{s['ticker']}**: {s['text'][:180]}...")
            
            bullet_points = "\n".join(news_items) if news_items else "- No specific data matches found."

            if is_hinglish:
                answer = f"### 💬 Market Sentiment & News Analysis\n\n" \
                         f"Maine research reports aur news database ko scan kiya hai. Yahan related developments hain:\n\n" \
                         f"{bullet_points}\n\n" \
                         f"Aap kisi specific company jaise TCS, INFY, ya RELIANCE ke financials check karne ke liye prompt kar sakte hain."
            else:
                answer = f"### 💬 Market Sentiment & News Analysis\n\n" \
                         f"I have scanned the research reports and news database. Here are the related developments:\n\n" \
                         f"{bullet_points}\n\n" \
                         f"You can prompt to check financials for any specific company like TCS, INFY, or RELIANCE."

            return {
                "answer": answer,
                "type": "text",
                "data": {},
                "suggestions": [
                    "Show me TCS news and sentiment score",
                    "Compare TCS vs INFY"
                ],
                "links": [],
                "sources": ["FAISS Semantic News Index", "yfinance News Feed"]
            }

