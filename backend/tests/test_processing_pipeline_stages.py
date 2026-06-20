import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.core.config import get_settings
from app.models.document import Document, ProcessingStatus
from app.services.document_processor import DocumentProcessor
from app.services.queue_service import DeferredLocalDocumentQueue


class FakeSession:
    def __init__(self, document: Document) -> None:
        self.document = document

    def add(self, document: Document) -> None:
        self.document = document

    def commit(self) -> None:
        return None

    def refresh(self, document: Document) -> None:
        return None

    def rollback(self) -> None:
        return None

    def get(self, model, document_id):
        return self.document


class SlowVLWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[Path] = []

    def enabled(self) -> bool:
        return True

    def analyze(self, file_path: Path, *, original_filename: str = "") -> dict:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(file_path)
        time.sleep(0.03)
        with self._lock:
            self.active -= 1
        return {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "warn",
            "text": "문서번호 DOC-TEST-001\n품명 수량\nS45C PIN 1",
            "elapsed_ms": 30,
            "validation": {"status": "warn", "ok": False},
        }


def _document(path: Path) -> Document:
    return Document(
        id=uuid4(),
        original_filename=path.name,
        stored_file_path=str(path),
        mime_type="image/png" if path.suffix == ".png" else "application/pdf",
        processing_status=ProcessingStatus.uploaded,
        line_items=[],
    )


def test_queue_enqueue_records_pending_processing_stage(tmp_path):
    path = tmp_path / "queued.png"
    Image.new("RGB", (80, 120), (240, 240, 240)).save(path)
    document = _document(path)

    queued = DeferredLocalDocumentQueue().enqueue(FakeSession(document), document)

    assert queued.processing_status == ProcessingStatus.queued
    assert queued.workflow_metadata["processing_stage"]["stage"] == "pending"
    assert queued.workflow_metadata["processing_stage_events"][-1]["stage"] == "pending"


def test_cpu_prepare_preview_cache_preserves_original_file(tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (320, 420), (230, 230, 228)).save(path)
    original_bytes = path.read_bytes()
    document = _document(path)
    processor = DocumentProcessor()
    processor.settings.upload_dir = tmp_path / "uploads"

    timings: dict[str, int] = {}
    metadata = processor._prepare_cpu_stage_assets(path, document, timings)

    assert path.read_bytes() == original_bytes
    assert metadata["original_file_preserved"] is True
    assert metadata["preview_cache"]["created"] is True
    assert document.preview_image_path
    assert Path(document.preview_image_path).exists()
    assert Path(document.preview_image_path) != path
    assert "preview_thumbnail_ms" in timings


def test_vl_inference_stage_is_serialized_even_when_documents_run_in_parallel(tmp_path):
    paths = []
    for index in range(4):
        path = tmp_path / f"doc-{index}.pdf"
        path.write_bytes(b"%PDF-1.4\n% fake")
        paths.append(path)
    worker = SlowVLWorker()
    processor = DocumentProcessor()
    processor.vl_worker = worker
    documents = [_document(path) for path in paths]

    threads = [
        threading.Thread(
            target=processor._vl_primary_reader_attempt,
            args=(path, document, {}),
        )
        for path, document in zip(paths, documents, strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert worker.max_active <= get_settings().paddleocr_vl_gguf_concurrency
    assert worker.max_active == 1
    assert len(worker.calls) == len(paths)
