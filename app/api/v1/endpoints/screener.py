from typing import List, Optional
from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.company import Company
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class ScreenerResponse(BaseModel):
    id: int
    ticker: str
    name: str
    sector: Optional[str]
    latest_kundli_score: Optional[int]
    previous_kundli_score: Optional[int]
    last_analyzed_at: Optional[datetime]

@router.get("/top-rated", response_model=List[ScreenerResponse])
async def get_top_rated_stocks(limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Fetch Top 10 Stocks with Highest Kundli Score"""
    stmt = select(Company).where(
        Company.is_active == True,
        Company.latest_kundli_score != None
    ).order_by(
        desc(Company.latest_kundli_score)
    ).limit(limit)
    
    result = await db.execute(stmt)
    companies = result.scalars().all()
    
    return [
        ScreenerResponse(
            id=c.id,
            ticker=c.ticker,
            name=c.name,
            sector=c.sector,
            latest_kundli_score=c.latest_kundli_score,
            previous_kundli_score=c.previous_kundli_score,
            last_analyzed_at=c.last_analyzed_at
        )
        for c in companies
    ]

@router.get("/turning-bullish", response_model=List[ScreenerResponse])
async def get_bullish_stocks(limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Fetch Stocks whose score increased significantly (Turning Bullish)"""
    stmt = select(Company).where(
        Company.is_active == True,
        Company.latest_kundli_score != None,
        Company.previous_kundli_score != None,
        (Company.latest_kundli_score - Company.previous_kundli_score) >= 10
    ).order_by(
        desc(Company.latest_kundli_score - Company.previous_kundli_score)
    ).limit(limit)
    
    result = await db.execute(stmt)
    companies = result.scalars().all()
    
    return [
        ScreenerResponse(
            id=c.id,
            ticker=c.ticker,
            name=c.name,
            sector=c.sector,
            latest_kundli_score=c.latest_kundli_score,
            previous_kundli_score=c.previous_kundli_score,
            last_analyzed_at=c.last_analyzed_at
        )
        for c in companies
    ]
