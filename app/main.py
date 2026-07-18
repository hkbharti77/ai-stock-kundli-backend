"""
AI Stock Kundli — Main Application Entry Point
FastAPI application with CORS, API routing, and startup events.

IMPORTANT LEGAL NOTICE:
This platform is for EDUCATIONAL AND RESEARCH PURPOSES ONLY.
It is NOT registered with SEBI, SEC, FCA, or any financial regulator.
All AI-generated data is NOT investment advice.
Investments are at your own risk. You may lose money.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.core.config import get_settings
from app.api.v1.router import api_router
from app.core.logging_config import setup_logging

settings = get_settings()

# Setup file logging (all, error, warning, success)
setup_logging()

# Short disclaimer injected into every API response header
_DISCLAIMER_HEADER = (
    "FOR RESEARCH USE ONLY. Not investment advice. "
    "Not SEBI/SEC/FCA registered. Invest at your own risk."
)

if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=1.0 if settings.DEBUG else 0.2,
        profiles_sample_rate=1.0 if settings.DEBUG else 0.2,
    )



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # ── Startup ──────────────────────────────────────────
    print(f"[STARTUP] {settings.APP_NAME} v{settings.APP_VERSION} starting...")
    print(f"[STARTUP] Environment: {settings.ENVIRONMENT}")
    
    # Self-healing database schema sync
    try:
        from app.core.database import Base, sync_engine
        import anyio
        
        def _create_tables():
            # Create new tables
            Base.metadata.create_all(bind=sync_engine)
            # Alter existing tables if columns are missing
            from sqlalchemy import text
            with sync_engine.connect() as conn:
                # users table
                res_users = conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
                )
                cols_users = [row[0] for row in res_users.all()]
                if "tenant_id" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL"))
                    print("[STARTUP] Added tenant_id column to users table.")
                if "role" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'Viewer'"))
                    print("[STARTUP] Added role column to users table.")
                if "is_suspended" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_suspended BOOLEAN DEFAULT FALSE"))
                    print("[STARTUP] Added is_suspended column to users table.")
                
                # Subscription Lifecycle Columns
                if "subscription_status" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50)"))
                if "subscription_started_at" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN subscription_started_at TIMESTAMP"))
                if "subscription_ends_at" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN subscription_ends_at TIMESTAMP"))
                if "provider_subscription_id" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN provider_subscription_id VARCHAR(255)"))
                if "trial_used" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT FALSE"))
                if "trial_expires_at" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN trial_expires_at TIMESTAMP"))

                # ── Custom White-Label Advisor Branding ──
                if "advisor_brand_name" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN advisor_brand_name VARCHAR(255)"))
                if "advisor_logo_url" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN advisor_logo_url VARCHAR(500)"))
                if "advisor_brand_color" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN advisor_brand_color VARCHAR(7)"))
                if "advisor_brand_color_secondary" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN advisor_brand_color_secondary VARCHAR(7)"))

                # ── Multi-Step SEBI Compliance & Registration Fields ──
                if "phone" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(20)"))
                if "otp_verified" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN otp_verified BOOLEAN DEFAULT FALSE"))
                if "city" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN city VARCHAR(100)"))
                if "dob" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN dob VARCHAR(20)"))
                if "pan" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN pan VARCHAR(10)"))
                
                # ── Investor Profile ──
                if "risk_appetite" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN risk_appetite VARCHAR(50)"))
                if "experience" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN experience VARCHAR(50)"))
                if "goal" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN goal VARCHAR(100)"))
                if "horizon" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN horizon VARCHAR(50)"))
                
                # ── Legal & Mandate ──
                if "disclaimer_accepted" not in cols_users:
                    conn.execute(text("ALTER TABLE users ADD COLUMN disclaimer_accepted BOOLEAN DEFAULT FALSE"))
                

                # api_keys table
                res_keys = conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'api_keys'")
                )
                cols_keys = [row[0] for row in res_keys.all()]
                if "tenant_id" not in cols_keys:
                    conn.execute(text("ALTER TABLE api_keys ADD COLUMN tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL"))
                    print("[STARTUP] Added tenant_id column to api_keys table.")

                # api_usage_logs table
                res_usage = conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'api_usage_logs'")
                )
                cols_usage = [row[0] for row in res_usage.all()]
                if "tenant_id" not in cols_usage:
                    conn.execute(text("ALTER TABLE api_usage_logs ADD COLUMN tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL"))
                    print("[STARTUP] Added tenant_id column to api_usage_logs table.")

                conn.commit()
            
        await anyio.to_thread.run_sync(_create_tables)
        print("[STARTUP] Database schema verified & synced successfully.")
        
        # Initialize vector store (DISABLED TEMPORARILY FOR RENDER FREE TIER OOM AVOIDANCE)
        # from app.services.embedding_service import EmbeddingService
        # EmbeddingService.initialize_store()
        # print("[STARTUP] FAISS Vector Store initialized successfully.")
    except Exception as e:
        print(f"[STARTUP ERROR] Startup initialization failed: {e}")
        
    # Start the background intraday price loop
    import asyncio
    from app.services.intraday import run_intraday_ticker_loop
    intraday_task = asyncio.create_task(run_intraday_ticker_loop())
    print("[STARTUP] Background Intraday Loop task created.")
    
    # Start the background Screener task (APScheduler)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.tasks.screener_job import run_screener_batch
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_screener_batch, 'interval', minutes=60)
    scheduler.start()
    print("[STARTUP] APScheduler for Screener task started.")
    
    yield
    # ── Shutdown ─────────────────────────────────────────
    print(f"[SHUTDOWN] {settings.APP_NAME} shutting down...")
    intraday_task.cancel()
    try:
        await intraday_task
    except asyncio.CancelledError:
        print("[SHUTDOWN] Background Intraday Loop task cancelled successfully.")
        
    try:
        scheduler.shutdown()
        print("[SHUTDOWN] APScheduler shut down successfully.")
    except Exception as e:
        print(f"[SHUTDOWN] Error shutting down scheduler: {e}")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "⚠️ FOR EDUCATIONAL AND RESEARCH USE ONLY — NOT INVESTMENT ADVICE. "
        "This platform is NOT registered with SEBI, SEC, FCA, or any financial regulator. "
        "All AI-generated data does NOT constitute personalized financial advice. "
        "Investments are at your own risk. You may lose money.\n\n"
        "Enterprise-grade AI research platform. "
        "Analyzes NSE/BSE-listed companies with multi-agent AI "
        "to produce explainable, multi-dimensional research reports."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS Middleware ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Disclaimer Header Middleware ─────────────────────────────
# Injects a legal disclaimer into every API response so that
# third-party consumers and API clients always see the notice.
@app.middleware("http")
async def add_disclaimer_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Platform-Disclaimer"] = _DISCLAIMER_HEADER
    response.headers["X-Not-Investment-Advice"] = "true"
    response.headers["X-Educational-Use-Only"] = "true"
    response.headers["X-Invest-At-Own-Risk"] = "true"
    return response

# ── Register API Routes ─────────────────────────────────────
app.include_router(api_router)


# ── Root Redirect ────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
