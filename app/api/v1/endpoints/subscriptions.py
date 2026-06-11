"""
Subscriptions & Billing Endpoints — Razorpay payments integration with sandbox fail-safe overrides.
"""

import hmac
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user_id
from app.core.plans import PLAN_PRICES_INR, TRIAL_PRICE_INR, TRIAL_DURATION_DAYS
from app.models.user import User
from app.core.email import send_subscription_receipt_email

logger = logging.getLogger("app.api.subscriptions")
router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])
settings = get_settings()


class CheckoutRequest(BaseModel):
    plan: str = "standard"


class SandboxUpgradeRequest(BaseModel):
    plan: str = "standard"


@router.post("/trial")
async def create_trial_session(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a Razorpay payment order for the ₹10 2-Day Pro Trial.
    """
    # Check if user already used trial
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    from app.api.v1.endpoints.auth import check_trial_eligibility
    is_eligible = await check_trial_eligibility(user, db)
    if not is_eligible:
        raise HTTPException(status_code=400, detail="Trial already used. Please upgrade to a full plan.")
        
    amount = int(TRIAL_PRICE_INR * 100)
    
    if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        try:
            import httpx
            auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
            data = {
                "amount": amount,
                "currency": "INR",
                "receipt": f"receipt_usr_{user_id}_trial",
                "notes": {
                    "user_id": str(user_id),
                    "plan": "pro_trial"
                }
            }
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://api.razorpay.com/v1/orders",
                    json=data,
                    auth=auth
                )
            if res.status_code == 200:
                order_data = res.json()
                return {
                    "id": order_data["id"],
                    "currency": "INR",
                    "amount": amount,
                    "key": settings.RAZORPAY_KEY_ID,
                    "sandbox": False
                }
        except Exception as e:
            logger.error(f"Failed to create trial Razorpay Order: {e}")
            
    import random
    return {
        "id": f"order_mock_{random.randint(100000, 999999)}",
        "currency": "INR",
        "amount": amount,
        "key": "rzp_test_mockkey12345",
        "sandbox": True
    }


@router.post("/checkout")
async def create_checkout_session(
    payload: CheckoutRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a Razorpay payment order.
    """
    plan_name = payload.plan.lower()
    if plan_name not in ["standard", "pro"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan requested"
        )
        
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    
    amount_inr = PLAN_PRICES_INR.get(plan_name, 299)
    
    if user and user.plan in PLAN_PRICES_INR and user.subscription_status == "active" and user.subscription_started_at:
        current_plan_inr = PLAN_PRICES_INR[user.plan]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        days_passed = (now - user.subscription_started_at).days
        
        # Only prorate if upgrading and within the 30-day cycle
        if 0 <= days_passed < 30 and current_plan_inr < amount_inr:
            unused_days = 30 - days_passed
            daily_rate = current_plan_inr / 30.0
            unused_amount = unused_days * daily_rate
            amount_inr = max(0, amount_inr - unused_amount)
            
    amount = int(amount_inr * 100)
        
    if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        try:
            import httpx
            auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
            data = {
                "amount": amount,
                "currency": "INR",
                "receipt": f"receipt_usr_{user_id}_{plan_name}",
                "notes": {
                    "user_id": str(user_id),
                    "plan": plan_name
                }
            }
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://api.razorpay.com/v1/orders",
                    json=data,
                    auth=auth
                )
            if res.status_code == 200:
                order_data = res.json()
                return {
                    "id": order_data["id"],
                    "currency": "INR",
                    "amount": amount,
                    "key": settings.RAZORPAY_KEY_ID,
                    "sandbox": False
                }
        except Exception as e:
            logger.error(f"Failed to create Razorpay Order: {e}")
            
    import random
    return {
        "id": f"order_mock_{random.randint(100000, 999999)}",
        "currency": "INR",
        "amount": amount,
        "key": "rzp_test_mockkey12345",
        "sandbox": True
    }


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Razorpay Webhooks. Updates user subscription tier on success.
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    
    verified = False
    payload = {}
    
    try:
        import json
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload formatting"
        )
        
    if signature == "mock-sandbox-signature":
        verified = True
    elif settings.RAZORPAY_WEBHOOK_SECRET:
        expected_sig = hmac.new(
            settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected_sig, signature):
            verified = True

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification failed"
        )
        
    event = payload.get("event")
    if event in ["payment.captured", "order.paid", "subscription.completed"]:
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        if not entity:
            entity = payload.get("payload", {}).get("order", {}).get("entity", {})
            
        notes = entity.get("notes", {})
        user_id_str = notes.get("user_id")
        plan_name = notes.get("plan", "standard")
        
        if user_id_str:
            user_id = int(user_id_str)
            stmt = select(User).where(User.id == user_id)
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()
            if user:
                now = datetime.now(timezone.utc)
                if plan_name in ["pro_trial", "standard_trial"]:
                    user.plan = plan_name.replace("_trial", "")
                    user.subscription_status = "trialing"
                    user.trial_expires_at = now + timedelta(days=TRIAL_DURATION_DAYS)
                    user.trial_used = True
                else:
                    user.plan = plan_name
                    user.subscription_status = "active"
                    user.subscription_started_at = now
                    
                await db.commit()
                await reset_user_rate_limits(user_id)
                
                # Send email receipt
                amount = int(PLAN_PRICES_INR.get(plan_name, 299) * 100)
                if plan_name in ["pro_trial", "standard_trial"]:
                    amount = int(TRIAL_PRICE_INR * 100)
                order_id = entity.get("id", "webhook_event")
                background_tasks.add_task(
                    send_subscription_receipt_email,
                    user.email,
                    plan_name,
                    amount,
                    order_id
                )
                
                return {"status": "success"}
                
    elif event == "subscription.cancelled":
         entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
         notes = entity.get("notes", {})
         user_id_str = notes.get("user_id")
         if user_id_str:
            user_id = int(user_id_str)
            stmt = select(User).where(User.id == user_id)
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()
            if user:
                user.subscription_status = "cancelled"
                user.plan = "free"
                await db.commit()
                await reset_user_rate_limits(user_id)
                return {"status": "success"}
                
    return {"status": "ignored"}


async def reset_user_rate_limits(user_id: int):
    """
    Clear both Redis-based and local-in-memory rate limit entries for this user
    upon subscription plan upgrade/change.
    """
    from datetime import datetime
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    limit_key = f"ratelimit:user:{user_id}:date:{today_str}"
    
    try:
        from app.api.v1.endpoints.companies import local_rate_limit_store
        if limit_key in local_rate_limit_store:
            del local_rate_limit_store[limit_key]
    except Exception:
        pass

    try:
        from app.core.cache import cache
        redis_client = cache.client
        if redis_client:
            await redis_client.delete(limit_key)
    except Exception:
        pass


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    plan: str = "standard"


@router.post("/verify")
async def verify_payment(
    payload: VerifyPaymentRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Verify Razorpay payment signature and upgrade user plan.
    """
    plan_name = payload.plan.lower()
    if plan_name not in ["standard", "pro", "pro_trial", "standard_trial"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan requested"
        )

    verified = False
    if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        try:
            signature_payload = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}"
            expected_sig = hmac.new(
                settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
                signature_payload.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(expected_sig, payload.razorpay_signature):
                verified = True
        except Exception:
            pass
    else:
        verified = True

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment signature verification failed"
        )

    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if plan_name in ["pro_trial", "standard_trial"]:
        user.plan = plan_name.replace("_trial", "")
        user.subscription_status = "trialing"
        user.trial_expires_at = now + timedelta(days=TRIAL_DURATION_DAYS)
        user.trial_used = True
    else:
        user.plan = plan_name
        user.subscription_status = "active"
        user.subscription_started_at = now
        
    await db.commit()
    await db.refresh(user)
    await reset_user_rate_limits(user.id)

    # Send email receipt
    amount = int(PLAN_PRICES_INR.get(plan_name, 299) * 100)
    if plan_name in ["pro_trial", "standard_trial"]:
        amount = int(TRIAL_PRICE_INR * 100)
    background_tasks.add_task(
        send_subscription_receipt_email,
        user.email,
        plan_name,
        amount,
        payload.razorpay_order_id
    )

    return {
        "status": "success",
        "message": f"Payment verified. User plan upgraded.",
        "user": {
            "id": user.id,
            "plan": user.plan,
            "subscription_status": user.subscription_status
        }
    }


@router.post("/sandbox-upgrade")
async def sandbox_upgrade(
    payload: SandboxUpgradeRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Developer Sandbox Endpoint. Immediately sets the user's plan.
    """
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    requested_plan = payload.plan.lower()
    if requested_plan not in ["free", "standard", "pro", "pro_trial", "standard_trial"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan level"
        )
        
    amount_inr = PLAN_PRICES_INR.get(requested_plan, 299)
    if requested_plan in ["pro_trial", "standard_trial"]:
        amount_inr = TRIAL_PRICE_INR
        
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if user.plan in PLAN_PRICES_INR and user.subscription_status == "active" and user.subscription_started_at:
        current_plan_inr = PLAN_PRICES_INR[user.plan]
        days_passed = (now - user.subscription_started_at).days
        if 0 <= days_passed < 30 and current_plan_inr < amount_inr:
            unused_days = 30 - days_passed
            daily_rate = current_plan_inr / 30.0
            unused_amount = unused_days * daily_rate
            amount_inr = max(0, amount_inr - unused_amount)
            
    amount = int(amount_inr * 100)

    if requested_plan in ["pro_trial", "standard_trial"]:
        user.plan = requested_plan.replace("_trial", "")
        user.subscription_status = "trialing"
        user.trial_expires_at = now + timedelta(days=TRIAL_DURATION_DAYS)
        user.trial_used = True
    else:
        user.plan = requested_plan
        user.subscription_status = "active"
        if requested_plan == "free":
            user.subscription_status = "expired"
            user.trial_expires_at = None
        user.subscription_started_at = now
        
    await db.commit()
    await db.refresh(user)
    await reset_user_rate_limits(user.id)
    
    # Send email receipt if upgraded to a paid plan or trial
    if requested_plan in ["standard", "pro", "pro_trial", "standard_trial"]:
        background_tasks.add_task(
            send_subscription_receipt_email,
            user.email,
            requested_plan,
            amount,
            "sandbox_order_123"
        )
    
    return {
        "message": f"Sandbox: Subscription changed to {requested_plan}",
        "user_id": user.id,
        "plan": user.plan,
        "status": user.subscription_status
    }
