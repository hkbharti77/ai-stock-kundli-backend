"""
Embedding and FAISS Vector Store Service.
Handles generating local text embeddings and indexing company financials and news.
"""

import os
import json
import logging
import numpy as np
import faiss
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.company import Company
from app.models.news_article import NewsArticle
from app.models.financial import Financial

logger = logging.getLogger("EmbeddingService")

# Paths for persisting the FAISS index and metadata
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "vector_store.faiss")
METADATA_PATH = os.path.join(DATA_DIR, "vector_store_meta.json")

# Ensure the data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

class EmbeddingService:
    _model = None
    _index = None
    _metadata: List[Dict[str, Any]] = []

    @classmethod
    def get_model(cls):
        """Lazy loading of the SentenceTransformer model to save startup memory."""
        if cls._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading SentenceTransformer model 'all-MiniLM-L6-v2'...")
            # Use 'all-MiniLM-L6-v2' (384 dimensions)
            cls._model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("SentenceTransformer model loaded successfully.")
        return cls._model

    @classmethod
    def initialize_store(cls):
        """Initializes the FAISS index and loads existing index/metadata if available."""
        dimension = 384  # Dimension of all-MiniLM-L6-v2
        
        if os.path.exists(FAISS_INDEX_PATH) and os.path.exists(METADATA_PATH):
            try:
                logger.info(f"Loading FAISS index from {FAISS_INDEX_PATH}...")
                cls._index = faiss.read_index(FAISS_INDEX_PATH)
                with open(METADATA_PATH, "r", encoding="utf-8") as f:
                    cls._metadata = json.load(f)
                logger.info(f"FAISS index loaded. Total items: {len(cls._metadata)}")
                return
            except Exception as e:
                logger.error(f"Failed to load FAISS index: {e}. Recreating...")
        
        # Recreate fresh IndexFlatIP (Inner Product index for Cosine Similarity when normalized)
        logger.info("Initializing fresh FAISS IndexFlatIP...")
        cls._index = faiss.IndexFlatIP(dimension)
        cls._metadata = []
        cls.save_store()

    @classmethod
    def save_store(cls):
        """Saves FAISS index and metadata to disk."""
        if cls._index is None:
            return
        try:
            faiss.write_index(cls._index, FAISS_INDEX_PATH)
            with open(METADATA_PATH, "w", encoding="utf-8") as f:
                json.dump(cls._metadata, f, indent=2, ensure_ascii=False)
            logger.info("FAISS index and metadata saved to disk.")
        except Exception as e:
            logger.error(f"Failed to save FAISS store: {e}")

    @classmethod
    def get_embedding(cls, text: str) -> np.ndarray:
        """Generates a normalized embedding for a single text string."""
        model = cls.get_model()
        emb = model.encode(text, convert_to_numpy=True)
        # Normalize vector for cosine similarity
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        return emb

    @classmethod
    def add_to_store(cls, doc_id: str, category: str, ticker: str, text: str, source_id: int):
        """Adds a document to the FAISS store, avoiding duplicates of same doc_id."""
        if cls._index is None:
            cls.initialize_store()
            
        # Check if doc_id already exists in metadata to avoid duplication
        for idx, meta in enumerate(cls._metadata):
            if meta["id"] == doc_id:
                # Document already exists, let's just return
                # If we need updates, we could delete/rebuild, but for financial/news data
                # incremental additions are standard.
                return

        try:
            emb = cls.get_embedding(text)
            emb_matrix = np.array([emb]).astype("float32")
            cls._index.add(emb_matrix)
            cls._metadata.append({
                "id": doc_id,
                "category": category,
                "ticker": ticker,
                "text": text,
                "source_id": source_id
            })
        except Exception as e:
            logger.error(f"Error adding doc {doc_id} to store: {e}")

    @classmethod
    def search_similar(cls, query: str, category: Optional[str] = None, k: int = 5) -> List[Dict[str, Any]]:
        """Searches for similar documents in the FAISS index, optionally filtering by category."""
        if cls._index is None:
            cls.initialize_store()
            
        if cls._index.ntotal == 0:
            return []

        try:
            query_emb = cls.get_embedding(query)
            query_matrix = np.array([query_emb]).astype("float32")
            
            # Since we might filter after fetching, let's query a larger number of candidates
            fetch_k = min(cls._index.ntotal, k * 4 if category else k)
            distances, indices = cls._index.search(query_matrix, fetch_k)
            
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0 or idx >= len(cls._metadata):
                    continue
                meta = cls._metadata[idx]
                if category and meta["category"] != category:
                    continue
                results.append({
                    "id": meta["id"],
                    "category": meta["category"],
                    "ticker": meta["ticker"],
                    "text": meta["text"],
                    "source_id": meta["source_id"],
                    "score": float(dist)  # L2 index gives distance; InnerProduct with normalized gives cosine similarity (0 to 1)
                })
                if len(results) >= k:
                    break
            return results
        except Exception as e:
            logger.error(f"Error searching FAISS store: {e}")
            return []

    @classmethod
    def build_company_summary_text(cls, company: Company) -> str:
        """Formulates a comprehensive textual summary of a company's business and latest financials."""
        text_parts = [
            f"Company Name: {company.name}",
            f"Ticker: {company.ticker}",
            f"Sector: {company.sector or 'N/A'}",
            f"Sub-Sector: {company.sub_sector or 'N/A'}",
            f"Market Capitalization: {company.market_cap or 'N/A'} Crores"
        ]

        # Extract latest annual financials
        annual_financials = [f for f in company.financials if f.period_type == "annual"]
        if annual_financials:
            # Sort by period_end descending
            annual_financials.sort(key=lambda x: x.period_end, reverse=True)
            latest = annual_financials[0]
            text_parts.append(
                f"Latest Annual Financials (as of {latest.period_end}): "
                f"Revenue: {latest.revenue} Cr, "
                f"Gross Profit: {latest.gross_profit} Cr, "
                f"EBITDA: {latest.ebitda} Cr, "
                f"PAT (Net Profit): {latest.pat} Cr, "
                f"EPS: {latest.eps}, "
                f"Return on Equity (ROE): {latest.roe}%, "
                f"Return on Capital Employed (ROCE): {latest.roce}%, "
                f"Debt/Equity: {latest.debt_equity}, "
                f"Current Ratio: {latest.current_ratio}."
            )
            # Add Shareholding pattern
            text_parts.append(
                f"Shareholding Pattern: "
                f"Promoters: {latest.promoter_holding_pct or 'N/A'}% (Pledged: {latest.promoter_pledge_pct or 0}%), "
                f"FIIs: {latest.fii_holding_pct or 'N/A'}%, "
                f"DIIs: {latest.dii_holding_pct or 'N/A'}%, "
                f"Public: {latest.public_holding_pct or 'N/A'}%."
            )

        # Extract latest quarterly financials
        quarterly_financials = [f for f in company.financials if f.period_type == "quarterly"]
        if quarterly_financials:
            quarterly_financials.sort(key=lambda x: x.period_end, reverse=True)
            # Take up to last 3 quarters
            q_summaries = []
            for q in quarterly_financials[:3]:
                q_summaries.append(
                    f"Quarter ending {q.period_end} - Revenue: {q.revenue} Cr, PAT: {q.pat} Cr, ROE: {q.roe}%"
                )
            text_parts.append("Recent Quarterly Performances: " + " | ".join(q_summaries))

        return ". ".join(text_parts)

    @classmethod
    def build_news_summary_text(cls, news: NewsArticle, company_name: str) -> str:
        """Formulates a comprehensive news representation for semantic RAG search."""
        return (
            f"News for {company_name} ({news.company.ticker}) published by {news.source} on {news.published_at.strftime('%Y-%m-%d')}. "
            f"Title: {news.title}. "
            f"Content: {news.content or ''}. "
            f"AI Sentiment: {news.sentiment}. "
            f"Classification: {news.classification}."
        )

    @classmethod
    def index_all_data(cls, db: Session) -> Dict[str, int]:
        """Scans the database and incrementally indexes all companies and news articles."""
        if cls._index is None:
            cls.initialize_store()

        companies_indexed = 0
        news_indexed = 0

        # Index Companies
        companies = db.query(Company).filter(Company.is_active == True).all()
        for company in companies:
            doc_id = f"company_{company.ticker}"
            # Check if already in metadata
            if any(meta["id"] == doc_id for meta in cls._metadata):
                continue
            
            text = cls.build_company_summary_text(company)
            cls.add_to_store(
                doc_id=doc_id,
                category="company",
                ticker=company.ticker,
                text=text,
                source_id=company.id
            )
            companies_indexed += 1

        # Index News
        news_articles = db.query(NewsArticle).all()
        for article in news_articles:
            doc_id = f"news_{article.id}"
            if any(meta["id"] == doc_id for meta in cls._metadata):
                continue

            text = cls.build_news_summary_text(article, article.company.name)
            cls.add_to_store(
                doc_id=doc_id,
                category="news",
                ticker=article.company.ticker,
                text=text,
                source_id=article.id
            )
            news_indexed += 1

        if companies_indexed > 0 or news_indexed > 0:
            cls.save_store()

        return {
            "companies_indexed": companies_indexed,
            "news_indexed": news_indexed,
            "total_indexed": len(cls._metadata)
        }

    @classmethod
    def get_index_status(cls) -> Dict[str, Any]:
        """Gets current counts of indexed items in the store without triggering re-indexing."""
        if cls._index is None:
            cls.initialize_store()
        
        companies = sum(1 for m in cls._metadata if m.get("category") == "company")
        news = sum(1 for m in cls._metadata if m.get("category") == "news")
        total = len(cls._metadata)
        
        last_updated = None
        if os.path.exists(FAISS_INDEX_PATH):
            mtime = os.path.getmtime(FAISS_INDEX_PATH)
            from datetime import datetime
            last_updated = datetime.fromtimestamp(mtime).isoformat()
            
        return {
            "companies_indexed": companies,
            "news_indexed": news,
            "total_indexed": total,
            "last_updated": last_updated
        }

