import logging
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.macro import MacroData
from app.models.agent_output import AgentOutput
from app.services.llm import LLMService

logger = logging.getLogger("MacroAnalystAgent")

class MacroAnalystAgent:
    @classmethod
    async def analyze_company(cls, db: Session, company: Company) -> AgentOutput:
        """
        Runs the full sector-specific macroeconomic research agent.
        Gathers system-wide economic variables (repo rates, inflation index, forex levels, FII capital flows),
        correlates them with the company's specific industrial sector, queries the LLM/simulation fallback,
        and persists/updates the AgentOutput table.
        """
        logger.info(f"Starting macroeconomic sector analyst agent for {company.name} ({company.ticker})")

        # 1. Fetch system-wide macroeconomic metrics from database
        macro_records = db.query(MacroData).all()
        macro_variables = {
            "repo_rate": 6.50,
            "cpi_inflation": 4.85,
            "fii_flows_monthly": 12450.0,
            "inr_usd": 83.45
        }
        
        for rec in macro_records:
            if rec.indicator in macro_variables:
                macro_variables[rec.indicator] = float(rec.value)

        # 2. Get company's operational sector (fallback to General)
        sector = company.sector or "General Equities"

        # 3. Generate Analysis using LLM Client
        analysis_data = await LLMService.generate_macro_analysis(
            ticker=company.ticker,
            company_name=company.name,
            sector=sector,
            macro_variables=macro_variables
        )

        # 4. Persist or Update AgentOutput
        stmt = select(AgentOutput).where(
            AgentOutput.company_id == company.id,
            AgentOutput.agent_type == "macro_analyst"
        ).order_by(AgentOutput.updated_at.desc()).limit(1)
        existing_output = db.execute(stmt).scalars().first()

        score = int(analysis_data.get("score", 60))
        confidence = int(analysis_data.get("confidence", 92))
        trend = str(analysis_data.get("trend", "neutral"))
        strengths = list(analysis_data.get("strengths", []))
        concerns = list(analysis_data.get("concerns", []))
        reasoning = str(analysis_data.get("reasoning", ""))

        if existing_output:
            logger.info(f"Updating existing macroeconomic analysis output for company ID {company.id}")
            existing_output.score = score
            existing_output.confidence = confidence
            existing_output.trend = trend
            existing_output.strengths = strengths
            existing_output.concerns = concerns
            existing_output.reasoning = reasoning
            existing_output.updated_at = datetime.utcnow()
            agent_output = existing_output
        else:
            logger.info(f"Creating new macroeconomic analysis output for company ID {company.id}")
            agent_output = AgentOutput(
                company_id=company.id,
                agent_type="macro_analyst",
                score=score,
                confidence=confidence,
                trend=trend,
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
            logger.info(f"Successfully persisted macro analyst agent output for company {company.ticker}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to commit macro agent output to database: {str(e)}")
            raise e

        return agent_output
