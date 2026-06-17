from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

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


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def predict(self, image_path: str, *, max_new_tokens: int) -> list[dict]:
        self.calls.append((image_path, max_new_tokens))
        return [{"text": "견적서\n견적번호 QT-REMOTE-001\n총액 473,000"}]


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
    assert payload["remote_upload"]["uploaded_bytes"] == len(b"fake-jpeg-fixture")
    assert payload["remote_upload"]["saved_path"].endswith(".jpg")
    assert "견적서" in payload["text_preview"]
    assert fake_pipeline.calls
    saved_path = Path(payload["remote_upload"]["saved_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"fake-jpeg-fixture"
