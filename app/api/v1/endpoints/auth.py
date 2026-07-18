"""
Auth — Authentication endpoints (signup, login, refresh, me, send-otp, verify-otp, profile update).
"""

import smtplib
import random
import time
import redis
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user_id,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.user import (
    MessageResponse,
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserProfileUpdate,
    SendOTPRequest,
    VerifyOTPRequest,
    ForgotPasswordRequest,
    VerifyResetOTPRequest,
    ResetPasswordRequest,
)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Redis Client & In-Memory Fallback ────────────────────────
redis_client = None
try:
    if settings.REDIS_URL:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        # Verify connection
        redis_client.ping()
except Exception as e:
    print(f"[Redis Warning] Failed to connect to Redis ({e}). Using in-memory backup store.")
    redis_client = None

# In-memory backup store for OTPs if Redis is down
# Format: {email: (otp_code, expires_at)}
otp_store: dict[str, tuple[str, float]] = {}


# ── SMTP Email Helper ────────────────────────────────────────
def send_otp_email(to_email: str, otp_code: str):
    """Send verification OTP using configured SMTP settings or Resend."""
    if not settings.SMTP_USERNAME and not settings.RESEND_API_KEY:
        # Fallback mock logging for safe local execution without credentials
        print(f"[MOCK SMTP] SMTP/Resend not configured. OTP for {to_email} is {otp_code}")
        return

    from app.core.email import get_master_email_template

    subject = f"{otp_code} is your AI Stock Kundli Verification Code"
    text_content = f"Your verification code is {otp_code}. It is valid for 5 minutes."

    html_body = f"""
    <h2 style="margin-top: 0; color: #E2E8F0; font-size: 20px;">Secure Compliance Registration</h2>
    <p>Hello,</p>
    <p>Thank you for taking the first step to securing your premium research dashboard. Please use the secure verification code below to verify your email address:</p>
    
    <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 30px auto;">
      <tr>
        <td style="background: rgba(59, 130, 246, 0.15); border: 1px solid #3B82F6; color: #3B82F6; font-size: 32px; font-weight: bold; letter-spacing: 6px; padding: 14px 35px; border-radius: 8px; font-family: 'Courier New', Courier, monospace; text-align: center;">
          {otp_code}
        </td>
      </tr>
    </table>
    
    <p style="font-size: 14px; line-height: 1.5; color: #94A3B8; margin: 24px 0 0 0;">This OTP code is valid for <strong>5 minutes</strong>. If you did not request this verification, please safely ignore this email.</p>
    """
    
    full_html = get_master_email_template(subject, html_body)

    # Use Resend if API key is provided
    if settings.RESEND_API_KEY:
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "from": "AI Stock Kundli <onboarding@resend.dev>",
            "to": [to_email],
            "subject": subject,
            "html": full_html,
        }
        try:
            with httpx.Client() as client:
                res = client.post("https://api.resend.com/emails", json=data, headers=headers, timeout=10.0)
                res.raise_for_status()
            return
        except Exception as e:
            print(f"\n[⚠️ RESEND ERROR] Failed to send email via Resend API: {e}")
            print(f"[✅ OTP BYPASS] Verification OTP for {to_email} is: {otp_code}\n")
            return

    # Fallback to SMTP
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"AI Stock Kundli <{settings.SMTP_USERNAME}>"
    msg["To"] = to_email

    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(full_html, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USERNAME, to_email, msg.as_string())
    except OSError as e:
        print(f"\n[⚠️ SMTP BLOCKED] Network unreachable (Render Free Tier?).")
        print(f"[✅ OTP BYPASS] Verification OTP for {to_email} is: {otp_code}\n")
    except Exception as e:
        print(f"[SMTP Error] Failed to send email to {to_email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SMTP dispatch failure: {str(e)}"
        )


def _send_reset_otp_email(to_email: str, otp_code: str):
    """Send a password-reset OTP email (distinct styling from registration OTP)."""
    if not settings.SMTP_USERNAME and not settings.RESEND_API_KEY:
        print(f"[MOCK SMTP] Password reset OTP for {to_email}: {otp_code}")
        return

    from app.core.email import get_master_email_template

    subject = f"{otp_code} — AI Stock Kundli Password Reset"
    text_content = f"Your password reset code is {otp_code}. It is valid for 5 minutes."

    html_body = f"""
    <h2 style="margin-top: 0; color: #F59E0B; font-size: 20px;">🔐 Password Reset</h2>
    <p>Hello,</p>
    <p>We received a request to reset your AI Stock Kundli account password. Use the secure code below to proceed:</p>
    
    <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 30px auto;">
      <tr>
        <td style="background: rgba(245, 158, 11, 0.12); border: 1px solid #F59E0B; color: #F59E0B; font-size: 32px; font-weight: bold; letter-spacing: 6px; padding: 14px 35px; border-radius: 8px; font-family: 'Courier New', Courier, monospace; text-align: center;">
          {otp_code}
        </td>
      </tr>
    </table>
    
    <p style="font-size: 14px; line-height: 1.5; color: #94A3B8; margin: 24px 0 0 0;">This code expires in <strong>5 minutes</strong>. If you did not request a password reset, please ignore this email — your account is safe.</p>
    """
    
    full_html = get_master_email_template(subject, html_body)

    # Use Resend if API key is provided
    if settings.RESEND_API_KEY:
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "from": "AI Stock Kundli <onboarding@resend.dev>",
            "to": [to_email],
            "subject": subject,
            "html": full_html,
        }
        try:
            with httpx.Client() as client:
                res = client.post("https://api.resend.com/emails", json=data, headers=headers, timeout=10.0)
                res.raise_for_status()
            return
        except Exception as e:
            print(f"\n[⚠️ RESEND ERROR] Failed to send reset email via Resend API: {e}")
            print(f"[✅ OTP BYPASS] Password Reset OTP for {to_email} is: {otp_code}\n")
            return

    # Fallback to SMTP
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"AI Stock Kundli <{settings.SMTP_USERNAME}>"
    msg["To"] = to_email

    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(full_html, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USERNAME, to_email, msg.as_string())
    except OSError as e:
        print(f"\n[⚠️ SMTP BLOCKED] Network unreachable (Render Free Tier?).")
        print(f"[✅ OTP BYPASS] Password Reset OTP for {to_email} is: {otp_code}\n")
    except Exception as e:
        print(f"[SMTP Error] Failed to send reset email to {to_email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SMTP dispatch failure: {str(e)}"
        )


# ── Auth Endpoints ───────────────────────────────────────────

@router.post(
    "/send-otp",
    response_model=MessageResponse,
    summary="Generate and send an email verification OTP code",
)
async def send_otp(payload: SendOTPRequest):
    """Generate 6-digit code, store in Redis/RAM cache, and dispatch email."""
    otp_code = f"{random.randint(100000, 999999)}"
    
    # Store with 5-minute TTL
    if redis_client:
        redis_client.setex(f"otp:{payload.email}", 300, otp_code)
    else:
        otp_store[payload.email] = (otp_code, time.time() + 300)
    
    # Send email asynchronously
    send_otp_email(payload.email, otp_code)
    
    return MessageResponse(message="Verification OTP has been sent to your email address")


@router.post(
    "/verify-otp",
    response_model=MessageResponse,
    summary="Validate user email registration OTP code",
)
async def verify_otp(payload: VerifyOTPRequest):
    """Validate 6-digit OTP code from user email."""
    verified = False
    
    if redis_client:
        cached_otp = redis_client.get(f"otp:{payload.email}")
        if cached_otp and cached_otp == payload.code:
            redis_client.delete(f"otp:{payload.email}")
            # Cache verification flag so signup can read it if needed
            redis_client.setex(f"verified:{payload.email}", 600, "true")
            verified = True
    else:
        if payload.email in otp_store:
            cached_otp, expires_at = otp_store[payload.email]
            if time.time() < expires_at and cached_otp == payload.code:
                del otp_store[payload.email]
                # Cache in RAM
                otp_store[f"verified:{payload.email}"] = ("true", time.time() + 600)
                verified = True
                
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code"
        )
        
    return MessageResponse(message="Email verified successfully")


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
)
async def signup(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user account, mark OTP verified, and return JWT tokens."""
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == payload.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # Double check OTP verification cache to enforce security
    otp_verified_in_cache = False
    if redis_client:
        otp_verified_in_cache = redis_client.get(f"verified:{payload.email}") == "true"
        if otp_verified_in_cache:
            redis_client.delete(f"verified:{payload.email}")
    else:
        cache_key = f"verified:{payload.email}"
        if cache_key in otp_store:
            cached_val, expires_at = otp_store[cache_key]
            if time.time() < expires_at and cached_val == "true":
                del otp_store[cache_key]
                otp_verified_in_cache = True
                
    # Fallback to local dev testing if SMTP not fully populated
    if not settings.SMTP_USERNAME:
        otp_verified_in_cache = True

    if not otp_verified_in_cache:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address via OTP first"
        )

    # Create user
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        otp_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Generate tokens
    token_data = {"sub": str(user.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(
    payload: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate a user and return JWT tokens."""
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token_data = {"sub": str(user.id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(payload: TokenRefresh):
    """Exchange a valid refresh token for a new token pair."""
    decoded = decode_token(payload.refresh_token)

    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — expected refresh token",
        )

    token_data = {"sub": decoded["sub"]}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


async def check_trial_eligibility(user: User, db: AsyncSession) -> bool:
    """Check if user or any associated accounts have used a trial in the last 90 days."""
    conditions = [User.email == user.email]
    if user.phone:
        conditions.append(User.phone == user.phone)
        
    from sqlalchemy import or_
    result = await db.execute(select(User).where(or_(*conditions)))
    associated_users = result.scalars().all()
    
    for u in associated_users:
        if u.trial_used or u.plan in ["pro", "pro_trial"]:
            # Check if their last subscription or trial expired > 90 days ago
            last_active = u.subscription_ends_at or u.trial_expires_at
            if last_active:
                if datetime.utcnow() - last_active <= timedelta(days=90):
                    return False
            else:
                # Used trial/plan but no expiry (meaning it's active or corrupted)
                return False
                
    return True

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's profile."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Compute dynamic trial eligibility
    user_data = user.__dict__.copy()
    user_data["can_use_trial"] = await check_trial_eligibility(user, db)

    return user_data


@router.put(
    "/profile",
    response_model=UserResponse,
    summary="Update current user registration steps and settings",
)
async def update_profile(
    payload: UserProfileUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Update profile fields for SEBI identity compliance, investor profile and disclaimers."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Apply partial updates dynamically
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    
    # Compute dynamic trial eligibility
    user_data = user.__dict__.copy()
    user_data["can_use_trial"] = await check_trial_eligibility(user, db)

    return user_data


# ── Password Reset via Email OTP ────────────────────────────────

@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Send a password reset OTP to a registered email",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 of password reset.
    Checks that the email exists in the DB, then sends a 6-digit reset OTP.
    Always returns 200 regardless of whether the email exists (security best practice).
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # Always generate and "send" to prevent user enumeration
    otp_code = f"{random.randint(100000, 999999)}"

    if user:
        # Store with "reset:" prefix to distinguish from registration OTPs
        if redis_client:
            redis_client.setex(f"reset:{payload.email}", 300, otp_code)
        else:
            otp_store[f"reset:{payload.email}"] = (otp_code, time.time() + 300)

        # Build a distinct password-reset email
        _send_reset_otp_email(payload.email, otp_code)

    return MessageResponse(
        message="If an account with that email exists, a password reset code has been sent."
    )


@router.post(
    "/verify-reset-otp",
    response_model=MessageResponse,
    summary="Verify the password reset OTP code",
)
async def verify_reset_otp(
    payload: VerifyResetOTPRequest,
):
    """
    Step 2 of password reset.
    Validates the 6-digit OTP and stamps a short-lived 'reset_verified' flag.
    """
    verified = False

    if redis_client:
        cached = redis_client.get(f"reset:{payload.email}")
        if cached and cached == payload.code:
            redis_client.delete(f"reset:{payload.email}")
            redis_client.setex(f"reset_verified:{payload.email}", 600, "true")
            verified = True
    else:
        key = f"reset:{payload.email}"
        if key in otp_store:
            cached_otp, expires_at = otp_store[key]
            if time.time() < expires_at and cached_otp == payload.code:
                del otp_store[key]
                otp_store[f"reset_verified:{payload.email}"] = ("true", time.time() + 600)
                verified = True

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code. Please request a new one.",
        )

    return MessageResponse(message="Reset code verified. You may now set a new password.")


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password after OTP verification",
)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 3 of password reset.
    Re-validates the OTP (idempotent safety) and updates the password hash.
    The reset_verified flag is consumed on success.
    """
    # Re-check the OTP so this endpoint can't be called without prior verification
    verified = False

    if redis_client:
        cached = redis_client.get(f"reset:{payload.email}")
        if cached and cached == payload.code:
            redis_client.delete(f"reset:{payload.email}")
            redis_client.delete(f"reset_verified:{payload.email}")
            verified = True
        elif redis_client.get(f"reset_verified:{payload.email}") == "true":
            redis_client.delete(f"reset_verified:{payload.email}")
            verified = True
    else:
        reset_key = f"reset:{payload.email}"
        verified_key = f"reset_verified:{payload.email}"
        if reset_key in otp_store:
            cached_otp, expires_at = otp_store[reset_key]
            if time.time() < expires_at and cached_otp == payload.code:
                del otp_store[reset_key]
                otp_store.pop(verified_key, None)
                verified = True
        elif verified_key in otp_store:
            cached_val, expires_at = otp_store[verified_key]
            if time.time() < expires_at and cached_val == "true":
                del otp_store[verified_key]
                verified = True

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reset not authorised. Please request a new OTP.",
        )

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password_hash = hash_password(payload.new_password)
    await db.commit()

    return MessageResponse(message="Password has been reset successfully. You may now log in.")


# ── Daily Usage Endpoint ────────────────────────────────────

@router.get(
    "/me/usage",
    summary="Get current user's daily Kundli usage count",
)
async def get_usage(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return how many Kundli reports the user has consumed today, and their daily limit."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    plan = user.plan.lower()
    if plan == "standard" or plan == "starter":
        limit = 20
    elif plan in ["pro", "advisor", "admin"]:
        limit = -1  # Unlimited
    else:  # free
        limit = 3

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    limit_key = f"ratelimit:user:{user_id}:date:{today_str}"

    used = 0

    # Try async Redis via cache client first
    try:
        from app.core.cache import cache
        redis_async = cache.client
        if redis_async:
            val = await redis_async.get(limit_key)
            used = int(val) if val else 0
        else:
            raise Exception("No async Redis client")
    except Exception:
        # Fall back to sync redis_client in this module
        if redis_client:
            try:
                val = redis_client.get(limit_key)
                used = int(val) if val else 0
            except Exception:
                pass
        else:
            # Fall back to in-memory store from companies module
            try:
                from app.api.v1.endpoints.companies import local_rate_limit_store
                used = local_rate_limit_store.get(limit_key, 0)
            except Exception:
                pass

    return {"used": used, "limit": limit, "plan": plan}
