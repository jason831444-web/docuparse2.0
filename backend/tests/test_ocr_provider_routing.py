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
import app.services.ocr as ocr_module
from app.services.ocr import OCRResult, OCRService, PaddleOCRProvider
from fastapi.testclient import TestClient
import app.services.ocr_worker_server as ocr_worker_server


def _ocr_settings(**overrides):
    values = {
        "ocr_fallback_provider": "paddleocr_ppocrv4",
        "ocr_worker_url": "http://ocr-worker:8010",
        "ocr_worker_timeout_seconds": 120.0,
        "ai_primary_provider": "paddleocr_vl_1_6_gguf",
        "enable_paddleocr_vl_gguf": False,
        "paddleocr_vl_gguf_repo_id": "PaddlePaddle/PaddleOCR-VL-1.6-GGUF",
        "paddleocr_vl_gguf_model_dir": Path("/app/models/paddleocr_vl_1_6_gguf"),
        "paddleocr_vl_gguf_model_file": "PaddleOCR-VL-1.6-GGUF.gguf",
        "paddleocr_vl_gguf_mmproj_file": "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
        "paddleocr_vl_gguf_server_url": "http://vl-worker-gguf:8080/v1",
        "paddleocr_vl_gguf_worker_url": "http://vl-worker-api:8020",
        "paddleocr_vl_gguf_timeout_seconds": 120.0,
        "paddleocr_vl_gguf_max_pages": 1,
        "paddleocr_vl_gguf_concurrency": 1,
        "paddleocr_vl_gguf_n_predict": 512,
        "paddleocr_vl_gguf_smoke_passed": False,
        "paddleocr_vl_gguf_primary_reader_enabled": True,
        "paddleocr_vl_gguf_upload_pipeline_enabled": True,
        "paddleocr_vl_gguf_in_process_enabled": False,
        "enable_paddleocr_vl": True,
        "paddleocr_vl_model_name": "PaddleOCR-VL-1.6",
        "paddleocr_vl_model_dir": None,
        "paddleocr_vl_hf_repo": "PaddlePaddle/PaddleOCR-VL-1.6",
        "paddleocr_vl_device": "cpu",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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
            line_candidates=[
                {
                    "text": "볼트",
                    "confidence": 0.94,
                    "bbox": [[10.0, 20.0], [50.0, 20.0], [50.0, 38.0], [10.0, 38.0]],
                    "x_min": 10.0,
                    "y_min": 20.0,
                    "x_max": 50.0,
                    "y_max": 38.0,
                }
            ],
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


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")


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


def test_image_ingestion_preserves_ocr_line_candidates_in_raw_blocks(tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    ocr = OCRService(paddle_provider=FakePaddleProvider(), tesseract_provider=FakeTesseractProvider(), prefer_paddleocr=True)
    ingestion = FileIngestionService(detector=ImageDetector(), ocr=ocr)

    normalized = ingestion.ingest(image_path, "scan.png", "image/png")

    assert normalized.file_metadata["ocr_line_candidate_count"] == 1
    assert normalized.raw_extracted_blocks[0]["line_candidates"][0]["text"] == "볼트"
    assert normalized.raw_extracted_blocks[0]["line_candidates"][0]["bbox"]


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
    text, confidence, _, line_candidates = provider._normalize_output(output)

    assert ocr.calls == ["ocr:False"]
    assert text == "PO-123 TEST"
    assert confidence == 0.98
    assert line_candidates == [
        {
            "text": "PO-123 TEST",
            "confidence": 0.98,
            "bbox": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "x_min": 0.0,
            "y_min": 0.0,
            "x_max": 1.0,
            "y_max": 1.0,
        }
    ]


def test_paddleocr_output_normalization_preserves_bbox_candidates():
    provider = PaddleOCRProvider()
    output = [
        [
            [[10, 20], [120, 20], [120, 42], [10, 42]],
            ("M8 볼트 / 와셔 SET", 0.91),
        ],
        {
            "rec_text": "합계",
            "rec_score": 0.86,
            "bbox": [[500, 700], [620, 700], [620, 730], [500, 730]],
        },
    ]

    text, confidence, table_blocks, line_candidates = provider._normalize_output(output)

    assert "M8 볼트 / 와셔 SET" in text
    assert "합계" in text
    assert table_blocks == []
    assert confidence == 0.885
    assert line_candidates[0]["text"] == "M8 볼트 / 와셔 SET"
    assert line_candidates[0]["x_min"] == 10.0
    assert line_candidates[0]["y_max"] == 42.0
    assert line_candidates[1]["text"] == "합계"
    assert line_candidates[1]["confidence"] == 0.86


def test_provider_health_reports_gguf_disabled_with_ppocr_fallback(monkeypatch):
    monkeypatch.setattr(ocr_module, "get_settings", lambda: _ocr_settings())
    monkeypatch.setattr(ocr_module.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr_module, "_paddleocr_usable", lambda: (True, None))
    monkeypatch.setattr(
        ocr_module,
        "_paddleocr_vl_status",
        lambda: {
            "importable": False,
            "usable": False,
            "error": "cannot import name 'PaddleOCRVL'",
            "modules": {"paddleocr": True, "paddlex": False},
        },
    )
    monkeypatch.setattr(
        ocr_module,
        "_ocr_worker_health",
        lambda url, timeout: (
            {
                "status": "ok",
                "ocr_engine": "PP-OCRv4",
                "model": "PP-OCRv4",
                "ocr_version": "PP-OCRv4",
                "device": "cpu",
                "runtime_strategy": "paddleocr_2x_legacy_ocr_api",
            },
            None,
        ),
    )

    payload = ocr_module.provider_health()

    assert payload["primary_provider"] == "paddleocr_vl_1_6_gguf"
    assert payload["primary_provider_available"] is False
    assert payload["primary_provider_status"] == "disabled"
    assert payload["fallback_provider"] == "paddleocr_ppocrv4"
    assert payload["ocr_engine"] == "PP-OCRv4"
    assert payload["ocr_model"] == "PP-OCRv4"
    assert payload["fallback_reason"] == "paddleocr_vl_gguf_disabled"
    assert payload["paddleocr_vl_gguf"]["status"] == "disabled"
    assert payload["paddleocr_vl_official_full"]["status"] == "memory_blocked_on_8gb_cpu"


def test_provider_health_reports_gguf_primary_reader_before_confirmed_integration(monkeypatch, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "PaddleOCR-VL-1.6-GGUF.gguf").write_text("model")
    (model_dir / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf").write_text("mmproj")
    monkeypatch.setattr(
        ocr_module,
        "get_settings",
        lambda: _ocr_settings(
            enable_paddleocr_vl_gguf=True,
            paddleocr_vl_gguf_model_dir=model_dir,
            paddleocr_vl_gguf_smoke_passed=True,
        ),
    )
    monkeypatch.setattr(ocr_module.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr_module, "_paddleocr_usable", lambda: (True, None))
    monkeypatch.setattr(ocr_module, "_paddleocr_vl_status", lambda: {"importable": True, "usable": True, "error": None})
    monkeypatch.setattr(ocr_module.urllib.request, "urlopen", lambda request, timeout=5.0: _JsonResponse({"status": "ok"}))
    monkeypatch.setattr(
        ocr_module,
        "_ocr_worker_health",
        lambda url, timeout: (
            {"status": "ok", "ocr_engine": "PP-OCRv4", "model": "PP-OCRv4", "ocr_version": "PP-OCRv4"},
            None,
        ),
    )

    payload = ocr_module.provider_health()

    assert payload["ocr_engine"] == "PaddleOCR-VL GGUF"
    assert payload["ocr_model"] == "PaddleOCR-VL-1.6-GGUF.gguf"
    assert payload["primary_provider"] == "paddleocr_vl_1_6_gguf"
    assert payload["primary_provider_available"] is True
    assert payload["primary_provider_candidate_available"] is True
    assert payload["primary_reader_available"] is True
    assert payload["primary_reader_mode"] == "candidate_only_validated_by_parser"
    assert payload["primary_provider_status"] == "primary_reader"
    assert payload["runtime_strategy"] == "paddleocr_vl_1_6_gguf_primary_reader_with_ppocrv4_validation_fallback"
    assert payload["fallback_reason"] is None
    assert payload["paddleocr_vl_runtime_mode"] == "primary_provider"
    assert payload["paddleocr_vl_gguf"]["status"] == "primary_reader_candidate"
    assert payload["paddleocr_vl_gguf"]["primary_reader_available"] is True


def test_provider_health_reports_remote_vl_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ocr_module,
        "get_settings",
        lambda: _ocr_settings(
            enable_paddleocr_vl_gguf=True,
            paddleocr_vl_gguf_worker_url="http://172.18.0.1:18024",
            paddleocr_vl_gguf_smoke_passed=True,
        ),
    )
    monkeypatch.setattr(ocr_module.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr_module, "_paddleocr_usable", lambda: (True, None))
    monkeypatch.setattr(ocr_module, "_paddleocr_vl_status", lambda: {"importable": True, "usable": True, "error": None})
    monkeypatch.setattr(
        ocr_module.urllib.request,
        "urlopen",
        lambda request, timeout=5.0: _JsonResponse({
            "status": "ok",
            "model_file_exists": True,
            "mmproj_file_exists": True,
            "llama_server_url": "http://127.0.0.1:8080/v1",
            "pipeline_initialized": True,
        }),
    )
    monkeypatch.setattr(
        ocr_module,
        "_ocr_worker_health",
        lambda url, timeout: (
            {"status": "ok", "ocr_engine": "PP-OCRv4", "model": "PP-OCRv4", "ocr_version": "PP-OCRv4"},
            None,
        ),
    )

    payload = ocr_module.provider_health()

    assert payload["primary_reader_available"] is True
    assert payload["paddleocr_vl_gguf"]["status"] == "remote_primary_reader_candidate"
    assert payload["paddleocr_vl_gguf"]["worker_location"] == "remote"
    assert payload["paddleocr_vl_gguf"]["worker_provider"] == "remote_vl_worker"
    assert payload["paddleocr_vl_gguf"]["worker_url_host"] == "remote-gateway"
    assert payload["paddleocr_vl_gguf"]["worker_transport"] == "multipart_upload"
    assert payload["paddleocr_vl_gguf"]["worker_health"]["pipeline_initialized"] is True


def test_provider_health_reports_gguf_primary_only_when_in_process_enabled(monkeypatch, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "PaddleOCR-VL-1.6-GGUF.gguf").write_text("model")
    (model_dir / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf").write_text("mmproj")
    monkeypatch.setattr(
        ocr_module,
        "get_settings",
        lambda: _ocr_settings(
            enable_paddleocr_vl_gguf=True,
            paddleocr_vl_gguf_model_dir=model_dir,
            paddleocr_vl_gguf_smoke_passed=True,
            paddleocr_vl_gguf_in_process_enabled=True,
        ),
    )
    monkeypatch.setattr(ocr_module.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr_module, "_paddleocr_usable", lambda: (True, None))
    monkeypatch.setattr(ocr_module, "_paddleocr_vl_status", lambda: {"importable": True, "usable": True, "error": None})
    monkeypatch.setattr(ocr_module.urllib.request, "urlopen", lambda request, timeout=5.0: _JsonResponse({"status": "ok"}))
    monkeypatch.setattr(
        ocr_module,
        "_ocr_worker_health",
        lambda url, timeout: (
            {"status": "ok", "ocr_engine": "PP-OCRv4", "model": "PP-OCRv4", "ocr_version": "PP-OCRv4"},
            None,
        ),
    )

    payload = ocr_module.provider_health()

    assert payload["ocr_engine"] == "PaddleOCR-VL GGUF"
    assert payload["ocr_model"] == "PaddleOCR-VL-1.6-GGUF.gguf"
    assert payload["primary_provider"] == "paddleocr_vl_1_6_gguf"
    assert payload["primary_provider_available"] is True
    assert payload["primary_provider_candidate_available"] is True
    assert payload["primary_reader_available"] is True
    assert payload["runtime_strategy"] == "paddleocr_vl_1_6_gguf_primary_reader_with_ppocrv4_validation_fallback"
    assert payload["primary_provider_status"] == "primary_reader"
    assert payload["paddleocr_vl_gguf"]["status"] == "active_candidate"


def test_provider_health_does_not_activate_gguf_without_smoke_gate(monkeypatch, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "PaddleOCR-VL-1.6-GGUF.gguf").write_text("model")
    (model_dir / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf").write_text("mmproj")
    monkeypatch.setattr(
        ocr_module,
        "get_settings",
        lambda: _ocr_settings(enable_paddleocr_vl_gguf=True, paddleocr_vl_gguf_model_dir=model_dir),
    )
    monkeypatch.setattr(ocr_module.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))
    monkeypatch.setattr(ocr_module, "_paddleocr_usable", lambda: (True, None))
    monkeypatch.setattr(ocr_module, "_paddleocr_vl_status", lambda: {"importable": True, "usable": True, "error": None})
    monkeypatch.setattr(ocr_module.urllib.request, "urlopen", lambda request, timeout=5.0: _JsonResponse({"status": "ok"}))
    monkeypatch.setattr(
        ocr_module,
        "_ocr_worker_health",
        lambda url, timeout: (
            {"status": "ok", "ocr_engine": "PP-OCRv4", "model": "PP-OCRv4", "ocr_version": "PP-OCRv4"},
            None,
        ),
    )

    payload = ocr_module.provider_health()

    assert payload["primary_provider_available"] is False
    assert payload["primary_provider_status"] == "llama_server_ready"
    assert payload["fallback_reason"] == "paddleocr_vl_gguf_smoke_not_run"


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


def test_ocr_worker_proactively_resets_provider_after_request_limit(monkeypatch, tmp_path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")
    calls = {"reset": 0}

    class FakeProvider:
        def _load(self):
            return self

        def _run_ocr(self, ocr, path: Path):
            return [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("PO-123 TEST", 0.97)]]

        def _normalize_output(self, output):
            return "PO-123 TEST", 0.97, []

    def fake_reset_provider():
        calls["reset"] += 1
        monkeypatch.setattr(ocr_worker_server, "_provider", None)
        monkeypatch.setattr(ocr_worker_server, "_requests_since_provider_reset", 0)

    monkeypatch.setenv("OCR_WORKER_RESET_AFTER_REQUESTS", "1")
    monkeypatch.setattr(ocr_worker_server, "_provider", object())
    monkeypatch.setattr(ocr_worker_server, "_requests_since_provider_reset", 1)
    monkeypatch.setattr(ocr_worker_server, "_get_provider", lambda: FakeProvider())
    monkeypatch.setattr(ocr_worker_server, "_reset_provider", fake_reset_provider)
    monkeypatch.setattr(ocr_worker_server, "_last_error", None)

    client = TestClient(ocr_worker_server.app)
    response = client.post("/ocr", json={"image_path": str(image_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["retry_used"] is False
    assert payload["provider_reset_used"] is True
    assert payload["provider_reset_reason"] == "request_limit"
    assert payload["requests_since_provider_reset"] == 1
    assert calls["reset"] == 1


def test_ocr_worker_health_identifies_ppocrv4_fallback_worker(monkeypatch):
    monkeypatch.setenv("PADDLEOCR_OCR_VERSION", "PP-OCRv4")
    monkeypatch.setattr(ocr_worker_server.PaddleOCRProvider, "is_available", classmethod(lambda cls: True))

    client = TestClient(ocr_worker_server.app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ocr_engine"] == "PP-OCRv4"
    assert payload["model"] == "PP-OCRv4"
    assert payload["primary_provider"] == "paddleocr_ppocrv4"
    assert payload["fallback_provider"] == "tesseract"
    assert payload["runtime_strategy"] == "paddleocr_2x_legacy_ocr_api"
