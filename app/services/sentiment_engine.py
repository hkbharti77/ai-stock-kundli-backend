"""
FinBERTSentimentService — High-performance sentiment classifier.
Attempts to run HuggingFace's ProsusAI/finbert model, falling back gracefully
to our pre-compiled Indian Financial Lexicon matcher.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("app.services.sentiment_engine")

# Global pipeline cache
_finbert_pipeline = None
_transformers_loaded = False

try:
    import torch
    from transformers import pipeline
    _transformers_loaded = True
except ImportError:
    logger.info("PyTorch/Transformers not installed. Gracefully using high-fidelity Lexicon Engine.")


class FinBERTSentimentService:
    """Handles core sentiment model execution."""

    @classmethod
    def _get_pipeline(cls):
        """Lazy load FinBERT pipeline to avoid loading model during import time."""
        global _finbert_pipeline
        if not _transformers_loaded:
            return None
        
        if _finbert_pipeline is None:
            try:
                logger.info("Initializing HuggingFace FinBERT pipeline (ProsusAI/finbert)...")
                # Load with local cache or fallback to cpu
                device = 0 if torch.cuda.is_available() else -1
                _finbert_pipeline = pipeline(
                    "sentiment-analysis",
                    model="ProsusAI/finbert",
                    device=device
                )
                logger.info("FinBERT pipeline loaded successfully.")
            except Exception as e:
                logger.warning(f"Failed to load FinBERT model: {e}. Falling back to Lexicon Engine.")
                _finbert_pipeline = None
        return _finbert_pipeline

    @classmethod
    async def analyze_text(cls, text: str) -> Dict[str, Any]:
        """
        Analyzes raw financial text.
        Returns a dictionary with scores between -100.0 and +100.0:
        {
           "score": float,
           "sentiment": "positive" | "negative" | "neutral",
           "confidence": float,
           "engine": "FinBERT" | "Lexicon"
        }
        """
        if not text or not text.strip():
            return {"score": 0.0, "sentiment": "neutral", "confidence": 100.0, "engine": "Lexicon"}

        # Attempt to run FinBERT
        pipe = cls._get_pipeline()
        if pipe is not None:
            try:
                # Limit text size to prevent token overflow
                truncated_text = text[:1000]
                results = pipe(truncated_text)
                if results:
                    res = results[0]
                    label = res.get("label", "neutral").lower()
                    score = res.get("score", 0.8)
                    
                    # Convert to -100 to +100 scale
                    if label == "positive":
                        overall_score = round(score * 100, 1)
                        sentiment = "positive"
                    elif label == "negative":
                        overall_score = round(-score * 100, 1)
                        sentiment = "negative"
                    else:
                        overall_score = 0.0
                        sentiment = "neutral"
                        
                    return {
                        "score": overall_score,
                        "sentiment": sentiment,
                        "confidence": round(score * 100, 1),
                        "engine": "FinBERT"
                    }
            except Exception as e:
                logger.warning(f"FinBERT pipeline execution failed: {e}. Falling back to Lexicon Engine.")

        # Fallback Lexicon Engine
        return cls._analyze_lexicon(text)

    @classmethod
    def _analyze_lexicon(cls, text: str) -> Dict[str, Any]:
        """High-fidelity financial lexicon parser."""
        text_lower = text.lower()
        
        # Word counters
        positive_words = [
            "profit", "growth", "record revenue", "contract win", "capex", "dividend",
            "upgrade", "target raised", "beats estimate", "buy", "outperform", "expand",
            "bullish", "record high", "earnings beat", "demand increase", "order win"
        ]
        
        negative_words = [
            "loss", "decline", "fall", "gst notice", "sebi penalty", "rbi fine", "scam",
            "fraud", "pledge", "resign", "debt increase", "weak quarter", "miss estimate",
            "underperform", "sell", "bearish", "compress", "headwind", "margin decrease"
        ]
        
        pos_count = sum(text_lower.count(w) for w in positive_words)
        neg_count = sum(text_lower.count(w) for w in negative_words)
        total = pos_count + neg_count
        
        if total == 0:
            return {"score": 10.0, "sentiment": "neutral", "confidence": 75.0, "engine": "Lexicon"}
            
        pos_ratio = pos_count / total
        neg_ratio = neg_count / total
        
        score = (pos_ratio - neg_ratio) * 100
        score = min(max(score, -100.0), 100.0)
        
        if score > 15.0:
            sentiment = "positive"
        elif score < -15.0:
            sentiment = "negative"
        else:
            sentiment = "neutral"
            
        confidence = 65.0 + min(total * 5.0, 25.0)
        
        return {
            "score": round(score, 1),
            "sentiment": sentiment,
            "confidence": confidence,
            "engine": "Lexicon"
        }
