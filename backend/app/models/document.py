import enum
import uuid
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class DocumentType(str, enum.Enum):
    purchase_order = "purchase_order"
    quotation = "quotation"
    transaction_statement = "transaction_statement"
    delivery_note = "delivery_note"
    invoice = "invoice"
    packing_list = "packing_list"
    inspection_report = "inspection_report"
    contract = "contract"
    general_document = "general_document"
    receipt = "receipt"
    notice = "notice"
    document = "document"
    memo = "memo"
    presentation = "presentation"
    other = "other"


class ProcessingStatus(str, enum.Enum):
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    ready = "ready"
    needs_review = "needs_review"
    confirmed = "confirmed"
    completed = "completed"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_file_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ingestion_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType, name="document_type"), default=DocumentType.general_document)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    extracted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    line_items: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    low_confidence_fields: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    ai_document_type: Mapped[DocumentType | None] = mapped_column(Enum(DocumentType, name="document_type"), nullable=True)
    ai_confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    ai_extraction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    refinement_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_chain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merge_strategy: Mapped[str | None] = mapped_column(String(120), nullable=True)
    field_sources: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    workflow_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    key_dates: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    urgency_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    follow_up_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    workflow_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"),
        default=ProcessingStatus.uploaded,
        index=True,
    )
    preview_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CategoryFolder(Base):
    __tablename__ = "category_folders"
    __table_args__ = (UniqueConstraint("value", name="uq_category_folders_value"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    value: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    parent: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportTemplate(Base):
    __tablename__ = "export_templates"
    __table_args__ = (UniqueConstraint("name", "scope", name="uq_export_templates_name_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(40), default="global", nullable=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    template_columns: Mapped[list[dict]] = mapped_column("columns", JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ItemMaster(Base):
    __tablename__ = "item_masters"
    __table_args__ = (UniqueConstraint("internal_item_code", name="uq_item_masters_internal_item_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    internal_item_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_item_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    standard_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    last_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    alias_records: Mapped[list["ItemAlias"]] = relationship("ItemAlias", back_populates="item_master", cascade="all, delete-orphan")


class ItemAlias(Base):
    __tablename__ = "item_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_master_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("item_masters.id", ondelete="CASCADE"), nullable=False, index=True)
    alias_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_alias_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    alias_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    item_master: Mapped[ItemMaster] = relationship("ItemMaster", back_populates="alias_records")
