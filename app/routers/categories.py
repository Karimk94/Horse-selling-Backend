"""Category management endpoints: list category tree, admin CRUD, category seeding."""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.models import Category, ListingModule, User
from app.schemas import (
    CategoryCreateRequest,
    CategoryResponse,
    CategoryTreeNode,
    CategoryUpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Categories"])


def build_tree(categories: list[Category], parent_id: Optional[uuid.UUID] = None) -> list[CategoryTreeNode]:
    """Recursively build nested CategoryTreeNode hierarchy from a list of Category ORM models."""
    nodes = []
    children_map = {}
    
    # Group categories by parent_id
    for cat in categories:
        pid = cat.parent_id
        if pid not in children_map:
            children_map[pid] = []
        children_map[pid].append(cat)

    def _convert(cat: Category) -> CategoryTreeNode:
        kids = children_map.get(cat.id, [])
        kids.sort(key=lambda x: x.display_order)
        return CategoryTreeNode(
            id=cat.id,
            name_ar=cat.name_ar,
            name_en=cat.name_en,
            slug=cat.slug,
            module=cat.module.value if isinstance(cat.module, ListingModule) else str(cat.module),
            parent_id=cat.parent_id,
            icon_name=cat.icon_name,
            display_order=cat.display_order,
            is_active=cat.is_active,
            created_at=cat.created_at,
            updated_at=cat.updated_at,
            children=[_convert(k) for k in kids]
        )

    roots = children_map.get(parent_id, [])
    roots.sort(key=lambda x: x.display_order)
    return [_convert(r) for r in roots]


@router.get("/categories", response_model=list[CategoryTreeNode] | list[CategoryResponse], summary="List categories")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    module: Optional[str] = Query(None, description="equipment, rider_gear, or services"),
    tree: bool = Query(True, description="Return nested tree structure if True, flat list if False"),
    active_only: bool = Query(True, description="Filter only active categories"),
):
    stmt = select(Category)
    if active_only:
        stmt = stmt.where(Category.is_active.is_(True))
    if module:
        stmt = stmt.where(Category.module == module)
    
    stmt = stmt.order_by(Category.display_order, Category.name_en)
    result = await db.execute(stmt)
    categories = list(result.scalars().all())

    if not tree:
        return [
            CategoryResponse(
                id=c.id,
                name_ar=c.name_ar,
                name_en=c.name_en,
                slug=c.slug,
                module=c.module.value if isinstance(c.module, ListingModule) else str(c.module),
                parent_id=c.parent_id,
                icon_name=c.icon_name,
                display_order=c.display_order,
                is_active=c.is_active,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in categories
        ]

    return build_tree(categories, parent_id=None)


@router.get("/categories/{category_id}", response_model=CategoryResponse, summary="Get single category by ID")
async def get_category(
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Category).where(Category.id == category_id)
    result = await db.execute(stmt)
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    
    return CategoryResponse(
        id=category.id,
        name_ar=category.name_ar,
        name_en=category.name_en,
        slug=category.slug,
        module=category.module.value if isinstance(category.module, ListingModule) else str(category.module),
        parent_id=category.parent_id,
        icon_name=category.icon_name,
        display_order=category.display_order,
        is_active=category.is_active,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


@router.post("/admin/categories", response_model=CategoryResponse, summary="Create category (Admin)")
async def create_category(
    payload: CategoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Check slug uniqueness
    stmt = select(Category).where(Category.slug == payload.slug)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Category slug '{payload.slug}' already exists",
        )

    # Validate parent if provided
    if payload.parent_id:
        parent_stmt = select(Category).where(Category.id == payload.parent_id)
        parent_cat = (await db.execute(parent_stmt)).scalar_one_or_none()
        if not parent_cat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent category not found",
            )

    category = Category(
        name_ar=payload.name_ar,
        name_en=payload.name_en,
        slug=payload.slug,
        module=ListingModule(payload.module),
        parent_id=payload.parent_id,
        icon_name=payload.icon_name,
        display_order=payload.display_order,
        is_active=payload.is_active,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)

    return CategoryResponse(
        id=category.id,
        name_ar=category.name_ar,
        name_en=category.name_en,
        slug=category.slug,
        module=category.module.value if isinstance(category.module, ListingModule) else str(category.module),
        parent_id=category.parent_id,
        icon_name=category.icon_name,
        display_order=category.display_order,
        is_active=category.is_active,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


@router.put("/admin/categories/{category_id}", response_model=CategoryResponse, summary="Update category (Admin)")
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    stmt = select(Category).where(Category.id == category_id)
    category = (await db.execute(stmt)).scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    if payload.slug is not None and payload.slug != category.slug:
        slug_check = select(Category).where(Category.slug == payload.slug)
        existing = (await db.execute(slug_check)).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category slug '{payload.slug}' already exists",
            )
        category.slug = payload.slug

    if payload.name_ar is not None:
        category.name_ar = payload.name_ar
    if payload.name_en is not None:
        category.name_en = payload.name_en
    if payload.module is not None:
        category.module = ListingModule(payload.module)
    if payload.parent_id is not None:
        if payload.parent_id == category.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category cannot be its own parent",
            )
        category.parent_id = payload.parent_id
    if payload.icon_name is not None:
        category.icon_name = payload.icon_name
    if payload.display_order is not None:
        category.display_order = payload.display_order
    if payload.is_active is not None:
        category.is_active = payload.is_active

    await db.commit()
    await db.refresh(category)

    return CategoryResponse(
        id=category.id,
        name_ar=category.name_ar,
        name_en=category.name_en,
        slug=category.slug,
        module=category.module.value if isinstance(category.module, ListingModule) else str(category.module),
        parent_id=category.parent_id,
        icon_name=category.icon_name,
        display_order=category.display_order,
        is_active=category.is_active,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


@router.delete("/admin/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete category (Admin)")
async def delete_category(
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    stmt = select(Category).where(Category.id == category_id)
    category = (await db.execute(stmt)).scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    await db.delete(category)
    await db.commit()
    return None
