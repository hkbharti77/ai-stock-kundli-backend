"""
Developer API Router — Endpoints for enterprise API access, webhook subscriptions, and key management.
"""

import logging
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio
import anyio

from fastapi import APIRouter, Depends, HTTPException, Query, Header, status, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, SessionLocal
from app.core.cache import cache
from app.core.security import get_current_user_id, verify_api_key
from app.models.user import User
from app.models.company import Company
from app.models.agent_output import AgentOutput
from app.models.signal_history import SignalHistory
from app.models.developer import APIKey, APIUsageLog, WebhookSubscription, WebhookDeliveryLog
from app.schemas.developer import (
    APIKeyCreate,
    APIKeyResponse,
    APIKeyCreatedResponse,
    WebhookSubscriptionCreate,
    WebhookSubscriptionResponse,
    WebhookDeliveryLogResponse,
    UsageStatsResponse,
    DailyVolumePoint,
    DailyCostPoint,
    StatusDistributionPoint,
)
from app.schemas.kundli_report import KundliReportResponse
from app.services.agent_fundamental import FundamentalAnalystAgent
from app.services.agent_technical import TechnicalAnalystAgent
from app.services.agent_news import NewsAnalystAgent
from app.services.agent_risk import RiskAnalystAgent
from app.services.agent_macro import MacroAnalystAgent
from app.services.agent_valuation import ValuationAnalystAgent
from app.services.agent_sector import SectorAnalystAgent
from app.services.agent_aggregator import AggregatorAgent

logger = logging.getLogger("app.api.developer")
router = APIRouter(tags=["Developer / Enterprise API"])


# ── Dependency to fetch Current User ──
async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
) -> User:
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Endpoint: Get Structured Kundli Report (Protected by API Key) ──
@router.get("/kundli/{ticker}", response_model=dict)
async def get_enterprise_kundli_report(
    ticker: str,
    lang: str = Query("en", description="Report language: 'en' or 'hi'"),
    api_key: APIKey = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Enterprise API — Get structured multi-agent market intelligence (Kundli report) for any ticker.
    Protected by X-API-Key header.
    """
    ticker_clean = ticker.strip().upper()
    cache_key = f"company:kundli_report:{ticker_clean}:{lang.lower()}"

    # Try cache first
    cached = await cache.get(cache_key)
    report_dict = None
    status_code = 200

    if cached:
        cached["cached"] = True
        report_dict = cached
    else:
        # Check company existence
        stmt = select(Company).where(
            Company.ticker == ticker_clean,
            Company.is_active == True,
        )
        res = await db.execute(stmt)
        company = res.scalar_one_or_none()
        if not company:
            # Log usage for failed lookups as well
            await log_api_usage(db, api_key, "/api/v1/kundli/{ticker}", ticker_clean, 404)
            raise HTTPException(status_code=404, detail=f"Company '{ticker_clean}' not found.")

        # Ensure agent outputs are present & fresh
        await ensure_agent_outputs_fresh(company)

        # Generate report
        def _run_aggregator():
            sync_db = SessionLocal()
            try:
                sync_company = sync_db.query(Company).filter(Company.ticker == ticker_clean).first()
                if not sync_company:
                    return None
                report = AggregatorAgent.generate_kundli_report(sync_db, sync_company, lang=lang)
                return report.model_dump(mode="json")
            finally:
                sync_db.close()

        report_dict = await anyio.to_thread.run_sync(_run_aggregator)
        if report_dict is None:
            await log_api_usage(db, api_key, "/api/v1/kundli/{ticker}", ticker_clean, 404)
            raise HTTPException(status_code=404, detail=f"Company '{ticker_clean}' not found.")

        # Cache for 4 hours
        await cache.set(cache_key, report_dict, ttl_seconds=14400)

    # Log API Usage & Billing
    await log_api_usage(db, api_key, "/api/v1/kundli/{ticker}", ticker_clean, status_code)

    return report_dict


# ── Endpoint: Get Historical Signal Changes (Protected by API Key) ──
@router.get("/kundli/{ticker}/history", response_model=List[dict])
async def get_historical_signals(
    ticker: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    api_key: APIKey = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Enterprise API — Fetch historical signal transitions for strategy replaying and backtesting.
    Protected by X-API-Key header.
    """
    ticker_clean = ticker.strip().upper()
    stmt = select(Company).where(Company.ticker == ticker_clean)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()
    if not company:
        await log_api_usage(db, api_key, "/api/v1/kundli/{ticker}/history", ticker_clean, 404)
        raise HTTPException(status_code=404, detail=f"Company '{ticker_clean}' not found.")

    query = select(SignalHistory).where(SignalHistory.company_id == company.id)

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.where(SignalHistory.changed_at >= start_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")

    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.where(SignalHistory.changed_at < end_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD.")

    query = query.order_by(SignalHistory.changed_at.desc())
    history_res = await db.execute(query)
    records = history_res.scalars().all()

    payload = []
    for r in records:
        payload.append({
            "ticker": ticker_clean,
            "old_score": r.old_score,
            "new_score": r.new_score,
            "old_signal": r.old_signal,
            "new_signal": r.new_signal,
            "changed_at": r.changed_at.isoformat()
        })

    await log_api_usage(db, api_key, "/api/v1/kundli/{ticker}/history", ticker_clean, 200)
    return payload


# ── API Key Management ──

@router.get("/developer/keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all active/inactive API keys for the current user."""
    stmt = select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post("/developer/keys", response_model=APIKeyCreatedResponse)
async def generate_api_key(
    payload: APIKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a new secure API key. The plain text key will be returned only once."""
    raw_key = f"sk_live_{secrets.token_hex(24)}"
    hashed = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    prefix = raw_key[:12]

    # Map user plan to rate limit tier
    tier = user.plan.lower()
    if tier not in ["free", "starter", "pro", "enterprise"]:
        tier = "free"

    new_key = APIKey(
        user_id=user.id,
        name=payload.name,
        prefix=prefix,
        hashed_key=hashed,
        is_active=True,
        rate_limit_tier=tier
    )
    db.add(new_key)
    await db.flush()

    # Create response and inject temporary plain_key
    new_key.plain_key = raw_key
    resp = APIKeyCreatedResponse.model_validate(new_key)

    await db.commit()
    return resp


@router.delete("/developer/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke or delete an API key by ID."""
    stmt = select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    res = await db.execute(stmt)
    key_obj = res.scalar_one_or_none()
    if not key_obj:
        raise HTTPException(status_code=404, detail="API Key not found")

    await db.delete(key_obj)
    await db.commit()
    return


@router.post("/developer/keys/{key_id}/rotate", response_model=APIKeyCreatedResponse)
async def rotate_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rotate an existing API key. Generates a new key and updates the database, revoking the old one."""
    stmt = select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    res = await db.execute(stmt)
    key_obj = res.scalar_one_or_none()
    if not key_obj:
        raise HTTPException(status_code=404, detail="API Key not found")

    raw_key = f"sk_live_{secrets.token_hex(24)}"
    hashed = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    prefix = raw_key[:12]

    key_obj.prefix = prefix
    key_obj.hashed_key = hashed
    key_obj.created_at = datetime.utcnow()
    key_obj.last_used_at = None

    db.add(key_obj)
    await db.flush()

    key_obj.plain_key = raw_key
    resp = APIKeyCreatedResponse.model_validate(key_obj)

    await db.commit()
    return resp


# ── Webhook Subscription Management ──

@router.get("/developer/webhooks", response_model=List[WebhookSubscriptionResponse])
async def list_webhooks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List webhook subscriptions for the current user."""
    stmt = select(WebhookSubscription).where(WebhookSubscription.user_id == user.id).order_by(WebhookSubscription.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post("/developer/webhooks", response_model=WebhookSubscriptionResponse)
async def create_webhook(
    payload: WebhookSubscriptionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Subscribe a URL to real-time signal change notifications."""
    signing_secret = f"whsec_{secrets.token_hex(24)}"
    
    # Process tickers to clean uppercase
    tickers_list = None
    if payload.tickers:
        tickers_list = [t.strip().upper() for t in payload.tickers if t.strip()]

    new_sub = WebhookSubscription(
        user_id=user.id,
        url=payload.url,
        secret=signing_secret,
        is_active=True,
        tickers=tickers_list
    )
    db.add(new_sub)
    await db.commit()
    await db.refresh(new_sub)
    return new_sub


@router.delete("/developer/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a webhook subscription."""
    stmt = select(WebhookSubscription).where(WebhookSubscription.id == webhook_id, WebhookSubscription.user_id == user.id)
    res = await db.execute(stmt)
    sub = res.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")

    await db.delete(sub)
    await db.commit()
    return


# ── Developer Usage & Billing Analytics ──

@router.get("/developer/usage", response_model=UsageStatsResponse)
async def get_usage_metrics(
    days: int = Query(30, description="Number of days of history to retrieve"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Fetch usage stats, total billing costs, status codes, and history graphs."""
    # 1. Get total calls & cost
    stmt_totals = select(
        func.count(APIUsageLog.id),
        func.sum(APIUsageLog.cost_inr)
    ).where(APIUsageLog.user_id == user.id)
    res_totals = await db.execute(stmt_totals)
    res_totals_row = res_totals.first()
    if res_totals_row:
        total_calls = res_totals_row[0] or 0
        total_cost = float(res_totals_row[1] or 0.0)
    else:
        total_calls = 0
        total_cost = 0.0

    # 2. Get status code distribution
    stmt_status = select(
        APIUsageLog.status_code,
        func.count(APIUsageLog.id)
    ).where(APIUsageLog.user_id == user.id).group_by(APIUsageLog.status_code)
    res_status = await db.execute(stmt_status)
    by_status = {str(row[0]): row[1] for row in res_status.all()}

    # 3. Get usage over the last N days
    timeframe_ago = datetime.utcnow() - timedelta(days=days)
    stmt_series = select(
        func.to_char(APIUsageLog.timestamp, "YYYY-MM-DD").label("day"),
        func.count(APIUsageLog.id).label("calls"),
        func.sum(APIUsageLog.cost_inr).label("cost")
    ).where(
        APIUsageLog.user_id == user.id,
        APIUsageLog.timestamp >= timeframe_ago
    ).group_by("day").order_by("day")
    res_series = await db.execute(stmt_series)
    series_rows = res_series.all()

    # Pre-populate N days of empty records to make the chart smooth
    day_map = { (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d"): {"calls": 0, "cost": 0.0} for i in range(days) }
    for r in series_rows:
        day_map[r[0]] = {"calls": r[1], "cost": float(r[2] or 0.0)}

    daily_volume = [
        DailyVolumePoint(date=d, count=metrics["calls"])
        for d, metrics in sorted(day_map.items())
    ]
    daily_cost = [
        DailyCostPoint(date=d, cost=metrics["cost"])
        for d, metrics in sorted(day_map.items())
    ]
    status_codes = [
        StatusDistributionPoint(status=k, count=v)
        for k, v in by_status.items()
    ]

    return {
        "total_calls": total_calls,
        "total_cost_inr": total_cost,
        "daily_volume": daily_volume,
        "daily_cost": daily_cost,
        "status_codes": status_codes
    }


# ── Helper Functions ──

async def log_api_usage(db: AsyncSession, api_key: APIKey, endpoint: str, ticker: str, status_code: int):
    """Calculate call cost based on tier & aggregate logs, and insert APIUsageLog."""
    cost = 5.0  # Standard pay-as-you-go cost (₹5)

    try:
        tier = api_key.user.plan.lower()
        if tier == "pro":
            cost = 0.0
        elif tier == "enterprise":
            # For enterprise, check if usage has exceeded 10,000 requests this calendar month
            now = datetime.utcnow()
            start_of_month = datetime(now.year, now.month, 1)
            stmt = select(func.count(APIUsageLog.id)).where(
                APIUsageLog.user_id == api_key.user_id,
                APIUsageLog.timestamp >= start_of_month
            )
            res = await db.execute(stmt)
            count = res.scalar() or 0
            # Under 10k is included, thereafter ₹2 per call
            cost = 2.0 if count >= 10000 else 0.0
    except Exception as e:
        logger.error(f"Error calculating call cost: {e}")

    log = APIUsageLog(
        api_key_id=api_key.id,
        user_id=api_key.user_id,
        tenant_id=getattr(api_key.user, "tenant_id", None),
        endpoint=endpoint,
        ticker=ticker,
        status_code=status_code,
        cost_inr=cost
    )
    db.add(log)
    await db.flush()

    # Also update last used timestamp on API Key
    api_key.last_used_at = datetime.utcnow()
    db.add(api_key)


async def ensure_agent_outputs_fresh(company: Company):
    """Verify that all 7 analyst agents have fresh outputs; run them in parallel if stale."""
    # Check outputs in DB
    sync_db = SessionLocal()
    try:
        existing_outputs = sync_db.query(AgentOutput).filter(AgentOutput.company_id == company.id).all()
        agent_map = {o.agent_type: o for o in existing_outputs}
    finally:
        sync_db.close()

    need_fundamental = "fundamental_analyst" not in agent_map or (datetime.utcnow() - agent_map["fundamental_analyst"].updated_at).days >= 7
    need_technical = "technical_analyst" not in agent_map or (datetime.utcnow() - agent_map["technical_analyst"].updated_at).days >= 1
    need_news = "news_analyst" not in agent_map or ((datetime.utcnow() - agent_map["news_analyst"].updated_at).total_seconds() / 3600) >= 4
    need_risk = "risk_analyst" not in agent_map or (datetime.utcnow() - agent_map["risk_analyst"].updated_at).days >= 3
    need_macro = "macro_analyst" not in agent_map or (datetime.utcnow() - agent_map["macro_analyst"].updated_at).days >= 7
    need_valuation = "valuation_analyst" not in agent_map or (datetime.utcnow() - agent_map["valuation_analyst"].updated_at).days >= 3
    need_sector = "sector_analyst" not in agent_map or (datetime.utcnow() - agent_map["sector_analyst"].updated_at).days >= 7

    if not (need_fundamental or need_technical or need_news or need_risk or need_macro or need_valuation or need_sector):
        return

    # Define thread runners
    def run_fundamental_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                if not comp.financials:
                    from app.services.ingestion import IngestionService
                    IngestionService.enrich_company_data_live(sync_db, comp)
                asyncio.run(FundamentalAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Fundamental agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_technical_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                from sqlalchemy import select
                from app.models.price_history import PriceHistory
                stmt_price = select(PriceHistory).where(PriceHistory.company_id == comp.id).limit(1)
                has_price = sync_db.execute(stmt_price).scalar() is not None
                if not has_price:
                    from app.services.ingestion import IngestionService
                    IngestionService.enrich_company_data_live(sync_db, comp)
                asyncio.run(TechnicalAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Technical agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_news_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                asyncio.run(NewsAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"News agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_risk_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                asyncio.run(RiskAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Risk agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_macro_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                asyncio.run(MacroAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Macro agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_valuation_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                asyncio.run(ValuationAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Valuation agent parallel thread error: {e}")
        finally:
            sync_db.close()

    def run_sector_sync(t_clean: str):
        sync_db = SessionLocal()
        try:
            comp = sync_db.query(Company).filter(Company.ticker == t_clean).first()
            if comp:
                asyncio.run(SectorAnalystAgent.analyze_company(sync_db, comp))
        except Exception as e:
            logger.error(f"Sector agent parallel thread error: {e}")
        finally:
            sync_db.close()

    # Orchestrate execution in thread pool
    async def run_agents_in_parallel():
        async with anyio.create_task_group() as tg:
            if need_fundamental:
                tg.start_soon(anyio.to_thread.run_sync, run_fundamental_sync, company.ticker)
            if need_technical:
                tg.start_soon(anyio.to_thread.run_sync, run_technical_sync, company.ticker)
            if need_news:
                tg.start_soon(anyio.to_thread.run_sync, run_news_sync, company.ticker)
            if need_risk:
                tg.start_soon(anyio.to_thread.run_sync, run_risk_sync, company.ticker)
            if need_macro:
                tg.start_soon(anyio.to_thread.run_sync, run_macro_sync, company.ticker)
            if need_valuation:
                tg.start_soon(anyio.to_thread.run_sync, run_valuation_sync, company.ticker)
            if need_sector:
                tg.start_soon(anyio.to_thread.run_sync, run_sector_sync, company.ticker)

    await run_agents_in_parallel()
