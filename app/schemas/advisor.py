from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Optional

class AdvisorClientCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None

class AdvisorClientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class AdvisorClientResponse(BaseModel):
    id: int
    advisor_id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AdvisorBrandingUpdate(BaseModel):
    advisor_brand_name: Optional[str] = None
    advisor_logo_url: Optional[str] = None
    advisor_brand_color: Optional[str] = None
    advisor_brand_color_secondary: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    brand_color: Optional[str] = None
    brand_color_secondary: Optional[str] = None

class AdvisorClientOverview(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    holdings_count: int
    portfolio_value: float
    risk_status: str  # "Low", "Medium", "High", "Critical"
    deteriorating_signals_count: int
