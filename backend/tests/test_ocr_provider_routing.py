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
from app.services.ocr import OCRResult, OCRService, PaddleOCRProvider
from fastapi.testclient import TestClient
import app.services.ocr_worker_server as ocr_worker_server


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


def test_paddleocr_provider_prefers_legacy_ocr_api_over_predict(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")

    class DualApiOCR:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def ocr(self, path: str, cls: bool = False):
            self.calls.append(f"ocr:{cls}")
            return [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("PO-123 TEST", 0.98)]]

        def predict(self, path: str):
            self.calls.append("predict")
            return [{"text": "PREDICT PATH", "score": 0.5}]

    ocr = DualApiOCR()
    provider = PaddleOCRProvider()

    output = provider._run_ocr(ocr, image_path)
    text, confidence, _ = provider._normalize_output(output)

    assert ocr.calls == ["ocr:False"]
    assert text == "PO-123 TEST"
    assert confidence == 0.98


def test_ocr_worker_resets_provider_and_retries_paddle_runtime_error(monkeypatch, tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    calls = {"get": 0, "reset": 0}

    class FakeProvider:
        def _load(self):
            return self

        def _run_ocr(self, ocr, path: Path):
            calls["get"] += 1
            if calls["get"] == 1:
                raise RuntimeError("PreconditionNotMet: Tensor holds no memory [operator < elementwise_mul > error]")
            return [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("PO-123 TEST", 0.99)]]

        def _normalize_output(self, output):
            return "PO-123 TEST", 0.99, []

    def fake_get_provider():
        return FakeProvider()

    def fake_reset_provider():
        calls["reset"] += 1

    monkeypatch.setattr(ocr_worker_server, "_get_provider", fake_get_provider)
    monkeypatch.setattr(ocr_worker_server, "_reset_provider", fake_reset_provider)
    monkeypatch.setattr(ocr_worker_server, "_last_error", None)

    client = TestClient(ocr_worker_server.app)
    response = client.post("/ocr", json={"image_path": str(image_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["engine_name"] == "ocr_worker_paddleocr"
    assert payload["text"] == "PO-123 TEST"
    assert payload["retry_used"] is True
    assert payload["provider_reset_used"] is True
    assert payload["worker_attempt_count"] == 2
    assert calls["reset"] == 1


def test_ocr_worker_returns_clear_retry_failure_body(monkeypatch, tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    calls = {"reset": 0}

    class FakeProvider:
        def _load(self):
            return self

        def _run_ocr(self, ocr, path: Path):
            raise RuntimeError("PreconditionNotMet: Tensor holds no memory [operator < elementwise_add > error]")

        def _normalize_output(self, output):
            return "", 0.0, []

    monkeypatch.setattr(ocr_worker_server, "_get_provider", lambda: FakeProvider())
    monkeypatch.setattr(ocr_worker_server, "_reset_provider", lambda: calls.__setitem__("reset", calls["reset"] + 1))
    monkeypatch.setattr(ocr_worker_server, "_last_error", None)

    client = TestClient(ocr_worker_server.app)
    response = client.post("/ocr", json={"image_path": str(image_path)})

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert "Tensor holds no memory" in payload["error"]
    assert payload["retry_used"] is True
    assert payload["provider_reset_used"] is True
    assert calls["reset"] == 1
