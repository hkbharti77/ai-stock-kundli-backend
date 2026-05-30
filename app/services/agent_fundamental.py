import logging
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.financial import Financial
from app.models.agent_output import AgentOutput
from app.services.llm import LLMService

logger = logging.getLogger("FundamentalAnalystAgent")

class FundamentalAnalystAgent:
    @classmethod
    async def analyze_company(cls, db: Session, company: Company) -> AgentOutput:
        """
        Runs the full fundamental analyst agent on a company.
        Queries the database for historical financials, computes key metrics/CAGRs,
        queries the LLM, and persists/updates the AgentOutput table.
        """
        logger.info(f"Starting fundamental analysis agent for {company.name} ({company.ticker})")

        # 1. Fetch all annual financials sorted by period_end
        annual_financials: List[Financial] = sorted(
            [f for f in company.financials if f.period_type == "annual"],
            key=lambda x: x.period_end
        )

        if not annual_financials:
            logger.warning(f"No annual financials found for {company.ticker}. Triggering default metrics.")
            ratios = cls._compute_default_ratios()
            fin_summary = "No annual financial statements were available in the database."
        else:
            ratios = cls._compute_financial_ratios(annual_financials)
            is_global = company.exchange not in ["NSE", "BSE"]
            fin_summary = cls._generate_financials_summary(annual_financials, is_global=is_global)

        # 2. Call the LLM Client Service
        analysis_data = await LLMService.generate_fundamental_analysis(
            ticker=company.ticker,
            company_name=company.name,
            ratios=ratios,
            financial_statements_summary=fin_summary
        )

        # 3. Create or update the AgentOutput record in the database
        stmt = select(AgentOutput).where(
            AgentOutput.company_id == company.id,
            AgentOutput.agent_type == "fundamental_analyst"
        )
        existing_output = db.execute(stmt).scalar_one_or_none()

        # Safely parse JSON values
        score = int(analysis_data.get("score", 65))
        confidence = int(analysis_data.get("confidence", 90))
        trend = str(analysis_data.get("trend", "stable"))
        strengths = list(analysis_data.get("strengths", []))
        concerns = list(analysis_data.get("concerns", []))
        reasoning = str(analysis_data.get("reasoning", ""))

        if existing_output:
            logger.info(f"Updating existing fundamental analysis output for company ID {company.id}")
            existing_output.score = score
            existing_output.confidence = confidence
            existing_output.trend = trend
            existing_output.strengths = strengths
            existing_output.concerns = concerns
            existing_output.reasoning = reasoning
            existing_output.updated_at = datetime.utcnow()
            agent_output = existing_output
        else:
            logger.info(f"Creating new fundamental analysis output for company ID {company.id}")
            agent_output = AgentOutput(
                company_id=company.id,
                agent_type="fundamental_analyst",
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
            logger.info(f"Successfully persisted fundamental analyst agent output for company {company.ticker}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to commit agent output to database: {str(e)}")
            raise e

        return agent_output

    @staticmethod
    def _compute_financial_ratios(financials: List[Financial]) -> Dict[str, Any]:
        """
        Computes financial ratios, CAGRs, and operating margins.
        """
        latest = financials[-1]
        
        # Safe float conversion
        def get_val(val):
            return float(val) if val is not None else None

        roce = get_val(latest.roce)
        roe = get_val(latest.roe)
        debt_equity = get_val(latest.debt_equity)
        current_ratio = get_val(latest.current_ratio)
        
        latest_rev = get_val(latest.revenue)
        latest_ebitda = get_val(latest.ebitda)
        op_margin = (latest_ebitda / latest_rev * 100.0) if (latest_rev and latest_ebitda) else None

        # Compute 3-Year CAGR for Revenue & PAT
        rev_cagr = None
        pat_cagr = None
        
        if len(financials) >= 4:
            first_period = financials[-4]
            years = 3.0
            
            # Revenue CAGR
            start_rev = get_val(first_period.revenue)
            end_rev = get_val(latest.revenue)
            if start_rev and end_rev and start_rev > 0 and end_rev > 0:
                rev_cagr = ((end_rev / start_rev) ** (1.0 / years) - 1.0) * 100.0
                
            # PAT CAGR
            start_pat = get_val(first_period.pat)
            end_pat = get_val(latest.pat)
            if start_pat and end_pat and start_pat > 0 and end_pat > 0:
                pat_cagr = ((end_pat / start_pat) ** (1.0 / years) - 1.0) * 100.0

        return {
            "latest_roce": roce,
            "latest_roe": roe,
            "latest_debt_equity": debt_equity,
            "latest_current_ratio": current_ratio,
            "latest_revenue": latest_rev,
            "latest_ebitda": latest_ebitda,
            "latest_op_margin": op_margin,
            "revenue_cagr_3y": rev_cagr,
            "pat_cagr_3y": pat_cagr,
        }

    @staticmethod
    def _compute_default_ratios() -> Dict[str, Any]:
        return {
            "latest_roce": 15.0,
            "latest_roe": 12.0,
            "latest_debt_equity": 0.5,
            "latest_current_ratio": 1.5,
            "latest_revenue": None,
            "latest_ebitda": None,
            "latest_op_margin": 15.0,
            "revenue_cagr_3y": 8.0,
            "pat_cagr_3y": 10.0,
        }

    @staticmethod
    def _generate_financials_summary(financials: List[Financial], is_global: bool = False) -> str:
        """
        Creates a clean Markdown table summarizing the historical financial periods.
        """
        unit = "$M" if is_global else "Cr"
        summary_lines = [
            f"| Period End | Revenue ({unit}) | EBITDA ({unit}) | PAT ({unit}) | ROE (%) | ROCE (%) | Debt/Equity |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        
        for f in financials:
            rev = f"{float(f.revenue):.2f}" if f.revenue is not None else "—"
            ebitda = f"{float(f.ebitda):.2f}" if f.ebitda is not None else "—"
            pat = f"{float(f.pat):.2f}" if f.pat is not None else "—"
            roe = f"{float(f.roe):.2f}%" if f.roe is not None else "—"
            roce = f"{float(f.roce):.2f}%" if f.roce is not None else "—"
            de = f"{float(f.debt_equity):.2f}" if f.debt_equity is not None else "—"
            
            date_str = f.period_end.strftime("%Y-%m-%d")
            summary_lines.append(f"| {date_str} | {rev} | {ebitda} | {pat} | {roe} | {roce} | {de} |")

        return "\n".join(summary_lines)
