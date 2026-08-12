"""Voucher endpoints."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_current_admin
from app.database import get_db
from app.models import User, Voucher, DiscountType
from app.schemas import (
    VoucherCreateRequest, VoucherResponse, VoucherValidateRequest, VoucherValidateResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Vouchers"])


@router.post("/vouchers", response_model=VoucherResponse, status_code=status.HTTP_201_CREATED, summary="Create a voucher (Admin only)")
async def create_voucher(body: VoucherCreateRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    result = await db.execute(select(Voucher).where(Voucher.code == body.code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Voucher code already exists")
    voucher = Voucher(code=body.code, discount_type=DiscountType(body.discount_type), discount_value=body.discount_value, valid_from=body.valid_from, valid_until=body.valid_until, usage_limit=body.usage_limit, is_active=body.is_active)
    db.add(voucher)
    await db.commit()
    await db.refresh(voucher)
    return voucher


@router.get("/vouchers", response_model=list[VoucherResponse], summary="List all vouchers (Admin only)")
async def list_vouchers(db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    result = await db.execute(select(Voucher).order_by(Voucher.created_at.desc()))
    return result.scalars().all()


@router.post("/vouchers/validate", response_model=VoucherValidateResponse, summary="Validate a voucher code")
async def validate_voucher(body: VoucherValidateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(Voucher).where(Voucher.code == body.code)
    result = await db.execute(stmt)
    voucher = result.scalar_one_or_none()
    if not voucher:
        return VoucherValidateResponse(valid=False, message="Invalid voucher code")
    if not voucher.is_active:
        return VoucherValidateResponse(valid=False, message="Voucher is inactive")
    now = datetime.now(timezone.utc)
    if voucher.valid_from and voucher.valid_from > now:
        return VoucherValidateResponse(valid=False, message="Voucher is not yet active")
    if voucher.valid_until and voucher.valid_until < now:
        return VoucherValidateResponse(valid=False, message="Voucher has expired")
    if voucher.usage_limit is not None and voucher.used_count >= voucher.usage_limit:
        return VoucherValidateResponse(valid=False, message="Voucher usage limit reached")
    new_price = None
    if body.current_price is not None:
        if voucher.discount_type == DiscountType.PERCENTAGE:
            new_price = body.current_price - (body.current_price * (voucher.discount_value / 100))
        elif voucher.discount_type == DiscountType.FIXED:
            new_price = max(0, body.current_price - voucher.discount_value)
    return VoucherValidateResponse(valid=True, message="Voucher Applied", discount_type=voucher.discount_type, discount_value=voucher.discount_value, new_price=new_price)
