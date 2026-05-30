from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit_log import AuditLog

async def log_audit_action(
    db: AsyncSession,
    request: Request | None,
    user_id: int | None,
    tenant_id: int | None,
    action: str,
    details: dict | None = None
) -> AuditLog:
    """Helper to log sensitive actions to the centralized audit logs table."""
    ip_address = None
    user_agent = None
    
    if request:
        # Extract IP address
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            ip_address = forwarded_for.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
            
        # Extract User Agent
        user_agent = request.headers.get("user-agent")
        if user_agent and len(user_agent) > 255:
            user_agent = user_agent[:252] + "..."
            
    log_entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent
    )
    db.add(log_entry)
    await db.flush()
    return log_entry
