"""
News — Pydantic schemas for NewsArticle and news-related API responses.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class NewsArticleResponse(BaseModel):
    id: int
    company_id: int
    title: str
    content: Optional[str] = None
    source: str
    url: Optional[str] = None
    published_at: datetime
    classification: str
    impact_score: int
    sentiment: str
    risk_flags: Optional[List[str]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NewsListResponse(BaseModel):
    ticker: str
    articles: List[NewsArticleResponse]
    count: int
    sentiment_breakdown: Dict[str, int]
    sentiment_trend: List[Dict[str, Any]]


class NewsAnalysisResponse(BaseModel):
    id: int
    company_id: int
    agent_type: str
    score: int
    confidence: int
    trend: Optional[str]
    news_sentiment: Optional[str] = None
    strengths: Optional[List[str]] = None
    concerns: Optional[List[str]] = None
    reasoning: Optional[str] = None
    top_material_events: Optional[List[Dict[str, Any]]] = None
    risk_flags: Optional[List[str]] = None
    sentiment_trend_30d: Optional[str] = None
    article_count_analyzed: Optional[int] = None
    sentiment_trend_data: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
