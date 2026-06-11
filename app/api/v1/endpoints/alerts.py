from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user_id
from app.models.company import Company
from app.models.alert_rule import AlertRule
from app.models.alert_history import AlertHistory
from app.models.watchlist import Watchlist
from app.models.user_event import UserEvent
from app.models.user import User
from app.core.plans import get_effective_plan
from app.core.websocket import manager

router = APIRouter()


class AlertRuleCreate(BaseModel):
    ticker: Optional[str] = None
    trigger_type: str  # price_movement, volume_spike, news_event, sentiment_shift, etc.
    threshold_value: Optional[float] = None
    delivery_channel: str = "both"  # push, email, both
    quiet_hours_enabled: bool = True


class AlertRuleUpdate(BaseModel):
    threshold_value: Optional[float] = None
    delivery_channel: Optional[str] = None
    is_active: Optional[bool] = None
    quiet_hours_enabled: Optional[bool] = None
    mute_duration_hours: Optional[int] = None  # mute rule for X hours


@router.get("/rules", response_model=dict)
async def get_alert_rules(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Retrieves all configured alert rules for a user."""
    stmt = select(AlertRule, Company).outerjoin(Company, Company.id == AlertRule.company_id).where(AlertRule.user_id == user_id)
    res = await db.execute(stmt)
    results = res.all()

    rules_list = []
    for rule, comp in results:
        rules_list.append({
            "id": rule.id,
            "ticker": comp.ticker if comp else "GLOBAL",
            "company_name": comp.name if comp else "All Active Equities",
            "trigger_type": rule.trigger_type,
            "threshold_value": rule.threshold_value,
            "delivery_channel": rule.delivery_channel,
            "is_active": rule.is_active,
            "quiet_hours_enabled": rule.quiet_hours_enabled,
            "is_muted": rule.muted_until > datetime.utcnow() if rule.muted_until else False,
            "created_at": rule.created_at.strftime("%Y-%m-%d %H:%M:%S")
        })

    return {"rules": rules_list}


@router.post("/rules", response_model=dict)
async def create_alert_rule(rule_in: AlertRuleCreate, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Creates a new alert rule."""
    company_id = None
    
    user_stmt = select(User).where(User.id == user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    
    if user:
        plan = get_effective_plan(user)
        if plan == "free":
            raise HTTPException(status_code=403, detail="Free plan does not support Price Alerts. Please upgrade.")
        elif plan == "standard":
            count_stmt = select(func.count(AlertRule.id)).where(AlertRule.user_id == user_id)
            current_count = await db.scalar(count_stmt)
            if current_count >= 3:
                raise HTTPException(status_code=403, detail="Standard plan is limited to 3 active alerts. Please upgrade to Pro.")
                
    if rule_in.ticker:
        stmt = select(Company).where(Company.ticker == rule_in.ticker.strip().upper())
        res = await db.execute(stmt)
        company = res.scalar()
        if not company:
            raise HTTPException(status_code=404, detail=f"Company with ticker {rule_in.ticker} not found")
        company_id = company.id

    rule = AlertRule(
        user_id=user_id,
        company_id=company_id,
        trigger_type=rule_in.trigger_type,
        threshold_value=rule_in.threshold_value,
        delivery_channel=rule_in.delivery_channel,
        quiet_hours_enabled=rule_in.quiet_hours_enabled,
        is_active=True
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    return {"status": "success", "rule_id": rule.id, "message": "Alert rule created successfully."}


@router.put("/rules/{rule_id}", response_model=dict)
async def update_alert_rule(rule_id: int, update_in: AlertRuleUpdate, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Updates or mutes an existing alert rule."""
    stmt = select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)
    res = await db.execute(stmt)
    rule = res.scalar()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    if update_in.threshold_value is not None:
        rule.threshold_value = update_in.threshold_value
    if update_in.delivery_channel is not None:
        rule.delivery_channel = update_in.delivery_channel
    if update_in.is_active is not None:
        rule.is_active = update_in.is_active
    if update_in.quiet_hours_enabled is not None:
        rule.quiet_hours_enabled = update_in.quiet_hours_enabled
    if update_in.mute_duration_hours is not None:
        if update_in.mute_duration_hours > 0:
            rule.muted_until = datetime.utcnow() + timedelta(hours=update_in.mute_duration_hours)
        else:
            rule.muted_until = None

    await db.commit()
    return {"status": "success", "message": "Alert rule updated successfully."}


@router.delete("/rules/{rule_id}", response_model=dict)
async def delete_alert_rule(rule_id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Deletes an alert rule."""
    stmt = delete(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)
    await db.execute(stmt)
    await db.commit()
    return {"status": "success", "message": "Alert rule deleted successfully."}


@router.get("/history", response_model=dict)
async def get_alert_history(user_id: int = Depends(get_current_user_id), ticker: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Retrieves chronological alert logs list."""
    stmt = select(AlertHistory, Company).outerjoin(Company, Company.id == AlertHistory.company_id).where(AlertHistory.user_id == user_id)
    
    if ticker:
        stmt = stmt.where(Company.ticker == ticker.strip().upper())
    else:
        # Fetch user's watchlist company IDs
        watchlist_stmt = select(Watchlist.company_id).where(Watchlist.user_id == user_id)
        wl_res = await db.execute(watchlist_stmt)
        watchlist_company_ids = [r[0] for r in wl_res.all() if r[0] is not None]

        # Fetch user's visited/searched stock tickers from UserEvents
        events_stmt = select(UserEvent.event_data).where(
            UserEvent.user_id == user_id,
            UserEvent.event_name.in_(["view_stock", "search_stock"])
        )
        evt_res = await db.execute(events_stmt)
        visited_tickers = set()
        for r in evt_res.all():
            data = r[0]
            if isinstance(data, dict) and "ticker" in data:
                visited_tickers.add(data["ticker"].strip().upper())

        # Resolve visited/searched tickers to company IDs
        visited_company_ids = []
        if visited_tickers:
            comp_stmt = select(Company.id).where(Company.ticker.in_(list(visited_tickers)))
            comp_res = await db.execute(comp_stmt)
            visited_company_ids = [r[0] for r in comp_res.all()]

        # Combine company IDs
        allowed_company_ids = list(set(watchlist_company_ids + visited_company_ids))

        # Filter the statement (allow global/system alerts with company_id = None too)
        if allowed_company_ids:
            stmt = stmt.where(
                (AlertHistory.company_id.in_(allowed_company_ids)) | 
                (AlertHistory.company_id == None)
            )
        else:
            stmt = stmt.where(AlertHistory.company_id == None)
        
    stmt = stmt.order_by(AlertHistory.delivered_at.desc()).limit(50)
    res = await db.execute(stmt)
    results = res.all()

    logs = []
    for log, comp in results:
        logs.append({
            "id": log.id,
            "ticker": comp.ticker if comp else "GLOBAL",
            "company_name": comp.name if comp else "System Wide",
            "title": log.title,
            "message": log.message,
            "severity": log.severity,
            "channel": log.channel,
            "delivered_at": log.delivered_at.strftime("%Y-%m-%d %H:%M:%S")
        })

    return {"alerts": logs}


@router.websocket("/ws/{user_id}")
async def websocket_alerts_endpoint(websocket: WebSocket, user_id: int):
    """WebSocket endpoint connecting web clients to receive push notifications."""
    await manager.connect(websocket, user_id)
    try:
        while True:
            # Maintain connection alive, ignore incoming user text payloads
            data = await websocket.receive_text()
            # Respond to ping to keep the connection alive
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
