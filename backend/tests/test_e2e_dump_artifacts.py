from pathlib import Path
import json

from app.scripts.extract_image_pdf_sample_fixtures import _write_api_dumps


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
