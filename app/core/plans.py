"""
AI Stock Kundli — Plan Definitions & Feature Access Control
Single source of truth for all subscription tiers and feature access.

Plans: free / standard / pro
Feature-based (not agent-name-based) access control.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User

# ── Feature Definitions ──────────────────────────────────────────────────────
FEATURES = {
    # Core analysis (Free+)
    "fundamental_analysis",
    "technical_analysis",
    # Standard+
    "news_analysis",
    "risk_analysis",
    "valuation_analysis",
    "basic_kundli",       # Partial Kundli: score + signal + top strengths/risks
    # Pro only
    "sector_analysis",
    "macro_analysis",
    "portfolio_advice",
    "sentiment_analysis",
    "full_kundli",        # Complete 7-agent weighted Kundli report
    "advanced_scoring",   # Confidence %, probability horizons, sensitizers
}

# ── Plan → Feature Mapping ────────────────────────────────────────────────────
PLAN_FEATURES: dict[str, set[str]] = {
    "free": set(),
    "standard": {
        "fundamental_analysis",
        "technical_analysis",
        "news_analysis",
        "risk_analysis",
        "valuation_analysis",
        "basic_kundli",
        "portfolio_advice",
    },
    "pro": {
        "fundamental_analysis",
        "technical_analysis",
        "news_analysis",
        "risk_analysis",
        "valuation_analysis",
        "basic_kundli",
        "sector_analysis",
        "macro_analysis",
        "portfolio_advice",
        "sentiment_analysis",
        "full_kundli",
        "advanced_scoring",
    },
}

# ── Agent → Feature Mapping ────────────────────────────────────────────────────
# Plans never reference agent classes directly — only features.
AGENT_FEATURE_MAP: dict[str, str] = {
    "fundamental_analyst": "fundamental_analysis",
    "technical_analyst":   "technical_analysis",
    "news_analyst":        "news_analysis",
    "risk_analyst":        "risk_analysis",
    "valuation_analyst":   "valuation_analysis",
    "sector_analyst":      "sector_analysis",
    "macro_analyst":       "macro_analysis",
    "portfolio_advisor":   "portfolio_advice",
    "sentiment_analyst":   "sentiment_analysis",
}

# ── Sections locked for Standard (shown in partial Kundli) ───────────────────
STANDARD_LOCKED_SECTIONS = [
    "Sector Analysis",
    "Macro Analysis",
    "Sentiment Analysis",
    "Confidence Score",
    "Probability Horizons",
    "Advanced Scoring",
]

# ── Pricing ───────────────────────────────────────────────────────────────────
PLAN_PRICES_INR: dict[str, float] = {
    "free":     0.0,
    "standard": 299.0,
    "pro":      799.0,
}

TRIAL_PRICE_INR  = 10.0
TRIAL_DURATION_DAYS = 2


# ── Helper Functions ──────────────────────────────────────────────────────────

def get_plan_features(plan: str) -> set[str]:
    """Return the set of features available for a given plan name."""
    plan_lower = (plan or "free").lower()
    return PLAN_FEATURES.get(plan_lower, PLAN_FEATURES["free"])


def has_feature(plan: str, feature: str) -> bool:
    """Check whether a plan includes a specific feature."""
    return feature in get_plan_features(plan)


def get_effective_plan(user: "User") -> str:
    """
    Resolve the user's effective plan at runtime.
    If the user is on a Pro trial that hasn't expired yet → return 'pro'.
    Otherwise return the user's plan field as-is.
    """
    plan = (user.plan or "free").lower()

    # Check active trial
    if (
        plan == "pro"
        and getattr(user, "subscription_status", None) == "trialing"
        and getattr(user, "trial_expires_at", None) is not None
    ):
        expires_at = user.trial_expires_at
        # Make timezone-aware for comparison
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < expires_at:
            return "pro"
        # Trial expired but not yet cleaned up by background job
        return "standard"

    return plan


def get_upgrade_message(feature: str, current_plan: str) -> str:
    """Return a human-friendly upgrade message for a locked feature."""
    feature_labels = {
        "news_analysis":       "News Sentiment Analysis",
        "risk_analysis":       "Risk & Governance Analysis",
        "valuation_analysis":  "Valuation Analysis",
        "basic_kundli":        "Kundli Report",
        "sector_analysis":     "Sector Intelligence",
        "macro_analysis":      "Macro Economic Analysis",
        "portfolio_advice":    "Portfolio Advisor",
        "sentiment_analysis":  "Sentiment Engine",
        "full_kundli":         "Full Kundli Report",
        "advanced_scoring":    "Advanced Scoring & Probability Horizons",
    }
    label = feature_labels.get(feature, feature.replace("_", " ").title())

    if current_plan == "free":
        return (
            f"🔒 {label} is available in Standard (₹299/month) and Pro (₹799/month) plans. "
            f"Upgrade to access this feature."
        )
    elif current_plan == "standard":
        return (
            f"🔒 {label} is available in the Pro plan (₹799/month). "
            f"Try Pro for 2 days at just ₹10, or upgrade to unlock full access."
        )
    return f"🔒 {label} requires a higher plan."
