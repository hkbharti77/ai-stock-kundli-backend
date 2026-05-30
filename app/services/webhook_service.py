"""
WebhookService — Sprint 29-30: Asynchronously dispatches rating signal changes
to active webhook subscribers with HMAC payload signatures and automatic retries.
"""

import json
import hmac
import hashlib
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.company import Company
from app.models.developer import WebhookSubscription, WebhookDeliveryLog

logger = logging.getLogger("app.services.webhook_service")


class WebhookService:
    """Service to manage and dispatch webhook events asynchronously."""

    @classmethod
    def trigger_signal_change(
        cls,
        db_session,
        company_id: int,
        old_signal: str,
        new_signal: str,
        old_score: float,
        new_score: float
    ):
        """
        Main entrypoint. Fetches subscriptions, filters by ticker if necessary,
        and schedules async webhook dispatch tasks.
        """
        # Fetch company details
        company = db_session.query(Company).filter(Company.id == company_id).first()
        if not company:
            logger.error(f"[WebhookService] Company ID {company_id} not found.")
            return

        ticker = company.ticker

        # Fetch active subscriptions
        subscriptions = (
            db_session.query(WebhookSubscription)
            .filter(WebhookSubscription.is_active == True)
            .all()
        )

        matching_subs = []
        for sub in subscriptions:
            # Check if subscription is filtered by tickers
            if sub.tickers and ticker not in sub.tickers:
                continue
            matching_subs.append(sub)

        if not matching_subs:
            return

        # Prepare payload
        payload = {
            "event": "signal_change",
            "ticker": ticker,
            "old_signal": old_signal,
            "new_signal": new_signal,
            "old_score": float(old_score) if old_score is not None else None,
            "new_score": float(new_score) if new_score is not None else None,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        # Schedule async dispatches
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                for sub in matching_subs:
                    loop.create_task(
                        cls.deliver_webhook_with_retry(
                            subscription_id=sub.id,
                            url=sub.url,
                            secret=sub.secret,
                            payload=payload
                        )
                    )
        except RuntimeError:
            # Fallback if no event loop is running (e.g. CLI or sync execution context)
            for sub in matching_subs:
                asyncio.run(
                    cls.deliver_webhook_with_retry(
                        subscription_id=sub.id,
                        url=sub.url,
                        secret=sub.secret,
                        payload=payload
                    )
                )

    @classmethod
    async def deliver_webhook_with_retry(
        cls,
        subscription_id: int,
        url: str,
        secret: str,
        payload: Dict[str, Any]
    ):
        """Dispatches the payload to the subscriber URL with retries and signature verification."""
        payload_str = json.dumps(payload, sort_keys=True)
        payload_bytes = payload_str.encode("utf-8")
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Stock-Kundli-Signature": signature,
            "User-Agent": "Stock-Kundli-Webhook-Dispatcher/1.0"
        }

        max_attempts = 3
        attempt = 1
        success = False
        last_status_code = None

        async with httpx.AsyncClient(timeout=10.0) as client:
            while attempt <= max_attempts:
                try:
                    logger.info(
                        f"[WebhookService] Delivering webhook subscription {subscription_id} (Attempt {attempt}/{max_attempts}) to {url}"
                    )
                    response = await client.post(url, content=payload_str, headers=headers)
                    last_status_code = response.status_code
                    
                    if 200 <= response.status_code < 300:
                        success = True
                        logger.info(
                            f"[WebhookService] Webhook subscription {subscription_id} delivered successfully with status {response.status_code}"
                        )
                        break
                    else:
                        logger.warning(
                            f"[WebhookService] Webhook subscription {subscription_id} returned status {response.status_code}"
                        )
                except Exception as e:
                    logger.error(
                        f"[WebhookService] Error delivering webhook subscription {subscription_id} (Attempt {attempt}): {e}"
                    )
                    last_status_code = None

                # Wait before next attempt (exponential backoff)
                if attempt < max_attempts:
                    await asyncio.sleep(attempt * 2)
                attempt += 1

        # Audit log the attempt using a standalone DB session to avoid session sharing issues
        sync_db = SessionLocal()
        try:
            log = WebhookDeliveryLog(
                subscription_id=subscription_id,
                event_type=payload["event"],
                response_status=last_status_code,
                is_successful=success,
                attempt_number=min(attempt, max_attempts)
            )
            sync_db.add(log)
            sync_db.commit()
        except Exception as e:
            logger.error(f"[WebhookService] Failed to log webhook delivery: {e}")
            sync_db.rollback()
        finally:
            sync_db.close()
