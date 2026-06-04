from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.document import ItemAlias, ItemMaster
from app.schemas.item_master import (
    ItemAliasCreate,
    ItemAliasRead,
    ItemAliasUpdate,
    ItemMasterCreate,
    ItemMasterListResponse,
    ItemMasterRead,
    ItemMasterStats,
    ItemMasterUpdate,
    ItemMasterUploadResult,
)
from app.services.item_master_matcher import ItemMasterImportService, normalize_item_text, normalize_spec_text


router = APIRouter(prefix="/item-master", tags=["item-master"])
import_service = ItemMasterImportService()


@router.get("", response_model=ItemMasterListResponse)
def list_item_master(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    db: Session = Depends(get_db),
) -> ItemMasterListResponse:
    return _list_items(page, page_size, search, category, active, db)


@router.get("/items", response_model=ItemMasterListResponse)
def list_item_master_items(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    query: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    db: Session = Depends(get_db),
) -> ItemMasterListResponse:
    return _list_items(page, page_size, query, category, active, db)


def _list_items(
    page: int,
    page_size: int,
    search: str | None,
    category: str | None,
    active: bool | None,
    db: Session,
) -> ItemMasterListResponse:
    query = select(ItemMaster).options(selectinload(ItemMaster.alias_records))
    count_query = select(func.count()).select_from(ItemMaster)
    filters = []
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(or_(ItemMaster.internal_item_code.ilike(pattern), ItemMaster.item_name.ilike(pattern), ItemMaster.spec.ilike(pattern)))
    if category:
        filters.append(ItemMaster.category == category)
    if active is not None:
        filters.append(ItemMaster.active.is_(active))
    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)
    total = int(db.scalar(count_query) or 0)
    items = db.scalars(query.order_by(ItemMaster.internal_item_code.asc()).offset((page - 1) * page_size).limit(page_size)).all()
    return ItemMasterListResponse(items=[ItemMasterRead.model_validate(item) for item in items], total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=ItemMasterStats)
def item_master_stats(db: Session = Depends(get_db)) -> ItemMasterStats:
    total = int(db.scalar(select(func.count()).select_from(ItemMaster)) or 0)
    active = int(db.scalar(select(func.count()).select_from(ItemMaster).where(ItemMaster.active.is_(True))) or 0)
    alias_count = int(db.scalar(select(func.count()).select_from(ItemAlias).where(ItemAlias.active.is_(True))) or 0)
    last_uploaded_at = db.scalar(select(func.max(ItemMaster.last_uploaded_at)))
    last_updated_at = db.scalar(select(func.max(ItemMaster.updated_at)))
    return ItemMasterStats(total_items=total, active_items=active, inactive_items=total - active, alias_count=alias_count, last_uploaded_at=last_uploaded_at, last_updated_at=last_updated_at)


@router.post("/upload", response_model=ItemMasterUploadResult)
async def upload_item_master(
    file: Annotated[UploadFile, File(...)],
    mode: Annotated[str, Query(pattern="^(upsert|replace)$")] = "upsert",
    db: Session = Depends(get_db),
) -> ItemMasterUploadResult:
    content = await file.read()
    result = import_service.upload(db, file.filename or "item_master.csv", content, mode=mode)
    return ItemMasterUploadResult(**result)


@router.post("/items", response_model=ItemMasterRead, status_code=201)
def create_item_master_item(payload: ItemMasterCreate, db: Session = Depends(get_db)) -> ItemMasterRead:
    code = payload.internal_item_code.strip()
    if db.scalar(select(ItemMaster).where(ItemMaster.internal_item_code == code)):
        raise HTTPException(status_code=409, detail="이미 존재하는 내부 품목코드입니다")
    item = ItemMaster(
        internal_item_code=code,
        item_name=payload.item_name.strip(),
        normalized_item_name=normalize_item_text(payload.item_name),
        spec=(payload.spec or "").strip() or None,
        normalized_spec=normalize_spec_text(payload.spec),
        unit=(payload.unit or "").strip() or None,
        category=(payload.category or "").strip() or None,
        standard_price=payload.standard_price,
        active=payload.active,
        aliases=[alias.strip() for alias in payload.aliases if alias.strip()],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return ItemMasterRead.model_validate(item)


@router.get("/items/{item_id}", response_model=ItemMasterRead)
def get_item_master_item(item_id: UUID, db: Session = Depends(get_db)) -> ItemMasterRead:
    item = db.scalar(select(ItemMaster).options(selectinload(ItemMaster.alias_records)).where(ItemMaster.id == item_id))
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    return ItemMasterRead.model_validate(item)


@router.patch("/items/{item_id}", response_model=ItemMasterRead)
def update_item_master_item(item_id: UUID, payload: ItemMasterUpdate, db: Session = Depends(get_db)) -> ItemMasterRead:
    item = db.scalar(select(ItemMaster).options(selectinload(ItemMaster.alias_records)).where(ItemMaster.id == item_id))
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    updates = payload.model_dump(exclude_unset=True)
    if "item_name" in updates and updates["item_name"] is not None:
        item.item_name = updates["item_name"].strip()
        item.normalized_item_name = normalize_item_text(item.item_name)
    if "spec" in updates:
        item.spec = (updates["spec"] or "").strip() or None
        item.normalized_spec = normalize_spec_text(item.spec)
    for field in ["unit", "category"]:
        if field in updates:
            setattr(item, field, (updates[field] or "").strip() or None)
    for field in ["standard_price", "active", "aliases"]:
        if field in updates:
            value = updates[field]
            if field == "aliases" and value is not None:
                value = [alias.strip() for alias in value if alias.strip()]
            setattr(item, field, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    return ItemMasterRead.model_validate(item)


@router.delete("/items/{item_id}")
def deactivate_item_master_item(item_id: UUID, db: Session = Depends(get_db)) -> Response:
    item = db.get(ItemMaster, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    item.active = False
    db.add(item)
    db.commit()
    return Response(status_code=204)


@router.post("/items/{item_id}/aliases", response_model=ItemAliasRead, status_code=201)
def create_item_alias(item_id: UUID, payload: ItemAliasCreate, db: Session = Depends(get_db)) -> ItemAliasRead:
    item = db.get(ItemMaster, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    alias = ItemAlias(
        item_master_id=item.id,
        alias_name=payload.alias_name.strip(),
        normalized_alias_name=normalize_item_text(payload.alias_name),
        alias_spec=(payload.alias_spec or "").strip() or None,
        vendor_name=(payload.vendor_name or "").strip() or None,
        customer_name=(payload.customer_name or "").strip() or None,
        source=payload.source,
        confidence=payload.confidence,
        memo=payload.memo,
        active=payload.active,
    )
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return ItemAliasRead.model_validate(alias)


@router.patch("/aliases/{alias_id}", response_model=ItemAliasRead)
def update_item_alias(alias_id: UUID, payload: ItemAliasUpdate, db: Session = Depends(get_db)) -> ItemAliasRead:
    alias = db.get(ItemAlias, alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail="별칭을 찾을 수 없습니다")
    updates = payload.model_dump(exclude_unset=True)
    if "alias_name" in updates and updates["alias_name"] is not None:
        alias.alias_name = updates["alias_name"].strip()
        alias.normalized_alias_name = normalize_item_text(alias.alias_name)
    for field in ["alias_spec", "vendor_name", "customer_name", "source", "confidence", "memo", "active"]:
        if field in updates:
            value = updates[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(alias, field, value)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return ItemAliasRead.model_validate(alias)


@router.delete("/aliases/{alias_id}")
def deactivate_item_alias(alias_id: UUID, db: Session = Depends(get_db)) -> Response:
    alias = db.get(ItemAlias, alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail="별칭을 찾을 수 없습니다")
    alias.active = False
    db.add(alias)
    db.commit()
    return Response(status_code=204)


@router.delete("")
def clear_item_master(db: Session = Depends(get_db)) -> dict[str, int]:
    alias_count = int(db.scalar(select(func.count()).select_from(ItemAlias)) or 0)
    item_count = int(db.scalar(select(func.count()).select_from(ItemMaster)) or 0)
    db.execute(delete(ItemAlias))
    db.execute(delete(ItemMaster))
    db.commit()
    return {"deleted_items": item_count, "deleted_aliases": alias_count}
