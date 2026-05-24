"""
Subscriptions & Billing Endpoints — Razorpay payments integration with sandbox fail-safe overrides.
"""

import hmac
import hashlib
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user_id
from app.models.user import User

logger = logging.getLogger("app.api.subscriptions")
router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])
settings = get_settings()


class CheckoutRequest(BaseModel):
    plan: str = "starter"


class SandboxUpgradeRequest(BaseModel):
    plan: str = "starter"


@router.post("/checkout")
async def create_checkout_session(
    payload: CheckoutRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a Razorpay payment order.
    If RAZORPAY_KEY_ID is missing, it falls back to a Sandbox Mock Order.
    """
    plan_name = payload.plan.lower()
    if plan_name not in ["starter", "pro", "advisor"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan requested"
        )
        
    amount = 29900  # Default starter amount in paise (INR 299.00)
    if plan_name == "pro":
        amount = 79900
    elif plan_name == "advisor":
        amount = 499900
        
    # Check settings for keys
    if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        try:
            # We can create a real Razorpay Order via HTTP Basic Auth to Razorpay API
            # This is 100% robust and doesn't rely on third-party SDK dependencies
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
            else:
                logger.error(f"Razorpay API Error: {res.text}")
        except Exception as e:
            logger.error(f"Failed to create Razorpay Order: {e}")
            
    # Mock Order for development sandbox flow
    import random
    mock_order_id = f"order_mock_{random.randint(100000, 999999)}"
    return {
        "id": mock_order_id,
        "currency": "INR",
        "amount": amount,
        "key": "rzp_test_mockkey12345",
        "sandbox": True
    }


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Razorpay Webhooks. Updates user subscription tier on success.
    Verify signature or support simulated local sandbox webhook.
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    
    # ── Webhook Signature Validation ───────────────────────────
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
        # Dev local simulation override
        verified = True
    elif settings.RAZORPAY_WEBHOOK_SECRET:
        # Standard HMAC SHA256 Signature Verification
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
        
    # ── Process Webhook Event ──────────────────────────────────
    event = payload.get("event")
    if event in ["payment.captured", "order.paid"]:
        # Extract metadata
        entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        if not entity:
            entity = payload.get("payload", {}).get("order", {}).get("entity", {})
            
        notes = entity.get("notes", {})
        user_id_str = notes.get("user_id")
        plan_name = notes.get("plan", "starter")
        
        if user_id_str:
            user_id = int(user_id_str)
            stmt = select(User).where(User.id == user_id)
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()
            if user:
                user.plan = plan_name
                await db.commit()
                await reset_user_rate_limits(user_id)
                logger.info(f"User {user.email} plan upgraded to '{plan_name}' via webhook.")
                return {"status": "success", "message": f"User upgraded to {plan_name}"}
                
    return {"status": "ignored"}

async def reset_user_rate_limits(user_id: int):
    """
    Clear both Redis-based and local-in-memory rate limit entries for this user
    upon subscription plan upgrade/change.
    """
    from datetime import datetime
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    limit_key = f"ratelimit:user:{user_id}:date:{today_str}"
    
    # 1. Clear local memory store key
    try:
        from app.api.v1.endpoints.companies import local_rate_limit_store
        if limit_key in local_rate_limit_store:
            del local_rate_limit_store[limit_key]
            logger.info(f"Cleared local memory rate limit for user {user_id}")
    except Exception as e:
        logger.error(f"Error clearing local memory rate limit: {e}")

    # 2. Clear Redis cache key
    try:
        from app.core.cache import cache
        redis_client = cache.client
        if redis_client:
            await redis_client.delete(limit_key)
            logger.info(f"Cleared Redis rate limit for user {user_id}")
    except Exception as e:
        logger.error(f"Error clearing Redis rate limit: {e}")



class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    plan: str = "starter"


@router.post("/verify")
async def verify_payment(
    payload: VerifyPaymentRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Verify Razorpay payment signature and upgrade user plan.
    Supports mock/sandbox keys in local development.
    """
    plan_name = payload.plan.lower()
    if plan_name not in ["starter", "pro", "advisor"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan requested"
        )

    # 1. Perform signature verification if real keys are present
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
            else:
                logger.error("Razorpay signature mismatch")
        except Exception as e:
            logger.error(f"Error verifying signature: {e}")
    else:
        # Sandbox / mock mode
        verified = True

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment signature verification failed"
        )

    # 2. Upgrade user plan in DB
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    user.plan = plan_name
    await db.commit()
    await db.refresh(user)
    await reset_user_rate_limits(user.id)

    return {
        "status": "success",
        "message": f"Payment verified. User plan upgraded to {plan_name}",
        "user": {
            "id": user.id,
            "email": user.email,
            "plan": user.plan
        }
    }


@router.post("/sandbox-upgrade")
async def sandbox_upgrade(
    payload: SandboxUpgradeRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Developer Sandbox Endpoint. Immediately sets the user's plan to 'starter' or requested tier.
    Perfect for testing the dashboard subscription UI state locally.
    """
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    requested_plan = payload.plan.lower()
    if requested_plan not in ["free", "starter", "pro", "advisor"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan level"
        )
        
    user.plan = requested_plan
    await db.commit()
    await db.refresh(user)
    await reset_user_rate_limits(user.id)
    
    return {
        "message": f"Sandbox: Subscription changed to {requested_plan}",
        "user_id": user.id,
        "plan": user.plan
    }
