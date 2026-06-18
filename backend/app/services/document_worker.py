import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, ProcessingStatus
from app.core.config import get_settings
from app.services.document_processor import DocumentProcessor


class DocumentWorker:
    """Small local worker scaffold for deployment-style processing."""

    def __init__(self, processor: DocumentProcessor | None = None, *, concurrency: int | None = None) -> None:
        self.processor = processor or DocumentProcessor()
        self.concurrency = concurrency or get_settings().document_processing_concurrency

    def process_document(self, db: Session, document_id: UUID) -> Document | None:
        document = db.get(Document, document_id)
        if not document:
            return None
        return self.processor.process(db, document)

    def process_next(self, db: Session) -> Document | None:
        document = db.scalars(
            select(Document)
            .where(Document.processing_status == ProcessingStatus.queued)
            .order_by(Document.created_at)
            .limit(1)
        ).first()
        if not document:
            return None
        return self.processor.process(db, document)

    def claim_next(self, db: Session) -> UUID | None:
        stmt = (
            select(Document)
            .where(Document.processing_status == ProcessingStatus.queued)
            .order_by(Document.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        document = db.scalars(stmt).first()
        if not document:
            return None
        document.processing_status = ProcessingStatus.processing
        document.ingestion_metadata = {
            **(document.ingestion_metadata or {}),
            "queue_backend": "local_db",
            "queue_worker_claimed": True,
            "queue_worker_concurrency": self.concurrency,
        }
        db.add(document)
        db.commit()
        return document.id

    def process_claimed_document(self, db_factory, document_id: UUID) -> Document | None:
        with db_factory() as db:
            document = db.get(Document, document_id)
            if not document:
                return None
            return self.processor.process(db, document)

    def run_forever(self, db_factory, poll_seconds: float = 2.0) -> None:
        active: set[Future] = set()
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            while True:
                active = {future for future in active if not future.done()}
                while len(active) < self.concurrency:
                    with db_factory() as db:
                        document_id = self.claim_next(db)
                    if not document_id:
                        break
                    active.add(executor.submit(self.process_claimed_document, db_factory, document_id))
                time.sleep(poll_seconds)
