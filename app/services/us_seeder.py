"""
US Stocks Seeder Service — Dynamically populates and enriches the top 100 NYSE/NASDAQ equities.
"""

import logging
import time
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.company import Company
from app.services.ingestion import IngestionService

logger = logging.getLogger("app.us_seeder")
logging.basicConfig(level=logging.INFO)

TOP_US_TICKERS = [
    # Mega Cap Tech
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corporation", "NASDAQ"),
    ("NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("AMZN", "Amazon.com, Inc.", "NASDAQ"),
    ("GOOGL", "Alphabet Inc.", "NASDAQ"),
    ("META", "Meta Platforms, Inc.", "NASDAQ"),
    ("TSLA", "Tesla, Inc.", "NASDAQ"),
    ("AVGO", "Broadcom Inc.", "NASDAQ"),
    ("AMD", "Advanced Micro Devices, Inc.", "NASDAQ"),
    ("INTC", "Intel Corporation", "NASDAQ"),
    # Financials & Payment giants
    ("JPM", "JPMorgan Chase & Co.", "NYSE"),
    ("BAC", "Bank of America Corporation", "NYSE"),
    ("V", "Visa Inc.", "NYSE"),
    ("MA", "Mastercard Incorporated", "NYSE"),
    ("JNJ", "Johnson & Johnson", "NYSE"),
    ("UNH", "UnitedHealth Group Incorporated", "NYSE"),
    ("WMT", "Walmart Inc.", "NYSE"),
    ("PG", "Procter & Gamble Company", "NYSE"),
    ("XOM", "Exxon Mobil Corporation", "NYSE"),
    ("LLY", "Eli Lilly and Company", "NYSE"),
    # Technology, Networking & Enterprise Software
    ("ADBE", "Adobe Inc.", "NASDAQ"),
    ("CRM", "Salesforce, Inc.", "NYSE"),
    ("CSCO", "Cisco Systems, Inc.", "NASDAQ"),
    ("ORCL", "Oracle Corporation", "NYSE"),
    ("ACN", "Accenture plc", "NYSE"),
    ("QCOM", "QUALCOMM Incorporated", "NASDAQ"),
    ("TXN", "Texas Instruments Incorporated", "NASDAQ"),
    ("AMAT", "Applied Materials, Inc.", "NASDAQ"),
    ("MU", "Micron Technology, Inc.", "NASDAQ"),
    ("NFLX", "Netflix, Inc.", "NASDAQ"),
    # Retail, Entertainment & Consumer
    ("KO", "The Coca-Cola Company", "NYSE"),
    ("PEP", "PepsiCo, Inc.", "NASDAQ"),
    ("MCD", "McDonald's Corporation", "NYSE"),
    ("NKE", "NIKE, Inc.", "NYSE"),
    ("DIS", "The Walt Disney Company", "NYSE"),
    ("SBUX", "Starbucks Corporation", "NASDAQ"),
    ("COST", "Costco Wholesale Corporation", "NASDAQ"),
    ("HD", "The Home Depot, Inc.", "NYSE"),
    ("TMO", "Thermo Fisher Scientific Inc.", "NYSE"),
    ("ABT", "Abbott Laboratories", "NYSE"),
    # Volatile & High-Active retail trading stocks
    ("PLTR", "Palantir Technologies Inc.", "NYSE"),
    ("COIN", "Coinbase Global, Inc.", "NASDAQ"),
    ("SOFI", "SoFi Technologies, Inc.", "NASDAQ"),
    ("GME", "GameStop Corp.", "NYSE"),
    ("AMC", "AMC Entertainment Holdings, Inc.", "NYSE"),
    ("SMCI", "Super Micro Computer, Inc.", "NASDAQ"),
    ("BABA", "Alibaba Group Holding Limited", "NYSE"),
    ("PDD", "PDD Holdings Inc.", "NASDAQ"),
    ("JD", "JD.com, Inc.", "NASDAQ"),
    ("ARM", "ARM Holdings plc", "NASDAQ"),
    ("PYPL", "PayPal Holdings, Inc.", "NASDAQ"),
    ("INTU", "Intuit Inc.", "NASDAQ"),
    ("MRVL", "Marvell Technology, Inc.", "NASDAQ"),
    ("CAT", "Caterpillar Inc.", "NYSE"),
    ("GE", "General Electric Company", "NYSE"),
    ("ISRG", "Intuitive Surgical, Inc.", "NASDAQ"),
    ("PANW", "Palo Alto Networks, Inc.", "NASDAQ"),
    ("SNPS", "Synopsys, Inc.", "NASDAQ"),
    ("CDNS", "Cadence Design Systems, Inc.", "NASDAQ"),
    ("KLAC", "KLA Corporation", "NASDAQ"),
    ("ZS", "Zscaler, Inc.", "NASDAQ"),
    ("CRWD", "CrowdStrike Holdings, Inc.", "NASDAQ"),
    ("DDOG", "Datadog, Inc.", "NASDAQ"),
    ("OKTA", "Okta, Inc.", "NASDAQ"),
    ("TEAM", "Atlassian Corporation", "NASDAQ"),
    ("MDB", "MongoDB, Inc.", "NASDAQ"),
    ("ZS", "Zscaler, Inc.", "NASDAQ"),
    ("ASML", "ASML Holding N.V.", "NASDAQ"),
    ("LRCX", "Lam Research Corporation", "NASDAQ"),
    ("MRK", "Merck & Co., Inc.", "NYSE"),
    ("PFE", "Pfizer Inc.", "NYSE"),
    ("ABBV", "AbbVie Inc.", "NYSE"),
    ("CVX", "Chevron Corporation", "NYSE"),
    ("T", "AT&T Inc.", "NYSE"),
    ("VZ", "Verizon Communications Inc.", "NYSE"),
    ("CMCSA", "Comcast Corporation", "NASDAQ"),
    ("TMUS", "T-Mobile US, Inc.", "NASDAQ"),
    ("AXP", "American Express Company", "NYSE"),
    ("MS", "Morgan Stanley", "NYSE"),
    ("GS", "The Goldman Sachs Group, Inc.", "NYSE"),
    ("SCHW", "The Charles Schwab Corporation", "NYSE"),
    ("C", "Citigroup Inc.", "NYSE"),
    ("BLK", "BlackRock, Inc.", "NYSE"),
    ("HON", "Honeywell International Inc.", "NYSE"),
    ("UNP", "Union Pacific Corporation", "NYSE"),
    ("UPS", "United Parcel Service, Inc.", "NYSE"),
    ("FDX", "FedEx Corporation", "NYSE"),
    ("LMT", "Lockheed Martin Corporation", "NYSE"),
    ("RTX", "RTX Corporation", "NYSE"),
    ("BA", "The Boeing Company", "NYSE"),
    ("DE", "Deere & Company", "NYSE"),
    ("MMM", "3M Company", "NYSE"),
    ("GEHC", "GE HealthCare Technologies Inc.", "NASDAQ"),
    ("EL", "The Estée Lauder Companies Inc.", "NYSE"),
    ("TGT", "Target Corporation", "NYSE"),
    ("TJX", "The TJX Companies, Inc.", "NYSE"),
    ("BKNG", "Booking Holdings Inc.", "NASDAQ"),
    ("ABNB", "Airbnb, Inc.", "NASDAQ"),
    ("UBER", "Uber Technologies, Inc.", "NYSE")
]

def seed_us_stocks(limit: int = 10):
    """
    Seeds and enriches US stocks. 
    limit parameter helps keep execution fast during startup/seeding (defaults to 10 for quick start).
    """
    db: Session = SessionLocal()
    try:
        logger.info(f"Starting seeding of US Stocks (limiting to top {limit} first)...")
        count = 0
        for ticker, name, exchange in TOP_US_TICKERS[:limit]:
            # Check if company exists
            comp = db.query(Company).filter(Company.ticker == ticker).first()
            if not comp:
                comp = Company(
                    ticker=ticker,
                    name=name,
                    exchange=exchange,
                    sector="Global",
                    sub_sector="Global Equities",
                    is_active=True
                )
                db.add(comp)
                db.commit()
                db.refresh(comp)
                logger.info(f"Registered new US stock: {ticker}")
            
            # Enrich profile, prices, financials dynamically
            try:
                IngestionService.enrich_company_data_live(db, comp)
                logger.info(f"Successfully fully enriched US Stock: {ticker}")
                count += 1
            except Exception as e:
                logger.error(f"Failed to enrich US Stock {ticker}: {e}")
                
            time.sleep(0.5)
            
        logger.info(f"US Stock seeding complete! {count} equities loaded & enriched.")
    finally:
        db.close()

if __name__ == "__main__":
    # Seed top 15 US stocks by default for high-speed robust validation
    seed_us_stocks(15)
