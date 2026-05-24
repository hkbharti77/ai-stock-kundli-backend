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
            Base.metadata.create_all(bind=sync_engine)
            
        await anyio.to_thread.run_sync(_create_tables)
        print("[STARTUP] Database schema verified & synced successfully.")
    except Exception as e:
        print(f"[STARTUP ERROR] Database schema sync failed: {e}")
        
    yield
    # ── Shutdown ─────────────────────────────────────────
    print(f"[SHUTDOWN] {settings.APP_NAME} shutting down...")


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
