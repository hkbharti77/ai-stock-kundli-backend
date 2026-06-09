import logging
import math
from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.financial import Financial
from app.models.price_history import PriceHistory
from app.models.news_article import NewsArticle
from app.models.agent_output import AgentOutput
from app.services.llm import LLMService

logger = logging.getLogger("RiskAnalystAgent")

class RiskAnalystAgent:
    @classmethod
    async def analyze_company(cls, db: Session, company: Company) -> AgentOutput:
        """
        Runs the full corporate risk and governance analyst agent.
        Calculates price volatility, checks promoter pledging patterns, audits debt leverage ratios,
        scans news feeds for active SEBI/legal warnings, queries the LLM, and persists/updates the AgentOutput table.
        """
        logger.info(f"Starting corporate risk analysis agent for {company.name} ({company.ticker})")

        # 1. Fetch latest annual financials for ownership and debt info
        annual_financials: List[Financial] = sorted(
            [f for f in company.financials if f.period_type == "annual"],
            key=lambda x: x.period_end
        )
        
        promoter_holding_pct = 54.5
        promoter_pledge_pct = 0.0
        fii_holding_pct = 18.2
        dii_holding_pct = 12.3
        public_holding_pct = 15.0
        debt_equity = 0.5
        
        if annual_financials:
            latest = annual_financials[-1]
            if latest.promoter_holding_pct is not None:
                promoter_holding_pct = float(latest.promoter_holding_pct)
            if latest.promoter_pledge_pct is not None:
                promoter_pledge_pct = float(latest.promoter_pledge_pct)
            if latest.fii_holding_pct is not None:
                fii_holding_pct = float(latest.fii_holding_pct)
            if latest.dii_holding_pct is not None:
                dii_holding_pct = float(latest.dii_holding_pct)
            if latest.public_holding_pct is not None:
                public_holding_pct = float(latest.public_holding_pct)
            if latest.debt_equity is not None:
                debt_equity = float(latest.debt_equity)

        # 2. Compute 30-day Price Volatility (std dev of daily returns)
        cutoff_date = datetime.utcnow() - timedelta(days=45)
        prices = db.query(PriceHistory).filter(
            PriceHistory.company_id == company.id,
            PriceHistory.date >= cutoff_date
        ).order_by(PriceHistory.date.asc()).all()
        
        volatility = 22.5  # Standard default standard deviation percentage
        if len(prices) >= 5:
            daily_returns = []
            for i in range(1, len(prices)):
                prev_close = float(prices[i-1].close)
                curr_close = float(prices[i].close)
                if prev_close > 0:
                    ret = (curr_close - prev_close) / prev_close * 100.0
                    daily_returns.append(ret)
            
            if daily_returns:
                n = len(daily_returns)
                mean = sum(daily_returns) / n
                variance = sum((r - mean) ** 2 for r in daily_returns) / max(n - 1, 1)
                # Annualized standard deviation: scale by sqrt(252)
                volatility = math.sqrt(variance) * math.sqrt(252)
                if math.isnan(volatility):
                    volatility = 22.5
        
        # 3. Check for Regulatory or Legal Alerts in News Table
        is_global = (company.exchange or "").upper() not in ["NSE", "BSE", "NSI", "BOM"]
        regulator = "sec" if is_global else "sebi"
        legal_keywords = [regulator, "legal", "fraud", "scam", "lawsuit", "investigation", "litigation", "court", "complaint", "order", "audit"]
        news_records = db.query(NewsArticle).filter(NewsArticle.company_id == company.id).all()
        
        has_legal_alerts = False
        for ns in news_records:
            title_lower = (ns.title or "").lower()
            content_lower = (ns.content or "").lower()
            if any(kw in title_lower or kw in content_lower for kw in legal_keywords):
                has_legal_alerts = True
                break
                
        # Define the complete risk metrics dict
        metrics = {
            "promoter_holding_pct": promoter_holding_pct,
            "promoter_pledge_pct": promoter_pledge_pct,
            "fii_holding_pct": fii_holding_pct,
            "dii_holding_pct": dii_holding_pct,
            "public_holding_pct": public_holding_pct,
            "debt_equity": debt_equity,
            "volatility_30d": volatility,
            "has_legal_alerts": has_legal_alerts,
            "regulator_name": "SEBI" if not is_global else "SEC"
        }

        # 4. Generate Analysis using LLM Client
        analysis_data = await LLMService.generate_risk_analysis(
            ticker=company.ticker,
            company_name=company.name,
            metrics=metrics
        )

        # 5. Persist or Update AgentOutput
        stmt = select(AgentOutput).where(
            AgentOutput.company_id == company.id,
            AgentOutput.agent_type == "risk_analyst"
        ).order_by(AgentOutput.updated_at.desc()).limit(1)
        existing_output = db.execute(stmt).scalars().first()

        score = int(analysis_data.get("score", 75))
        confidence = int(analysis_data.get("confidence", 95))
        risk_category = str(analysis_data.get("risk_category", "Low"))
        strengths = list(analysis_data.get("strengths", []))
        concerns = list(analysis_data.get("concerns", []))
        reasoning = str(analysis_data.get("reasoning", ""))

        if existing_output:
            logger.info(f"Updating existing corporate risk analysis output for company ID {company.id}")
            existing_output.score = score
            existing_output.confidence = confidence
            existing_output.trend = risk_category  # Map risk category to the trend field
            existing_output.strengths = strengths
            existing_output.concerns = concerns
            existing_output.reasoning = reasoning
            existing_output.updated_at = datetime.utcnow()
            agent_output = existing_output
        else:
            logger.info(f"Creating new corporate risk analysis output for company ID {company.id}")
            agent_output = AgentOutput(
                company_id=company.id,
                agent_type="risk_analyst",
                score=score,
                confidence=confidence,
                trend=risk_category,  # Map risk category to the trend field
                strengths=strengths,
                concerns=concerns,
                reasoning=reasoning,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(agent_output)

        try:
            db.commit()
            db.refresh(agent_output)
            logger.info(f"Successfully persisted risk analyst agent output for company {company.ticker}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to commit risk agent output to database: {str(e)}")
            raise e

        return agent_output
