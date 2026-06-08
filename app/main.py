"""
AI Stock Kundli — Main Application Entry Point
FastAPI application with CORS, API routing, and startup events.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1.router import api_router

settings = get_settings()

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
        
        # Initialize vector store
        from app.services.embedding_service import EmbeddingService
        EmbeddingService.initialize_store()
        print("[STARTUP] FAISS Vector Store initialized successfully.")
    except Exception as e:
        print(f"[STARTUP ERROR] Startup initialization failed: {e}")
        
    # Start the background intraday price loop
    import asyncio
    from app.services.intraday import run_intraday_ticker_loop
    intraday_task = asyncio.create_task(run_intraday_ticker_loop())
    print("[STARTUP] Background Intraday Loop task created.")
    
    yield
    # ── Shutdown ─────────────────────────────────────────
    print(f"[SHUTDOWN] {settings.APP_NAME} shutting down...")
    intraday_task.cancel()
    try:
        await intraday_task
    except asyncio.CancelledError:
        print("[SHUTDOWN] Background Intraday Loop task cancelled successfully.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Enterprise-grade investment intelligence platform. "
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
