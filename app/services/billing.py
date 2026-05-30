from datetime import datetime, timedelta
import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tenant import Tenant
from app.models.user import User
from app.models.developer import APIUsageLog
from app.models.invoice import Invoice

logger = logging.getLogger("app.services.billing")

class BillingService:
    @staticmethod
    async def generate_tenant_invoice(
        db: AsyncSession,
        tenant_id: int,
        start_date: datetime,
        end_date: datetime
    ) -> Invoice:
        """Calculate and generate an invoice for an enterprise tenant."""
        # 1. Base fee
        base_fee = 50000.0  # Enterprise base subscription fee in INR
        
        # 2. Calculate active users count
        stmt_users = select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.is_suspended == False
        )
        res_users = await db.execute(stmt_users)
        user_count = res_users.scalar() or 0
        users_fee = user_count * 500.0  # ₹500 per active user
        
        # 3. Calculate usage cost from APIUsageLog
        stmt_usage = select(func.sum(APIUsageLog.cost_inr)).where(
            APIUsageLog.tenant_id == tenant_id,
            APIUsageLog.timestamp >= start_date,
            APIUsageLog.timestamp <= end_date
        )
        res_usage = await db.execute(stmt_usage)
        usage_fee = float(res_usage.scalar() or 0.0)
        
        total_amount = base_fee + users_fee + usage_fee
        
        invoice = Invoice(
            tenant_id=tenant_id,
            user_id=None,
            billing_period_start=start_date,
            billing_period_end=end_date,
            amount_inr=total_amount,
            status="pending",
            created_at=datetime.utcnow()
        )
        db.add(invoice)
        await db.flush()
        logger.info(f"Generated invoice for tenant={tenant_id} amount={total_amount}")
        return invoice

    @staticmethod
    async def generate_user_invoice(
        db: AsyncSession,
        user_id: int,
        start_date: datetime,
        end_date: datetime
    ) -> Invoice | None:
        """Calculate and generate an invoice for a retail user."""
        stmt = select(User).where(User.id == user_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            return None
            
        plan_lower = user.plan.lower()
        if plan_lower == "free":
            return None  # No invoice for free plan
            
        # Standard flat monthly rates
        plan_costs = {
            "starter": 299.0,
            "pro": 999.0,
            "advisor": 2499.0
        }
        cost = plan_costs.get(plan_lower, 0.0)
        if cost == 0.0:
            return None
            
        invoice = Invoice(
            tenant_id=None,
            user_id=user_id,
            billing_period_start=start_date,
            billing_period_end=end_date,
            amount_inr=cost,
            status="pending",
            created_at=datetime.utcnow()
        )
        db.add(invoice)
        await db.flush()
        logger.info(f"Generated invoice for user={user_id} amount={cost} plan={plan_lower}")
        return invoice

    @staticmethod
    async def generate_monthly_invoices(db: AsyncSession) -> list[Invoice]:
        """Runs a monthly billing batch for all tenants and users."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=30)
        end_date = now
        
        invoices = []
        
        # Tenants
        stmt_tenants = select(Tenant).where(Tenant.is_active == True)
        res_tenants = await db.execute(stmt_tenants)
        tenants = res_tenants.scalars().all()
        for tenant in tenants:
            invoice = await BillingService.generate_tenant_invoice(db, tenant.id, start_date, end_date)
            invoices.append(invoice)
            
        # Retail Users
        stmt_users = select(User).where(
            User.tenant_id == None,
            User.plan != "free"
        )
        res_users = await db.execute(stmt_users)
        users = res_users.scalars().all()
        for user in users:
            invoice = await BillingService.generate_user_invoice(db, user.id, start_date, end_date)
            if invoice:
                invoices.append(invoice)
                
        await db.commit()
        return invoices
