import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.company import Company
from app.services.agent_fundamental import FundamentalAnalystAgent
from app.services.agent_technical import TechnicalAnalystAgent
from app.services.agent_risk import RiskAnalystAgent
from app.services.agent_macro import MacroAnalystAgent
from app.services.agent_news import NewsAnalystAgent
from app.services.agent_valuation import ValuationAnalystAgent
from app.services.agent_sector import SectorAnalystAgent
from app.services.agent_aggregator import AggregatorAgent

logger = logging.getLogger("ScreenerJob")

async def analyze_company_for_screener(company: Company, db: SessionLocal):
    """Run all 7 agents sequentially with delay to avoid rate limits."""
    try:
        # Run agents sequentially
        await FundamentalAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await TechnicalAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await RiskAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await MacroAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await NewsAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await ValuationAnalystAgent.analyze_company(db, company)
        await asyncio.sleep(2)
        await SectorAnalystAgent.analyze_company(db, company)
        
        # Aggregate
        report = AggregatorAgent.generate_kundli_report(db, company)
        
        # Save to DB
        company.previous_kundli_score = company.latest_kundli_score
        company.latest_kundli_score = report.kundli_score
        company.last_analyzed_at = datetime.utcnow()
        db.commit()
        logger.info(f"Successfully screened {company.ticker} with score {report.kundli_score}")
    except Exception as e:
        logger.error(f"Error screening {company.ticker}: {e}")
        db.rollback()

def run_screener_batch():
    """Fetches a batch of companies and analyzes them."""
    db = SessionLocal()
    try:
        # Get 5 companies that haven't been analyzed in the last 24 hours
        cutoff = datetime.utcnow() - timedelta(hours=24)
        companies = db.query(Company).filter(
            Company.is_active == True,
            (Company.last_analyzed_at == None) | (Company.last_analyzed_at < cutoff)
        ).order_by(Company.last_analyzed_at.asc().nulls_first()).limit(5).all()
        
        if not companies:
            logger.info("No companies need screening right now.")
            return

        logger.info(f"Starting screener batch for {len(companies)} companies.")
        
        # We need to run the async functions in a new event loop since APScheduler might run sync jobs
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        for comp in companies:
            logger.info(f"Screening {comp.ticker}...")
            loop.run_until_complete(analyze_company_for_screener(comp, db))
            # Sleep between companies to avoid rate limit
            loop.run_until_complete(asyncio.sleep(10))
            
        loop.close()
        logger.info("Screener batch completed.")
        
    except Exception as e:
        logger.error(f"Error in screener batch: {e}")
    finally:
        db.close()
