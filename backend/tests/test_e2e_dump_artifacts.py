from pathlib import Path
import json
from types import SimpleNamespace

from app.scripts.extract_image_pdf_sample_fixtures import _cleanup_api_document_if_requested, _write_api_dumps


def test_write_api_dumps_creates_detail_and_export_json(tmp_path):
    pdf_path = Path("21_photo_fax_po_misaligned_amounts.pdf")
    detail_dir = tmp_path / "document_details"
    export_dir = tmp_path / "document_exports"
    payload = {
        "current_parsed": {
            "id": "doc-1",
            "line_items": [{"item_name": "S45C PIN 8X60"}],
            "workflow_metadata": {
                "layout_debug": {
                    "parser_integrated": False,
                    "bbox_table_candidates": [],
                }
            },
        },
        "export_json": {
            "canonical_export": {
                "review_candidates": {
                    "bbox_candidate_summary": {"candidate_count": 3},
                }
            }
        },
    }

    _write_api_dumps(pdf_path, payload, detail_dir, export_dir)

    detail = json.loads((detail_dir / "21_photo_fax_po_misaligned_amounts.json").read_text())
    export = json.loads((export_dir / "21_photo_fax_po_misaligned_amounts.json").read_text())
    assert detail["workflow_metadata"]["layout_debug"]["parser_integrated"] is False
    assert export["canonical_export"]["review_candidates"]["bbox_candidate_summary"]["candidate_count"] == 3


def test_delete_after_dump_cleanup_calls_document_delete(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.get_method()))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    payload = {"provider_metadata": {"api_document_id": "doc-123"}}
    args = SimpleNamespace(delete_after_dump=True, mode="api", api_base="http://backend/api")

    assert _cleanup_api_document_if_requested(payload, args) is None
    assert calls == [("http://backend/api/documents/doc-123", "DELETE")]


def test_delete_after_dump_cleanup_is_opt_in(monkeypatch):
    def fake_urlopen(request, timeout):  # pragma: no cover - should not be called
        raise AssertionError("cleanup should be opt-in")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    payload = {"provider_metadata": {"api_document_id": "doc-123"}}
    args = SimpleNamespace(delete_after_dump=False, mode="api", api_base="http://backend/api")

    assert _cleanup_api_document_if_requested(payload, args) is None
