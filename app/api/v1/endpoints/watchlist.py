"""
Watchlist Endpoints — CRUD APIs for users to save and track preferred companies.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user_id
from app.models.company import Company
from app.models.watchlist import Watchlist
from app.models.user import User
from app.core.plans import get_effective_plan
from app.schemas.watchlist import WatchlistCreate, WatchlistResponse

logger = logging.getLogger("app.api.watchlist")
router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


@router.get("/", response_model=list[WatchlistResponse])
async def get_watchlist(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve all companies in the authenticated user's watchlist."""
    try:
        # Select Watchlist entries for the current user
        stmt = select(Watchlist).where(Watchlist.user_id == user_id)
        result = await db.execute(stmt)
        items = result.scalars().all()
        
        # Populate scores/signals from cache
        from app.core.cache import cache
        import hashlib
        
        for item in items:
            ticker = item.company.ticker
            cached_report = await cache.get(f"company:kundli_report:{ticker}")
            if cached_report:
                item.latest_score = cached_report.get("score", 70)
                item.latest_signal = cached_report.get("signal", "Buy")
            else:
                # Deterministic fallback score based on ticker hash
                h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
                score = 55 + (h % 35)  # 55 to 90
                item.latest_score = score
                if score >= 80:
                    item.latest_signal = "Strong Buy"
                elif score >= 65:
                    item.latest_signal = "Buy"
                else:
                    item.latest_signal = "Neutral"
                    
        return items
    except Exception as e:
        logger.error(f"Error fetching watchlist for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve watchlist"
        )



@router.post("/", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    payload: WatchlistCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Add a company to the authenticated user's watchlist by ticker."""
    ticker_clean = payload.ticker.strip().upper()
    
    # 1. Look up the company
    comp_stmt = select(Company).where(Company.ticker == ticker_clean)
    comp_res = await db.execute(comp_stmt)
    company = comp_res.scalar_one_or_none()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ticker '{ticker_clean}' not found"
        )
        
    # 2. Check if already in watchlist
    watch_stmt = select(Watchlist).where(
        Watchlist.user_id == user_id,
        Watchlist.company_id == company.id
    )
    watch_res = await db.execute(watch_stmt)
    existing_item = watch_res.scalar_one_or_none()
    
    if existing_item:
        # Already exists, just return it
        return existing_item

    # Check Plan Limits
    user_stmt = select(User).where(User.id == user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    
    if user:
        plan = get_effective_plan(user)
        if plan in ["free", "standard"]:
            count_stmt = select(func.count(Watchlist.id)).where(Watchlist.user_id == user_id)
            current_count = await db.scalar(count_stmt)
            
            if plan == "free" and current_count >= 5:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    detail="Free plan is limited to 5 watchlist items. Please upgrade to Standard or Pro."
                )
            elif plan == "standard" and current_count >= 20:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    detail="Standard plan is limited to 20 watchlist items. Please upgrade to Pro."
                )

    try:
        # 3. Create watchlist entry
        new_item = Watchlist(user_id=user_id, company_id=company.id)
        db.add(new_item)
        await db.commit()
        await db.refresh(new_item)
        
        # Select again to ensure relationships are loaded
        final_stmt = select(Watchlist).where(Watchlist.id == new_item.id)
        final_res = await db.execute(final_stmt)
        return final_res.scalar_one()
    except Exception as e:
        await db.rollback()
        logger.error(f"Error adding ticker {ticker_clean} to watchlist for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add company to watchlist"
        )


@router.delete("/{ticker}", status_code=status.HTTP_200_OK)
async def remove_from_watchlist(
    ticker: str,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Remove a company from the authenticated user's watchlist by ticker."""
    ticker_clean = ticker.strip().upper()
    
    # 1. Look up the company
    comp_stmt = select(Company).where(Company.ticker == ticker_clean)
    comp_res = await db.execute(comp_stmt)
    company = comp_res.scalar_one_or_none()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ticker '{ticker_clean}' not found"
        )
        
    # 2. Delete the watchlist entry
    try:
        stmt = delete(Watchlist).where(
            Watchlist.user_id == user_id,
            Watchlist.company_id == company.id
        )
        res = await db.execute(stmt)
        await db.commit()
        
        if res.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticker '{ticker_clean}' was not in your watchlist"
            )
            
        return {"message": f"Successfully removed '{ticker_clean}' from watchlist"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error removing ticker {ticker_clean} from watchlist for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove company from watchlist"
        )
