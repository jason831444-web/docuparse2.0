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


class _FakeNonJsonSchemaPromptResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": "\n".join(
                            [
                                "입고 검사 기록서",
                                "문서번호 DOC-001",
                                "No 품명 Lot/Code 입고수량 검사항목 판정 비고",
                                "1 스테인리스 브라켓 BRK-SUS 20 외관/치수 합격 이상 없음",
                            ]
                        )
                    }
                }
            ]
        }


class _FakeSchemaRepairResponse:
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
                          "raw_text": "입고 검사 기록서\\n문서번호 DOC-001",
                          "document_type": "inspection_report",
                          "tables": [
                            {
                              "table_type": "incoming_inspection",
                              "review_required": true,
                              "rows": [
                                {
                                  "no": 1,
                                  "item_name": "스테인리스 브라켓",
                                  "document_item_code": "BRK-SUS",
                                  "received_quantity": 20,
                                  "inspection_item": "외관/치수",
                                  "result": "합격",
                                  "note": "이상 없음"
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


class _FakeBadSchemaRepairResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "아직 JSON이 아닙니다"}}]}


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


class _FakeOfficialPaddleResult:
    @property
    def json(self) -> dict:
        return {
            "res": {
                "input_path": "incoming-inspection.jpg",
                "width": 2074,
                "height": 2878,
                "parsing_res_list": [
                    {
                        "block_label": "header",
                        "block_content": "문서번호: DOC-001",
                        "block_bbox": [176, 155, 426, 195],
                    },
                    {
                        "block_label": "doc_title",
                        "block_content": "입고 검사 기록서",
                        "block_bbox": [834, 149, 1239, 216],
                    },
                    {
                        "block_label": "table",
                        "block_content": (
                            "<table>"
                            "<tr><td>No</td><td>품명</td><td>Lot/Code</td><td>입고수량</td><td>합격</td><td>불량</td><td>검사항목</td><td>판정</td><td>비고</td></tr>"
                            "<tr><td>1</td><td>스테인리스 브라젯</td><td>BRK-SUS</td><td>20</td><td>19</td><td>1</td><td>외관/치수</td><td>조건부합격</td><td>이상 없음</td></tr>"
                            "<tr><td>2</td><td>SUS 볼트 M5x20</td><td>BOLT-M5X20</td><td>120</td><td>120</td><td>0</td><td>외관/치수</td><td>합격</td><td>치수 재확인</td></tr>"
                            "<tr><td>3</td><td>PCB Connector 12P</td><td>CONN-12P</td><td>20</td><td>20</td><td>0</td><td>외관/치수</td><td>합격</td><td>이상 없음</td></tr>"
                            "</table>"
                        ),
                        "block_bbox": [177, 598, 1926, 948],
                        "block_polygon_points": [[177, 598], [1926, 598], [1926, 948], [177, 948]],
                    },
                    {
                        "block_label": "text",
                        "block_content": "※ 검사 기록서는 금액이 없는 품질 확인 문서입니다.",
                        "block_bbox": [176, 1030, 801, 1073],
                    },
                ],
            }
        }


class _FakeOfficialInspectionPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def predict(self, image_path: str, *, max_new_tokens: int):
        self.calls.append((image_path, max_new_tokens))
        yield _FakeOfficialPaddleResult()


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
    fake_pipeline = _FakeOfficialInspectionPipeline()
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
    monkeypatch.setattr(vl_worker_server, "_image_size", lambda image_path: (2074, 2878))
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
    assert "key_values" in payload["structured_schema"]["response_fields"]
    assert payload["schema_prompt"]["transport"] == "paddleocrvl_predict_official_result"
    assert payload["schema_prompt"]["prompt_bypassed"] is True
    assert payload["key_values"][0]["key"] == "문서번호"
    assert payload["key_values"][0]["value"] == "DOC-001"
    assert payload["key_values"][0]["source"] == "vl_key_value"
    assert payload["key_values"][0]["vl_source"] == "paddleocrvl_official_text_block"
    assert "bbox" not in payload["key_values"][0]
    assert "key_bbox" not in payload["key_values"][0]
    assert "value_bbox" not in payload["key_values"][0]
    assert payload["tables"][0]["table_type"] == "incoming_inspection"
    assert payload["tables"][0]["source"] == "paddleocrvl_official_table_html"
    assert payload["tables"][0]["review_required"] is True
    assert payload["tables"][0]["raw_columns"] == ["No", "품명", "Lot/Code", "입고수량", "합격", "불량", "검사항목", "판정", "비고"]
    assert payload["tables"][0]["provenance"]["block_bbox"] == [177, 598, 1926, 948]
    quality = payload["tables"][0]["official_table_quality"]
    assert quality["table_count"] == 1
    assert quality["document_type_guess"] == "inspection_report"
    assert quality["column_count"] == 9
    assert quality["row_count"] == 3
    assert quality["expected_column_coverage"] >= 0.85
    assert quality["row_boundary_quality"] >= 0.85
    assert quality["quality_score"] >= 0.80
    assert "품명" in quality["covered_expected_columns"]
    assert "검사항목" in quality["covered_expected_columns"]
    rows = payload["tables"][0]["rows"]
    assert rows[0]["item_name"] == "스테인리스 브라젯"
    assert rows[0]["document_item_code"] == "BRK-SUS"
    assert rows[0]["received_quantity"] == 20
    assert rows[0]["accepted_quantity"] == 19
    assert rows[0]["defective_quantity"] == 1
    assert rows[0]["inspection_item"] == "외관/치수"
    assert rows[0]["result"] == "조건부 합격"
    assert rows[0]["note"] == "이상 없음"
    assert rows[1]["item_name"] == "SUS 볼트"
    assert rows[1]["specification"] == "M5x20"
    assert rows[1]["document_item_code"] == "BOLT-M5X20"
    assert rows[1]["received_quantity"] == 120
    assert "unit_price" not in rows[0]
    assert "line_total" not in rows[0]
    assert fake_pipeline.calls


def test_official_table_quality_scores_visible_amount_columns():
    quality = vl_worker_server._official_table_quality(
        columns=["품목", "규격", "수량", "단가", "공급가액", "세액", "합계"],
        raw_rows=[
            ["PCB Connector", "12P", "200", "1,250", "250,000", "25,000", "275,000"],
            ["Cable Harness", "500mm", "80", "2,800", "224,000", "22,400", "246,400"],
        ],
        canonical_rows=[
            {
                "item_name": "PCB Connector",
                "specification": "12P",
                "quantity": 200,
                "unit_price": 1250,
                "supply_amount": 250000,
                "tax_amount": 25000,
                "line_total": 275000,
            },
            {
                "item_name": "Cable Harness",
                "specification": "500mm",
                "quantity": 80,
                "unit_price": 2800,
                "supply_amount": 224000,
                "tax_amount": 22400,
                "line_total": 246400,
            },
        ],
        table_type="line_items",
        text="세금계산서 문서번호 INV-2026-0002",
        original_filename="MFG-002_tax_invoice_uncropped.png",
    )

    assert quality["document_type_guess"] == "invoice"
    assert quality["expected_column_coverage"] == 1.0
    assert quality["amount_column_coverage"] == 1.0
    assert quality["quality_score"] >= 0.90
    assert quality["missing_expected_columns"] == []


def test_vl_worker_structures_incoming_inspection_rows_with_received_quantity_only():
    tables = vl_worker_server._extract_structured_tables(
        "\n".join(
            [
                "입고 검사 기록서",
                "문서번호: DOC-001",
                "No 품명 Lot/Code 입고수량 검사항목 판정 비고",
                "1 스테인리스 브라젯 BRK-SUS 20 외관/치수 합격 이상 없음",
                "2 SUS 볼트 M5x20 BOLT-M5X20 120 외관/치수 합격 치수 재확인",
                "3 PCB Connector 12P CONN-12P 20 외관/치수 합격 이상 없음",
                "검사 기록서는 금액이 없는 품질 확인 문서입니다.",
            ]
        ),
        [],
        original_filename="incoming-inspection-photo.jpg",
    )

    assert tables
    assert tables[0]["table_type"] == "incoming_inspection"
    rows = tables[0]["rows"]
    assert len(rows) == 3
    assert rows[0]["item_name"] == "스테인리스 브라젯"
    assert rows[0]["document_item_code"] == "BRK-SUS"
    assert rows[0]["received_quantity"] == 20
    assert rows[0]["inspection_item"] == "외관/치수"
    assert rows[0]["result"] == "합격"
    assert rows[0]["note"] == "이상 없음"
    assert rows[1]["item_name"] == "SUS 볼트"
    assert rows[1]["specification"] == "M5x20"
    assert rows[1]["document_item_code"] == "BOLT-M5X20"
    assert rows[1]["received_quantity"] == 120
    assert rows[2]["item_name"] == "PCB Connector"
    assert rows[2]["specification"] == "12P"
    assert rows[2]["document_item_code"] == "CONN-12P"
    assert rows[2]["received_quantity"] == 20


def test_vl_worker_key_values_from_official_crop_blocks():
    output = [
        {
            "json": {
                "res": {
                    "parsing_res_list": [
                        {
                            "block_label": "table",
                            "block_content": (
                                "<table>"
                                "<tr><td>공급자</td></tr>"
                                "<tr><td>상호: (주)미래테크</td></tr>"
                                "<tr><td>사업자번호: 123-45-67890</td></tr>"
                                "<tr><td>당당: 김선영 / 회계팀</td></tr>"
                                "</table>"
                            ),
                            "block_bbox": [100, 100, 300, 220],
                        },
                        {
                            "block_label": "text",
                            "block_content": "공급받는자",
                            "block_bbox": [400, 100, 500, 130],
                        },
                        {
                            "block_label": "text",
                            "block_content": "상호: (주)시흥대야점\n작성일: 2026.06.07\n유효기간: 견적일로부터 14일",
                            "block_bbox": [400, 130, 700, 220],
                        },
                        {
                            "block_label": "table",
                            "block_content": (
                                "<table><tr><td>No</td><td>품목명</td><td>금액</td></tr>"
                                "<tr><td>1</td><td>HDPE 포장필름</td><td>1,120,000</td></tr></table>"
                            ),
                            "block_bbox": [100, 240, 700, 420],
                        },
                    ]
                }
            }
        }
    ]

    values = vl_worker_server._key_values_from_official_paddle_output(
        output,
        width=800,
        height=500,
        origin_x=0,
        origin_y=200,
        full_width=1000,
        full_height=1400,
    )

    by_key = {item["key"]: item for item in values}
    assert by_key["공급자 상호"]["value"] == "(주)미래테크"
    assert by_key["공급자 사업자번호"]["value"] == "123-45-67890"
    assert by_key["공급자 담당"]["value"] == "김선영 / 회계팀"
    assert by_key["공급받는자 상호"]["value"] == "(주)시흥대야점"
    assert by_key["작성일"]["value"] == "2026.06.07"
    assert by_key["유효기간"]["value"] == "견적일로부터 14일"
    assert "품목명" not in by_key
    assert by_key["공급자 상호"]["source"] == "vl_key_value"
    assert by_key["공급받는자 상호"]["source"] == "vl_key_value"
    assert all("bbox" not in item and "key_bbox" not in item and "value_bbox" not in item for item in values)


def test_vl_worker_analyze_upload_prefers_schema_prompt_json_tables(monkeypatch, tmp_path: Path):
    calls: list[dict] = []
    fake_pipeline = _FakePipeline()
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

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        assert url == "http://localhost:8080/v1/chat/completions"
        content = kwargs["json"]["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert "incoming inspection" in content[0]["text"]
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return _FakeSchemaPromptResponse()

    monkeypatch.setattr(vl_worker_server, "_get_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(vl_worker_server, "extract_text", lambda output: output[0]["text"])
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
    assert fake_pipeline.calls
    assert calls


def test_vl_worker_repairs_non_json_schema_prompt_into_table_json(monkeypatch, tmp_path: Path):
    calls: list[dict] = []
    fake_pipeline = _FakePipeline()
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

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        assert url == "http://localhost:8080/v1/chat/completions"
        if len(calls) == 1:
            return _FakeNonJsonSchemaPromptResponse()
        assert "Convert the following VLM extraction output into ONLY valid JSON" in kwargs["json"]["messages"][0]["content"]
        return _FakeSchemaRepairResponse()

    monkeypatch.setattr(vl_worker_server, "_get_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(vl_worker_server, "extract_text", lambda output: output[0]["text"])
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
    assert payload["schema_prompt"]["repair_attempted"] is True
    assert payload["schema_prompt"]["repair_used"] is True
    assert payload["schema_prompt"]["transport"] == "llama_server_chat_completions_repair"
    assert payload["tables"][0]["source"] == "vl_schema_prompt_repair"
    row = payload["tables"][0]["rows"][0]
    assert row["item_name"] == "스테인리스 브라켓"
    assert row["document_item_code"] == "BRK-SUS"
    assert row["received_quantity"] == 20
    assert fake_pipeline.calls
    assert len(calls) == 2


def test_vl_worker_repairs_paddle_vlm_text_into_table_json_before_table_extractor(monkeypatch, tmp_path: Path):
    calls: list[dict] = []
    fake_pipeline = _FakeInspectionPipeline()
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

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        if len(calls) == 1:
            return _FakeNonJsonSchemaPromptResponse()
        if len(calls) == 2:
            return _FakeBadSchemaRepairResponse()
        assert "베어링 하우징" in kwargs["json"]["messages"][0]["content"]
        return _FakeSchemaRepairResponse()

    def fail_table_extractor(*_args, **_kwargs):
        raise AssertionError("heuristic table extractor should not run when VLM text repair succeeds")

    monkeypatch.setattr(vl_worker_server, "_get_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr("app.services.vl_worker_server.requests.post", fake_post)
    monkeypatch.setattr(vl_worker_server, "_extract_structured_tables", fail_table_extractor)
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
    assert payload["schema_prompt"]["used"] is True
    assert payload["schema_prompt"]["transport"] == "paddleocr_predict_text_schema_repair"
    assert payload["schema_prompt"]["repair_used"] is True
    assert payload["tables"][0]["source"] == "vl_schema_prompt_repair"
    assert payload["tables"][0]["rows"][0]["item_name"] == "스테인리스 브라켓"
    assert fake_pipeline.calls
    assert len(calls) == 3
