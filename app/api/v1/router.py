"""
API v1 Router — Aggregates all v1 endpoint routers.
"""

from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.companies import router as companies_router
from app.api.v1.endpoints.watchlist import router as watchlist_router
from app.api.v1.endpoints.subscriptions import router as subscriptions_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health_router, tags=["System"])
api_router.include_router(auth_router)
api_router.include_router(companies_router, prefix="/companies", tags=["Companies"])
api_router.include_router(watchlist_router)
api_router.include_router(subscriptions_router)


