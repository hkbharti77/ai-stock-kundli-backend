"""
AlertEngine — Sprint 13
Coordinates real-time market event processing, trigger evaluations, quiet hours, and deduplication fatigue rules.
"""

import logging
from datetime import datetime, timedelta
import pytz
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.alert_rule import AlertRule
from app.models.alert_history import AlertHistory
from app.models.company import Company
from app.core.websocket import manager

logger = logging.getLogger("app.services.alert_engine")


class AlertEngine:
    """Core real-time trigger evaluation and delivery pipeline."""

    @classmethod
    def process_market_event(
        cls,
        db: Session,
        company_id: int,
        event_type: str,
        current_value: float,
        title: str,
        message: str,
        severity: str = "info",
        rule_owner_id: int = None
    ) -> int:
        """
        Processes a real-time event against configured alert rules.
        Filters by quiet hours and fatigue deduplication before dispatching notifications.
        Returns the count of triggered alerts.
        """
        # Resolve company
        company = db.query(Company).filter(Company.id == company_id).first()
        ticker = company.ticker if company else "GLOBAL"

        # Query active rules matching this trigger type and company
        query = db.query(AlertRule).filter(
            AlertRule.is_active == True,
            AlertRule.trigger_type == event_type
        )
        if company_id:
            query = query.filter((AlertRule.company_id == company_id) | (AlertRule.company_id == None))
        if rule_owner_id:
            query = query.filter(AlertRule.user_id == rule_owner_id)
            
        rules = query.all()
        triggered_count = 0

        for rule in rules:
            # Skip if user has muted the rule
            if rule.muted_until and rule.muted_until > datetime.utcnow():
                logger.info(f"[Alert Engine] Rule {rule.id} is muted for user {rule.user_id}. Skipping.")
                continue

            # Evaluate thresholds based on trigger type
            is_triggered = False
            if event_type == "price_movement":
                # Current value represents the percentage move
                is_triggered = abs(current_value) >= (rule.threshold_value or 0.0)
            elif event_type == "volume_spike":
                # Current value represents volume avg multiple
                is_triggered = current_value >= (rule.threshold_value or 0.0)
            elif event_type == "sentiment_shift":
                # Current value represents points shift
                is_triggered = abs(current_value) >= (rule.threshold_value or 0.0)
            else:
                # Earnings, technical breakouts, risk flags, signal changes trigger on event
                is_triggered = True

            if not is_triggered:
                continue

            # ── 1. Fatigue Deduplication Check (Same event type + company + user in last 1 hour) ──
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            recent_alert = db.query(AlertHistory).filter(
                AlertHistory.user_id == rule.user_id,
                AlertHistory.company_id == company_id,
                AlertHistory.alert_rule_id == rule.id,
                AlertHistory.delivered_at >= one_hour_ago
            ).first()

            if recent_alert:
                logger.info(f"[Alert Engine] Suppressing duplicate alert for user {rule.user_id} (Deduplication within 1hr active).")
                continue

            # ── 2. Quiet Hours Check (11:00 PM - 7:00 AM IST) ──
            ist_tz = pytz.timezone("Asia/Kolkata")
            now_ist = datetime.now(ist_tz)
            is_quiet_hours = now_ist.hour >= 23 or now_ist.hour < 7

            if rule.quiet_hours_enabled and is_quiet_hours and severity != "critical":
                logger.info(f"[Alert Engine] Suppressing non-critical alert during Quiet Hours (11PM-7AM IST) for user {rule.user_id}.")
                continue

            # Trigger dispatch
            triggered_count += 1
            channels = [rule.delivery_channel] if rule.delivery_channel != "both" else ["push", "email"]
            
            # ── Check user subscription tier for Pro+ SMS gating ──
            from app.models.user import User
            user = db.query(User).filter(User.id == rule.user_id).first()
            user_plan = user.plan.lower() if user and user.plan else "free"
            phone_number = user.phone if user and user.phone else "+91 99999 99999"

            # If user is Pro or above, automatically append SMS delivery channel if they chose both/push/email/sms
            if user_plan in ["pro", "advisor", "admin"] and "sms" not in channels:
                channels.append("sms")

            for channel in channels:
                # Deliver & Log
                history_entry = AlertHistory(
                    user_id=rule.user_id,
                    alert_rule_id=rule.id,
                    company_id=company_id,
                    title=f"[{ticker}] {title}",
                    message=message,
                    severity=severity,
                    channel=channel,
                    delivered_at=datetime.utcnow()
                )
                
                # Perform channel-specific delivery
                if channel == "push":
                    db.add(history_entry)
                    # WebSocket Real-Time Push
                    payload = {
                        "event": "alert_triggered",
                        "title": f"[{ticker}] {title}",
                        "message": message,
                        "severity": severity,
                        "ticker": ticker,
                        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    # We run this asynchronously
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        if loop.is_running():
                            loop.create_task(manager.send_personal_message(payload, rule.user_id))
                    except RuntimeError:
                        pass
                
                elif channel == "email":
                    db.add(history_entry)
                    # Simulated AWS SES Email Dispatch
                    logger.info(
                        f"\n=======================================================\n"
                        f"[SIMULATED EMAIL DISPATCH VIA AWS SES]\n"
                        f"Recipient User ID: {rule.user_id}\n"
                        f"Subject: Critical Alert - [{ticker}] {title}\n"
                        f"Message: {message}\n"
                        f"Delivery Status: SUCCESS (200 OK)\n"
                        f"======================================================="
                    )

                elif channel == "sms":
                    # Check SMS caps limit (Max 10 per user per day)
                    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                    sms_sent_today = db.query(AlertHistory).filter(
                        AlertHistory.user_id == rule.user_id,
                        AlertHistory.channel == "sms",
                        AlertHistory.delivered_at >= today_start
                    ).count()

                    if sms_sent_today >= 10:
                        logger.warning(f"[Alert Engine] Suppressing SMS for user {rule.user_id} - reached daily limit of 10.")
                        continue

                    # Log the SMS delivery
                    db.add(history_entry)
                    
                    # Simulated Twilio/MSG91 SMS Console Dispatch
                    import random
                    mock_sid = f"SM{random.randint(100000000000, 999999999999)}"
                    logger.info(
                        f"\n=======================================================\n"
                        f"[SIMULATED SMS DISPATCH VIA TWILIO / MSG91]\n"
                        f"Recipient User: {user.email if user else 'Unknown'} | Phone: {phone_number}\n"
                        f"Daily SMS Count: {sms_sent_today + 1} / 10\n"
                        f"Message Body: [{ticker}] {title}: {message}\n"
                        f"Twilio Message ID: {mock_sid}\n"
                        f"Delivery Status: DELIVERED (200 OK)\n"
                        f"======================================================="
                    )

        db.commit()
        return triggered_count
