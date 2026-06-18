from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import requests

from app.services import vl_worker_server
from app.services.vl_candidate_client import VLCandidateWorkerClient


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeSchemaPromptResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "raw_text": "입고 검사 기록서\\n문서번호 IQC-SCHEMA-001",
                          "document_type": "inspection_report",
                          "tables": [
                            {
                              "table_type": "incoming_inspection",
                              "review_required": true,
                              "rows": [
                                {
                                  "no": 1,
                                  "item_name": "베어링 하우징",
                                  "specification": "BH-220",
                                  "received_quantity": 80,
                                  "accepted_quantity": 78,
                                  "defective_quantity": 2,
                                  "result": "조건부합격",
                                  "note": "표면 흠집",
                                  "line_total": 999999
                                }
                              ],
                              "warnings": ["row_boundary_uncertain"]
                            }
                          ]
                        }
                        """
                    }
                }
            ]
        }


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def predict(self, image_path: str, *, max_new_tokens: int) -> list[dict]:
        self.calls.append((image_path, max_new_tokens))
        return [{"text": "견적서\n견적번호 QT-REMOTE-001\n총액 473,000"}]


class _FakeInspectionPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def predict(self, image_path: str, *, max_new_tokens: int) -> list[dict]:
        self.calls.append((image_path, max_new_tokens))
        return [
            {
                "text": "\n".join(
                    [
                        "입고 검사 기록서",
                        "문서번호 IQC-REMOTE-007",
                        "No 품목 규격 입고수량 합격 불량 판정 비고",
                        "1 베어링 하우징 BH-220 80 78 2 조건부합격 표면 흠집",
                        "2 S45C PIN 8X60 300 300 0 합격",
                        "금액 항목 없음",
                    ]
                )
            }
        ]


def test_vl_candidate_client_uploads_file_to_remote_worker(monkeypatch, tmp_path: Path):
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"%PDF-remote-worker-test")
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        uploaded = kwargs["files"]["file"]
        assert uploaded[0] == "sample.pdf"
        assert uploaded[1].read() == b"%PDF-remote-worker-test"
        assert kwargs["data"] == {"original_filename": "sample.pdf"}
        return _FakeResponse(
            {
                "ok": True,
                "classification": "pass",
                "text_preview": "견적서 QT-REMOTE-001",
            }
        )

    client = VLCandidateWorkerClient(worker_url="http://runpod-worker:8020", timeout_seconds=12)
    monkeypatch.setattr(client, "enabled", lambda: True)
    monkeypatch.setattr("app.services.vl_candidate_client.requests.post", fake_post)

    result = client.analyze(sample, original_filename="sample.pdf")

    assert calls[0]["url"] == "http://runpod-worker:8020/analyze-upload"
    assert result["ok"] is True
    assert result["worker_transport"] == "multipart_upload"
    assert result["worker_location"] == "remote"
    assert result["worker_provider"] == "remote_vl_worker"
    assert result["worker_url_host"] == "runpod-worker"


def test_vl_candidate_client_falls_back_to_path_endpoint_for_legacy_worker(monkeypatch, tmp_path: Path):
    sample = tmp_path / "legacy.pdf"
    sample.write_bytes(b"%PDF-legacy-worker-test")
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if url.endswith("/analyze-upload"):
            return _FakeResponse({"detail": "not found"}, status_code=404)
        assert kwargs["json"]["file_path"] == str(sample)
        return _FakeResponse({"ok": True, "classification": "pass"})

    client = VLCandidateWorkerClient(worker_url="http://legacy-worker:8020", timeout_seconds=12)
    monkeypatch.setattr(client, "enabled", lambda: True)
    monkeypatch.setattr("app.services.vl_candidate_client.requests.post", fake_post)

    result = client.analyze(sample, original_filename="legacy.pdf")

    assert calls == ["http://legacy-worker:8020/analyze-upload", "http://legacy-worker:8020/analyze"]
    assert result["ok"] is True
    assert result["worker_transport"] == "shared_file_path"
    assert result["worker_location"] == "remote"


def test_vl_candidate_client_retries_remote_upload_connection_error(monkeypatch, tmp_path: Path):
    sample = tmp_path / "retry.pdf"
    sample.write_bytes(b"%PDF-retry-worker-test")
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        uploaded = kwargs["files"]["file"]
        assert uploaded[1].read() == b"%PDF-retry-worker-test"
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("remote closed connection")
        return _FakeResponse({"ok": True, "classification": "pass"})

    client = VLCandidateWorkerClient(worker_url="http://runpod-worker:8020", timeout_seconds=12)
    monkeypatch.setattr(client, "enabled", lambda: True)
    monkeypatch.setattr("app.services.vl_candidate_client.requests.post", fake_post)
    monkeypatch.setattr("app.services.vl_candidate_client.time.sleep", lambda _seconds: None)

    result = client.analyze(sample, original_filename="retry.pdf")

    assert calls == [
        "http://runpod-worker:8020/analyze-upload",
        "http://runpod-worker:8020/analyze-upload",
    ]
    assert result["ok"] is True
    assert result["worker_transport"] == "multipart_upload"
    assert result["worker_location"] == "remote"


def test_vl_candidate_client_overrides_worker_runtime_metadata(monkeypatch, tmp_path: Path):
    sample = tmp_path / "metadata.pdf"
    sample.write_bytes(b"%PDF-metadata-worker-test")

    def fake_post(url, **kwargs):
        return _FakeResponse(
            {
                "ok": True,
                "classification": "pass",
                "worker_transport": "multipart_upload",
                "worker_location": "worker_runtime",
                "worker_provider": "vl_worker_api",
                "worker_url_host": "internal-worker",
            }
        )

    client = VLCandidateWorkerClient(worker_url="http://runpod-worker:8020", timeout_seconds=12)
    monkeypatch.setattr(client, "enabled", lambda: True)
    monkeypatch.setattr("app.services.vl_candidate_client.requests.post", fake_post)

    result = client.analyze(sample, original_filename="metadata.pdf")

    assert result["worker_location"] == "remote"
    assert result["worker_provider"] == "remote_vl_worker"
    assert result["worker_url_host"] == "runpod-worker"
    assert result["worker_transport"] == "multipart_upload"


def test_vl_worker_analyze_upload_saves_file_and_runs_pipeline(monkeypatch, tmp_path: Path):
    fake_pipeline = _FakePipeline()
    monkeypatch.setattr(
        vl_worker_server,
        "get_settings",
        lambda: SimpleNamespace(
            upload_dir=tmp_path,
            paddleocr_vl_gguf_n_predict=128,
            paddleocr_vl_gguf_model_dir=tmp_path,
            paddleocr_vl_gguf_model_file="model.gguf",
            paddleocr_vl_gguf_mmproj_file="mmproj.gguf",
            paddleocr_vl_gguf_server_url="http://localhost:8080/v1",
            paddleocr_vl_gguf_concurrency=1,
            paddleocr_vl_gguf_max_pages=1,
        ),
    )
    monkeypatch.setattr(vl_worker_server, "_get_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(vl_worker_server, "extract_text", lambda output: output[0]["text"])
    monkeypatch.setattr(
        vl_worker_server,
        "validate_output_text",
        lambda text, expected_terms: {"ok": True, "status": "pass", "matched_terms": ["견적서"]},
    )

    response = TestClient(vl_worker_server.app).post(
        "/analyze-upload",
        files={"file": ("견적서 sample.jpg", b"fake-jpeg-fixture", "image/jpeg")},
        data={"original_filename": "견적서 sample.jpg"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["worker_transport"] == "multipart_upload"
    assert payload["worker_provider"] == "vl_worker_api"
    assert payload["model_name"] == "PaddleOCR-VL-1.6-GGUF"
    assert payload["remote_upload"]["uploaded_bytes"] == len(b"fake-jpeg-fixture")
    assert payload["remote_upload"]["saved_path"].endswith(".jpg")
    assert "견적서" in payload["text_preview"]
    assert fake_pipeline.calls
    saved_path = Path(payload["remote_upload"]["saved_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"fake-jpeg-fixture"


def test_vl_worker_analyze_upload_returns_structured_inspection_tables(monkeypatch, tmp_path: Path):
    fake_pipeline = _FakeInspectionPipeline()
    monkeypatch.setattr(
        vl_worker_server,
        "get_settings",
        lambda: SimpleNamespace(
            upload_dir=tmp_path,
            paddleocr_vl_gguf_n_predict=128,
            paddleocr_vl_gguf_model_dir=tmp_path,
            paddleocr_vl_gguf_model_file="model.gguf",
            paddleocr_vl_gguf_mmproj_file="mmproj.gguf",
            paddleocr_vl_gguf_server_url="http://localhost:8080/v1",
            paddleocr_vl_gguf_concurrency=1,
            paddleocr_vl_gguf_max_pages=1,
        ),
    )
    monkeypatch.setattr(vl_worker_server, "_get_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(vl_worker_server, "extract_text", lambda output: output[0]["text"])
    monkeypatch.setattr(
        vl_worker_server,
        "validate_output_text",
        lambda text, expected_terms: {"ok": True, "status": "pass", "matched_terms": ["입고"]},
    )

    response = TestClient(vl_worker_server.app).post(
        "/analyze-upload",
        files={"file": ("incoming-inspection.jpg", b"fake-jpeg-fixture", "image/jpeg")},
        data={"original_filename": "incoming-inspection.jpg"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["structured_schema"]["version"] == "docparse_vl_table_schema_v1"
    assert payload["tables"][0]["table_type"] == "incoming_inspection"
    assert payload["tables"][0]["review_required"] is True
    rows = payload["tables"][0]["rows"]
    assert rows[0]["item_name"] == "베어링 하우징"
    assert rows[0]["specification"] == "BH-220"
    assert rows[0]["received_quantity"] == 80
    assert rows[0]["accepted_quantity"] == 78
    assert rows[0]["defective_quantity"] == 2
    assert rows[0]["result"] == "조건부 합격"
    assert rows[1]["item_name"] == "S45C PIN"


def test_vl_worker_analyze_upload_prefers_schema_prompt_json_tables(monkeypatch, tmp_path: Path):
    calls: list[dict] = []
    monkeypatch.setattr(
        vl_worker_server,
        "get_settings",
        lambda: SimpleNamespace(
            upload_dir=tmp_path,
            paddleocr_vl_gguf_n_predict=256,
            paddleocr_vl_gguf_model_dir=tmp_path,
            paddleocr_vl_gguf_model_file="model.gguf",
            paddleocr_vl_gguf_mmproj_file="mmproj.gguf",
            paddleocr_vl_gguf_server_url="http://localhost:8080/v1",
            paddleocr_vl_gguf_concurrency=1,
            paddleocr_vl_gguf_max_pages=1,
            paddleocr_vl_gguf_timeout_seconds=30,
            paddleocr_vl_gguf_schema_prompt_enabled=True,
            paddleocr_vl_gguf_direct_schema_prompt_enabled=True,
        ),
    )

    def fail_pipeline():
        raise AssertionError("PaddleOCRVL fallback should not run when schema prompt succeeds")

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        assert url == "http://localhost:8080/v1/chat/completions"
        content = kwargs["json"]["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert "incoming inspection" in content[0]["text"]
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return _FakeSchemaPromptResponse()

    monkeypatch.setattr(vl_worker_server, "_get_pipeline", fail_pipeline)
    monkeypatch.setattr("app.services.vl_worker_server.requests.post", fake_post)
    monkeypatch.setattr(
        vl_worker_server,
        "validate_output_text",
        lambda text, expected_terms: {"ok": True, "status": "pass", "matched_terms": ["입고"]},
    )

    response = TestClient(vl_worker_server.app).post(
        "/analyze-upload",
        files={"file": ("incoming-inspection.jpg", b"fake-jpeg-fixture", "image/jpeg")},
        data={"original_filename": "incoming-inspection.jpg"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["schema_prompt"]["used"] is True
    assert payload["schema_prompt"]["transport"] == "llama_server_chat_completions"
    assert payload["tables"][0]["source"] == "vl_schema_prompt"
    assert payload["tables"][0]["table_type"] == "incoming_inspection"
    row = payload["tables"][0]["rows"][0]
    assert row["item_name"] == "베어링 하우징"
    assert row["accepted_quantity"] == 78
    assert row["defective_quantity"] == 2
    assert row["result"] == "조건부 합격"
    assert "line_total" not in row
    assert calls
