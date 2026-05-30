import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.price_history import PriceHistory
from app.models.agent_output import AgentOutput
from app.models.portfolio import PortfolioHolding
from app.models.user import User
from app.services.llm import LLMService

logger = logging.getLogger("PortfolioAdvisorAgent")

class PortfolioAdvisorAgent:
    @classmethod
    async def analyze_portfolio(cls, db: Session, user_id: int) -> Dict[str, Any]:
        """
        Runs comprehensive portfolio analysis: sector exposure, diversification, correlation risk,
        overall portfolio risk, and generates a personalized investment recommendation report.
        """
        logger.info(f"Starting portfolio analysis for user ID {user_id}")
        
        # 1. Fetch holdings
        holdings = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user_id).all()
        if not holdings:
            return cls._get_empty_portfolio_response()

        # 2. Get current pricing and enrich holdings
        holdings_enriched = []
        total_value = 0.0
        total_cost = 0.0
        
        for h in holdings:
            company = h.company
            shares = float(h.shares)
            avg_price = float(h.average_price)
            
            # Fetch latest price from PriceHistory, fallback to average_price
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == company.id
            ).order_by(PriceHistory.date.desc()).first()
            
            curr_price = float(latest_price_rec.close) if latest_price_rec else avg_price
            curr_value = shares * curr_price
            cost_basis = shares * avg_price
            pnl = curr_value - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            
            total_value += curr_value
            total_cost += cost_basis
            
            holdings_enriched.append({
                "holding": h,
                "ticker": company.ticker,
                "sector": company.sector or "Other",
                "shares": shares,
                "avg_price": avg_price,
                "current_price": curr_price,
                "current_value": curr_value,
                "total_cost": cost_basis,
                "pnl": pnl,
                "pnl_percentage": pnl_pct
            })

        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0

        # 3. Calculate Sector Allocations & Diversification Score (HHI)
        sector_totals = {}
        for he in holdings_enriched:
            sec = he["sector"]
            sector_totals[sec] = sector_totals.get(sec, 0.0) + he["current_value"]

        sector_allocations = []
        hhi = 0.0
        for sec, val in sector_totals.items():
            pct = (val / total_value) if total_value > 0 else 0.0
            hhi += pct ** 2
            sector_allocations.append({
                "sector": sec,
                "value": val,
                "percentage": pct * 100.0
            })

        # Sort allocations by percentage descending
        sector_allocations.sort(key=lambda x: x["percentage"], reverse=True)

        # Diversification Score = 100 * (1 - HHI), clamped between 0 and 100
        # If HHI = 1.0 (all in one sector), score is 0. If HHI = 0.2 (equally in 5 sectors), score is 80.
        diversification_score = max(0.0, min(100.0, 100.0 * (1.0 - hhi)))

        # Concentration risk alert text
        top_sector = sector_allocations[0] if sector_allocations else {"sector": "None", "percentage": 0.0}
        if top_sector["percentage"] > 50:
            concentration_risk = f"HIGH — {top_sector['percentage']:.1f}% in single sector ({top_sector['sector']})"
        elif top_sector["percentage"] > 35:
            concentration_risk = f"MEDIUM — {top_sector['percentage']:.1f}% in single sector ({top_sector['sector']})"
        else:
            concentration_risk = "LOW — Good sector spread"

        # 4. Calculate Portfolio Risk Score
        # Risk score is a weighted average of individual stocks' risk safety scores (reversed to risk)
        weighted_risk = 0.0
        for he in holdings_enriched:
            comp = he["holding"].company
            weight = he["current_value"] / total_value
            
            # Fetch latest safety score from RiskAnalystAgent
            risk_rec = db.query(AgentOutput).filter(
                AgentOutput.company_id == comp.id,
                AgentOutput.agent_type == "risk_analyst"
            ).first()
            
            safety_score = float(risk_rec.score) if risk_rec else 75.0  # fallback safety score
            # Convert safety (0-100, high is safe) to risk (0-100, high is risky)
            stock_risk = 100.0 - safety_score
            weighted_risk += weight * stock_risk

        portfolio_risk_score = max(0.0, min(100.0, weighted_risk))

        # 5. Calculate Correlation Matrix
        correlations, correlation_alerts = cls._compute_correlation_matrix(db, holdings_enriched)

        # 6. Generate AI Advisor recommendations via LLM
        user = db.query(User).filter(User.id == user_id).first()
        ai_advisor_report = await cls._generate_ai_advisor_report(
            user=user,
            total_value=total_value,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            risk_score=portfolio_risk_score,
            div_score=diversification_score,
            sector_allocs=sector_allocations,
            alerts=correlation_alerts
        )

        return {
            "total_value": total_value,
            "total_cost": total_cost,
            "total_pnl": total_pnl,
            "total_pnl_percentage": total_pnl_pct,
            "risk_score": portfolio_risk_score,
            "diversification_score": diversification_score,
            "concentration_risk": concentration_risk,
            "sector_allocations": sector_allocations,
            "correlations": correlations,
            "correlation_alerts": correlation_alerts,
            "ai_advisor_report": ai_advisor_report
        }

    @classmethod
    def _compute_correlation_matrix(cls, db: Session, holdings: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Calculates pairwise stock correlation coefficients for the portfolio based on last 30 days returns.
        """
        correlations = []
        alerts = []
        n = len(holdings)
        if n < 2:
            return correlations, alerts

        # Fetch returns for all tickers
        cutoff = datetime.utcnow() - timedelta(days=45)
        price_data = {}
        
        for h in holdings:
            comp = h["holding"].company
            prices = db.query(PriceHistory).filter(
                PriceHistory.company_id == comp.id,
                PriceHistory.date >= cutoff
            ).order_by(PriceHistory.date.asc()).all()
            
            # Compute daily returns
            returns = {}
            for i in range(1, len(prices)):
                prev = float(prices[i-1].close)
                curr = float(prices[i].close)
                d = prices[i].date
                if hasattr(d, "date"):
                    d = d.date()
                if prev > 0:
                    returns[d] = (curr - prev) / prev
            price_data[h["ticker"]] = (h["sector"], returns)

        tickers = list(price_data.keys())
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                t1 = tickers[i]
                t2 = tickers[j]
                sec1, rets1 = price_data[t1]
                sec2, rets2 = price_data[t2]
                
                # Find common dates
                common_dates = set(rets1.keys()).intersection(set(rets2.keys()))
                
                if len(common_dates) >= 5:
                    # Calculate Pearson Correlation Coefficient
                    x = [rets1[d] for d in common_dates]
                    y = [rets2[d] for d in common_dates]
                    
                    mean_x = sum(x) / len(x)
                    mean_y = sum(y) / len(y)
                    
                    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
                    den_x = sum((xi - mean_x) ** 2 for xi in x)
                    den_y = sum((yi - mean_y) ** 2 for yi in y)
                    
                    if den_x > 0 and den_y > 0:
                        r = num / math.sqrt(den_x * den_y)
                    else:
                        r = 0.0
                else:
                    # Fallback based on sector similarity
                    if sec1 == sec2:
                        r = 0.75
                    else:
                        r = 0.15

                correlations.append({
                    "ticker1": t1,
                    "ticker2": t2,
                    "correlation": r
                })

                # High correlation warning
                if r > 0.7:
                    alerts.append(
                        f"⚠️ High overlap: {t1} and {t2} have a high correlation coefficient of {r:.2f}. "
                        f"Consider consolidating or diversifying their weights to minimize downside risk."
                    )

        return correlations, alerts

    @classmethod
    async def evaluate_stock_fit(cls, db: Session, user_id: int, ticker: str) -> Dict[str, Any]:
        """
        Calculates a compatibility fit score (0-100) for a candidate equity relative to a user's current holdings.
        """
        # Lookup candidate company
        candidate = db.query(Company).filter(Company.ticker == ticker.upper()).first()
        if not candidate:
            return {
                "ticker": ticker.upper(),
                "fit_score": 0,
                "recommendation": "NOT RECOMMEND — Company not registered in system databases.",
                "reasons": ["Company ticker does not exist in master tables."],
                "sector": "Unknown",
                "current_weight": 0.0,
                "prospective_weight": 0.0
            }

        holdings = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user_id).all()
        
        # If empty portfolio, everything fits perfectly!
        if not holdings:
            return {
                "ticker": candidate.ticker,
                "fit_score": 95,
                "recommendation": "STRONG BUY — Fits perfectly as the first holding in your portfolio.",
                "reasons": [
                    "Excellent first brick for building your customized wealth structure.",
                    f"Initiates sector exposure in {candidate.sector or 'Other'}."
                ],
                "sector": candidate.sector or "Other",
                "current_weight": 0.0,
                "prospective_weight": 100.0
            }

        # Calculate current portfolio values
        total_value = 0.0
        sector_values = {}
        redundant = False
        
        for h in holdings:
            comp = h.company
            shares = float(h.shares)
            
            # Fetch latest price
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == comp.id
            ).order_by(PriceHistory.date.desc()).first()
            curr_price = float(latest_price_rec.close) if latest_price_rec else float(h.average_price)
            curr_val = shares * curr_price
            
            total_value += curr_val
            sec = comp.sector or "Other"
            sector_values[sec] = sector_values.get(sec, 0.0) + curr_val
            
            if comp.ticker == candidate.ticker:
                redundant = True

        # Calculate Fit Score Metrics
        candidate_sector = candidate.sector or "Other"
        sector_val = sector_values.get(candidate_sector, 0.0)
        sector_pct = (sector_val / total_value * 100.0) if total_value > 0 else 0.0
        
        # Fit score deduction rules
        fit_score = 85
        reasons = []
        
        if redundant:
            fit_score -= 20
            reasons.append(f"Redundancy: You already hold {candidate.ticker} in your portfolio. Accumulating more increases asset concentration.")
        else:
            reasons.append(f"Diversification cushion: {candidate.ticker} represents a brand new company addition to your holdings list.")

        # Sector check
        if sector_pct > 35.0:
            fit_score -= 15
            reasons.append(f"Sector Concentration: {candidate_sector} exposure is already elevated at {sector_pct:.1f}% in your portfolio.")
        elif sector_pct > 15.0:
            fit_score -= 5
            reasons.append(f"Moderate Sector Overlap: Already have {sector_pct:.1f}% exposure in the {candidate_sector} sector.")
        else:
            reasons.append(f"Sector enhancement: Introduces clean exposure to {candidate_sector} sector with negligible current overlap.")

        # Check correlation with biggest holding
        biggest_holding = None
        biggest_value = -1.0
        for h in holdings:
            comp = h.company
            shares = float(h.shares)
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == comp.id
            ).order_by(PriceHistory.date.desc()).first()
            curr_price = float(latest_price_rec.close) if latest_price_rec else float(h.average_price)
            curr_val = shares * curr_price
            if curr_val > biggest_value:
                biggest_value = curr_val
                biggest_holding = comp

        if biggest_holding and biggest_holding.ticker != candidate.ticker:
            # Let's approximate correlation or calculate it
            # To keep it fast, if same sector -> correlation 0.75, else 0.15
            correlation_est = 0.75 if biggest_holding.sector == candidate.sector else 0.15
            if correlation_est > 0.6:
                fit_score -= 10
                reasons.append(f"High correlation risk: High correlation ({correlation_est:.2f}) with your largest holding {biggest_holding.ticker}.")

        # Retrieve safety score
        risk_rec = db.query(AgentOutput).filter(
            AgentOutput.company_id == candidate.id,
            AgentOutput.agent_type == "risk_analyst"
        ).first()
        safety_score = risk_rec.score if risk_rec else 75
        
        if safety_score < 50:
            fit_score -= 15
            reasons.append(f"High risk asset: Candidate stock safety rating is sub-optimal ({safety_score}/100). Might drag down portfolio safety metrics.")
        elif safety_score >= 80:
            fit_score += 5
            reasons.append(f"Governance anchor: Premium safety score ({safety_score}/100) enhances overall portfolio balance.")

        fit_score = max(0, min(100, fit_score))

        # Build recommendation text
        if fit_score >= 75:
            rec = "STRONG BUY — Fits perfectly, enhancing diversification parameters."
        elif fit_score >= 55:
            rec = "BUY/NEUTRAL — Moderate fit. Monitor sector allocation carefully."
        else:
            rec = "AVOID/CAUTION — High redundancy or sector concentration risks detected."

        # Weight simulation
        simulated_total = total_value + (total_value * 0.1) # Simulate adding a 10% weight position
        prospective_pct = ((sector_val + (total_value * 0.1)) / simulated_total * 100.0) if simulated_total > 0 else 10.0

        return {
            "ticker": candidate.ticker,
            "fit_score": fit_score,
            "recommendation": rec,
            "reasons": reasons,
            "sector": candidate_sector,
            "current_weight": sector_pct,
            "prospective_weight": prospective_pct
        }

    @classmethod
    async def _generate_ai_advisor_report(
        cls,
        user: User,
        total_value: float,
        total_pnl: float,
        total_pnl_pct: float,
        risk_score: float,
        div_score: float,
        sector_allocs: List[Dict[str, Any]],
        alerts: List[str]
    ) -> str:
        """
        Queries the Gemini LLM service to synthesize custom portfolio diversification insights in Hinglish.
        """
        # Read user profile values
        risk_appetite = user.risk_appetite if user and user.risk_appetite else "Medium"
        horizon = user.horizon if user and user.horizon else "Long-term (3-5 years)"
        goal = user.goal if user and user.goal else "Wealth Accumulation"

        sector_summary = ", ".join([f"{s['sector']}: {s['percentage']:.1f}%" for s in sector_allocs])
        alerts_summary = "\n".join(alerts) if alerts else "No high correlation risks detected."

        prompt = f"""
You are a senior SEBI-registered Portfolio Strategist and AI Wealth Advisor.
Generate a comprehensive, premium AI Wealth Advisory Report for the user's custom equity portfolio based on the calculations below.

---
Portfolio Value: ₹{total_value:,.2f}
Total PnL: ₹{total_pnl:,.2f} ({total_pnl_pct:.2f}%)
Portfolio Risk Score: {risk_score:.1f}/100 (where 0 is completely safe, 100 is highly volatile/risky)
Diversification Score: {div_score:.1f}/100 (where 100 is perfectly spread, 0 is concentrated in single sector)
Sector Exposure: {sector_summary}
Correlation Warnings:
{alerts_summary}

User Financial Goals:
- Risk Tolerance: {risk_appetite}
- Investment Horizon: {horizon}
- Wealth Goal: {goal}
---

Your response must be a professional investment advisory report written in high-quality Markdown.
Structure your report with the following specific headers:
1. ### **Executive Portfolio Verdict**
   - Summary of current portfolio performance, risk profile alignment with user risk tolerance, and key overall advice.
2. ### **Sector Exposure & Diversification Insights**
   - Detailed review of sector weight allocation. Explain if the Diversification Score is healthy. Suggest areas of expansion.
3. ### **Correlation Risk & Concentration Alerts**
   - Address the correlation warnings. Explain how having high-correlation pairs (e.g. RELIANCE and sector counterparts) creates hidden risk during drawdowns.
4. ### **Actionable Strategic Adjustments**
   - Provide 3 clear, concrete next steps for the user (e.g., rebalancing, adding specific sectors, defensive anchors) to align perfectly with their horizon of '{horizon}'.

Write in a highly engaging, natural Hinglish-English mix (Hinglish naturally woven). Make the advice feel personalized, deep, and extremely premium. Avoid boilerplate text.
Do NOT use markdown code fences like ```markdown ... ```. Return ONLY the raw Markdown string.
"""
        keys = LLMService.get_api_keys()

        # Try the full LLM chain: DeepSeek -> Gemini -> GPT-4o -> Ollama (local)
        has_any_key = any([keys["deepseek"], keys["gemini"], keys["openai"], keys["ollama"]])
        if has_any_key:
            logger.info("Calling LLM chain for AI Portfolio Advisory report...")
            try:
                result = await LLMService.generate_text(prompt)
                if result:
                    return result
            except Exception as e:
                logger.error(f"Failed to generate LLM portfolio report: {str(e)}")

        # Fallback Simulation Report
        return cls._generate_fallback_advisor_report(risk_score, div_score, sector_allocs, alerts, risk_appetite, horizon)


    @classmethod
    def _generate_fallback_advisor_report(
        cls,
        risk_score: float,
        div_score: float,
        sector_allocs: List[Dict[str, Any]],
        alerts: List[str],
        risk_appetite: str,
        horizon: str
    ) -> str:
        """
        Fallback high-fidelity simulation report generator when no LLM key succeeds.
        """
        top_sector = sector_allocs[0]["sector"] if sector_allocs else "Technology"
        top_pct = sector_allocs[0]["percentage"] if sector_allocs else 100.0

        report = f"""### **Executive Portfolio Verdict**

Aapka portfolio risk score **{risk_score:.1f}/100** hai, aur iska comparison aapke registered profile risk appetite (**{risk_appetite}**) se karne par ye pata chalta hai ki aapka setup parameters ke aligned hai. Total diversification parameters **{div_score:.1f}/100** par print ho rahe hain, jo ki ek moderate-to-good safety structure represent karta hai. We recommend adding some defensive assets during minor corrections.

---

### **Sector Exposure & Diversification Insights**

* **Highest Sector Exposure**: Currently, **{top_sector}** represents **{top_pct:.1f}%** of your total portfolio weight. 
* {"Aapka exposure high concentration ki taraf badh raha hai. Sector weighting strictly 30% ke niche manage karna chahiye." if top_pct > 35 else "Aapka sector weight distribution badhiya hai, koi immediate high-exposure threat nahi dikh raha hai."}
* We advise building defensive anchors in sectors like *FMCG* and *Financial Services* to cushion the impact of tech-industry drawdowns.

---

### **Correlation Risk & Concentration Alerts**

* **Active Alerts**: {"Aapke portfolio mein high price-return correlation pairs identify huye hain, jo market correction ke dauran single-block decline trigger kar sakte hain." if alerts else "Zero high-correlation pairs identified. Your stock combinations display dynamic independence."}
* High correlation assets (specifically in {top_sector}) reduce the mathematical benefits of diversification. Jab index correction phase mein jayega, tab correlated stocks sabse tezi se downslide trigger karenge.

---

### **Actionable Strategic Adjustments**

1. **Rebalance the Tech Weight**: {top_sector} weights ko decrease karke 25% zone mein maintain karein, specifically booking profits at peak cycles.
2. **Defensive Inflow**: Introduce 10-15% weighting in stable dividend yield companies (like ITC, HDFCBANK) to secure capital appreciation buffers.
3. **Horizon Alignment**: Since your investment horizon is **{horizon}**, buy on dips strategy support zones par initiate karein aur core companies ko multi-year targets tak hold karein.
"""
        return report

    @classmethod
    def _get_empty_portfolio_response(cls) -> Dict[str, Any]:
        return {
            "total_value": 0.0,
            "total_cost": 0.0,
            "total_pnl": 0.0,
            "total_pnl_percentage": 0.0,
            "risk_score": 0.0,
            "diversification_score": 100.0,
            "concentration_risk": "LOW — Empty Portfolio",
            "sector_allocations": [],
            "correlations": [],
            "correlation_alerts": [],
            "ai_advisor_report": "### **Start Your Wealth Portfolio**\n\nAdd stocks manually or upload a CSV holding file above to receive premium multi-agent risk scoring and diversification metrics."
        }
