"""
Health — System health check endpoint.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint — returns 200 OK if the service is running."""
    return {
        "status": "healthy",
        "service": "AI Stock Kundli API",
        "version": "0.1.0",
    }
