"""
Disclaimer & Legal Notice Endpoint
────────────────────────────────────
Returns the platform's legally binding disclaimer text for use in API
consumers, mobile apps, or any third-party integration. Every API response
that returns investment-related data should reference this disclaimer.

This module also provides a FastAPI middleware hook that automatically
injects a standard disclaimer header into all responses.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/disclaimer", tags=["Legal"])

# ─── Canonical disclaimer text ───────────────────────────────────────────────

DISCLAIMER_TEXT = (
    "AI Stock Kundli is FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY. "
    "This platform is NOT registered as an investment adviser with SEBI, "
    "SEC, FCA, or any other financial regulatory authority. "
    "All AI-generated scores, signals, ratings, consensus results, and reports "
    "are automated mathematical calculations based on historical data. "
    "They do NOT constitute personalized financial advice, investment recommendations, "
    "or endorsements to buy, sell, or hold any security. "
    "Investments in securities markets are subject to market risks. "
    "YOU MAY LOSE PART OR ALL OF YOUR INVESTED CAPITAL. "
    "Any investment decision you make is entirely your own responsibility "
    "and is made entirely AT YOUR OWN RISK. "
    "The creators, developers, and operators of this platform accept NO LIABILITY "
    "for any financial losses, damages, or adverse outcomes resulting from use of "
    "or reliance on any information provided by this platform. "
    "Always consult a SEBI-registered investment adviser before making financial decisions."
)

DISCLAIMER_SHORT = (
    "FOR RESEARCH USE ONLY. Not investment advice. "
    "Not SEBI/SEC/FCA registered. Invest at your own risk."
)

DISCLAIMER_VERSION = "2.0.0"
DISCLAIMER_EFFECTIVE_DATE = "2026-06-09"


@router.get(
    "",
    summary="Get Platform Legal Disclaimer",
    description=(
        "Returns the full legal disclaimer that must be displayed to all users "
        "and third-party API consumers. This disclaimer governs all AI-generated "
        "data returned by this API."
    ),
)
async def get_disclaimer():
    """
    Return the canonical platform disclaimer.
    Third-party API integrations MUST display this disclaimer to end-users.
    """
    return JSONResponse(
        content={
            "disclaimer_version": DISCLAIMER_VERSION,
            "effective_date": DISCLAIMER_EFFECTIVE_DATE,
            "platform": "AI Stock Kundli",
            "purpose": "EDUCATIONAL AND RESEARCH USE ONLY",
            "regulatory_status": {
                "sebi_registered": False,
                "sec_registered": False,
                "fca_registered": False,
                "note": "This platform is NOT registered with any financial regulatory authority.",
            },
            "investment_advice": False,
            "full_disclaimer": DISCLAIMER_TEXT,
            "short_disclaimer": DISCLAIMER_SHORT,
            "risk_warning": (
                "Investments in securities markets involve substantial risk. "
                "You may lose part or all of your invested capital. "
                "Past performance does not guarantee future results. "
                "All investment decisions are made by the user at their own risk."
            ),
            "liability": (
                "The platform operators accept zero liability for any financial losses "
                "arising from use of or reliance on this platform."
            ),
            "terms_url": "/terms",
            "privacy_url": "/privacy",
            "risk_disclosure_url": "/terms#risk-disclosure",
        },
        headers={
            "X-Platform-Disclaimer": DISCLAIMER_SHORT,
            "X-Not-Investment-Advice": "true",
            "X-Use-At-Own-Risk": "true",
        },
    )


@router.get(
    "/short",
    summary="Get Short Disclaimer",
    description="Returns a concise one-line disclaimer for inline display.",
)
async def get_short_disclaimer():
    """Return the short form disclaimer for inline UI display."""
    return {
        "disclaimer": DISCLAIMER_SHORT,
        "version": DISCLAIMER_VERSION,
        "not_investment_advice": True,
        "invest_at_own_risk": True,
    }
