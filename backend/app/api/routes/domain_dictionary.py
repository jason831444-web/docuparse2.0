from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.document import DomainDictionaryAlias, DomainDictionaryEntry, DomainDictionarySuggestionFeedback
from app.schemas.domain_dictionary import (
    DomainDictionaryAliasCreate,
    DomainDictionaryAliasRead,
    DomainDictionaryAliasUpdate,
    DomainDictionaryEntryCreate,
    DomainDictionaryEntryRead,
    DomainDictionaryEntryUpdate,
    DomainDictionaryFeedbackCreate,
    DomainDictionaryListResponse,
    DomainDictionaryStats,
)
from app.services.domain_dictionary import DomainDictionarySuggestionService


router = APIRouter(prefix="/domain-dictionary", tags=["domain-dictionary"])
service = DomainDictionarySuggestionService()


@router.get("", response_model=DomainDictionaryListResponse)
def list_domain_dictionary(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
    query: str | None = None,
    dictionary_type: str | None = None,
    active: bool | None = None,
    db: Session = Depends(get_db),
) -> DomainDictionaryListResponse:
    stmt = select(DomainDictionaryEntry).options(selectinload(DomainDictionaryEntry.aliases))
    count_stmt = select(func.count()).select_from(DomainDictionaryEntry)
    filters = []
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(or_(DomainDictionaryEntry.canonical_value.ilike(pattern), DomainDictionaryEntry.field.ilike(pattern), DomainDictionaryEntry.memo.ilike(pattern)))
    if dictionary_type:
        filters.append(DomainDictionaryEntry.dictionary_type == dictionary_type)
    if active is not None:
        filters.append(DomainDictionaryEntry.active.is_(active))
    for condition in filters:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = int(db.scalar(count_stmt) or 0)
    items = db.scalars(
        stmt.order_by(DomainDictionaryEntry.dictionary_type.asc(), DomainDictionaryEntry.canonical_value.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return DomainDictionaryListResponse(items=[DomainDictionaryEntryRead.model_validate(item) for item in items], total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=DomainDictionaryStats)
def domain_dictionary_stats(db: Session = Depends(get_db)) -> DomainDictionaryStats:
    total = int(db.scalar(select(func.count()).select_from(DomainDictionaryEntry)) or 0)
    active = int(db.scalar(select(func.count()).select_from(DomainDictionaryEntry).where(DomainDictionaryEntry.active.is_(True))) or 0)
    alias_count = int(db.scalar(select(func.count()).select_from(DomainDictionaryAlias).where(DomainDictionaryAlias.active.is_(True))) or 0)
    feedback_count = int(db.scalar(select(func.count()).select_from(DomainDictionarySuggestionFeedback)) or 0)
    rejected_count = int(db.scalar(select(func.count()).select_from(DomainDictionarySuggestionFeedback).where(DomainDictionarySuggestionFeedback.action == "rejected")) or 0)
    by_type_rows = db.execute(
        select(DomainDictionaryEntry.dictionary_type, func.count()).where(DomainDictionaryEntry.active.is_(True)).group_by(DomainDictionaryEntry.dictionary_type)
    ).all()
    return DomainDictionaryStats(
        total_entries=total,
        active_entries=active,
        inactive_entries=total - active,
        alias_count=alias_count,
        by_type={str(row[0]): int(row[1]) for row in by_type_rows},
        feedback_count=feedback_count,
        rejected_count=rejected_count,
    )


@router.post("", response_model=DomainDictionaryEntryRead, status_code=201)
def create_domain_dictionary_entry(payload: DomainDictionaryEntryCreate, db: Session = Depends(get_db)) -> DomainDictionaryEntryRead:
    dictionary_type = payload.dictionary_type.strip()
    canonical = payload.canonical_value.strip()
    if db.scalar(select(DomainDictionaryEntry).where(DomainDictionaryEntry.dictionary_type == dictionary_type, DomainDictionaryEntry.canonical_value == canonical)):
        raise HTTPException(status_code=409, detail="이미 존재하는 사전 항목입니다")
    entry = DomainDictionaryEntry(
        dictionary_type=dictionary_type,
        canonical_value=canonical,
        normalized_value=service.normalize_for_type(dictionary_type, canonical),
        field=(payload.field or "").strip() or None,
        source=(payload.source or "manual").strip(),
        memo=(payload.memo or "").strip() or None,
        active=payload.active,
    )
    db.add(entry)
    db.flush()
    for alias_value in payload.aliases:
        alias_text = alias_value.strip()
        if not alias_text:
            continue
        db.add(
            DomainDictionaryAlias(
                entry_id=entry.id,
                alias_value=alias_text,
                normalized_alias_value=service.normalize_for_type(dictionary_type, alias_text),
                source="manual",
                confidence=1,
                active=True,
            )
        )
    db.commit()
    db.refresh(entry)
    return DomainDictionaryEntryRead.model_validate(db.scalar(select(DomainDictionaryEntry).options(selectinload(DomainDictionaryEntry.aliases)).where(DomainDictionaryEntry.id == entry.id)))


@router.post("/feedback", status_code=201)
def create_domain_dictionary_feedback(payload: DomainDictionaryFeedbackCreate, db: Session = Depends(get_db)) -> dict[str, str]:
    feedback = DomainDictionarySuggestionFeedback(
        document_id=payload.document_id,
        target=payload.target.strip(),
        field=(payload.field or "").strip() or None,
        original_value=payload.original_value.strip(),
        suggested_value=payload.suggested_value.strip(),
        action=payload.action,
        feedback_metadata=payload.metadata,
    )
    db.add(feedback)
    db.commit()
    return {"status": "ok"}


@router.get("/{entry_id}", response_model=DomainDictionaryEntryRead)
def get_domain_dictionary_entry(entry_id: UUID, db: Session = Depends(get_db)) -> DomainDictionaryEntryRead:
    entry = db.scalar(select(DomainDictionaryEntry).options(selectinload(DomainDictionaryEntry.aliases)).where(DomainDictionaryEntry.id == entry_id))
    if not entry:
        raise HTTPException(status_code=404, detail="사전 항목을 찾을 수 없습니다")
    return DomainDictionaryEntryRead.model_validate(entry)


@router.patch("/{entry_id}", response_model=DomainDictionaryEntryRead)
def update_domain_dictionary_entry(entry_id: UUID, payload: DomainDictionaryEntryUpdate, db: Session = Depends(get_db)) -> DomainDictionaryEntryRead:
    entry = db.scalar(select(DomainDictionaryEntry).options(selectinload(DomainDictionaryEntry.aliases)).where(DomainDictionaryEntry.id == entry_id))
    if not entry:
        raise HTTPException(status_code=404, detail="사전 항목을 찾을 수 없습니다")
    updates = payload.model_dump(exclude_unset=True)
    if "canonical_value" in updates and updates["canonical_value"] is not None:
        entry.canonical_value = updates["canonical_value"].strip()
        entry.normalized_value = service.normalize_for_type(entry.dictionary_type, entry.canonical_value)
    for field in ("field", "source", "memo"):
        if field in updates:
            value = updates[field]
            if field == "source":
                entry.source = value.strip() or "manual" if isinstance(value, str) else (value or "manual")
            else:
                setattr(entry, field, value.strip() or None if isinstance(value, str) else value)
    if "active" in updates:
        entry.active = updates["active"]
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return DomainDictionaryEntryRead.model_validate(entry)


@router.delete("/{entry_id}")
def deactivate_domain_dictionary_entry(entry_id: UUID, db: Session = Depends(get_db)) -> Response:
    entry = db.get(DomainDictionaryEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="사전 항목을 찾을 수 없습니다")
    entry.active = False
    db.add(entry)
    db.commit()
    return Response(status_code=204)


@router.post("/{entry_id}/aliases", response_model=DomainDictionaryAliasRead, status_code=201)
def create_domain_dictionary_alias(entry_id: UUID, payload: DomainDictionaryAliasCreate, db: Session = Depends(get_db)) -> DomainDictionaryAliasRead:
    entry = db.get(DomainDictionaryEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="사전 항목을 찾을 수 없습니다")
    alias = DomainDictionaryAlias(
        entry_id=entry.id,
        alias_value=payload.alias_value.strip(),
        normalized_alias_value=service.normalize_for_type(entry.dictionary_type, payload.alias_value),
        source=(payload.source or "manual").strip(),
        confidence=payload.confidence,
        active=payload.active,
    )
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return DomainDictionaryAliasRead.model_validate(alias)


@router.patch("/aliases/{alias_id}", response_model=DomainDictionaryAliasRead)
def update_domain_dictionary_alias(alias_id: UUID, payload: DomainDictionaryAliasUpdate, db: Session = Depends(get_db)) -> DomainDictionaryAliasRead:
    alias = db.scalar(select(DomainDictionaryAlias).options(selectinload(DomainDictionaryAlias.entry)).where(DomainDictionaryAlias.id == alias_id))
    if not alias:
        raise HTTPException(status_code=404, detail="별칭을 찾을 수 없습니다")
    updates = payload.model_dump(exclude_unset=True)
    if "alias_value" in updates and updates["alias_value"] is not None:
        alias.alias_value = updates["alias_value"].strip()
        alias.normalized_alias_value = service.normalize_for_type(alias.entry.dictionary_type, alias.alias_value)
    for field in ("source", "confidence", "active"):
        if field in updates:
            value = updates[field]
            if isinstance(value, str):
                value = value.strip() or "manual"
            setattr(alias, field, value)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return DomainDictionaryAliasRead.model_validate(alias)


@router.delete("/aliases/{alias_id}")
def deactivate_domain_dictionary_alias(alias_id: UUID, db: Session = Depends(get_db)) -> Response:
    alias = db.get(DomainDictionaryAlias, alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail="별칭을 찾을 수 없습니다")
    alias.active = False
    db.add(alias)
    db.commit()
    return Response(status_code=204)
