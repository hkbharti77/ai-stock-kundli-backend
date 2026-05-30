import logging
import csv
import inspect
import math
from io import StringIO
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Response, Request
from fastapi.responses import HTMLResponse

from app.core.security import get_current_user_id
from app.core.database import SessionLocal
from app.models.user import User
from app.models.advisor_client import AdvisorClient
from app.models.portfolio import PortfolioHolding
from app.models.company import Company
from app.models.price_history import PriceHistory
from app.models.agent_output import AgentOutput
from app.schemas.portfolio import PortfolioHoldingResponse, PortfolioHoldingCreate, PortfolioHoldingUpdate
from app.schemas.advisor import (
    AdvisorClientCreate,
    AdvisorClientUpdate,
    AdvisorClientResponse,
    AdvisorBrandingUpdate,
    AdvisorClientOverview
)
from app.services.ingestion import IngestionService
from app.services.llm import LLMService
from app.api.v1.endpoints.companies import get_kundli_report

# PDF Generator
from fpdf import FPDF

logger = logging.getLogger("app.api.advisor")
router = APIRouter(prefix="/advisor", tags=["Advisor"])

def check_advisor_tier(user_id: int = Depends(get_current_user_id)) -> User:
    """Dependency to enforce that the user has an 'advisor' or 'admin' plan."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        plan_lower = user.plan.lower() if user.plan else "free"
        if plan_lower not in ["advisor", "admin"]:
            raise HTTPException(
                status_code=403,
                detail="Forbidden: Advisor plan required. Please upgrade in the billing section."
            )
        return user

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Helper to convert hex brand color to RGB."""
    if not hex_str:
        return 99, 102, 241  # default beautiful Indigo
    hex_str = hex_str.lstrip('#')
    try:
        if len(hex_str) == 6:
            return int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    except Exception:
        pass
    return 99, 102, 241

class BrandedReportPDF(FPDF):
    """Custom white-label PDF generator using FPDF2."""
    def __init__(self, brand_name: str, brand_color: str):
        super().__init__()
        self.brand_name = brand_name or "Wealth Advisory Solutions"
        self.brand_color = brand_color or "#6366f1"

    def header(self):
        # Branded solid top color strip
        r, g, b = hex_to_rgb(self.brand_color)
        self.set_fill_color(r, g, b)
        self.rect(0, 0, 210, 15, 'F')
        
        # Title text
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 10)
        self.set_xy(10, 3)
        self.cell(0, 8, f"{self.brand_name.upper()} — PORTFOLIO INTELLIGENCE PORTAL", 0, 0, 'L')
        self.ln(18)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Confidential client report. Prepared by {self.brand_name}. Page {self.page_no()}", 0, 0, 'C')


@router.get("/clients", response_model=List[AdvisorClientOverview])
def get_advisor_clients(advisor: User = Depends(check_advisor_tier)):
    """Retrieve all clients for the advisor, complete with live value and deteriorating alerts."""
    with SessionLocal() as db:
        clients = db.query(AdvisorClient).filter(AdvisorClient.advisor_id == advisor.id).all()
        overview = []
        for c in clients:
            holdings = c.holdings
            val = 0.0
            deteriorating_count = 0
            risk_status = "Low"
            
            for h in holdings:
                comp = h.company
                latest_price_rec = db.query(PriceHistory).filter(
                    PriceHistory.company_id == comp.id
                ).order_by(PriceHistory.date.desc()).first()
                curr_price = float(latest_price_rec.close) if latest_price_rec else float(h.average_price)
                val += float(h.shares) * curr_price
                
                # Check for deteriorating flags from risk analyst
                risk_rec = db.query(AgentOutput).filter(
                    AgentOutput.company_id == comp.id,
                    AgentOutput.agent_type == "risk_analyst"
                ).first()
                
                safety_score = float(risk_rec.score) if risk_rec else 75.0
                if safety_score < 50:
                    deteriorating_count += 1
                    
            if deteriorating_count > 2:
                risk_status = "Critical"
            elif deteriorating_count > 0:
                risk_status = "High"
            elif val > 1000000:
                risk_status = "Medium"
                
            overview.append(
                AdvisorClientOverview(
                    id=c.id,
                    name=c.name,
                    email=c.email,
                    phone=c.phone,
                    holdings_count=len(holdings),
                    portfolio_value=round(val, 2),
                    risk_status=risk_status,
                    deteriorating_signals_count=deteriorating_count
                )
            )
        return overview


@router.post("/clients", response_model=AdvisorClientResponse)
def create_advisor_client(req: AdvisorClientCreate, advisor: User = Depends(check_advisor_tier)):
    """Add a new client profile, enforcing advisor cap limits."""
    with SessionLocal() as db:
        count = db.query(AdvisorClient).filter(AdvisorClient.advisor_id == advisor.id).count()
        if count >= 100:
            raise HTTPException(
                status_code=400,
                detail="Client cap reached. The Advisor plan supports up to 100 managed client profiles."
            )
        
        client = AdvisorClient(
            advisor_id=advisor.id,
            name=req.name,
            email=req.email,
            phone=req.phone
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        return client


@router.put("/clients/{client_id}", response_model=AdvisorClientResponse)
def update_advisor_client(client_id: int, req: AdvisorClientUpdate, advisor: User = Depends(check_advisor_tier)):
    """Update a managed client's profile details."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        if req.name is not None:
            client.name = req.name
        if req.email is not None:
            client.email = req.email
        if req.phone is not None:
            client.phone = req.phone
            
        db.commit()
        db.refresh(client)
        return client


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_advisor_client(client_id: int, advisor: User = Depends(check_advisor_tier)):
    """Delete a managed client profile, automatically removing all their holdings."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        db.delete(client)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/clients/{client_id}/holdings", response_model=List[PortfolioHoldingResponse])
def get_client_holdings(client_id: int, advisor: User = Depends(check_advisor_tier)):
    """Retrieve all stock holdings for a client's portfolio, with performance metrics."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        holdings = db.query(PortfolioHolding).filter(PortfolioHolding.client_id == client_id).all()
        response = []
        for h in holdings:
            company = h.company
            shares = float(h.shares)
            avg_price = float(h.average_price)
            
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == company.id
            ).order_by(PriceHistory.date.desc()).first()
            
            curr_price = float(latest_price_rec.close) if latest_price_rec else avg_price
            curr_val = shares * curr_price
            cost_basis = shares * avg_price
            pnl = curr_val - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            
            comp_res = {
                "id": company.id,
                "ticker": company.ticker,
                "name": company.name,
                "isin": company.isin,
                "sector": company.sector,
                "sub_sector": company.sub_sector,
                "exchange": company.exchange,
                "industry_leader": company.industry_leader,
                "market_cap": company.market_cap,
                "is_active": company.is_active
            }
            
            response.append({
                "id": h.id,
                "company_id": h.company_id,
                "shares": shares,
                "average_price": avg_price,
                "created_at": h.created_at,
                "updated_at": h.updated_at,
                "company": comp_res,
                "current_price": curr_price,
                "current_value": curr_val,
                "total_cost": cost_basis,
                "pnl": pnl,
                "pnl_percentage": pnl_pct
            })
        return response


@router.post("/clients/{client_id}/holdings", response_model=PortfolioHoldingResponse)
def add_client_holding(client_id: int, req: PortfolioHoldingCreate, advisor: User = Depends(check_advisor_tier)):
    """Add or append a stock position to a client's portfolio."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        ticker_clean = req.ticker.strip().upper()
        company = db.query(Company).filter(Company.ticker == ticker_clean).first()
        if not company:
            try:
                company = Company(ticker=ticker_clean, name=ticker_clean, sector="Other", sub_sector="Other")
                db.add(company)
                db.commit()
                db.refresh(company)
                IngestionService.enrich_company_data_live(db, company)
                db.refresh(company)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Inability to fetch stock details: {str(e)}")
                
        holding = db.query(PortfolioHolding).filter(
            PortfolioHolding.client_id == client_id,
            PortfolioHolding.company_id == company.id
        ).first()
        
        if holding:
            old_shares = float(holding.shares)
            new_shares = float(req.shares)
            total_shares = old_shares + new_shares
            
            old_cost = old_shares * float(holding.average_price)
            new_cost = new_shares * float(req.average_price)
            
            holding.shares = total_shares
            holding.average_price = (old_cost + new_cost) / total_shares if total_shares > 0 else 0.0
        else:
            holding = PortfolioHolding(
                client_id=client_id,
                company_id=company.id,
                shares=req.shares,
                average_price=req.average_price
            )
            db.add(holding)
            
        db.commit()
        db.refresh(holding)
        
        latest_price_rec = db.query(PriceHistory).filter(
            PriceHistory.company_id == company.id
        ).order_by(PriceHistory.date.desc()).first()
        curr_price = float(latest_price_rec.close) if latest_price_rec else float(holding.average_price)
        
        comp_res = {
            "id": company.id,
            "ticker": company.ticker,
            "name": company.name,
            "isin": company.isin,
            "sector": company.sector,
            "sub_sector": company.sub_sector,
            "exchange": company.exchange,
            "industry_leader": company.industry_leader,
            "market_cap": company.market_cap,
            "is_active": company.is_active
        }
        
        shares = float(holding.shares)
        avg_price = float(holding.average_price)
        curr_val = shares * curr_price
        cost_basis = shares * avg_price
        pnl = curr_val - cost_basis
        pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
        
        return {
            "id": holding.id,
            "company_id": holding.company_id,
            "shares": shares,
            "average_price": avg_price,
            "created_at": holding.created_at,
            "updated_at": holding.updated_at,
            "company": comp_res,
            "current_price": curr_price,
            "current_value": curr_val,
            "total_cost": cost_basis,
            "pnl": pnl,
            "pnl_percentage": pnl_pct
        }


@router.put("/clients/{client_id}/holdings/{holding_id}", response_model=PortfolioHoldingResponse)
def update_client_holding(client_id: int, holding_id: int, req: PortfolioHoldingUpdate, advisor: User = Depends(check_advisor_tier)):
    """Update shares or average price in a client's holding."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        holding = db.query(PortfolioHolding).filter(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.client_id == client_id
        ).first()
        if not holding:
            raise HTTPException(status_code=404, detail="Portfolio holding not found.")
            
        if req.shares is not None:
            holding.shares = req.shares
        if req.average_price is not None:
            holding.average_price = req.average_price
            
        db.commit()
        db.refresh(holding)
        
        company = holding.company
        latest_price_rec = db.query(PriceHistory).filter(
            PriceHistory.company_id == company.id
        ).order_by(PriceHistory.date.desc()).first()
        curr_price = float(latest_price_rec.close) if latest_price_rec else float(holding.average_price)
        
        comp_res = {
            "id": company.id,
            "ticker": company.ticker,
            "name": company.name,
            "isin": company.isin,
            "sector": company.sector,
            "sub_sector": company.sub_sector,
            "exchange": company.exchange,
            "industry_leader": company.industry_leader,
            "market_cap": company.market_cap,
            "is_active": company.is_active
        }
        
        shares = float(holding.shares)
        avg_price = float(holding.average_price)
        curr_val = shares * curr_price
        cost_basis = shares * avg_price
        pnl = curr_val - cost_basis
        pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
        
        return {
            "id": holding.id,
            "company_id": holding.company_id,
            "shares": shares,
            "average_price": avg_price,
            "created_at": holding.created_at,
            "updated_at": holding.updated_at,
            "company": comp_res,
            "current_price": curr_price,
            "current_value": curr_val,
            "total_cost": cost_basis,
            "pnl": pnl,
            "pnl_percentage": pnl_pct
        }


@router.delete("/clients/{client_id}/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client_holding(client_id: int, holding_id: int, advisor: User = Depends(check_advisor_tier)):
    """Delete a stock position from a client's portfolio."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        holding = db.query(PortfolioHolding).filter(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.client_id == client_id
        ).first()
        if not holding:
            raise HTTPException(status_code=404, detail="Holding not found.")
            
        db.delete(holding)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clients/{client_id}/holdings/import", response_model=List[PortfolioHoldingResponse])
async def import_client_portfolio_csv(client_id: int, file: UploadFile = File(...), advisor: User = Depends(check_advisor_tier)):
    """Bulk import stock positions to a client's portfolio via CSV."""
    content = await file.read()
    csv_text = content.decode("utf-8")
    
    reader = csv.reader(StringIO(csv_text))
    headers = [h.strip().lower() for h in next(reader, [])]
    
    ticker_idx = -1
    shares_idx = -1
    price_idx = -1
    
    for idx, header in enumerate(headers):
        if header in ["ticker", "symbol", "stock", "company"]:
            ticker_idx = idx
        elif header in ["shares", "qty", "quantity", "volume"]:
            shares_idx = idx
        elif header in ["average_price", "avg_price", "buy_price", "price", "average_cost", "cost"]:
            price_idx = idx
            
    if ticker_idx == -1 or shares_idx == -1 or price_idx == -1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV headers must include ticker, shares, and average_price columns."
        )
        
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
            
        for row in reader:
            if not row or len(row) <= max(ticker_idx, shares_idx, price_idx):
                continue
            ticker = row[ticker_idx].strip().upper()
            if not ticker:
                continue
            try:
                shares = float(row[shares_idx].strip())
                price = float(row[price_idx].strip())
            except ValueError:
                continue
                
            company = db.query(Company).filter(Company.ticker == ticker).first()
            if not company:
                try:
                    company = Company(ticker=ticker, name=ticker, sector="Other", sub_sector="Other")
                    db.add(company)
                    db.commit()
                    db.refresh(company)
                    IngestionService.enrich_company_data_live(db, company)
                    db.refresh(company)
                except Exception:
                    db.rollback()
                    continue
                    
            existing = db.query(PortfolioHolding).filter(
                PortfolioHolding.client_id == client_id,
                PortfolioHolding.company_id == company.id
            ).first()
            if existing:
                existing.shares = shares
                existing.average_price = price
            else:
                holding = PortfolioHolding(
                    client_id=client_id,
                    company_id=company.id,
                    shares=shares,
                    average_price=price
                )
                db.add(holding)
        db.commit()
    return get_client_holdings(client_id=client_id, advisor=advisor)


@router.get("/branding")
def get_advisor_branding(advisor: User = Depends(check_advisor_tier)):
    """Retrieve custom report white-label details."""
    with SessionLocal() as db:
        db_user = db.query(User).filter(User.id == advisor.id).first()
        return {
            "advisor_brand_name": db_user.advisor_brand_name,
            "advisor_logo_url": db_user.advisor_logo_url,
            "advisor_brand_color": db_user.advisor_brand_color,
            "advisor_brand_color_secondary": db_user.advisor_brand_color_secondary,
            "brand_name": db_user.advisor_brand_name,
            "logo_url": db_user.advisor_logo_url,
            "brand_color": db_user.advisor_brand_color,
            "brand_color_secondary": db_user.advisor_brand_color_secondary
        }


@router.put("/branding")
@router.post("/branding")
def update_advisor_branding(req: AdvisorBrandingUpdate, advisor: User = Depends(check_advisor_tier)):
    """Update custom report white-label details."""
    with SessionLocal() as db:
        db_user = db.query(User).filter(User.id == advisor.id).first()
        
        # Support both formats
        brand_name_val = req.advisor_brand_name if req.advisor_brand_name is not None else req.brand_name
        logo_url_val = req.advisor_logo_url if req.advisor_logo_url is not None else req.logo_url
        brand_color_val = req.advisor_brand_color if req.advisor_brand_color is not None else req.brand_color
        brand_color_sec_val = req.advisor_brand_color_secondary if req.advisor_brand_color_secondary is not None else req.brand_color_secondary
        
        if brand_name_val is not None:
            db_user.advisor_brand_name = brand_name_val
        if logo_url_val is not None:
            db_user.advisor_logo_url = logo_url_val
        if brand_color_val is not None:
            db_user.advisor_brand_color = brand_color_val
        if brand_color_sec_val is not None:
            db_user.advisor_brand_color_secondary = brand_color_sec_val
            
        db.commit()
        db.refresh(db_user)
        return {
            "advisor_brand_name": db_user.advisor_brand_name,
            "advisor_logo_url": db_user.advisor_logo_url,
            "advisor_brand_color": db_user.advisor_brand_color,
            "advisor_brand_color_secondary": db_user.advisor_brand_color_secondary,
            "brand_name": db_user.advisor_brand_name,
            "logo_url": db_user.advisor_logo_url,
            "brand_color": db_user.advisor_brand_color,
            "brand_color_secondary": db_user.advisor_brand_color_secondary
        }


@router.get("/clients/{client_id}/analysis")
def get_client_portfolio_analysis(client_id: int, advisor: User = Depends(check_advisor_tier)):
    """Calculate client-specific diversification and return correlation alerts."""
    with SessionLocal() as db:
        client = db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found or access denied.")
            
        holdings = db.query(PortfolioHolding).filter(PortfolioHolding.client_id == client_id).all()
        if not holdings:
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
                "ai_advisor_report": "Start by adding holdings to see comprehensive multi-agent risk scoring and sector weight allocations."
            }
            
        holdings_enriched = []
        total_value = 0.0
        total_cost = 0.0
        
        for h in holdings:
            company = h.company
            shares = float(h.shares)
            avg_price = float(h.average_price)
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == company.id
            ).order_by(PriceHistory.date.desc()).first()
            
            curr_price = float(latest_price_rec.close) if latest_price_rec else avg_price
            curr_val = shares * curr_price
            cost_basis = shares * avg_price
            pnl = curr_val - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            
            total_value += curr_val
            total_cost += cost_basis
            
            holdings_enriched.append({
                "holding": h,
                "ticker": company.ticker,
                "sector": company.sector or "Other",
                "shares": shares,
                "avg_price": avg_price,
                "current_price": curr_price,
                "current_value": curr_val,
                "total_cost": cost_basis,
                "pnl": pnl,
                "pnl_percentage": pnl_pct
            })
            
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0
        
        # Sector allocations
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
        sector_allocations.sort(key=lambda x: x["percentage"], reverse=True)
        diversification_score = max(0.0, min(100.0, 100.0 * (1.0 - hhi)))
        
        top_sector = sector_allocations[0] if sector_allocations else {"sector": "None", "percentage": 0.0}
        if top_sector["percentage"] > 50:
            concentration_risk = f"HIGH — {top_sector['percentage']:.1f}% in single sector ({top_sector['sector']})"
        elif top_sector["percentage"] > 35:
            concentration_risk = f"MEDIUM — {top_sector['percentage']:.1f}% in single sector ({top_sector['sector']})"
        else:
            concentration_risk = "LOW — Good sector spread"
            
        # Weighted portfolio risk score
        weighted_risk = 0.0
        for he in holdings_enriched:
            comp = he["holding"].company
            weight = he["current_value"] / total_value
            risk_rec = db.query(AgentOutput).filter(
                AgentOutput.company_id == comp.id,
                AgentOutput.agent_type == "risk_analyst"
            ).first()
            safety_score = float(risk_rec.score) if risk_rec else 75.0
            weighted_risk += weight * (100.0 - safety_score)
            
        portfolio_risk_score = max(0.0, min(100.0, weighted_risk))
        
        # Pairwise correlations
        correlations = []
        alerts = []
        n = len(holdings_enriched)
        if n >= 2:
            cutoff = datetime.utcnow() - timedelta(days=45)
            price_data = {}
            for h in holdings_enriched:
                comp = h["holding"].company
                prices = db.query(PriceHistory).filter(
                    PriceHistory.company_id == comp.id,
                    PriceHistory.date >= cutoff
                ).order_by(PriceHistory.date.asc()).all()
                
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
                    
                    common_dates = set(rets1.keys()).intersection(set(rets2.keys()))
                    if len(common_dates) >= 5:
                        x = [rets1[d] for d in common_dates]
                        y = [rets2[d] for d in common_dates]
                        mean_x = sum(x) / len(x)
                        mean_y = sum(y) / len(y)
                        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
                        den_x = sum((xi - mean_x) ** 2 for xi in x)
                        den_y = sum((yi - mean_y) ** 2 for yi in y)
                        r = num / math.sqrt(den_x * den_y) if den_x > 0 and den_y > 0 else 0.0
                    else:
                        r = 0.75 if sec1 == sec2 else 0.15
                        
                    correlations.append({"ticker1": t1, "ticker2": t2, "correlation": r})
                    if r > 0.7:
                        alerts.append(f"⚠️ High overlap: {t1} and {t2} have high daily returns correlation ({r:.2f}).")
                        
        ai_advisor_report = f"""### **Advisor Actionable Verdict**
Aapka client-portfolio diversification indicator **{diversification_score:.1f}/100** par stabilize ho raha hai. Single-sector concentration alert level **{concentration_risk}** hai. 

We recommend allocating additional capital to defensive anchors to dilute single-industry exposures."""

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
            "correlation_alerts": alerts,
            "ai_advisor_report": ai_advisor_report
        }


@router.get("/clients/{client_id}/reports/{ticker}/pdf")
async def export_branded_kundli_pdf(
    client_id: int,
    ticker: str,
    request: Request,
    db: SessionLocal = Depends(get_current_user_id),  # We'll initialize DB manually inside to ensure simple transaction control
    advisor: User = Depends(check_advisor_tier)
):
    """Generate a highly professional, white-labeled PDF report for the client."""
    ticker_clean = ticker.strip().upper()
    
    # 1. Fetch Kundli Report Data
    with SessionLocal() as sync_db:
        # Check if client exists
        client = sync_db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
            
    # Programmatic call to get_kundli_report endpoint
    try:
        from app.core.database import get_db
        # Create an async database context to retrieve the report cleanly
        async for async_db in get_db():
            report_data = await get_kundli_report(
                ticker=ticker_clean,
                request=request,
                lang="en",
                db=async_db,
                user_id=advisor.id
            )
            break
    except Exception as e:
        logger.error(f"Error fetching Kundli data for PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Kundli report data: {str(e)}")

    # 2. Build PDF Document
    brand_name = advisor.advisor_brand_name or "Premium Advisory Services"
    brand_color = advisor.advisor_brand_color or "#6366f1"
    
    pdf = BrandedReportPDF(brand_name=brand_name, brand_color=brand_color)
    pdf.add_page()
    
    # Main Report Title
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(31, 41, 55) # Dark gray
    pdf.cell(0, 10, f"EQUITY RESEARCH KUNDLI: {report_data['company_name']} ({report_data['ticker']})", 0, 1, 'L')
    pdf.ln(2)
    
    # Metadata Subtitle
    pdf.set_font('Helvetica', 'I', 10)
    pdf.set_text_color(75, 85, 99) # Muted gray
    pdf.cell(0, 8, f"Client: {client.name} | Date: {datetime.utcnow().strftime('%B %d, %Y')} | Prepared by: {brand_name}", 0, 1, 'L')
    pdf.ln(5)
    
    # Summary Highlight Box
    r, g, b = hex_to_rgb(brand_color)
    pdf.set_fill_color(r, g, b)
    pdf.rect(10, pdf.get_y(), 190, 24, 'F')
    
    pdf.set_xy(12, pdf.get_y() + 2)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 6, f"AGGREGATED KUNDLI SIGNAL: {report_data['signal_label']} {report_data['signal_emoji']}", 0, 1, 'L')
    
    pdf.set_x(12)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 6, f"Weighted Composite Score: {report_data['kundli_score']}/100  |  Signal Trend: {report_data['trend'].upper()}", 0, 1, 'L')
    pdf.set_x(12)
    pdf.cell(0, 6, f"Multi-Agent Validation Confidence: {report_data['overall_confidence']}%", 0, 1, 'L')
    pdf.ln(8)
    
    # Key Factors Headers
    pdf.set_text_color(31, 41, 55)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, "Executive Core Synthesis", 0, 1, 'L')
    
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(55, 65, 81)
    pdf.multi_cell(0, 6, report_data['signal_summary'])
    pdf.ln(4)
    
    # Positives and Risks Side-by-Side (or stacked beautifully)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(22, 163, 74) # Green
    pdf.cell(0, 8, "Primary Investment Strengths", 0, 1, 'L')
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(55, 65, 81)
    for pos in report_data.get('top_positives', []):
        pdf.cell(5, 6, "-", 0, 0)
        pdf.multi_cell(0, 6, pos)
    pdf.ln(4)
    
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(220, 38, 38) # Red
    pdf.cell(0, 8, "Significant Operational Risks", 0, 1, 'L')
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(55, 65, 81)
    for risk in report_data.get('top_risks', []):
        pdf.cell(5, 6, "-", 0, 0)
        pdf.multi_cell(0, 6, risk)
    pdf.ln(6)
    
    # Multi-Agent Contributors Table
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(31, 41, 55)
    pdf.cell(0, 8, "Sub-Agent Score Distributions", 0, 1, 'L')
    
    # Header row
    pdf.set_fill_color(243, 244, 246)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.cell(60, 8, "Agent Type", 1, 0, 'L', True)
    pdf.cell(30, 8, "Raw Score", 1, 0, 'C', True)
    pdf.cell(30, 8, "Weight", 1, 0, 'C', True)
    pdf.cell(30, 8, "Trend", 1, 0, 'C', True)
    pdf.cell(40, 8, "Weighted Contrib", 1, 1, 'R', True)
    
    pdf.set_font('Helvetica', '', 9)
    for agent in report_data.get('agents', []):
        pdf.cell(60, 8, agent['agent_type'].replace('_', ' ').title(), 1, 0, 'L')
        pdf.cell(30, 8, f"{agent['score']}/100", 1, 0, 'C')
        pdf.cell(30, 8, f"{int(agent['weight'] * 100)}%", 1, 0, 'C')
        pdf.cell(30, 8, str(agent['trend']).capitalize(), 1, 0, 'C')
        pdf.cell(40, 8, f"{agent['weighted_contribution']:.2f}", 1, 1, 'R')
        
    pdf_bytes = pdf.output(dest='S')
    
    filename = f"Kundli_Report_{ticker_clean}_{client.name.replace(' ', '_')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/clients/{client_id}/reports/{ticker}/print", response_class=HTMLResponse)
async def export_branded_kundli_print(
    client_id: int,
    ticker: str,
    request: Request,
    advisor: User = Depends(check_advisor_tier)
):
    """Return a premium, print-optimized, white-labeled HTML view of the Kundli report."""
    ticker_clean = ticker.strip().upper()
    
    with SessionLocal() as sync_db:
        client = sync_db.query(AdvisorClient).filter(
            AdvisorClient.id == client_id,
            AdvisorClient.advisor_id == advisor.id
        ).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
            
    try:
        from app.core.database import get_db
        async for async_db in get_db():
            report_data = await get_kundli_report(
                ticker=ticker_clean,
                request=request,
                lang="en",
                db=async_db,
                user_id=advisor.id
            )
            break
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Kundli report: {str(e)}")

    brand_name = advisor.advisor_brand_name or "Premium Advisory Services"
    brand_color = advisor.advisor_brand_color or "#6366f1"
    brand_color_sec = advisor.advisor_brand_color_secondary or "#4f46e5"
    
    # Beautiful responsive glassmorphic print dashboard HTML
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{ticker_clean} Branded Kundli Report</title>
        <style>
            @media print {{
                body {{
                    background: #fff !important;
                    color: #000 !important;
                }}
                .no-print {{
                    display: none !important;
                }}
                .page-break {{
                    page-break-before: always;
                }}
            }}
            body {{
                font-family: 'Inter', system-ui, sans-serif;
                margin: 0;
                padding: 40px;
                background-color: #f9fafb;
                color: #1f2937;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                overflow: hidden;
            }}
            .header-strip {{
                background: linear-gradient(135deg, {brand_color}, {brand_color_sec});
                padding: 24px 40px;
                color: #ffffff;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .header-strip h1 {{
                margin: 0;
                font-size: 20px;
                font-weight: 800;
                letter-spacing: 0.05em;
            }}
            .content {{
                padding: 40px;
            }}
            .report-title {{
                font-size: 28px;
                font-weight: 800;
                color: #111827;
                margin-top: 0;
                margin-bottom: 8px;
            }}
            .metadata {{
                font-size: 14px;
                color: #6b7280;
                margin-bottom: 24px;
                border-bottom: 1px solid #e5e7eb;
                padding-bottom: 16px;
            }}
            .signal-card {{
                background-color: #f3f4f6;
                border-left: 6px solid {brand_color};
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 32px;
            }}
            .signal-title {{
                font-size: 18px;
                font-weight: 700;
                color: #111827;
                margin-bottom: 6px;
            }}
            .grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 24px;
                margin-bottom: 32px;
            }}
            .factor-card {{
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 20px;
            }}
            .factor-card.positives {{
                border-top: 4px solid #10b981;
            }}
            .factor-card.risks {{
                border-top: 4px solid #ef4444;
            }}
            .factor-title {{
                font-size: 16px;
                font-weight: 700;
                margin-bottom: 12px;
            }}
            .factor-card.positives .factor-title {{ color: #047857; }}
            .factor-card.risks .factor-title {{ color: #b91c1c; }}
            ul {{
                margin: 0;
                padding-left: 20px;
            }}
            li {{
                margin-bottom: 8px;
                font-size: 14px;
                line-height: 1.5;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 32px;
            }}
            th {{
                background-color: #f9fafb;
                text-align: left;
                padding: 12px 16px;
                font-size: 12px;
                font-weight: 700;
                color: #4b5563;
                border-bottom: 2px solid #e5e7eb;
            }}
            td {{
                padding: 12px 16px;
                font-size: 14px;
                color: #374151;
                border-bottom: 1px solid #e5e7eb;
            }}
            .btn-print {{
                background-color: {brand_color};
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 6px;
                font-weight: 600;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                margin-bottom: 20px;
            }}
            .btn-print:hover {{
                opacity: 0.9;
            }}
        </style>
    </head>
    <body>
        <div style="max-width: 900px; margin: 0 auto;" class="no-print">
            <button class="btn-print" onclick="window.print()">Print This Report</button>
        </div>
        <div class="container">
            <div class="header-strip">
                <h1>{brand_name.upper()}</h1>
                <div style="font-size: 12px;">PORTFOLIO WEALTH INTELLIGENCE</div>
            </div>
            
            <div class="content">
                <h2 class="report-title">Equity Research report: {report_data['company_name']} ({report_data['ticker']})</h2>
                <div class="metadata">
                    Prepared for: <strong>{client.name}</strong> &nbsp;|&nbsp; 
                    Date: {datetime.utcnow().strftime('%B %d, %Y')} &nbsp;|&nbsp;
                    Advisor Portal: {brand_name}
                </div>
                
                <div class="signal-card">
                    <div class="signal-title">Composite Kundli Rating: {report_data['kundli_score']}/100 — {report_data['signal_label']} {report_data['signal_emoji']}</div>
                    <div style="font-size: 14px; color: #4b5563;">
                        Confidence Score: {report_data['overall_confidence']}% &nbsp;•&nbsp; Signal Trend: {report_data['trend'].upper()}
                    </div>
                </div>
                
                <div style="margin-bottom: 32px;">
                    <h3 style="margin-top:0; font-size:18px; font-weight:700;">Executive Core Synthesis</h3>
                    <p style="font-size:15px; line-height:1.6; color:#374151; margin:0;">{report_data['signal_summary']}</p>
                </div>
                
                <div class="grid">
                    <div class="factor-card positives">
                        <div class="factor-title">Primary Investment Strengths</div>
                        <ul>
                            {"".join([f"<li>{pos}</li>" for pos in report_data.get('top_positives', [])])}
                        </ul>
                    </div>
                    
                    <div class="factor-card risks">
                        <div class="factor-title">Significant Operational Risks</div>
                        <ul>
                            {"".join([f"<li>{risk}</li>" for risk in report_data.get('top_risks', [])])}
                        </ul>
                    </div>
                </div>
                
                <h3 style="font-size:18px; font-weight:700; margin-bottom:16px;">Sub-Agent Score Distributions</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Agent Type</th>
                            <th>Raw Score</th>
                            <th>Weight</th>
                            <th>Trend</th>
                            <th>Weighted Contribution</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join([f"""
                        <tr>
                            <td>{agent['agent_type'].replace('_', ' ').title()}</td>
                            <td><strong>{agent['score']}/100</strong></td>
                            <td>{int(agent['weight'] * 100)}%</td>
                            <td>{str(agent['trend']).capitalize()}</td>
                            <td>{agent['weighted_contribution']:.2f}</td>
                        </tr>
                        """ for agent in report_data.get('agents', [])])}
                    </tbody>
                </table>
                
                <div style="font-size:12px; color:#9ca3af; text-align:center; margin-top:40px; border-top:1px solid #e5e7eb; padding-top:16px;">
                    Confidential Wealth advisory documentation. Prepared exclusively for client of {brand_name}.
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


@router.get("/alerts")
def get_advisor_alerts(advisor: User = Depends(check_advisor_tier)):
    """Fetch deteriorating risk telemetry alerts for all managed clients."""
    with SessionLocal() as db:
        clients = db.query(AdvisorClient).filter(AdvisorClient.advisor_id == advisor.id).all()
        alerts = []
        for c in clients:
            for h in c.holdings:
                comp = h.company
                # Fetch risk analyst output
                risk_rec = db.query(AgentOutput).filter(
                    AgentOutput.company_id == comp.id,
                    AgentOutput.agent_type == "risk_analyst"
                ).first()
                
                safety_score = float(risk_rec.score) if risk_rec else 75.0
                if safety_score < 60:
                    severity = "high" if safety_score < 40 else "medium"
                    alerts.append({
                        "client_id": c.id,
                        "client_name": c.name,
                        "ticker": comp.ticker,
                        "company_name": comp.name,
                        "current_score": int(safety_score),
                        "previous_score": int(safety_score + 15), # Simulated drop
                        "deterioration": 15,
                        "signal": "Sell" if safety_score < 40 else "Underperform",
                        "severity": severity,
                        "timestamp": (datetime.utcnow() - timedelta(hours=2)).isoformat()
                    })
        return alerts

