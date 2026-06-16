import re
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import String, and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models.document import CategoryFolder, Document, DocumentType, ProcessingStatus
from app.schemas.document import (
    ActivitySummary,
    BulkDocumentRequest,
    CategoryFolderCreate,
    DocumentCalendarItem,
    DocumentListResponse,
    DocumentNotification,
    DocumentRead,
    DocumentStats,
    DocumentUpdate,
    FolderSummary,
    ReviewApprovalRequest,
    ReviewIssueUpdate,
    ReviewReopenRequest,
)
from app.services.export import document_to_json, documents_to_csv, documents_to_excel, tax_invoice_to_draft_xml
from app.services.category_taxonomy import category_path_for, clean_tags_for_context, display_label, normalize_category_value
from app.services.persistence_safety import sanitize_for_postgres
from app.services.queue_service import get_document_queue
from app.services.storage import get_storage_service
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService
from app.services.document_processor import DocumentProcessor
from app.services.review_workflow import approve_document, reopen_document, review_metadata, update_issue_status

router = APIRouter(prefix="/documents", tags=["documents"])


def _to_read(document: Document) -> DocumentRead:
    storage = get_storage_service()
    return DocumentRead.model_validate(
        {**document.__dict__, "file_url": storage.public_url(document.stored_file_path)}
    )


def _search_filter(search: str):
    terms = [term for term in search.strip().split() if term]
    if not terms:
        return None
    searchable_fields = [
        Document.title,
        Document.summary,
        Document.workflow_summary,
        Document.merchant_name,
        Document.vendor_name,
        Document.customer_name,
        Document.document_number,
        func.cast(Document.line_items, String),
        Document.raw_text,
        Document.original_filename,
        Document.category,
    ]
    per_term = []
    for term in terms:
        needle = f"%{term}%"
        per_term.append(or_(*(func.coalesce(field, "").ilike(needle) for field in searchable_fields)))
    return and_(*per_term)


def _process_document_in_background(document_id: UUID) -> None:
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if not document:
            return
        DocumentProcessor().process(db, document)


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: Annotated[UploadFile, File(...)],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> DocumentRead:
    storage = get_storage_service()
    try:
        stored_path = storage.save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document = Document(
        original_filename=_safe_original_filename(file.filename),
        stored_file_path=str(stored_path),
        mime_type=_safe_mime_type(file.content_type),
        processing_status=ProcessingStatus.uploaded,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    document = get_document_queue().enqueue(db, document, process_inline=False)
    background_tasks.add_task(_process_document_in_background, document.id)
    return _to_read(document)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    db: Session = Depends(get_db),
    search: str | None = Query(default=None, max_length=200),
    document_type: DocumentType | None = None,
    category: str | None = Query(default=None, max_length=120),
    source_file_type: str | None = Query(default=None, max_length=40, pattern=r"^[A-Za-z0-9._-]+$"),
    processing_status: ProcessingStatus | None = None,
    is_favorite: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    sort_by: str = Query(default="updated_at", pattern="^(created_at|updated_at|extracted_date|extracted_amount|title)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> DocumentListResponse:
    filters = []
    if search:
        search_filter = _search_filter(search)
        if search_filter is not None:
            filters.append(search_filter)
    if document_type:
        filters.append(Document.document_type == document_type)
    if category:
        normalized_category = normalize_category_value(category)
        if normalized_category:
            filters.append(Document.category == normalized_category)
    if source_file_type:
        filters.append(Document.source_file_type == source_file_type)
    if processing_status:
        filters.append(Document.processing_status == processing_status)
    if is_favorite is not None:
        filters.append(Document.is_favorite == is_favorite)
    if date_from:
        filters.append(Document.extracted_date >= date_from)
    if date_to:
        filters.append(Document.extracted_date <= date_to)
    if amount_min is not None:
        filters.append(Document.extracted_amount >= amount_min)
    if amount_max is not None:
        filters.append(Document.extracted_amount <= amount_max)

    where_clause = and_(*filters) if filters else None
    count_stmt = select(func.count()).select_from(Document)
    stmt = select(Document)
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
        stmt = stmt.where(where_clause)

    sort_column = getattr(Document, sort_by)
    stmt = stmt.order_by(asc(sort_column) if order == "asc" else desc(sort_column))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    documents = list(db.scalars(stmt).all())
    total = db.scalar(count_stmt) or 0
    items = [_to_read(document) for document in documents]
    return DocumentListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=DocumentStats)
def get_stats(db: Session = Depends(get_db)) -> DocumentStats:
    recent_uploads = db.scalars(select(Document).order_by(desc(Document.created_at)).limit(5)).all()
    recent_updated = db.scalars(select(Document).order_by(desc(Document.updated_at)).limit(5)).all()
    recent_review = db.scalars(
        select(Document).where(Document.processing_status == ProcessingStatus.needs_review).order_by(desc(Document.updated_at)).limit(5)
    ).all()
    pinned = db.scalars(select(Document).where(Document.is_favorite.is_(True)).order_by(desc(Document.updated_at)).limit(6)).all()
    category_overview = _folder_summary_rows(db, by="category")
    file_type_overview = _folder_summary_rows(db, by="source_file_type")
    return DocumentStats(
        total=db.scalar(select(func.count()).select_from(Document)) or 0,
        receipts=db.scalar(select(func.count()).select_from(Document).where(Document.document_type == DocumentType.receipt)) or 0,
        notices=db.scalar(select(func.count()).select_from(Document).where(Document.document_type == DocumentType.notice)) or 0,
        completed=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status.in_([ProcessingStatus.completed, ProcessingStatus.ready]))) or 0,
        confirmed=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status == ProcessingStatus.confirmed)) or 0,
        processing=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status.in_([ProcessingStatus.processing, ProcessingStatus.queued]))) or 0,
        failed=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status == ProcessingStatus.failed)) or 0,
        needs_review=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status == ProcessingStatus.needs_review)) or 0,
        queued=db.scalar(select(func.count()).select_from(Document).where(Document.processing_status == ProcessingStatus.queued)) or 0,
        recent=[_to_read(document) for document in recent_uploads],
        recent_updated=[_to_read(document) for document in recent_updated],
        recent_review=[_to_read(document) for document in recent_review],
        pinned=[_to_read(document) for document in pinned],
        category_overview=[row.model_dump() for row in category_overview],
        file_type_overview=[row.model_dump() for row in file_type_overview],
        ocr_metrics=_ocr_metrics(db),
    )


@router.get("/activity", response_model=ActivitySummary)
def get_activity(db: Session = Depends(get_db)) -> ActivitySummary:
    return ActivitySummary(
        recent_uploads=[_to_read(document) for document in db.scalars(select(Document).order_by(desc(Document.created_at)).limit(8)).all()],
        recent_edits=[_to_read(document) for document in db.scalars(select(Document).order_by(desc(Document.updated_at)).limit(8)).all()],
        recent_needs_review=[_to_read(document) for document in db.scalars(
            select(Document).where(Document.processing_status == ProcessingStatus.needs_review).order_by(desc(Document.updated_at)).limit(8)
        ).all()],
        favorites=[_to_read(document) for document in db.scalars(
            select(Document).where(Document.is_favorite.is_(True)).order_by(desc(Document.updated_at)).limit(8)
        ).all()],
    )


@router.get("/notifications", response_model=list[DocumentNotification])
def list_notifications(db: Session = Depends(get_db)) -> list[DocumentNotification]:
    documents = db.scalars(select(Document).order_by(desc(Document.updated_at)).limit(40)).all()
    notifications: list[DocumentNotification] = []
    for document in documents:
        if document.processing_status in {ProcessingStatus.processing, ProcessingStatus.queued}:
            notifications.append(_notification(document, "processing", "문서 처리 중", "문서 유형 분류와 업무 데이터 추출이 진행 중입니다."))
        elif document.processing_status == ProcessingStatus.failed:
            notifications.append(_notification(document, "failed", "처리 실패", document.processing_error or "문서를 확인한 뒤 다시 처리하세요."))
        elif document.processing_status == ProcessingStatus.needs_review:
            notifications.append(_notification(document, "review", "검토 필요", "확정 처리 전에 사람이 확인해야 하는 항목이 있습니다."))
        else:
            notifications.append(_notification(document, "processed", "자동 추출 완료", "문서 처리가 완료되어 업무데이터/엑셀 입력용 데이터로 검토할 수 있습니다."))
    return notifications[:30]


@router.get("/calendar", response_model=list[DocumentCalendarItem])
def list_calendar_items(
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=120, ge=1, le=500),
) -> list[DocumentCalendarItem]:
    documents = db.scalars(select(Document).order_by(asc(Document.due_date), desc(Document.updated_at)).limit(1000)).all()
    today = date.today()
    items: list[DocumentCalendarItem] = []
    for document in documents:
        for role, label, value in _document_calendar_dates(document):
            if date_from and value < date_from:
                continue
            if date_to and value > date_to:
                continue
            days = (value - today).days
            status_label = "오늘" if days == 0 else "지난 일정" if days < 0 else "임박" if days <= 7 else "예정"
            items.append(DocumentCalendarItem(
                id=f"{document.id}:{role}:{value.isoformat()}",
                document_id=document.id,
                document_title=_display_title(document),
                document_number=document.document_number,
                original_filename=document.original_filename,
                document_type=document.document_type,
                vendor_name=document.vendor_name or document.merchant_name,
                customer_name=document.customer_name,
                date=value,
                date_role=role,
                date_label=label,
                status=status_label,
                days_from_today=days,
                processing_status=document.processing_status,
                review_required=document.review_required,
                action_url=f"/documents/{document.id}",
            ))
    items.sort(key=lambda item: (abs(item.days_from_today) if item.days_from_today < 0 else item.days_from_today, item.date))
    return items[:limit]


@router.get("/categories", response_model=list[FolderSummary])
def list_categories(db: Session = Depends(get_db)) -> list[FolderSummary]:
    return _folder_summary_rows(db, by="category")


@router.post("/categories", response_model=FolderSummary, status_code=status.HTTP_201_CREATED)
def create_category_folder(payload: CategoryFolderCreate, db: Session = Depends(get_db)) -> FolderSummary:
    category = normalize_category_value(payload.category or payload.label)
    if not category:
        raise HTTPException(status_code=400, detail="Category folder name is required.")
    parent = normalize_category_value(payload.parent)
    if parent == category:
        raise HTTPException(status_code=400, detail="Category folder cannot be nested under itself.")
    value = f"{parent}>{category}" if parent else category
    existing = db.scalar(select(CategoryFolder).where(CategoryFolder.value == value))
    if existing:
        return FolderSummary(
            label=existing.label,
            value=existing.value,
            count=0,
            parent=existing.parent,
            depth=1 if existing.parent else 0,
            category=existing.category,
            custom=True,
        )
    folder = CategoryFolder(
        value=value,
        label=f"{display_label(parent)} > {display_label(category)}" if parent else display_label(category),
        parent=parent,
        category=category,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return FolderSummary(label=folder.label, value=folder.value, count=0, parent=folder.parent, depth=1 if folder.parent else 0, category=folder.category, custom=True)


@router.delete("/categories/{folder_value:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category_folder(folder_value: str, db: Session = Depends(get_db)) -> Response:
    normalized_value = _normalize_folder_value(folder_value)
    folder = db.scalar(select(CategoryFolder).where(CategoryFolder.value == normalized_value))
    if not folder:
        raise HTTPException(status_code=404, detail="Category folder not found.")
    in_use = _category_document_count(db, normalized_value)
    if in_use:
        raise HTTPException(status_code=409, detail="Category folder is still in use and cannot be deleted.")
    db.delete(folder)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/file-types", response_model=list[FolderSummary])
def list_file_types(db: Session = Depends(get_db)) -> list[FolderSummary]:
    return _folder_summary_rows(db, by="source_file_type")


@router.get("/review", response_model=DocumentListResponse)
def list_needs_review(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> DocumentListResponse:
    stmt = (
        select(Document)
        .where(Document.processing_status == ProcessingStatus.needs_review)
        .order_by(desc(Document.updated_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = db.scalar(select(func.count()).select_from(Document).where(Document.processing_status == ProcessingStatus.needs_review)) or 0
    return DocumentListResponse(items=[_to_read(document) for document in db.scalars(stmt).all()], total=total, page=page, page_size=page_size)


@router.get("/favorites", response_model=DocumentListResponse)
def list_favorites(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> DocumentListResponse:
    stmt = (
        select(Document)
        .where(Document.is_favorite.is_(True))
        .order_by(desc(Document.updated_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = db.scalar(select(func.count()).select_from(Document).where(Document.is_favorite.is_(True))) or 0
    return DocumentListResponse(items=[_to_read(document) for document in db.scalars(stmt).all()], total=total, page=page, page_size=page_size)


@router.get("/export/csv")
def export_csv(
    db: Session = Depends(get_db),
    document_ids: list[UUID] | None = Query(default=None),
    document_type: DocumentType | None = None,
    category: str | None = Query(default=None, max_length=120),
) -> Response:
    documents = _export_documents(db, document_ids=document_ids, document_type=document_type, category=category)
    return Response(
        documents_to_csv(documents),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=docuparse-documents.csv"},
    )


@router.get("/export/xlsx")
def export_excel(
    db: Session = Depends(get_db),
    document_ids: list[UUID] | None = Query(default=None),
    document_type: DocumentType | None = None,
    category: str | None = Query(default=None, max_length=120),
    sheet_mode: str = Query(default="combined", pattern="^(combined|party_tabs)$"),
) -> Response:
    documents = _export_documents(db, document_ids=document_ids, document_type=document_type, category=category)
    return Response(
        documents_to_excel(documents, sheet_mode=sheet_mode),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=docuparse-manufacturing-documents.xlsx"},
    )


@router.post("/bulk/download")
def bulk_download_originals(payload: BulkDocumentRequest, db: Session = Depends(get_db)) -> Response:
    documents = db.scalars(select(Document).where(Document.id.in_(payload.ids)).order_by(Document.created_at)).all()
    if not documents:
        raise HTTPException(status_code=404, detail="No matching documents found.")
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        used: set[str] = set()
        for document in documents:
            path = Path(document.stored_file_path)
            if not path.is_file():
                continue
            name = _zip_member_name(document.original_filename or path.name)
            if name in used:
                stem = Path(name).stem
                suffix = Path(name).suffix
                name = f"{stem}-{document.id}{suffix}"
            used.add(name)
            archive.write(path, arcname=name)
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=docuparse-originals.zip"},
    )


@router.post("/bulk/delete")
def bulk_delete_documents(payload: BulkDocumentRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    documents = db.scalars(select(Document).where(Document.id.in_(payload.ids))).all()
    storage = get_storage_service()
    deleted = 0
    for document in documents:
        storage.delete(document.stored_file_path)
        db.delete(document)
        deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: UUID, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return _to_read(document)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(document_id: UUID, payload: DocumentUpdate, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    previous_review = (document.workflow_metadata or {}).get("review") if isinstance(document.workflow_metadata, dict) else None
    values = sanitize_for_postgres(payload.model_dump(exclude_unset=True))
    if "category" in values:
        values["category"] = normalize_category_value(values.get("category"))
    if "tags" in values:
        values["tags"] = clean_tags_for_context(
            values.get("tags") or [],
            category=values.get("category") or document.category,
            document_type=getattr(document.document_type, "value", str(document.document_type)),
            key_dates=document.key_dates,
            follow_up_required=document.follow_up_required,
            urgency_level=document.urgency_level,
        )
    for key, value in values.items():
        setattr(document, key, value)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, document.raw_text)
    document.workflow_summary = workflow.workflow_summary
    document.action_items = workflow.action_items
    document.warnings = workflow.warnings
    document.key_dates = workflow.key_dates
    document.urgency_level = workflow.urgency_level
    document.follow_up_required = workflow.follow_up_required
    workflow_metadata = workflow.workflow_metadata or {}
    if isinstance(previous_review, dict):
        workflow_metadata["review"] = previous_review
    document.workflow_metadata = sanitize_for_postgres(workflow_metadata or None)
    document.tags = clean_tags_for_context(
        document.tags,
        category=document.category,
        profile=(workflow.workflow_metadata or {}).get("content_profile") if workflow.workflow_metadata else None,
        document_type=getattr(document.document_type, "value", str(document.document_type)),
        key_dates=workflow.key_dates,
        follow_up_required=workflow.follow_up_required,
        urgency_level=workflow.urgency_level,
    )
    document.review_required = bool(workflow_metadata.get("review_required")) if "review_required" in workflow_metadata else bool(workflow.warnings)
    if document.processing_status not in {ProcessingStatus.processing, ProcessingStatus.queued, ProcessingStatus.failed, ProcessingStatus.confirmed}:
        document.processing_status = ProcessingStatus.needs_review if document.review_required else ProcessingStatus.ready
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.post("/{document_id}/confirm", response_model=DocumentRead)
def confirm_document(document_id: UUID, payload: ReviewApprovalRequest | None = None, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    validation = approve_document(document, approval_note=payload.approval_note if payload else None)
    if not validation.ok:
        db.add(document)
        db.commit()
        raise HTTPException(status_code=409, detail={"message": "Approval blocked by unresolved review issues.", **validation.to_dict()})
    document.review_required = False
    document.processing_status = ProcessingStatus.confirmed
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.post("/{document_id}/needs-review", response_model=DocumentRead)
def mark_document_needs_review(document_id: UUID, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    document.review_required = True
    document.processing_status = ProcessingStatus.needs_review
    reopen_document(document)
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.get("/{document_id}/review", response_model=dict)
def get_document_review(document_id: UUID, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return review_metadata(document)


@router.post("/{document_id}/review/issues", response_model=DocumentRead)
def update_document_review_issue(document_id: UUID, payload: ReviewIssueUpdate, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        update_issue_status(document, payload.key, payload.status, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.post("/{document_id}/review/reopen", response_model=DocumentRead)
def reopen_document_review(document_id: UUID, payload: ReviewReopenRequest | None = None, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    reopen_document(document, note=payload.note if payload else None)
    document.review_required = True
    document.processing_status = ProcessingStatus.needs_review
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.post("/{document_id}/favorite", response_model=DocumentRead)
def toggle_favorite(document_id: UUID, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    document.is_favorite = not document.is_favorite
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, db: Session = Depends(get_db)) -> Response:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    get_storage_service().delete(document.stored_file_path)
    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/reprocess", response_model=DocumentRead)
def reprocess_document(document_id: UUID, db: Session = Depends(get_db)) -> DocumentRead:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    document = get_document_queue().enqueue(db, document)
    return _to_read(document)


@router.get("/{document_id}/export/json")
def export_document_json(document_id: UUID, db: Session = Depends(get_db)) -> Response:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(
        document_to_json(document),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=document-{document.id}.json"},
    )


@router.get("/{document_id}/export/tax-invoice-xml")
def export_tax_invoice_xml(document_id: UUID, db: Session = Depends(get_db)) -> Response:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        payload = tax_invoice_to_draft_xml(document)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"tax-invoice-draft-{document.document_number or document.id}.xml"
    return Response(
        payload,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _notification(document: Document, kind: str, title: str, message: str) -> DocumentNotification:
    category = normalize_category_value(document.category)
    return DocumentNotification(
        id=f"{kind}:{document.id}:{document.updated_at.isoformat() if document.updated_at else ''}",
        document_id=document.id,
        kind=kind,
        title=title,
        message=message,
        document_title=_display_title(document),
        category=category,
        category_label=display_label(category),
        processing_status=document.processing_status,
        created_at=document.updated_at or document.created_at,
        action_url=f"/documents/{document.id}",
        action_required=kind in {"review", "failed"},
    )


def _display_title(document: Document) -> str:
    return document.document_number or document.title or document.original_filename


def _export_documents(
    db: Session,
    document_ids: list[UUID] | None = None,
    document_type: DocumentType | None = None,
    category: str | None = None,
) -> list[Document]:
    filters = []
    if document_ids:
        filters.append(Document.id.in_(document_ids))
    if document_type:
        filters.append(Document.document_type == document_type)
    if category:
        normalized_category = normalize_category_value(category)
        if normalized_category:
            filters.append(Document.category == normalized_category)
    stmt = select(Document).order_by(desc(Document.created_at))
    if filters:
        stmt = stmt.where(and_(*filters))
    documents = list(db.scalars(stmt).all())
    if document_ids and not documents:
        raise HTTPException(status_code=404, detail="No matching documents found.")
    return documents


def _document_calendar_dates(document: Document) -> list[tuple[str, str, date]]:
    dates: list[tuple[str, str, date]] = []
    doc_type = getattr(document.document_type, "value", str(document.document_type))
    if document.issue_date or document.extracted_date:
        dates.append(("issue_date", "발행일", document.issue_date or document.extracted_date))
    if document.due_date:
        label = {
            "purchase_order": "납기일",
            "delivery_note": "납품일",
            "invoice": "지급기한",
            "quotation": "유효기간",
            "transaction_statement": "거래일자",
        }.get(doc_type, "기한")
        dates.append(("due_date", label, document.due_date))
    for raw in document.key_dates or []:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(raw))
        if not match:
            continue
        parsed = date.fromisoformat(match.group(1))
        if any(existing[2] == parsed for existing in dates):
            continue
        dates.append(("key_date", "문서 일정", parsed))
    return dates


def _ocr_metrics(db: Session) -> dict[str, int | float]:
    documents = list(db.scalars(select(Document)).all())
    metrics: dict[str, int | float] = {
        "total_documents": len(documents),
        "paddleocr_success": 0,
        "paddleocr_retry": 0,
        "provider_reset": 0,
        "tesseract_fallback": 0,
        "failed": 0,
        "ready": 0,
        "needs_review": 0,
        "average_processing_ms": 0,
    }
    elapsed_values: list[float] = []
    for document in documents:
        metadata = document.ingestion_metadata or {}
        file_metadata = metadata.get("file_metadata") if isinstance(metadata.get("file_metadata"), dict) else metadata
        provider = str(file_metadata.get("ocr_provider_succeeded") or file_metadata.get("ocr_engine") or "")
        if "paddleocr" in provider:
            metrics["paddleocr_success"] += 1
        if file_metadata.get("retry_used") or file_metadata.get("ocr_worker_retry_used"):
            metrics["paddleocr_retry"] += 1
        if file_metadata.get("provider_reset_used") or file_metadata.get("ocr_worker_provider_reset_used"):
            metrics["provider_reset"] += 1
        if file_metadata.get("ocr_fallback_used") or provider == "tesseract":
            metrics["tesseract_fallback"] += 1
        if document.processing_status == ProcessingStatus.failed:
            metrics["failed"] += 1
        if document.processing_status in {ProcessingStatus.ready, ProcessingStatus.confirmed, ProcessingStatus.completed}:
            metrics["ready"] += 1
        if document.processing_status == ProcessingStatus.needs_review:
            metrics["needs_review"] += 1
        elapsed = file_metadata.get("processing_time_ms") or file_metadata.get("ocr_worker_elapsed_ms") or file_metadata.get("elapsed_ms")
        try:
            if elapsed is not None:
                elapsed_values.append(float(elapsed))
        except (TypeError, ValueError):
            pass
    if elapsed_values:
        metrics["average_processing_ms"] = round(sum(elapsed_values) / len(elapsed_values), 1)
    return metrics


def _safe_original_filename(filename: str | None) -> str:
    storage = get_storage_service()
    sanitizer = getattr(storage, "safe_original_filename", None)
    if callable(sanitizer):
        return sanitizer(filename)
    return Path(filename or "upload").name[:180] or "upload"


def _safe_mime_type(value: str | None) -> str:
    if not value:
        return "application/octet-stream"
    cleaned = value.split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", cleaned):
        return "application/octet-stream"
    return cleaned[:100]


def _zip_member_name(filename: str) -> str:
    cleaned = _safe_original_filename(filename)
    if cleaned in {"", ".", ".."}:
        return "document"
    return cleaned


def _normalize_folder_value(value: str) -> str:
    parts = [normalize_category_value(part) for part in value.split(">")]
    cleaned = [part for part in parts if part]
    if not cleaned:
        raise HTTPException(status_code=400, detail="Category folder value is required.")
    return ">".join(cleaned)


def _category_document_count(db: Session, category_or_path: str) -> int:
    leaf = normalize_category_value(category_or_path)
    if not leaf:
        return 0
    return db.scalar(select(func.count()).select_from(Document).where(Document.category == leaf)) or 0


def _folder_summary_rows(db: Session, by: str) -> list[FolderSummary]:
    if by != "category":
        field = getattr(Document, by)
        rows = db.execute(
            select(
                field,
                func.count(Document.id),
                func.count().filter(Document.processing_status == ProcessingStatus.needs_review),
                func.count().filter(Document.processing_status == ProcessingStatus.confirmed),
                func.count().filter(Document.processing_status.in_([ProcessingStatus.processing, ProcessingStatus.queued])),
            )
            .where(field.is_not(None))
            .group_by(field)
            .order_by(desc(func.count(Document.id)), asc(field))
        ).all()
        result: list[FolderSummary] = []
        for value, count, needs_review, confirmed, processing in rows:
            if not value:
                continue
            result.append(
                FolderSummary(
                    label=display_label(str(value)),
                    value=str(value),
                    count=count or 0,
                    needs_review=needs_review or 0,
                    confirmed=confirmed or 0,
                    processing=processing or 0,
                )
            )
        return result

    documents = db.scalars(select(Document)).all()
    grouped: dict[str, dict] = {}
    for document in documents:
        path = category_path_for(document)
        row = grouped.setdefault(
            path.value,
            {
                "label": path.label,
                "value": path.value,
                "count": 0,
                "needs_review": 0,
                "confirmed": 0,
                "processing": 0,
                "parent": path.parent,
                "depth": path.depth,
                "category": path.category,
                "custom": False,
            },
        )
        row["count"] += 1
        if document.processing_status == ProcessingStatus.needs_review:
            row["needs_review"] += 1
        if document.processing_status == ProcessingStatus.confirmed:
            row["confirmed"] += 1
        if document.processing_status in {ProcessingStatus.processing, ProcessingStatus.queued}:
            row["processing"] += 1

    for folder in db.scalars(select(CategoryFolder).order_by(CategoryFolder.value)).all():
        grouped.setdefault(
            folder.value,
            {
                "label": folder.label,
                "value": folder.value,
                "count": 0,
                "needs_review": 0,
                "confirmed": 0,
                "processing": 0,
                "parent": folder.parent,
                "depth": 1 if folder.parent else 0,
                "category": folder.category,
                "custom": True,
            },
        )

    return [
        FolderSummary(**row)
        for row in sorted(grouped.values(), key=lambda item: (-item["count"], item["label"]))
    ]
