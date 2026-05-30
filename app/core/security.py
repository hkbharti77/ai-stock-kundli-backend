"""
AI Stock Kundli — Security Utilities
JWT token management and password hashing.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request, Header
from fastapi.security import OAuth2PasswordBearer
import hashlib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.database import get_db
from app.core.cache import cache
from app.models.developer import APIKey

settings = get_settings()

import bcrypt

# ── Password Hashing ────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt directly."""
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash directly."""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False



# ── JWT Token Management ────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(data: dict) -> str:
    """Create a signed JWT refresh token with longer expiry."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_id(
    token: str = Depends(oauth2_scheme),
) -> int:
    """FastAPI dependency — extracts user ID from a valid JWT token."""
    payload = decode_token(token)
    user_id: Optional[int] = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identifier",
        )
    return int(user_id)


async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
) -> "User":
    """FastAPI dependency — fetches user object and checks suspension."""
    from app.models.user import User
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if getattr(user, "is_suspended", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended by an administrator."
        )
    return user


async def get_optional_user_id(request: Request) -> Optional[int]:
    """FastAPI dependency — optionally extracts user ID if valid JWT is present, else None."""
    authorization: str = request.headers.get("Authorization")
    if not authorization:
        return None
    try:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1]
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = payload.get("sub")
        return int(user_id) if user_id else None
    except Exception:
        return None


# ── Developer API Key Verification & Rate Limiting ──

API_RATE_LIMITS = {
    "free": {"daily": 10, "minute": 2},
    "starter": {"daily": 100, "minute": 10},
    "pro": {"daily": 1000, "minute": 30},
    "enterprise": {"daily": 100000, "minute": 100},
}

local_api_rate_limit_store: dict[str, int] = {}


async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    """
    Dependency to verify API keys in the X-API-Key header.
    Validates key active state and enforces minute and daily rate limits.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key is missing in X-API-Key header",
        )

    # Hash key to check against database
    hashed = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()
    stmt = select(APIKey).where(APIKey.hashed_key == hashed)
    res = await db.execute(stmt)
    key_obj = res.scalar_one_or_none()

    if not key_obj or not key_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key",
        )

    if key_obj.user and getattr(key_obj.user, "is_suspended", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The owner account for this API Key is suspended.",
        )

    # Enforce Rate Limiting
    tier = key_obj.rate_limit_tier.lower()
    limits = API_RATE_LIMITS.get(tier, API_RATE_LIMITS["free"])

    now = datetime.utcnow()
    day_str = now.strftime("%Y-%m-%d")
    minute_str = now.strftime("%Y-%m-%d-%H-%M")

    day_key = f"ratelimit:key:{key_obj.id}:day:{day_str}"
    minute_key = f"ratelimit:key:{key_obj.id}:minute:{minute_str}"

    redis_client = None
    try:
        redis_client = cache.client
    except Exception:
        pass

    # 1. Check Minute Limit
    current_min_usage = 0
    if redis_client:
        try:
            val = await redis_client.get(minute_key)
            current_min_usage = int(val) if val else 0
        except Exception:
            pass
    else:
        current_min_usage = local_api_rate_limit_store.get(minute_key, 0)

    if current_min_usage >= limits["minute"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Your plan limit is {limits['minute']} requests/minute.",
        )

    # 2. Check Daily Limit
    current_day_usage = 0
    if redis_client:
        try:
            val = await redis_client.get(day_key)
            current_day_usage = int(val) if val else 0
        except Exception:
            pass
    else:
        current_day_usage = local_api_rate_limit_store.get(day_key, 0)

    if current_day_usage >= limits["daily"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Your plan limit is {limits['daily']} requests/day.",
        )

    # Increment Usage Counters
    if redis_client:
        try:
            pipe = redis_client.pipeline()
            await pipe.incr(minute_key)
            await pipe.expire(minute_key, 60)
            await pipe.incr(day_key)
            await pipe.expire(day_key, 86400)
            await pipe.execute()
        except Exception:
            pass
    else:
        local_api_rate_limit_store[minute_key] = current_min_usage + 1
        local_api_rate_limit_store[day_key] = current_day_usage + 1

    return key_obj



