"""
Celery Task Definitions — Asynchronous market data workers.
"""

import logging
from celery.utils.log import get_task_logger
from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.services.ingestion import IngestionService

logger = get_task_logger("app.tasks.ingestion")


@celery_app.task(name="app.tasks.ingestion_tasks.task_ingest_company_master", bind=True, max_retries=3)
def task_ingest_company_master(self):
    """Weekly task to fetch and upsert NSE listed equities."""
    logger.info("Executing Celery Task: task_ingest_company_master")
    db = SessionLocal()
    try:
        res = IngestionService.ingest_company_master(db)
        
        # After updating company master, automatically trigger profile enrichment for new tickers
        logger.info("Triggering profile enrichment for newly added companies...")
        enrich_res = IngestionService.enrich_company_profiles(db, limit=100)
        
        db.close()
        return {"master_res": res, "enrich_res": enrich_res}
    except Exception as exc:
        db.rollback()
        db.close()
        logger.error(f"Error in task_ingest_company_master: {exc}")
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.tasks.ingestion_tasks.task_ingest_eod_prices", bind=True, max_retries=3)
def task_ingest_eod_prices(self, force_backfill: bool = False):
    """Daily task to fetch EOD price candles for top 500 stocks."""
    logger.info("Executing Celery Task: task_ingest_eod_prices")
    db = SessionLocal()
    try:
        # First, ensure we enrich profiles so we have correct market cap rankings
        logger.info("Enriched profiles before querying top 500 stocks...")
        IngestionService.enrich_company_profiles(db, limit=50)
        
        res = IngestionService.ingest_daily_prices(db, force_backfill=force_backfill)
        db.close()
        return res
    except Exception as exc:
        db.rollback()
        db.close()
        logger.error(f"Error in task_ingest_eod_prices: {exc}")
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="app.tasks.ingestion_tasks.task_ingest_financials", bind=True, max_retries=2)
def task_ingest_financials(self, limit: int = 20):
    """Periodic task to scrape 10-year financials for top companies."""
    logger.info(f"Executing Celery Task: task_ingest_financials (limit={limit})")
    db = SessionLocal()
    try:
        res = IngestionService.ingest_company_financials(db, limit=limit)
        db.close()
        return res
    except Exception as exc:
        db.rollback()
        db.close()
        logger.error(f"Error in task_ingest_financials: {exc}")
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.ingestion_tasks.task_ingest_news", bind=True, max_retries=2)
def task_ingest_news(self, limit: int = 50):
    """Periodic task (every 15 min) to ingest & classify news for top companies."""
    logger.info(f"Executing Celery Task: task_ingest_news (top {limit} companies)")
    from app.services.news import NewsService
    db = SessionLocal()
    try:
        res = NewsService.ingest_news_for_top_companies(db, limit=limit)
        db.close()
        return res
    except Exception as exc:
        db.rollback()
        db.close()
        logger.error(f"Error in task_ingest_news: {exc}")
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="app.tasks.ingestion_tasks.task_scrape_sebi_orders", bind=True, max_retries=2)
def task_scrape_sebi_orders(self):
    """Daily task to scrape SEBI orders and circulars via RSS feed."""
    logger.info("Executing Celery Task: task_scrape_sebi_orders")
    from app.services.news import NewsService
    db = SessionLocal()
    try:
        res = NewsService.scrape_sebi_orders(db)
        db.close()
        return res
    except Exception as exc:
        db.rollback()
        db.close()
        logger.error(f"Error in task_scrape_sebi_orders: {exc}")
        raise self.retry(exc=exc, countdown=300)
