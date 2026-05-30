import logging
import math
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.company import Company
from app.models.price_history import PriceHistory
from app.models.agent_output import AgentOutput
from app.services.probability_engine import ProbabilityEngine

logger = logging.getLogger("PositionSizingService")

class PositionSizingEngine:
    @classmethod
    def calculate_position_size(
        cls,
        db: Session,
        ticker: str,
        total_capital: float,
        risk_profile: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        manual_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculates optimal position sizing using a Kelly-adjusted formula,
        constrained by the maximum Capital at Risk per trade rules.
        """
        logger.info(f"Calculating position sizing for {ticker} with capital ₹{total_capital} and profile {risk_profile}")

        # 1. Fetch current price from DB or fallback to manual input
        company = db.query(Company).filter(Company.ticker == ticker.upper()).first()
        price = manual_price
        
        if company and not price:
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == company.id
            ).order_by(PriceHistory.date.desc()).first()
            if latest_price_rec:
                price = float(latest_price_rec.close)

        if not price or price <= 0:
            price = manual_price or 100.0  # Fallback baseline price if completely unavailable

        # 2. Determine Win Probability (p)
        win_prob = 0.55  # Baseline default
        if company:
            # Query agent outputs to run probability estimates
            outputs = db.query(AgentOutput).filter(AgentOutput.company_id == company.id).all()
            if outputs:
                estimates = ProbabilityEngine.calculate_estimates(company.id, outputs)
                # Find the 1-month forecast horizon representing upside potential
                for est in estimates:
                    if est["horizon"] == "1 month":
                        win_prob = est["probability"] / 100.0
                        break

        # 3. Calculate Kelly fraction: f* = p - (1-p)/b where b = TP% / SL%
        sl_fraction = stop_loss_pct / 100.0
        tp_fraction = take_profit_pct / 100.0
        b = tp_fraction / sl_fraction if sl_fraction > 0 else 1.0
        
        kelly_fraction = 0.0
        if b > 0:
            kelly_fraction = win_prob - (1.0 - win_prob) / b
            kelly_fraction = max(0.0, min(1.0, kelly_fraction))

        # 4. Apply fractional Kelly multipliers based on risk profile to reduce volatility
        # Conservative = 0.2x Kelly, Moderate = 0.5x Kelly, Aggressive = 1.0x Kelly
        risk_profile_lower = risk_profile.lower()
        if risk_profile_lower == "conservative":
            kelly_multiplier = 0.2
            max_capital_risk_pct = 1.0  # 1% max capital at risk per trade
            max_holding_pct = 20.0      # max 20% capital in single stock
        elif risk_profile_lower == "moderate":
            kelly_multiplier = 0.5
            max_capital_risk_pct = 2.0  # 2% max capital at risk per trade
            max_holding_pct = 30.0      # max 30% capital in single stock
        else:  # aggressive
            kelly_multiplier = 1.0
            max_capital_risk_pct = 3.0  # 3% max capital at risk per trade
            max_holding_pct = 45.0      # max 45% capital in single stock

        kelly_suggested_allocation = total_capital * (kelly_fraction * kelly_multiplier)

        # 5. Risk budget constraints: Suggested allocation must satisfy Max Capital at Risk limit
        # Capital at Risk = Suggested Allocation * SL% <= Total Capital * Max Capital Risk%
        # Max Allocation = (Total Capital * Max Capital Risk%) / SL%
        max_capital_risk_amt = total_capital * (max_capital_risk_pct / 100.0)
        max_allocation_by_risk = max_capital_risk_amt / sl_fraction if sl_fraction > 0 else total_capital

        # 6. Apply constraints
        suggested_allocation = min(kelly_suggested_allocation, max_allocation_by_risk)
        
        # Clamp by max holding percentage of total capital for diversification
        allocation_ceiling = total_capital * (max_holding_pct / 100.0)
        suggested_allocation = min(suggested_allocation, allocation_ceiling)
        suggested_allocation = max(0.0, suggested_allocation)

        # 7. Final outputs
        shares = suggested_allocation / price if price > 0 else 0.0
        actual_capital_risk_amt = suggested_allocation * sl_fraction
        actual_capital_risk_pct = (actual_capital_risk_amt / total_capital * 100.0) if total_capital > 0 else 0.0

        stop_loss_level = price * (1.0 - sl_fraction)
        take_profit_level = price * (1.0 + tp_fraction)

        # Drawdown Scenarios
        normal_drawdown = actual_capital_risk_amt
        worst_case_drawdown = actual_capital_risk_amt * 1.5  # assuming a 1.5x stop-loss slippage/gap down
        extreme_market_drawdown = suggested_allocation * 0.20 # 20% correction scenario

        return {
            "ticker": ticker.upper(),
            "company_name": company.name if company else "Custom Entry",
            "risk_profile": risk_profile,
            "entry_price": price,
            "win_probability": win_prob * 100.0,
            "reward_risk_ratio": b,
            "kelly_fraction": kelly_fraction * 100.0,
            "suggested_allocation_amt": suggested_allocation,
            "suggested_allocation_pct": (suggested_allocation / total_capital * 100.0) if total_capital > 0 else 0.0,
            "suggested_shares": shares,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "stop_loss_price": stop_loss_level,
            "take_profit_price": take_profit_level,
            "max_capital_risk_pct_allowed": max_capital_risk_pct,
            "max_capital_risk_amt_allowed": max_capital_risk_amt,
            "actual_capital_risk_amt": actual_capital_risk_amt,
            "actual_capital_risk_pct": actual_capital_risk_pct,
            "normal_drawdown_scenario": normal_drawdown,
            "worst_case_drawdown_scenario": worst_case_drawdown,
            "extreme_drawdown_scenario": extreme_market_drawdown
        }


class PortfolioBuilderService:
    @classmethod
    def build_custom_portfolio(
        cls,
        db: Session,
        total_capital: float,
        risk_profile: str,
        horizon: str,
        preferences: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Creates a custom model portfolio of 4-8 stocks, subtracts recommended cash reserves,
        allocates amounts dynamically based on composite quality ratings, and displays risk drawdowns.
        """
        logger.info(f"Building portfolio wizard recommendations for capital={total_capital}, profile={risk_profile}, horizon={horizon}")

        # 1. Determine cash reserves
        risk_profile_lower = risk_profile.lower()
        if risk_profile_lower == "conservative":
            cash_reserve_pct = 20.0
            num_stocks = 6
            sl_pct = 5.0
            tp_pct = 15.0
        elif risk_profile_lower == "moderate":
            cash_reserve_pct = 15.0
            num_stocks = 5
            sl_pct = 8.0
            tp_pct = 24.0
        else:  # aggressive
            cash_reserve_pct = 10.0
            num_stocks = 4
            sl_pct = 12.0
            tp_pct = 36.0

        cash_reserve_amt = total_capital * (cash_reserve_pct / 100.0)
        investable_capital = total_capital - cash_reserve_amt

        # 2. Select diverse stocks based on quality scoring
        companies = db.query(Company).filter(Company.is_active == True).all()
        scored_candidates = []

        for c in companies:
            # Verify company has closing price
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == c.id
            ).order_by(PriceHistory.date.desc()).first()
            
            if not latest_price_rec:
                continue

            price = float(latest_price_rec.close)
            if price <= 0:
                continue

            # Load agent analyst outputs to establish composite score
            outputs = db.query(AgentOutput).filter(AgentOutput.company_id == c.id).all()
            agent_scores = {o.agent_type: o.score for o in outputs}

            fundamental_score = agent_scores.get("fundamental_analyst", 60)
            technical_score = agent_scores.get("technical_analyst", 60)
            risk_score = agent_scores.get("risk_analyst", 50)  # low risk score is safer (governance safety 0-100, high is safe)

            # Re-align risk score: risk score is governance safety. High safety is better!
            governance_score = risk_score  # higher is safer

            # Composite rating calculation
            composite_score = (fundamental_score * 0.4) + (technical_score * 0.3) + (governance_score * 0.3)

            # Apply preferences sector boost (+20 points)
            if preferences and c.sector in preferences:
                composite_score += 20.0

            scored_candidates.append({
                "company": c,
                "price": price,
                "composite_score": composite_score,
                "fundamental_score": fundamental_score,
                "technical_score": technical_score,
                "safety_score": governance_score
            })

        # Sort candidate pool descending
        scored_candidates.sort(key=lambda x: x["composite_score"], reverse=True)
        selected_pool = scored_candidates[:num_stocks]

        if not selected_pool:
            return {
                "total_capital": total_capital,
                "investable_capital": 0.0,
                "cash_reserve_amt": total_capital,
                "cash_reserve_pct": 100.0,
                "holdings": [],
                "expected_max_drawdown_pct": 0.0,
                "expected_max_drawdown_amt": 0.0
            }

        # 3. Dynamic weighting based on composite scores
        total_score_sum = sum(h["composite_score"] for h in selected_pool)
        
        holdings_recommendations = []
        total_capital_at_risk_amt = 0.0

        for h in selected_pool:
            comp = h["company"]
            price = h["price"]
            score_fraction = h["composite_score"] / total_score_sum if total_score_sum > 0 else (1.0 / num_stocks)
            
            allocated_amt = investable_capital * score_fraction
            shares = allocated_amt / price if price > 0 else 0.0
            
            stock_capital_risk_amt = allocated_amt * (sl_pct / 100.0)
            total_capital_at_risk_amt += stock_capital_risk_amt

            stop_loss_price = price * (1.0 - sl_pct / 100.0)
            take_profit_price = price * (1.0 + tp_pct / 100.0)

            holdings_recommendations.append({
                "ticker": comp.ticker,
                "company_name": comp.name,
                "sector": comp.sector or "Other",
                "price": price,
                "allocation_pct": (allocated_amt / total_capital) * 100.0,
                "allocation_amt": allocated_amt,
                "shares": shares,
                "suggested_stop_loss_pct": sl_pct,
                "suggested_take_profit_pct": tp_pct,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "capital_at_risk_amt": stock_capital_risk_amt,
                "capital_at_risk_pct": (stock_capital_risk_amt / total_capital) * 100.0,
                "composite_score": h["composite_score"]
            })

        # Aggregated drawdowns
        portfolio_max_drawdown_pct = (total_capital_at_risk_amt / total_capital) * 100.0
        worst_case_drawdown_pct = portfolio_max_drawdown_pct * 1.5

        return {
            "total_capital": total_capital,
            "investable_capital": investable_capital,
            "cash_reserve_amt": cash_reserve_amt,
            "cash_reserve_pct": cash_reserve_pct,
            "holdings": holdings_recommendations,
            "portfolio_max_drawdown_amt": total_capital_at_risk_amt,
            "portfolio_max_drawdown_pct": portfolio_max_drawdown_pct,
            "worst_case_drawdown_amt": total_capital_at_risk_amt * 1.5,
            "worst_case_drawdown_pct": worst_case_drawdown_pct,
            "extreme_drawdown_amt": investable_capital * 0.20,
            "extreme_drawdown_pct": (investable_capital * 0.20 / total_capital) * 100.0
        }
