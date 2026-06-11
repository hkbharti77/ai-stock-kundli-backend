"""
User — Pydantic schemas for auth requests and responses.
"""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Requests ─────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Sign-up request body."""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=20)


class UserLogin(BaseModel):
    """Login request body."""
    email: EmailStr
    password: str


class TokenRefresh(BaseModel):
    """Token refresh request body."""
    refresh_token: str


class UserProfileUpdate(BaseModel):
    """Profile update details for multi-step registration & settings."""
    full_name: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=20)
    city: str | None = Field(None, max_length=100)
    dob: str | None = Field(None, max_length=20)
    pan: str | None = Field(None, max_length=10)
    risk_appetite: str | None = Field(None, max_length=50)
    experience: str | None = Field(None, max_length=50)
    goal: str | None = Field(None, max_length=100)
    horizon: str | None = Field(None, max_length=50)
    disclaimer_accepted: bool | None = Field(None)


class SendOTPRequest(BaseModel):
    """Send OTP request body."""
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    """Verify OTP request body."""
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


# ── Password Reset Flow ──────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    """Initiate password reset — send OTP to registered email."""
    email: EmailStr


class VerifyResetOTPRequest(BaseModel):
    """Verify the password reset OTP code."""
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    """Set a new password after OTP verification."""
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)



# ── Responses ────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """JWT token pair returned on login/signup."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user profile."""
    id: int
    role: str
    email: str
    full_name: str | None
    phone: str | None
    otp_verified: bool
    city: str | None
    dob: str | None
    pan: str | None
    risk_appetite: str | None
    experience: str | None
    goal: str | None
    horizon: str | None
    disclaimer_accepted: bool
    plan: str
    is_verified: bool
    created_at: datetime
    
    subscription_status: str | None
    subscription_started_at: datetime | None
    subscription_ends_at: datetime | None
    provider_subscription_id: str | None
    trial_used: bool
    trial_expires_at: datetime | None
    
    can_use_trial: bool = True

    class Config:
        from_attributes = True



class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
