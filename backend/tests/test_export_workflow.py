import csv
import html
import json
from decimal import Decimal
from datetime import date
from io import BytesIO, StringIO
from zipfile import ZipFile

import pytest

from app.models.document import Document, DocumentType, ExportTemplate, ProcessingStatus
from app.services.export import document_read_safety_overrides, document_to_json, documents_to_csv, documents_to_excel, export_blocked_documents, tax_invoice_to_draft_xml


def _document(number: str, customer: str, amount: Decimal) -> Document:
    return Document(
        original_filename=f"{number}.pdf",
        stored_file_path=f"/tmp/{number}.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.invoice,
        document_number=number,
        vendor_name="공급사",
        customer_name=customer,
        issue_date=date(2026, 8, 3),
        subtotal=amount,
        tax=Decimal("0"),
        extracted_amount=amount,
        currency="KRW",
        processing_status=ProcessingStatus.ready,
        line_items=[{"item_name": "품목", "quantity": 1, "unit_price": amount, "supply_amount": amount, "tax_amount": 0, "line_total": amount}],
    )


def test_export_uses_only_documents_passed_by_caller():
    selected = [_document("INV-1", "네오팩토리", Decimal("100"))]
    csv = documents_to_csv(selected)

    assert "INV-1" in csv
    assert "INV-2" not in csv


def test_export_policy_blocks_unconfirmed_documents():
    document = _document("INV-1", "네오팩토리", Decimal("100"))

    blocked = export_blocked_documents([document])

    assert blocked == [{"id": str(document.id), "title": "INV-1.pdf", "processing_status": "ready"}]


def test_csv_export_applies_custom_template_columns_and_line_rows():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.line_items = [
        {"item_name": "S45C PIN", "specification": "8X60", "quantity": 2, "note": "입고대기"},
        {"item_name": "SUS 볼트", "specification": "M5X20", "quantity": 5, "note": "정상"},
    ]
    template = ExportTemplate(
        name="현장 검토용",
        template_columns=[
            {"header": "거래일자", "source_field": "document_date"},
            {"header": "거래처", "source_field": "customer_name"},
            {"header": "품목", "source_field": "line_items.item_name"},
            {"header": "규격", "source_field": "line_items.specification"},
            {"header": "수량", "source_field": "line_items.quantity"},
            {"header": "빈칸", "source_field": "__blank__"},
            {"header": "창고", "source_field": "__static__", "column_type": "static", "static_value": "A-01"},
        ],
    )

    rows = list(csv.DictReader(StringIO(documents_to_csv([document], template=template))))

    assert list(rows[0].keys()) == ["거래일자", "거래처", "품목", "규격", "수량", "빈칸", "창고"]
    assert len(rows) == 2
    assert rows[0]["품목"] == "S45C PIN"
    assert rows[1]["품목"] == "SUS 볼트"
    assert rows[0]["빈칸"] == ""
    assert rows[0]["창고"] == "A-01"


def test_excel_export_applies_template_headers_and_party_tabs():
    docs = [_document("INV-1", "네오팩토리", Decimal("100")), _document("INV-2", "오성테크", Decimal("200"))]
    template = ExportTemplate(
        name="더존 업로드용",
        template_columns=[
            {"header": "거래처", "source_field": "customer_name"},
            {"header": "품목명", "source_field": "line_items.item_name"},
            {"header": "없는필드", "source_field": "line_items.not_a_field"},
        ],
    )

    with ZipFile(BytesIO(documents_to_excel(docs, sheet_mode="party_tabs", template=template))) as archive:
        xml_payload = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml"))
    decoded_payload = html.unescape(xml_payload)

    assert "네오팩토리" in decoded_payload
    assert "오성테크" in decoded_payload
    assert "품목명" in decoded_payload
    assert "없는필드" in decoded_payload
    assert "거래처 탭" not in decoded_payload


def test_excel_export_can_split_by_party_tabs():
    docs = [_document("INV-1", "네오팩토리", Decimal("100")), _document("INV-2", "오성테크", Decimal("200"))]
    with ZipFile(BytesIO(documents_to_excel(docs, sheet_mode="party_tabs"))) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")

    assert "네오팩토리" in workbook_xml
    assert "오성테크" in workbook_xml


def test_tax_invoice_xml_draft_validates_amounts_before_export():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    xml = tax_invoice_to_draft_xml(document).decode("utf-8")

    assert "TaxInvoiceDraft" in xml
    assert "INV-1" in xml

    document.extracted_amount = Decimal("90")
    with pytest.raises(ValueError):
        tax_invoice_to_draft_xml(document)


def test_csv_export_appends_taxonomy_policy_columns_without_breaking_existing_columns():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "tax_invoice",
            "document_profile": "tax_document",
            "document_profiles": ["tax_document", "priced_document"],
            "layout_profile": "text_layer_pdf",
            "amount_required": True,
            "party_required": True,
            "evidence": ["세금계산서"],
        },
        "normalized_review_issues": [{"code": "internal_item_ambiguous", "message_ko": "품목 후보 확인 필요", "item_index": 0}],
    }

    csv = documents_to_csv([document])

    assert "문서유형,공급업체,고객사" in csv
    assert "document_subtype" in csv
    assert "document_profile" in csv
    assert "document_profiles" in csv
    assert "layout_profile" in csv
    assert "amount_required" in csv
    assert "export_policy" in csv
    assert "line_review_flags" in csv
    assert "tax_invoice" in csv
    assert "tax_document" in csv
    assert "internal_item_ambiguous" in csv


def test_json_export_includes_canonical_taxonomy_and_policy():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "tax_invoice",
            "document_profile": "tax_document",
            "document_profiles": ["tax_document", "priced_document"],
            "amount_required": True,
            "party_required": True,
        }
    }

    payload = json.loads(document_to_json(document))

    assert payload["document_taxonomy"]["document_subtype"] == "tax_invoice"
    assert payload["export_policy"]["export_policy"] == "tax_document_consistency"
    assert payload["canonical_export"]["document"]["document_subtype"] == "tax_invoice"
    assert payload["canonical_export"]["policy"]["amount_required"] is True
    assert payload["canonical_export"]["line_items"][0]["line_index"] == 1


def test_internal_transfer_export_treats_missing_amounts_as_policy_not_fake_total():
    document = Document(
        original_filename="TRF.pdf",
        stored_file_path="/tmp/TRF.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="TRF-2026-0922-002",
        processing_status=ProcessingStatus.needs_review,
        review_required=True,
        currency=None,
        extracted_amount=None,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "internal_transfer",
                "document_profile": "inventory_movement_document",
                "document_profiles": ["inventory_movement_document", "no_price_document"],
                "amount_required": False,
                "party_required": False,
            }
        },
        line_items=[{"item_name": "베어링 하우징", "quantity": 12, "unit": "EA"}],
    )

    csv = documents_to_csv([document])
    payload = json.loads(document_to_json(document))

    assert payload["canonical_export"]["policy"]["amount_required"] is False
    assert payload["canonical_export"]["policy"]["party_required"] is False
    assert payload["canonical_export"]["policy"]["review_required"] is True
    assert payload["canonical_export"]["document"]["currency"] is None
    assert payload["canonical_export"]["document"]["total"] == ""
    assert "inventory_movement_no_price" in csv
    assert "amount_not_required" in csv
    assert "TRF-2026-0922-002" in csv


def test_credit_note_export_preserves_review_warning_and_related_document():
    document = Document(
        original_filename="RTN.pdf",
        stored_file_path="/tmp/RTN.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="RTN-2026-0919-011",
        vendor_name="대영부품",
        customer_name="오성테크",
        extracted_amount=Decimal("12100"),
        currency="KRW",
        processing_status=ProcessingStatus.needs_review,
        review_required=True,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "business_fields": {"related_document_number": "DN-2026-0914-2F"},
        },
        line_items=[{"item_name": "반품품목", "line_total": Decimal("12100")}],
    )

    payload = json.loads(document_to_json(document))
    csv = documents_to_csv([document])

    assert payload["canonical_export"]["document"]["document_subtype"] == "credit_note"
    assert payload["canonical_export"]["policy"]["related_document_number"] == "DN-2026-0914-2F"
    assert payload["canonical_export"]["policy"]["review_required"] is True
    assert "amount_direction_requires_review" in payload["canonical_export"]["policy"]["export_warning"]
    assert "return_or_credit_review" in csv


def test_csv_export_does_not_copy_document_total_into_untrusted_line_amounts():
    document = _document("INV-US-2026-0916-EX", "NeoFactory Korea", Decimal("650"))
    document.currency = "USD"
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "commercial_invoice",
            "document_profile": "foreign_currency_document",
            "document_profiles": ["foreign_currency_document", "priced_document"],
        }
    }
    document.line_items = [
        {
            "item_name": "Linear Guide Rail HGW20",
            "quantity": 10,
            "unit": "EA",
            "document_item_code": "HGW20-1000",
            "validation_warnings": ["untrusted_ocr_amount"],
        }
    ]

    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert rows[0]["합계금액"] == ""
    assert rows[0]["document_total"] == "650"
    assert rows[0]["export_policy"] == "foreign_currency_document"


def test_export_dedupes_line_items_and_blocks_invalid_tax_amounts():
    document = _document("INV-1", "네오팩토리", Decimal("110"))
    document.line_items = [
        {"item_name": "고추장 소스", "quantity": 1, "supply_amount": 100, "tax_amount": 10, "line_total": 110},
        {"item_name": "고추장 소스", "quantity": 1, "supply_amount": 100, "tax_amount": 10, "line_total": 110},
        {
            "item_name": "월일 밀린 행",
            "quantity": 1,
            "supply_amount": 100,
            "tax_amount": 900,
            "line_total": 1000,
            "validation_warnings": ["invalid_tax_greater_than_supply"],
        },
    ]

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert len(payload["canonical_export"]["line_items"]) == 2
    assert payload["canonical_export"]["line_items"][1]["supply_amount"] is None
    assert rows[1]["공급가액"] == ""
    assert rows[1]["세액"] == ""


def test_export_dedupes_numeric_string_review_duplicate_rows():
    document = _document("INV-1", "네오팩토리", Decimal("19250"))
    document.line_items = [
        {
            "item_name": "평와셔 M5",
            "document_item_code": "HB-WH-014",
            "specification": "M5",
            "quantity": 500,
            "unit_price": 35,
            "supply_amount": 17500,
            "tax_amount": 1750,
            "line_total": 19250,
            "review_flags": ["ai_parsed_document_table_review_required"],
        },
        {
            "item_name": "평와셔 M5",
            "document_item_code": "HB-WH-014",
            "specification": "M5",
            "quantity": "500",
            "unit_price": "35",
            "supply_amount": "17,500",
            "tax_amount": "1,750",
            "line_total": "19,250",
            "review_flags": ["ai_parsed_document_table_review_required"],
        },
    ]

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert len(payload["canonical_export"]["line_items"]) == 1
    assert len(rows) == 1


def test_receipt_dirty_line_items_are_review_candidates_not_export_rows():
    document = _document("RC-1", "가온마트", Decimal("9000"))
    document.document_type = DocumentType.receipt
    document.tags = ["receipt"]
    document.line_items = [
        {
            "item_name": "영수증 품목 후보",
            "quantity": 1,
            "line_total": 9000,
            "validation_warnings": ["receipt_row_review_required"],
        }
    ]
    document.workflow_metadata = {"receipt_item_candidates": [{"item_name": "영수증 품목 후보", "status": "review_only"}]}

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert payload["canonical_export"]["line_items"] == []
    assert payload["canonical_export"]["review_candidates"]["receipt_item_candidates"]
    assert rows[0]["품목명"] == ""


def test_receipt_dirty_flags_and_item_name_blacklist_are_not_exported():
    document = _document("RC-1", "가온마트", Decimal("9000"))
    document.document_type = DocumentType.receipt
    document.tags = ["receipt"]
    document.line_items = [
        {"item_name": "수기 후보", "line_total": 1000, "review_flags": ["handwritten_requires_review"]},
        {"item_name": "끝 숫자 후보", "line_total": 2000, "review_flags": ["trailing_number_requires_review"]},
        {"item_name": "3EA X", "line_total": 3000},
        {"item_name": "공급기액", "line_total": 3000},
    ]

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert payload["canonical_export"]["line_items"] == []
    assert rows[0]["품목명"] == ""


def test_invalid_line_amount_rows_are_excluded_from_export():
    document = _document("INV-1", "네오팩토리", Decimal("110"))
    document.line_items = [
        {"item_name": "정상 행", "quantity": 1, "supply_amount": 100, "tax_amount": 10, "line_total": 110},
        {
            "item_name": "월일 밀린 행",
            "quantity": 6,
            "supply_amount": 8,
            "tax_amount": 73600,
            "line_total": 7360,
            "validation_warnings": ["invalid_line_amount"],
        },
    ]

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert [item["item_name"] for item in payload["canonical_export"]["line_items"]] == ["정상 행"]
    assert [row["품목명"] for row in rows] == ["정상 행"]


def test_return_credit_source_conflict_blocks_priced_export_values_and_template_rows():
    document = _document("PO-2026-0001", "대성정공", Decimal("95150"))
    document.original_filename = "DOC-009_return_credit_blurry_uncropped_photo.pdf"
    document.document_type = DocumentType.purchase_order
    document.title = "반품/크레딧 후보"
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "purchase_order",
            "document_profile": "priced_document",
            "document_profiles": ["priced_document"],
        },
        "ai_parsed_document": {"document_type_hint": "return_credit"},
    }
    document.line_items = [{"item_name": "S45C PIN", "quantity": 120, "supply_amount": 42000, "tax_amount": 4200, "line_total": 46200}]
    template = ExportTemplate(
        name="금액 템플릿",
        template_columns=[
            {"header": "문서번호", "source_field": "document_number"},
            {"header": "품목", "source_field": "line_items.item_name"},
            {"header": "합계", "source_field": "line_items.line_total"},
            {"header": "문서합계", "source_field": "total_amount"},
            {"header": "통화", "source_field": "currency"},
        ],
    )

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))
    template_rows = list(csv.DictReader(StringIO(documents_to_csv([document], template=template))))

    assert payload["canonical_export"]["line_items"] == []
    assert payload["canonical_export"]["document"]["total"] is None
    assert payload["canonical_export"]["document"]["currency"] is None
    assert document_read_safety_overrides(document)["line_items"] == []
    assert document_read_safety_overrides(document)["extracted_amount"] is None
    assert payload["canonical_export"]["policy"]["export_blocked"] is True
    assert "return_credit_source_conflicts_with_priced_export_blocked" in payload["canonical_export"]["policy"]["export_warning"]
    assert rows[0]["품목명"] == ""
    assert rows[0]["합계금액"] == ""
    assert rows[0]["document_total"] == ""
    assert template_rows[0]["품목"] == ""
    assert template_rows[0]["합계"] == ""
    assert template_rows[0]["문서합계"] == ""
    assert template_rows[0]["통화"] == ""


def test_excel_export_contains_taxonomy_columns_in_combined_sheet():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {"taxonomy": {"document_subtype": "commercial_invoice", "document_profile": "foreign_currency_document", "document_profiles": ["foreign_currency_document"]}}

    with ZipFile(BytesIO(documents_to_excel([document], sheet_mode="combined"))) as archive:
        xml_payload = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml"))

    assert "document_subtype" in xml_payload
    assert "document_profile" in xml_payload
    assert "commercial_invoice" in xml_payload


def test_export_without_taxonomy_metadata_still_works():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = None
    document.ingestion_metadata = None

    csv = documents_to_csv([document])
    payload = json.loads(document_to_json(document))

    assert "INV-1" in csv
    assert "document_subtype" in csv
    assert payload["canonical_export"]["document"]["document_type"] == "invoice"
    assert payload["canonical_export"]["policy"]["export_policy"] in {"priced_document", "tax_document_consistency"}


def test_export_includes_bbox_layout_candidate_summary_without_line_item_promotion():
    document = _document("FAX-PO-2026-0921", "오성테크", Decimal("418000"))
    document.document_type = DocumentType.purchase_order
    document.processing_status = ProcessingStatus.needs_review
    document.review_required = True
    document.line_items = [
        {"item_name": "베어링하우징", "line_total": Decimal("176000")},
        {"item_name": "S45C PIN 8X60", "line_total": Decimal("66000")},
    ]
    document.workflow_metadata = {
        "layout_debug": {
            "source": "bbox_table_reconstructor",
            "parser_integrated": False,
            "reconstructed_candidate_count": 3,
            "candidate_count": 1,
            "confirmed_line_item_count": 2,
            "uncertain_count": 1,
            "bbox_review_flags": ["missing_item_name_from_ocr", "row_boundary_uncertain", "untrusted_ocr_amount"],
            "bbox_table_candidates": [
                {
                    "source": "bbox_table_reconstructor",
                    "item_name": None,
                    "supply_amount": 176000,
                    "review_flags": ["missing_item_name_from_ocr", "row_boundary_uncertain", "untrusted_ocr_amount"],
                    "missing_fields": ["item_name"],
                    "source_text": "16000 1600C 176000",
                }
            ],
        }
    }

    payload = json.loads(document_to_json(document))
    csv = documents_to_csv([document])

    assert len(payload["canonical_export"]["line_items"]) == 2
    assert payload["canonical_export"]["review_candidates"]["bbox_candidate_summary"]["candidate_count"] == 1
    assert payload["canonical_export"]["review_candidates"]["bbox_candidate_summary"]["uncertain_count"] == 1
    assert payload["canonical_export"]["review_candidates"]["bbox_table_candidates"][0]["item_name"] is None
    assert "missing_item_name_from_ocr" in payload["canonical_export"]["review_candidates"]["bbox_table_candidates"][0]["review_flags"]
    assert "bbox_candidate_count" in csv
    assert "bbox_uncertain_candidate_count" in csv
    assert "bbox_review_flags" in csv
    assert "missing_item_name_from_ocr" in csv


def test_export_includes_vl_candidates_without_line_item_promotion():
    document = _document("FAX-PO-2026-0921", "오성테크", Decimal("418000"))
    document.document_type = DocumentType.purchase_order
    document.processing_status = ProcessingStatus.needs_review
    document.review_required = True
    document.line_items = [
        {"item_name": "베어링 하우징", "line_total": Decimal("176000")},
        {"item_name": "S45C PIN 8X60", "line_total": Decimal("66000")},
    ]
    document.workflow_metadata = {
        "vl_candidates": [
            {
                "source": "paddleocr_vl_1_6_gguf",
                "provider": "paddleocr_vl_1_6_gguf",
                "provider_available_candidate": False,
                "validation_severity": "warn",
                "issue_codes": ["vl_candidate_missing_document_total"],
                "issue_details": [
                    {
                        "code": "vl_candidate_missing_document_total",
                        "expected_value": "418,000",
                        "message": "The source PDF total is missing in the VL output.",
                    }
                ],
                "text_preview": (
                    "/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/21/sample_page_1.png\n"
                    "number\n"
                    "seal\n"
                    "1 베어링 하우징 ... 2 S45C PIN ... 3 M8 볼트/와서 ...\n"
                    "footer_image\n"
                ),
                "structured_candidate": {
                    "candidate_only": True,
                    "parser_integrated": False,
                    "parser_evaluated": True,
                    "confirmed_promotion": False,
                    "document": {
                        "document_type": "purchase_order",
                        "document_number": "FAX-PO-2026-0921",
                        "total": None,
                    },
                    "line_items": [
                        {"item_name": "베어링 하우징", "line_total": "176000"},
                        {"item_name": "S45C PIN 8X60", "line_total": "66000"},
                    ],
                    "line_item_count": 2,
                    "issue_codes": ["vl_candidate_missing_document_total"],
                    "review_flags": ["vl_candidate_missing_document_total"],
                },
                "manual_visual_check_validation": {
                    "severity": "warn",
                    "issue_codes": ["vl_candidate_missing_document_total"],
                    "missing_expected_values": {"total_amount": "418,000"},
                },
            }
        ],
        "vl_candidate_summary": {
            "candidate_count": 1,
            "warning_count": 1,
            "failure_count": 0,
            "issue_codes": ["vl_candidate_missing_document_total"],
            "provider": "paddleocr_vl_1_6_gguf",
            "provider_available_candidate": False,
        },
    }

    payload = json.loads(document_to_json(document))
    rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))

    assert len(payload["canonical_export"]["line_items"]) == 2
    assert payload["canonical_export"]["review_candidates"]["vl_candidate_summary"]["candidate_count"] == 1
    assert payload["canonical_export"]["review_candidates"]["vl_candidate_summary"]["provider_available_candidate"] is False
    assert payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["candidate_only"] is True
    assert payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["parser_integrated"] is False
    structured = payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["structured_candidate"]
    assert structured["candidate_only"] is True
    assert structured["parser_integrated"] is False
    assert structured["confirmed_promotion"] is False
    assert structured["line_item_count"] == 2
    assert structured["line_items"][0]["item_name"] == "베어링 하우징"
    gate = payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["promotion_gate"]
    assert gate["decision"] == "review_required"
    assert gate["auto_promote"] is False
    assert "vl_candidate_has_review_issues" in gate["reasons"]
    text_preview = payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["text_preview"]
    assert text_preview.startswith("1 베어링")
    assert "sample_page_1.png" not in text_preview
    assert "number" not in text_preview
    assert "seal" not in text_preview
    assert "footer_image" not in text_preview
    assert payload["canonical_export"]["review_candidates"]["vl_candidates"][0]["issue_details"][0]["expected_value"] == "418,000"
    assert "vl_candidate_review_required" in payload["canonical_export"]["policy"]["export_warning"]
    assert rows[0]["vl_candidate_count"] == "1"
    assert rows[0]["vl_candidate_warning_count"] == "1"
    assert rows[0]["vl_candidate_failure_count"] == "0"
    assert rows[0]["vl_candidate_issue_codes"] == "vl_candidate_missing_document_total"
    assert rows[0]["vl_candidate_provider"] == "paddleocr_vl_1_6_gguf"
    assert rows[0]["vl_candidate_gate_decision"] == "review_required"
    assert rows[0]["vl_candidate_gate_reasons"] == "vl_candidate_has_review_issues, provider_candidate_not_available"
