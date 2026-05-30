from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class TenantCreate(BaseModel):
    name: str = Field(..., max_length=255)
    domain: Optional[str] = Field(None, max_length=255)
    brand_name: Optional[str] = Field(None, max_length=255)
    logo_url: Optional[str] = Field(None, max_length=500)
    brand_color: Optional[str] = Field(None, max_length=7)
    brand_color_secondary: Optional[str] = Field(None, max_length=7)

class TenantResponse(BaseModel):
    id: int
    name: str
    domain: Optional[str]
    brand_name: Optional[str]
    logo_url: Optional[str]
    brand_color: Optional[str]
    brand_color_secondary: Optional[str]
    created_at: datetime
    is_active: bool

    class Config:
        from_attributes = True

class TenantBrandingUpdate(BaseModel):
    brand_name: Optional[str] = Field(None, max_length=255)
    logo_url: Optional[str] = Field(None, max_length=500)
    brand_color: Optional[str] = Field(None, max_length=7)
    brand_color_secondary: Optional[str] = Field(None, max_length=7)

class AdminUserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    plan: str
    role: str
    is_suspended: bool
    tenant_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True

class UserStatusUpdate(BaseModel):
    is_suspended: bool

class UserRoleUpdate(BaseModel):
    role: str
    plan: Optional[str] = None

class InvoiceResponse(BaseModel):
    id: int
    tenant_id: Optional[int]
    tenant_name: Optional[str] = None
    user_id: Optional[int]
    user_email: Optional[str] = None
    billing_period_start: datetime
    billing_period_end: datetime
    amount_inr: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class AuditLogResponse(BaseModel):
    id: int
    tenant_id: Optional[int]
    tenant_name: Optional[str] = None
    user_id: Optional[int]
    user_email: Optional[str] = None
    action: str
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str]
    user_agent: Optional[str]
    timestamp: datetime

    class Config:
        from_attributes = True

class DailyHitPoint(BaseModel):
    date: str
    count: int

class EndpointHitPoint(BaseModel):
    endpoint: str
    count: int

class StatusHitPoint(BaseModel):
    status: str
    count: int

class UsageAnalyticsResponse(BaseModel):
    total_calls: int
    active_users: int
    daily_volume: List[DailyHitPoint]
    endpoint_breakdown: List[EndpointHitPoint]
    status_distribution: List[StatusHitPoint]

class AgentLatencyPoint(BaseModel):
    agent: str
    avg_latency_ms: float
    error_rate: float
    fallback_rate: float

class ConfidenceDistributionPoint(BaseModel):
    score_range: str
    count: int

class AgentMonitoringResponse(BaseModel):
    agents: List[AgentLatencyPoint]
    confidence_distribution: List[ConfidenceDistributionPoint]
    api_uptime_pct: float
    llm_costs_inr: float
