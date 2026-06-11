"""
AI Stock Kundli — Feature Guard
FastAPI dependency factory for feature-based access control.

Usage:
    @router.get("/kundli")
    async def get_kundli(
        _: None = Depends(require_feature("full_kundli")),
        ...
    ):
        ...
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_optional_user_id, get_current_user_id
from app.core.plans import has_feature, get_effective_plan, get_upgrade_message
from app.models.user import User


async def _load_user(user_id: int, db: AsyncSession) -> User | None:
    """Load a user by ID from the database."""
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


def require_feature(feature: str, require_auth: bool = True):
    """
    FastAPI dependency factory.
    Returns a dependency that enforces the user has access to `feature`.

    Args:
        feature:      The feature key (e.g. "full_kundli", "portfolio_advice")
        require_auth: If True, unauthenticated users always get 401.
                      If False, unauthenticated users are treated as "free" plan.
    """
    async def _guard(
        db: AsyncSession = Depends(get_db),
        user_id: int | None = Depends(get_optional_user_id),
    ) -> str:
        """Returns the effective plan of the authenticated user."""

        if user_id is None:
            if require_auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required to access this feature.",
                )
            # Treat as free plan
            effective_plan = "free"
        else:
            user = await _load_user(user_id, db)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found.",
                )
            if getattr(user, "is_suspended", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your account has been suspended.",
                )
            effective_plan = get_effective_plan(user)

        if not has_feature(effective_plan, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "feature_locked",
                    "feature": feature,
                    "current_plan": effective_plan,
                    "upgrade_required": True,
                    "message": get_upgrade_message(feature, effective_plan),
                },
            )

        return effective_plan

    return _guard


def get_user_plan(require_auth: bool = False):
    """
    Dependency that resolves the user's effective plan WITHOUT blocking access.
    Use when the endpoint is accessible to all plans but behavior differs by plan.

    Returns:
        effective_plan (str): "free" | "standard" | "pro"
    """
    async def _resolver(
        db: AsyncSession = Depends(get_db),
        user_id: int | None = Depends(get_optional_user_id),
    ) -> str:
        if user_id is None:
            if require_auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required.",
                )
            return "free"

        user = await _load_user(user_id, db)
        if not user:
            return "free"

        return get_effective_plan(user)

    return _resolver
