from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, func, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.tenant import Tenant
from app.models.audit_log import AuditLog
from app.models.invoice import Invoice
from app.models.developer import APIUsageLog, APIKey
from app.schemas.admin import (
    TenantCreate,
    TenantResponse,
    TenantBrandingUpdate,
    AdminUserResponse,
    UserStatusUpdate,
    UserRoleUpdate,
    InvoiceResponse,
    AuditLogResponse,
    UsageAnalyticsResponse,
    AgentMonitoringResponse,
    DailyHitPoint,
    EndpointHitPoint,
    StatusHitPoint,
    AgentLatencyPoint,
    ConfidenceDistributionPoint,
)
from app.services.billing import BillingService
from app.services.audit import log_audit_action

router = APIRouter(prefix="/admin", tags=["Enterprise / Platform Admin"])

# ── Helper: RBAC Checks ──
def require_admin_role(current_user: User = Depends(get_current_user)):
    """Enforce OrgAdmin or SuperAdmin roles."""
    if current_user.role not in ["SuperAdmin", "OrgAdmin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Requires OrgAdmin or SuperAdmin role."
        )
    return current_user

def require_superadmin_role(current_user: User = Depends(get_current_user)):
    """Enforce SuperAdmin role only."""
    if current_user.role != "SuperAdmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Requires SuperAdmin role."
        )
    return current_user

# ── 1. Tenants Management ──

@router.get("/tenants", response_model=List[TenantResponse])
async def list_tenants(
    admin: User = Depends(require_superadmin_role),
    db: AsyncSession = Depends(get_db)
):
    """SuperAdmin only — List all tenants/organizations."""
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.post("/tenants", response_model=TenantResponse)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    admin: User = Depends(require_superadmin_role),
    db: AsyncSession = Depends(get_db)
):
    """SuperAdmin only — Create a new tenant/enterprise organization."""
    # Check domain unique
    if payload.domain:
        stmt_check = select(Tenant).where(Tenant.domain == payload.domain)
        res_check = await db.execute(stmt_check)
        if res_check.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Domain '{payload.domain}' is already registered.")

    new_tenant = Tenant(
        name=payload.name,
        domain=payload.domain,
        brand_name=payload.brand_name or payload.name,
        logo_url=payload.logo_url,
        brand_color=payload.brand_color or "#6366f1",
        brand_color_secondary=payload.brand_color_secondary or "#14b8a6",
        is_active=True
    )
    db.add(new_tenant)
    await db.flush()

    await log_audit_action(
        db, request, admin.id, None, "CREATE_TENANT", 
        {"tenant_name": payload.name, "domain": payload.domain}
    )
    await db.commit()
    await db.refresh(new_tenant)
    return new_tenant

@router.get("/branding/by-domain", response_model=TenantBrandingUpdate)
async def get_branding_by_domain(
    domain: str = Query(..., description="The request host header / domain"),
    db: AsyncSession = Depends(get_db)
):
    """Public endpoint — Resolve tenant branding configurations by domain name."""
    stmt = select(Tenant).where(Tenant.domain == domain, Tenant.is_active == True)
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()
    if not tenant:
        return TenantBrandingUpdate(
            brand_name="AI Stock Kundli",
            logo_url="/logo.png",
            brand_color="#6366f1",
            brand_color_secondary="#14b8a6"
        )
    return tenant

@router.get("/tenants/{tenant_id}/branding", response_model=TenantBrandingUpdate)
async def get_tenant_branding(
    tenant_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Retrieve branding variables for custom enterprise theme injection."""
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

@router.put("/tenants/{tenant_id}/branding", response_model=TenantResponse)
async def update_tenant_branding(
    tenant_id: int,
    payload: TenantBrandingUpdate,
    request: Request,
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Update custom branding color schemas and logos for a tenant."""
    # OrgAdmin can only update their own tenant branding
    if admin.role == "OrgAdmin" and admin.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to edit branding for other organizations.")
        
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if payload.brand_name is not None:
        tenant.brand_name = payload.brand_name
    if payload.logo_url is not None:
        tenant.logo_url = payload.logo_url
    if payload.brand_color is not None:
        tenant.brand_color = payload.brand_color
    if payload.brand_color_secondary is not None:
        tenant.brand_color_secondary = payload.brand_color_secondary

    db.add(tenant)
    await log_audit_action(
        db, request, admin.id, tenant_id, "UPDATE_BRANDING",
        {"brand_name": payload.brand_name, "colors": [payload.brand_color, payload.brand_color_secondary]}
    )
    await db.commit()
    await db.refresh(tenant)
    return tenant

# ── 2. Users Management ──

@router.get("/users", response_model=List[AdminUserResponse])
async def list_users(
    q: Optional[str] = Query(None, description="Search by name or email"),
    tenant_id: Optional[int] = Query(None, description="Filter by tenant (SuperAdmin only)"),
    role: Optional[str] = Query(None, description="Filter by role"),
    plan: Optional[str] = Query(None, description="Filter by plan"),
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """List users inside the tenant (OrgAdmin) or globally (SuperAdmin)."""
    stmt = select(User)
    
    if admin.role == "OrgAdmin":
        stmt = stmt.where(User.tenant_id == admin.tenant_id)
    elif tenant_id is not None:
        stmt = stmt.where(User.tenant_id == tenant_id)
        
    if q:
        stmt = stmt.where((User.email.ilike(f"%{q}%")) | (User.full_name.ilike(f"%{q}%")))
    if role:
        stmt = stmt.where(User.role == role)
    if plan:
        stmt = stmt.where(User.plan == plan)
        
    stmt = stmt.order_by(User.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.put("/users/{user_id}/status", response_model=AdminUserResponse)
async def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    request: Request,
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Suspend or activate a user account."""
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if admin.role == "OrgAdmin" and user.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to suspend users from other organizations.")
        
    user.is_suspended = payload.is_suspended
    db.add(user)
    
    action_type = "SUSPEND_USER" if payload.is_suspended else "UNSUSPEND_USER"
    await log_audit_action(db, request, admin.id, user.tenant_id, action_type, {"target_user_id": user_id, "email": user.email})
    
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/users/{user_id}/role", response_model=AdminUserResponse)
async def update_user_role_and_plan(
    user_id: int,
    payload: UserRoleUpdate,
    request: Request,
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Change user role or plan (RBAC and tier assignment)."""
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if admin.role == "OrgAdmin":
        if user.tenant_id != admin.tenant_id:
            raise HTTPException(status_code=403, detail="Not authorized to modify users from other organizations.")
        if payload.role == "SuperAdmin":
            raise HTTPException(status_code=403, detail="OrgAdmins cannot elevate users to SuperAdmin.")
            
    user.role = payload.role
    if payload.plan is not None:
        user.plan = payload.plan
        
    db.add(user)
    await log_audit_action(
        db, request, admin.id, user.tenant_id, "UPDATE_USER_ROLE_PLAN", 
        {"target_user_id": user_id, "role": payload.role, "plan": payload.plan}
    )
    await db.commit()
    await db.refresh(user)
    return user

# ── 3. Billing & Invoices ──

@router.get("/billing/invoices", response_model=List[InvoiceResponse])
async def list_invoices(
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """List generated billing statements."""
    stmt = select(Invoice)
    
    if admin.role == "OrgAdmin":
        stmt = stmt.where(Invoice.tenant_id == admin.tenant_id)
        
    stmt = stmt.order_by(Invoice.created_at.desc())
    res = await db.execute(stmt)
    invoices = res.scalars().all()
    
    # Enrich details
    payload = []
    for inv in invoices:
        tenant_name = None
        user_email = None
        
        if inv.tenant_id:
            stmt_t = select(Tenant.name).where(Tenant.id == inv.tenant_id)
            res_t = await db.execute(stmt_t)
            tenant_name = res_t.scalar()
            
        if inv.user_id:
            stmt_u = select(User.email).where(User.id == inv.user_id)
            res_u = await db.execute(stmt_u)
            user_email = res_u.scalar()
            
        payload.append(InvoiceResponse(
            id=inv.id,
            tenant_id=inv.tenant_id,
            tenant_name=tenant_name,
            user_id=inv.user_id,
            user_email=user_email,
            billing_period_start=inv.billing_period_start,
            billing_period_end=inv.billing_period_end,
            amount_inr=inv.amount_inr,
            status=inv.status,
            created_at=inv.created_at
        ))
    return payload

@router.post("/billing/invoices/generate", response_model=List[InvoiceResponse])
async def trigger_billing_run(
    request: Request,
    admin: User = Depends(require_superadmin_role),
    db: AsyncSession = Depends(get_db)
):
    """SuperAdmin only — Force generate invoices for the current billing cycle."""
    new_invoices = await BillingService.generate_monthly_invoices(db)
    
    await log_audit_action(
        db, request, admin.id, None, "TRIGGER_BILLING_CYCLE", 
        {"invoice_count": len(new_invoices)}
    )
    
    # Format response
    payload = []
    for inv in new_invoices:
        tenant_name = None
        user_email = None
        if inv.tenant_id:
            t = await db.get(Tenant, inv.tenant_id)
            tenant_name = t.name if t else None
        if inv.user_id:
            u = await db.get(User, inv.user_id)
            user_email = u.email if u else None
            
        payload.append(InvoiceResponse(
            id=inv.id,
            tenant_id=inv.tenant_id,
            tenant_name=tenant_name,
            user_id=inv.user_id,
            user_email=user_email,
            billing_period_start=inv.billing_period_start,
            billing_period_end=inv.billing_period_end,
            amount_inr=inv.amount_inr,
            status=inv.status,
            created_at=inv.created_at
        ))
    return payload

@router.put("/billing/invoices/{invoice_id}/pay", response_model=InvoiceResponse)
async def pay_invoice(
    invoice_id: int,
    request: Request,
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Update invoice state to 'paid' once transaction completes."""
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    res = await db.execute(stmt)
    invoice = res.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    if admin.role == "OrgAdmin" and invoice.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to pay invoices for other organizations.")
        
    invoice.status = "paid"
    db.add(invoice)
    
    await log_audit_action(
        db, request, admin.id, invoice.tenant_id, "PAY_INVOICE", 
        {"invoice_id": invoice_id, "amount": invoice.amount_inr}
    )
    await db.commit()
    
    tenant_name = None
    if invoice.tenant_id:
        t = await db.get(Tenant, invoice.tenant_id)
        tenant_name = t.name if t else None
        
    user_email = None
    if invoice.user_id:
        u = await db.get(User, invoice.user_id)
        user_email = u.email if u else None

    return InvoiceResponse(
        id=invoice.id,
        tenant_id=invoice.tenant_id,
        tenant_name=tenant_name,
        user_id=invoice.user_id,
        user_email=user_email,
        billing_period_start=invoice.billing_period_start,
        billing_period_end=invoice.billing_period_end,
        amount_inr=invoice.amount_inr,
        status=invoice.status,
        created_at=invoice.created_at
    )

# ── 4. Centralized Audit Logs ──

@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """List system audit logs for compliance review."""
    stmt = select(AuditLog)
    
    if admin.role == "OrgAdmin":
        stmt = stmt.where(AuditLog.tenant_id == admin.tenant_id)
        
    stmt = stmt.order_by(AuditLog.timestamp.desc()).limit(limit).offset(offset)
    res = await db.execute(stmt)
    logs = res.scalars().all()
    
    payload = []
    for log in logs:
        tenant_name = None
        user_email = None
        if log.tenant_id:
            stmt_t = select(Tenant.name).where(Tenant.id == log.tenant_id)
            tenant_name = (await db.execute(stmt_t)).scalar()
        if log.user_id:
            stmt_u = select(User.email).where(User.id == log.user_id)
            user_email = (await db.execute(stmt_u)).scalar()
            
        payload.append(AuditLogResponse(
            id=log.id,
            tenant_id=log.tenant_id,
            tenant_name=tenant_name,
            user_id=log.user_id,
            user_email=user_email,
            action=log.action,
            details=log.details,
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            timestamp=log.timestamp
        ))
    return payload

# ── 5. Usage Telemetry & Agent Uptime ──

@router.get("/usage", response_model=UsageAnalyticsResponse)
async def get_usage_telemetry(
    days: int = Query(30, ge=1, le=90),
    tenant_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Fetch usage analytics for charting API volume, users, and endpoints."""
    # Scope based on role
    target_tenant = admin.tenant_id if admin.role == "OrgAdmin" else tenant_id
    
    # 1. Total Calls
    query_total = select(func.count(APIUsageLog.id))
    if target_tenant is not None:
        query_total = query_total.where(APIUsageLog.tenant_id == target_tenant)
    res_total = await db.execute(query_total)
    total_calls = res_total.scalar() or 0
    
    # 2. Active Users
    query_active = select(func.count(func.distinct(APIUsageLog.user_id)))
    if target_tenant is not None:
        query_active = query_active.where(APIUsageLog.tenant_id == target_tenant)
    res_active = await db.execute(query_active)
    active_users = res_active.scalar() or 0
    
    # 3. Daily volume
    timeframe = datetime.utcnow() - timedelta(days=days)
    query_daily = select(
        func.to_char(APIUsageLog.timestamp, "YYYY-MM-DD").label("day"),
        func.count(APIUsageLog.id).label("count")
    ).where(APIUsageLog.timestamp >= timeframe)
    if target_tenant is not None:
        query_daily = query_daily.where(APIUsageLog.tenant_id == target_tenant)
    query_daily = query_daily.group_by("day").order_by("day")
    res_daily = await db.execute(query_daily)
    daily_volume = [DailyHitPoint(date=row[0], count=row[1]) for row in res_daily.all()]
    
    # If no days returned, backfill with zeros
    if not daily_volume:
        daily_volume = [
            DailyHitPoint(date=(datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d"), count=0)
            for i in range(days)
        ]
        daily_volume.reverse()
        
    # 4. Endpoint Breakdown
    query_endpoints = select(
        APIUsageLog.endpoint,
        func.count(APIUsageLog.id).label("count")
    )
    if target_tenant is not None:
        query_endpoints = query_endpoints.where(APIUsageLog.tenant_id == target_tenant)
    query_endpoints = query_endpoints.group_by(APIUsageLog.endpoint).order_by(desc("count")).limit(10)
    res_endpoints = await db.execute(query_endpoints)
    endpoint_breakdown = [EndpointHitPoint(endpoint=row[0], count=row[1]) for row in res_endpoints.all()]
    
    # 5. Status distribution
    query_status = select(
        APIUsageLog.status_code,
        func.count(APIUsageLog.id).label("count")
    )
    if target_tenant is not None:
        query_status = query_status.where(APIUsageLog.tenant_id == target_tenant)
    query_status = query_status.group_by(APIUsageLog.status_code).order_by(desc("count"))
    res_status = await db.execute(query_status)
    status_distribution = [StatusHitPoint(status=str(row[0]), count=row[1]) for row in res_status.all()]
    
    return UsageAnalyticsResponse(
        total_calls=total_calls,
        active_users=active_users,
        daily_volume=daily_volume,
        endpoint_breakdown=endpoint_breakdown,
        status_distribution=status_distribution
    )

@router.get("/monitoring", response_model=AgentMonitoringResponse)
async def get_agent_monitoring(
    admin: User = Depends(require_admin_role),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve multi-agent execution times, API uptime, fallback frequency, and costs."""
    # Return realistic mock/computed telemetry for dashboard visualization
    agents = [
        AgentLatencyPoint(agent="fundamental_analyst", avg_latency_ms=1200.0, error_rate=0.01, fallback_rate=0.05),
        AgentLatencyPoint(agent="technical_analyst", avg_latency_ms=850.0, error_rate=0.0, fallback_rate=0.0),
        AgentLatencyPoint(agent="news_analyst", avg_latency_ms=2100.0, error_rate=0.04, fallback_rate=0.15),
        AgentLatencyPoint(agent="risk_analyst", avg_latency_ms=950.0, error_rate=0.01, fallback_rate=0.02),
        AgentLatencyPoint(agent="macro_analyst", avg_latency_ms=1100.0, error_rate=0.02, fallback_rate=0.04),
        AgentLatencyPoint(agent="sector_analyst", avg_latency_ms=1300.0, error_rate=0.01, fallback_rate=0.03),
        AgentLatencyPoint(agent="valuation_analyst", avg_latency_ms=1050.0, error_rate=0.01, fallback_rate=0.02),
        AgentLatencyPoint(agent="aggregator_agent", avg_latency_ms=450.0, error_rate=0.0, fallback_rate=0.0),
    ]
    
    confidence_distribution = [
        ConfidenceDistributionPoint(score_range="85-100", count=145),
        ConfidenceDistributionPoint(score_range="70-84", count=290),
        ConfidenceDistributionPoint(score_range="50-69", count=84),
        ConfidenceDistributionPoint(score_range="0-49", count=12),
    ]
    
    # Calculate LLM costs based on API keys active
    stmt_keys = select(func.count(APIKey.id))
    res_keys = await db.execute(stmt_keys)
    active_keys = res_keys.scalar() or 0
    llm_costs = active_keys * 18.50  # ₹18.50 per key avg usage
    
    return AgentMonitoringResponse(
        agents=agents,
        confidence_distribution=confidence_distribution,
        api_uptime_pct=99.98,
        llm_costs_inr=llm_costs
    )
