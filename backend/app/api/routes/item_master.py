from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import ItemAlias, ItemMaster
from app.schemas.item_master import ItemMasterListResponse, ItemMasterRead, ItemMasterStats, ItemMasterUploadResult
from app.services.item_master_matcher import ItemMasterImportService


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
    query = select(ItemMaster)
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
    last_uploaded_at = db.scalar(select(func.max(ItemMaster.last_uploaded_at)))
    return ItemMasterStats(total_items=total, active_items=active, inactive_items=total - active, last_uploaded_at=last_uploaded_at)


@router.post("/upload", response_model=ItemMasterUploadResult)
async def upload_item_master(
    file: Annotated[UploadFile, File(...)],
    mode: Annotated[str, Query(pattern="^(upsert|replace)$")] = "upsert",
    db: Session = Depends(get_db),
) -> ItemMasterUploadResult:
    content = await file.read()
    result = import_service.upload(db, file.filename or "item_master.csv", content, mode=mode)
    return ItemMasterUploadResult(**result)


@router.delete("/{item_id}")
def deactivate_item_master(item_id: UUID, db: Session = Depends(get_db)) -> Response:
    item = db.get(ItemMaster, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    item.active = False
    db.add(item)
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
