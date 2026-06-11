"""
Query API Endpoints.
Exposes endpoints for chat, vector indexing, and starter query suggestions.
"""

import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
import anyio

from app.core.database import SessionLocal, get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.query_engine import QueryEngine
from app.services.embedding_service import EmbeddingService
from app.core.security import get_current_user_id
from app.models.user import User
from app.core.plans import get_effective_plan
from app.core.cache import cache
from datetime import datetime

logger = logging.getLogger("app.api.query")
router = APIRouter()

# ── Dependency ────────────────────────────────────────────────────────
def get_sync_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── Schemas ───────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []

class ChatResponse(BaseModel):
    answer: str
    type: str  # 'text' | 'table' | 'chart' | 'comparison'
    data: Optional[Dict[str, Any]] = None
    suggestions: Optional[List[str]] = []
    links: Optional[List[Dict[str, str]]] = []
    sources: Optional[List[str]] = []

class IndexStatusResponse(BaseModel):
    companies_indexed: int
    news_indexed: int
    total_indexed: int
    last_updated: Optional[str] = None

# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat_query(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    async_db: AsyncSession = Depends(get_db),
    db=Depends(get_sync_db)
):
    """
    Process natural language financial query using conversation history and RAG pipeline.
    """
    # Check Plan Limits
    user_stmt = select(User).where(User.id == user_id)
    user_res = await async_db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    
    if user:
        plan = get_effective_plan(user)
        if plan == "free":
            raise HTTPException(status_code=403, detail="Free plan does not support AI Chat Queries. Please upgrade.")
        elif plan == "standard":
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            rate_limit_key = f"chat_limit:{user_id}:{today_str}"
            try:
                current_usage = await cache.client.incr(rate_limit_key)
                if current_usage == 1:
                    await cache.client.expire(rate_limit_key, 86400) # 24 hours
                if current_usage > 10:
                    raise HTTPException(status_code=429, detail="Standard plan is limited to 10 queries per day. Please upgrade to Pro for unlimited access.")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error checking chat rate limit: {e}")

    try:
        # Convert Pydantic history objects to plain dicts
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]
        
        # Execute query engine
        result = await QueryEngine.execute_query(
            db,
            request.message,
            history_dicts,
            background_tasks=background_tasks
        )
        return result
    except Exception as e:
        logger.error(f"Error in chat_query: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")

@router.post("/index", response_model=IndexStatusResponse)
async def trigger_reindexing(
    db=Depends(get_sync_db)
):
    """
    Triggers incremental vector store re-indexing for all active companies and news.
    """
    try:
        status = await anyio.to_thread.run_sync(
            EmbeddingService.index_all_data,
            db
        )
        return status
    except Exception as e:
        logger.error(f"Error indexing data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")

@router.get("/index", response_model=IndexStatusResponse)
async def get_indexing_status():
    """
    Returns the current counts and metadata status of the vector database without re-indexing.
    """
    try:
        status = EmbeddingService.get_index_status()
        return status
    except Exception as e:
        logger.error(f"Error getting indexing status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch indexing status: {str(e)}")

@router.get("/suggestions", response_model=List[str])
async def get_query_suggestions(
    db=Depends(get_sync_db)
):
    """
    Returns dynamically generated starter suggestions for queries.
    """
    # Standard starter queries
    suggestions = [
        "Which PSU banks improved ROE in last 3 quarters?",
        "Compare TCS and Infosys on margins and growth",
        "Show me undervalued auto stocks",
        "Which stocks in the database have negative sentiment?",
        "Explain TCS promoters pledge status"
    ]
    return suggestions
