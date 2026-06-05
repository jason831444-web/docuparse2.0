from pathlib import Path
import sys
from types import SimpleNamespace

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.services.file_ingestion import FileIngestionService
from app.services.file_type_detection import DetectedFileType
from app.services.ocr import OCRResult, OCRService


class FakePaddleProvider:
    engine_name = "paddleocr"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def extract(self, image_path: Path) -> OCRResult:
        if self.fail:
            raise RuntimeError("paddle unavailable")
        return OCRResult(
            text="Paddle text",
            confidence=0.91,
            engine_name=self.engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
            table_blocks=[{"rows": [["품목명", "수량"], ["볼트", "10"]]}],
        )


class FakeWorkerProvider:
    engine_name = "ocr_worker_paddleocr"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def is_configured(self) -> bool:
        return True

    def extract(self, image_path: Path) -> OCRResult:
        if self.fail:
            raise RuntimeError("ocr_worker_timeout")
        return OCRResult(
            text="Worker Paddle text",
            confidence=0.93,
            engine_name=self.engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
            elapsed_ms=123,
            ocr_worker_url_used="http://ocr-worker:8010",
            ocr_worker_available=True,
        )


class FakeTesseractProvider:
    engine_name = "tesseract"

    def extract(self, image_path: Path) -> OCRResult:
        return OCRResult(
            text="Tesseract text",
            confidence=0.72,
            engine_name=self.engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
        )


class ImageDetector:
    def detect(self, path: Path, original_filename: str, declared_mime: str | None = None):
        return DetectedFileType(
            extension="png",
            mime_type="image/png",
            family="image",
            supported=True,
            partial=False,
        )


def test_paddleocr_route_is_selected_when_available(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    service = OCRService(paddle_provider=FakePaddleProvider(), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=True)

    result = service.extract(image_path)

    assert result.engine_name == "paddleocr"
    assert result.text == "Paddle text"
    assert result.confidence == 0.91
    assert result.table_blocks
    assert result.provider_attempted == ["paddleocr"]


def test_paddleocr_failure_falls_back_to_tesseract(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    service = OCRService(paddle_provider=FakePaddleProvider(fail=True), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=True)

    result = service.extract(image_path)

    assert result.engine_name == "tesseract"
    assert result.text == "Tesseract text"
    assert result.provider_attempted == ["paddleocr", "tesseract"]
    assert result.provider_failed_reason["paddleocr"] == "paddle unavailable"


def test_provider_diagnostics_are_saved_in_image_ingestion_metadata(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    ocr = OCRService(paddle_provider=FakePaddleProvider(fail=True), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=True)
    ingestion = FileIngestionService(detector=ImageDetector(), ocr=ocr)

    normalized = ingestion.ingest(image_path, "scan.png", "image/png")

    assert normalized.normalized_text == "Tesseract text"
    assert normalized.file_metadata["ocr_engine"] == "tesseract"
    assert normalized.file_metadata["ocr_provider_attempted"] == ["paddleocr", "tesseract"]
    assert normalized.file_metadata["ocr_provider_succeeded"] == "tesseract"
    assert normalized.file_metadata["ocr_provider_failed_reason"]["paddleocr"] == "paddle unavailable"


def test_all_ocr_provider_failures_return_empty_result_without_raising(tmp_path):
    class BrokenProvider:
        engine_name = "broken"

        def extract(self, image_path: Path) -> OCRResult:
            raise RuntimeError("boom")

    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    service = OCRService(paddle_provider=BrokenProvider(), tesseract_provider=BrokenProvider(), prefer_paddleocr=True)

    result = service.extract(image_path)

    assert result.engine_name == "unavailable"
    assert result.text == ""
    assert result.confidence == 0.0
    assert result.provider_failed_reason == {"broken": "boom"}


def test_ocr_worker_route_is_selected_when_configured(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    service = OCRService(worker_provider=FakeWorkerProvider(), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=False)
    service.prefer_ocr_worker = True

    result = service.extract(image_path)

    assert result.engine_name == "ocr_worker_paddleocr"
    assert result.text == "Worker Paddle text"
    assert result.provider_attempted == ["ocr_worker_paddleocr"]
    assert result.ocr_worker_url_used == "http://ocr-worker:8010"
    assert result.elapsed_ms == 123


def test_ocr_worker_timeout_falls_back_to_tesseract(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    service = OCRService(worker_provider=FakeWorkerProvider(fail=True), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=False)
    service.prefer_ocr_worker = True

    result = service.extract(image_path)

    assert result.engine_name == "tesseract"
    assert result.provider_attempted == ["ocr_worker_paddleocr", "tesseract"]
    assert result.provider_failed_reason["ocr_worker_paddleocr"] == "ocr_worker_timeout"
    assert result.ocr_fallback_used is True
