"""
API v1 Router — Aggregates all v1 endpoint routers.
"""

from fastapi import APIRouter
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.companies import router as companies_router
from app.api.v1.endpoints.watchlist import router as watchlist_router
from app.api.v1.endpoints.subscriptions import router as subscriptions_router
from app.api.v1.endpoints.alerts import router as alerts_router
from app.api.v1.endpoints.analytics import router as analytics_router
from app.api.v1.endpoints.portfolio import router as portfolio_router
from app.api.v1.endpoints.advisor import router as advisor_router
from app.api.v1.endpoints.backtest import router as backtest_router
from app.api.v1.endpoints.developer import router as developer_router
from app.api.v1.endpoints.admin import router as admin_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health_router, tags=["System"])
api_router.include_router(auth_router)
api_router.include_router(companies_router, prefix="/companies", tags=["Companies"])
api_router.include_router(watchlist_router)
api_router.include_router(subscriptions_router)
api_router.include_router(alerts_router, prefix="/alerts", tags=["Alerts"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(portfolio_router)
api_router.include_router(advisor_router)
api_router.include_router(backtest_router)
api_router.include_router(developer_router)
api_router.include_router(admin_router)




