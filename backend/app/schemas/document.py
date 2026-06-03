from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentType, ProcessingStatus


class DocumentBase(BaseModel):
    document_type: DocumentType = DocumentType.general_document
    title: str | None = Field(default=None, max_length=255)
    raw_text: str | None = None
    extracted_date: date | None = None
    extracted_amount: Decimal | None = Field(default=None, ge=0)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    merchant_name: str | None = Field(default=None, max_length=255)
    vendor_name: str | None = Field(default=None, max_length=255)
    customer_name: str | None = Field(default=None, max_length=255)
    document_number: str | None = Field(default=None, max_length=120)
    issue_date: date | None = None
    due_date: date | None = None
    line_items: list[dict] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None


class DocumentUpdate(DocumentBase):
    confidence_score: Decimal | None = Field(default=None, ge=0, le=1)
    processing_status: ProcessingStatus | None = None
    is_favorite: bool | None = None


class DocumentRead(DocumentBase):
    id: UUID
    original_filename: str
    stored_file_path: str
    mime_type: str
    source_file_type: str | None = None
    extraction_method: str | None = None
    ingestion_metadata: dict | None = None
    confidence_score: Decimal | None = None
    ai_document_type: DocumentType | None = None
    ai_confidence_score: Decimal | None = None
    ai_extraction_notes: str | None = None
    review_required: bool = False
    extraction_provider: str | None = None
    refinement_provider: str | None = None
    provider_chain: str | None = None
    merge_strategy: str | None = None
    field_sources: dict[str, str] | None = None
    workflow_summary: str | None = None
    action_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    key_dates: list[str] = Field(default_factory=list)
    urgency_level: str | None = None
    follow_up_required: bool = False
    workflow_metadata: dict | None = None
    is_favorite: bool = False
    processing_status: ProcessingStatus
    preview_image_path: str | None = None
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime
    file_url: str

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    page_size: int


class DocumentStats(BaseModel):
    total: int
    receipts: int
    notices: int
    completed: int
    confirmed: int = 0
    processing: int
    failed: int
    needs_review: int = 0
    queued: int = 0
    recent: list[DocumentRead]
    recent_updated: list[DocumentRead] = Field(default_factory=list)
    recent_review: list[DocumentRead] = Field(default_factory=list)
    pinned: list[DocumentRead] = Field(default_factory=list)
    category_overview: list[dict] = Field(default_factory=list)
    file_type_overview: list[dict] = Field(default_factory=list)


class FolderSummary(BaseModel):
    label: str
    value: str
    count: int
    needs_review: int = 0
    confirmed: int = 0
    processing: int = 0
    parent: str | None = None
    depth: int = 0
    category: str | None = None
    custom: bool = False


class CategoryFolderCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    parent: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)


class BulkDocumentRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=100)


class ActivitySummary(BaseModel):
    recent_uploads: list[DocumentRead] = Field(default_factory=list)
    recent_edits: list[DocumentRead] = Field(default_factory=list)
    recent_needs_review: list[DocumentRead] = Field(default_factory=list)
    favorites: list[DocumentRead] = Field(default_factory=list)


class DocumentNotification(BaseModel):
    id: str
    document_id: UUID
    kind: str
    title: str
    message: str
    document_title: str | None = None
    category: str | None = None
    category_label: str | None = None
    processing_status: ProcessingStatus
    created_at: datetime
    action_url: str
    action_required: bool = False
