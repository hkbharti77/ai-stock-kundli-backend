"""
Celery configuration, scheduler, and worker initialization.
"""

import os
from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

settings = get_settings()

# Initialize Celery
celery_app = Celery(
    "stockkundli",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Celery Configurations
celery_app.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=False,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    worker_prefetch_multiplier=1,
)

# Autodiscover tasks from app.tasks
celery_app.autodiscover_tasks(["app.tasks"])

# Scheduled Tasks (Celery Beat)
celery_app.conf.beat_schedule = {
    # 1. Weekly Company Master Ingestion (Sundays at 01:00 AM IST)
    "ingest-company-master-weekly": {
        "task": "app.tasks.ingestion_tasks.task_ingest_company_master",
        "schedule": crontab(day_of_week="0", hour="1", minute="0"),
    },
    # 2. Daily EOD Prices Ingestion (Mon-Fri at 16:30 IST - 30 minutes after market close)
    "ingest-eod-prices-daily": {
        "task": "app.tasks.ingestion_tasks.task_ingest_eod_prices",
        "schedule": crontab(day_of_week="1-5", hour="16", minute="30"),
    },
    # 3. Semi-Weekly Financials check (Mondays and Thursdays at 06:00 AM IST)
    "ingest-financials-semi-weekly": {
        "task": "app.tasks.ingestion_tasks.task_ingest_financials",
        "schedule": crontab(day_of_week="1,4", hour="6", minute="0"),
    },
    # 4. News Ingestion — Every 15 minutes during market hours (Mon–Sat, 09:00–16:00 IST)
    "ingest-news-every-15min": {
        "task": "app.tasks.ingestion_tasks.task_ingest_news",
        "schedule": crontab(day_of_week="1-6", hour="9-16", minute="0,15,30,45"),
    },
    # 5. SEBI Orders Scraper — Daily at 08:00 AM IST
    "scrape-sebi-orders-daily": {
        "task": "app.tasks.ingestion_tasks.task_scrape_sebi_orders",
        "schedule": crontab(hour="8", minute="0"),
    },
    # 6. Check and expire trials — Hourly
    "expire-trials-hourly": {
        "task": "app.tasks.ingestion_tasks.task_expire_trials",
        "schedule": crontab(minute="0"),
    },
}
