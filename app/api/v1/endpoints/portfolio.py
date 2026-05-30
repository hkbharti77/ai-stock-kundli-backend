import logging
import csv
import inspect
from io import StringIO
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Response

from app.core.security import get_current_user_id
from app.core.database import SessionLocal
from app.models.company import Company
from app.models.portfolio import PortfolioHolding
from app.models.price_history import PriceHistory
from app.schemas.portfolio import (
    PortfolioHoldingCreate,
    PortfolioHoldingUpdate,
    PortfolioHoldingResponse,
    PortfolioAnalysisResponse,
    FitEvaluation,
    PositionSizeRequest,
    PositionSizeResponse,
    PortfolioBuilderRequest,
    PortfolioBuilderResponse
)
from app.services.agent_portfolio import PortfolioAdvisorAgent
from app.services.ingestion import IngestionService
from app.services.position_sizing import PositionSizingEngine, PortfolioBuilderService

logger = logging.getLogger("app.api.portfolio")
router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

@router.get("/", response_model=List[PortfolioHoldingResponse])
def get_portfolio_holdings(user_id: int = Depends(get_current_user_id)):
    """Retrieve all holdings for the current user, calculating live performance indicators."""
    with SessionLocal() as db:
        holdings = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user_id).all()
        response = []
        for h in holdings:
            company = h.company
            shares = float(h.shares)
            avg_price = float(h.average_price)
            
            # Fetch latest price
            latest_price_rec = db.query(PriceHistory).filter(
                PriceHistory.company_id == company.id
            ).order_by(PriceHistory.date.desc()).first()
            
            curr_price = float(latest_price_rec.close) if latest_price_rec else avg_price
            curr_val = shares * curr_price
            cost_basis = shares * avg_price
            pnl = curr_val - cost_basis
            pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            
            # Build Pydantic CompanyResponse matching company schema structure
            comp_res = {
                "id": company.id,
                "ticker": company.ticker,
                "name": company.name,
                "isin": company.isin,
                "sector": company.sector,
                "sub_sector": company.sub_sector,
                "industry": company.sub_sector,
                "exchange": company.exchange,
                "market_cap": company.market_cap,
                "is_active": company.is_active,
                "current_price": curr_price,
            }
            
            response.append(
                PortfolioHoldingResponse(
                    id=h.id,
                    user_id=h.user_id,
                    company_id=h.company_id,
                    shares=shares,
                    average_price=avg_price,
                    created_at=h.created_at,
                    company=comp_res,
                    current_price=curr_price,
                    current_value=curr_val,
                    total_cost=cost_basis,
                    pnl=pnl,
                    pnl_percentage=pnl_pct
                )
            )
        return response

@router.post("/holding", response_model=PortfolioHoldingResponse)
def add_holding(payload: PortfolioHoldingCreate, user_id: int = Depends(get_current_user_id)):
    """Add a new stock holding manually."""
    with SessionLocal() as db:
        company = db.query(Company).filter(Company.ticker == payload.ticker.upper()).first()
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company with ticker '{payload.ticker.upper()}' is not supported in the database."
            )
            
        # Check if already exists
        existing = db.query(PortfolioHolding).filter(
            PortfolioHolding.user_id == user_id,
            PortfolioHolding.company_id == company.id
        ).first()
        if existing:
            # Dynamically merge/average-cost the position instead of failing!
            total_shares = float(existing.shares) + float(payload.shares)
            total_cost = (float(existing.shares) * float(existing.average_price)) + (float(payload.shares) * float(payload.average_price))
            existing.shares = total_shares
            existing.average_price = total_cost / total_shares if total_shares > 0 else 0
            db.commit()
            db.refresh(existing)
            holding = existing
        else:
            holding = PortfolioHolding(
                user_id=user_id,
                company_id=company.id,
                shares=payload.shares,
                average_price=payload.average_price
            )
            db.add(holding)
            db.commit()
            db.refresh(holding)
        
        # Build response
        curr_price = payload.average_price
        latest_price_rec = db.query(PriceHistory).filter(
            PriceHistory.company_id == company.id
        ).order_by(PriceHistory.date.desc()).first()
        if latest_price_rec:
            curr_price = float(latest_price_rec.close)
            
        comp_res = {
            "id": company.id,
            "ticker": company.ticker,
            "name": company.name,
            "isin": company.isin,
            "sector": company.sector,
            "sub_sector": company.sub_sector,
            "industry": company.sub_sector,
            "exchange": company.exchange,
            "market_cap": company.market_cap,
            "is_active": company.is_active,
            "current_price": curr_price,
        }
        
        return PortfolioHoldingResponse(
            id=holding.id,
            user_id=holding.user_id,
            company_id=holding.company_id,
            shares=float(holding.shares),
            average_price=float(holding.average_price),
            created_at=holding.created_at,
            company=comp_res,
            current_price=curr_price,
            current_value=float(holding.shares) * curr_price,
            total_cost=float(holding.shares) * float(holding.average_price),
            pnl=(float(holding.shares) * curr_price) - (float(holding.shares) * float(holding.average_price)),
            pnl_percentage=(((curr_price - float(holding.average_price)) / float(holding.average_price) * 100.0) if float(holding.average_price) > 0 else 0.0)
        )

@router.put("/holding/{holding_id}", response_model=PortfolioHoldingResponse)
def update_holding(holding_id: int, payload: PortfolioHoldingUpdate, user_id: int = Depends(get_current_user_id)):
    """Update shares quantity or cost basis of a holding."""
    with SessionLocal() as db:
        holding = db.query(PortfolioHolding).filter(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.user_id == user_id
        ).first()
        
        if not holding:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Portfolio holding not found."
            )
            
        holding.shares = payload.shares
        holding.average_price = payload.average_price
        db.commit()
        db.refresh(holding)
        
        company = holding.company
        curr_price = payload.average_price
        latest_price_rec = db.query(PriceHistory).filter(
            PriceHistory.company_id == company.id
        ).order_by(PriceHistory.date.desc()).first()
        if latest_price_rec:
            curr_price = float(latest_price_rec.close)
            
        comp_res = {
            "id": company.id,
            "ticker": company.ticker,
            "name": company.name,
            "isin": company.isin,
            "sector": company.sector,
            "sub_sector": company.sub_sector,
            "industry": company.sub_sector,
            "exchange": company.exchange,
            "market_cap": company.market_cap,
            "is_active": company.is_active,
            "current_price": curr_price,
        }
        
        return PortfolioHoldingResponse(
            id=holding.id,
            user_id=holding.user_id,
            company_id=holding.company_id,
            shares=float(holding.shares),
            average_price=float(holding.average_price),
            created_at=holding.created_at,
            company=comp_res,
            current_price=curr_price,
            current_value=float(holding.shares) * curr_price,
            total_cost=float(holding.shares) * float(holding.average_price),
            pnl=(float(holding.shares) * curr_price) - (float(holding.shares) * float(holding.average_price)),
            pnl_percentage=(((curr_price - float(holding.average_price)) / float(holding.average_price) * 100.0) if float(holding.average_price) > 0 else 0.0)
        )

@router.delete("/holding/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(holding_id: int, user_id: int = Depends(get_current_user_id)):
    """Delete a stock holding from the portfolio."""
    with SessionLocal() as db:
        holding = db.query(PortfolioHolding).filter(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.user_id == user_id
        ).first()
        
        if not holding:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Portfolio holding not found."
            )
            
        db.delete(holding)
        db.commit()
    return None

@router.post("/import", response_model=List[PortfolioHoldingResponse])
async def import_portfolio_csv(file: UploadFile = File(...), user_id: int = Depends(get_current_user_id)):
    """Bulk import stock holdings via CSV upload."""
    content = await file.read()
    csv_text = content.decode("utf-8")
    
    reader = csv.reader(StringIO(csv_text))
    headers = [h.strip().lower() for h in next(reader, [])]
    
    # Identify index columns with aliases
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
            detail="CSV must contain column headers for ticker (e.g. 'ticker' or 'symbol'), quantity ('shares' or 'qty'), and purchase price ('average_price' or 'buy_price')."
        )
        
    imported_holdings = []
    
    with SessionLocal() as db:
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
                continue # Skip invalid numeric rows
                
            # Find or dynamically enrich company
            company = db.query(Company).filter(Company.ticker == ticker).first()
            if not company:
                logger.info(f"Ticker '{ticker}' not found during import. Dynamically creating and enriching live...")
                try:
                    company = Company(ticker=ticker, name=ticker, sector="Other", sub_sector="Other")
                    db.add(company)
                    db.commit()
                    db.refresh(company)
                    
                    # Call enrichment
                    if inspect.iscoroutinefunction(IngestionService.enrich_company_data_live):
                        await IngestionService.enrich_company_data_live(db, company)
                    else:
                        IngestionService.enrich_company_data_live(db, company)
                    db.refresh(company)
                except Exception as e:
                    logger.error(f"Failed to dynamically enrich company '{ticker}': {str(e)}")
                    # Fallback database rollback and skip or create minimal
                    db.rollback()
                    company = db.query(Company).filter(Company.ticker == ticker).first()
                    if not company:
                        continue
                        
            # Upsert holding
            existing = db.query(PortfolioHolding).filter(
                PortfolioHolding.user_id == user_id,
                PortfolioHolding.company_id == company.id
            ).first()
            
            if existing:
                existing.shares = shares
                existing.average_price = price
            else:
                holding = PortfolioHolding(
                    user_id=user_id,
                    company_id=company.id,
                    shares=shares,
                    average_price=price
                )
                db.add(holding)
                
        db.commit()
        
    # Return the newly updated complete holdings list
    return get_portfolio_holdings(user_id=user_id)

@router.get("/template")
def get_portfolio_template():
    """Download a CSV template for bulk importing portfolio holdings."""
    csv_data = "ticker,shares,average_price\nRELIANCE,10,2450.50\nTCS,5,3200.00\nINFY,15,1500.25\n"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio_template.csv"}
    )

@router.get("/analysis", response_model=PortfolioAnalysisResponse)
async def get_portfolio_analysis(user_id: int = Depends(get_current_user_id)):
    """Compute sector exposure, diversification indices, stock return correlation risk, and generate wealth reports."""
    with SessionLocal() as db:
        result = await PortfolioAdvisorAgent.analyze_portfolio(db, user_id)
        return result

@router.get("/fit-check", response_model=FitEvaluation)
async def check_stock_fit(ticker: str, user_id: int = Depends(get_current_user_id)):
    """Evaluate compatibility fit score and diversification impact of a candidate stock ticker."""
    with SessionLocal() as db:
        result = await PortfolioAdvisorAgent.evaluate_stock_fit(db, user_id, ticker)
        return result

@router.post("/position-size", response_model=PositionSizeResponse)
def calculate_position_sizing(req: PositionSizeRequest, user_id: int = Depends(get_current_user_id)):
    """Calculate Kelly-adjusted position sizing and specific risk parameters for a trade."""
    with SessionLocal() as db:
        result = PositionSizingEngine.calculate_position_size(
            db=db,
            ticker=req.ticker,
            total_capital=req.total_capital,
            risk_profile=req.risk_profile,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            manual_price=req.manual_price
        )
        return result

@router.post("/builder", response_model=PortfolioBuilderResponse)
def build_recommended_portfolio(req: PortfolioBuilderRequest, user_id: int = Depends(get_current_user_id)):
    """Generate professional step-by-step portfolio recommendations with cash reserves and drawdown scenarios."""
    with SessionLocal() as db:
        result = PortfolioBuilderService.build_custom_portfolio(
            db=db,
            total_capital=req.total_capital,
            risk_profile=req.risk_profile,
            horizon=req.horizon,
            preferences=req.preferences
        )
        return result

