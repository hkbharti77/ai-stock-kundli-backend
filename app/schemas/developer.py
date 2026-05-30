"""
Developer — Pydantic schemas for API key and webhook models.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class APIKeyCreate(BaseModel):
    """API Key creation request payload."""
    name: str = Field(..., max_length=100, description="Friendly name for the API Key")


class APIKeyResponse(BaseModel):
    """API Key details response."""
    id: int
    name: str
    prefix: str
    is_active: bool
    rate_limit_tier: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class APIKeyCreatedResponse(APIKeyResponse):
    """API Key details with the full cleartext key (visible only once)."""
    plain_key: str = Field(..., description="The cleartext API key. Displayed only once.")


class WebhookSubscriptionCreate(BaseModel):
    """Webhook subscription creation request payload."""
    url: str = Field(..., max_length=500, description="The callback URL to receive webhooks")
    tickers: Optional[List[str]] = Field(None, description="List of stock tickers to watch (e.g. ['RELIANCE', 'TCS']). Leave empty or null to watch all.")


class WebhookSubscriptionResponse(BaseModel):
    """Webhook subscription details response."""
    id: int
    url: str
    secret: str
    is_active: bool
    tickers: Optional[List[str]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookDeliveryLogResponse(BaseModel):
    """Webhook delivery attempt log details."""
    id: int
    subscription_id: int
    event_type: str
    response_status: Optional[int] = None
    is_successful: bool
    attempt_number: int
    timestamp: datetime

    class Config:
        from_attributes = True


class DailyVolumePoint(BaseModel):
    """Single data point for daily request volume."""
    date: str
    count: int


class DailyCostPoint(BaseModel):
    """Single data point for daily billing cost."""
    date: str
    cost: float


class StatusDistributionPoint(BaseModel):
    """Single data point for status code distribution."""
    status: str
    count: int


class UsageStatsResponse(BaseModel):
    """Aggregated developer usage and billing metrics."""
    total_calls: int
    total_cost_inr: float
    daily_volume: List[DailyVolumePoint]
    daily_cost: List[DailyCostPoint]
    status_codes: List[StatusDistributionPoint]
