from typing import Protocol
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document, ProcessingStatus
from app.services.document_processor import DocumentProcessor


def _queue_stage_metadata(document: Document, stage: str) -> dict:
    metadata = dict(document.workflow_metadata or {})
    now = datetime.now(timezone.utc).isoformat()
    metadata["processing_stage"] = {
        "stage": stage,
        "updated_at": now,
    }
    events = list(metadata.get("processing_stage_events") or [])
    events.append({"stage": stage, "at": now})
    metadata["processing_stage_events"] = events[-12:]
    return metadata


class DocumentQueue(Protocol):
    def enqueue(self, db: Session, document: Document, *, process_inline: bool = True) -> Document:
        ...


class InlineDocumentQueue:
    """Local development queue: enqueue means process immediately in-process."""

    def enqueue(self, db: Session, document: Document, *, process_inline: bool = True) -> Document:
        document.processing_status = ProcessingStatus.queued
        document.workflow_metadata = _queue_stage_metadata(document, "pending")
        db.add(document)
        db.commit()
        db.refresh(document)
        if not process_inline:
            return document
        return DocumentProcessor().process(db, document)


class DeferredLocalDocumentQueue:
    """Queue-ready local mode.

    This records queued status without processing. A future worker can claim
    queued rows, or developers can call the reprocess endpoint manually.
    """

    def enqueue(self, db: Session, document: Document, *, process_inline: bool = True) -> Document:
        document.processing_status = ProcessingStatus.queued
        document.workflow_metadata = _queue_stage_metadata(document, "pending")
        db.add(document)
        db.commit()
        db.refresh(document)
        return document


class ExternalDocumentQueue:
    """Deployment scaffold for Redis/SQS/Celery/RQ style queueing."""

    def enqueue(self, db: Session, document: Document, *, process_inline: bool = True) -> Document:
        document.processing_status = ProcessingStatus.queued
        document.workflow_metadata = _queue_stage_metadata(document, "pending")
        document.ingestion_metadata = {
            **(document.ingestion_metadata or {}),
            "queue_backend": get_settings().queue_backend,
            "queue_note": "External queue dispatch is not implemented in this MVP scaffold.",
        }
        db.add(document)
        db.commit()
        db.refresh(document)
        return document


def get_document_queue() -> DocumentQueue:
    settings = get_settings()
    if settings.processing_mode == "inline":
        return InlineDocumentQueue()
    if settings.processing_mode in {"queued", "deferred"} and settings.queue_backend == "local":
        return DeferredLocalDocumentQueue()
    return ExternalDocumentQueue()
