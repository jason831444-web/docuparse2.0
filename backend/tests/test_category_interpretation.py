from app.models.document import Document, DocumentType
from app.services.category_interpretation import CategoryInterpretationService
from app.services.category_taxonomy import normalize_category_value
from app.services.parser import DocumentParser


def test_installation_guide_beats_person_name_profile_signal():
    text = """
    Sihoon
    Project Setup Guide
    Installation
    Prerequisites
    Docker
    Environment Variables
    Database
    Run this command to start the API server.
    """

    parsed = DocumentParser().parse(text, "project_setup.pdf")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="project_setup.pdf",
            stored_file_path="/tmp/project_setup.pdf",
            mime_type="application/pdf",
            document_type=parsed.document_type,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "installation_guide"
    assert interpretation.profile == "installation_guide"
    assert interpretation.category == "installation_guide"
    assert "setup" in (interpretation.title_hint or "").lower()


def test_spreadsheet_tracker_beats_profile_token():
    text = """
    Sheet: Implementation Schedule
    Feature | Task | Status | Claimed | Testing | Coverage | Pipeline
    Student profile API (/students/{id}) | Implement endpoint | In Progress | Alex | API tests | 80% | Passing
    Search filters | Verify category consistency | Open | Sam | Regression tests | 70% | Pending
    """

    parsed = DocumentParser().parse(text, "implementation_schedule.xlsx")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="implementation_schedule.xlsx",
            stored_file_path="/tmp/implementation_schedule.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            source_file_type="xlsx",
            document_type=DocumentType.document,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "implementation_schedule"
    assert interpretation.profile == "implementation_schedule"
    assert interpretation.category == "implementation_schedule"
    assert "schedule" in (interpretation.title_hint or "").lower()


def test_category_normalization_keeps_edit_and_filter_values_consistent():
    assert normalize_category_value("Engineering Planning") == "implementation_schedule"
    assert normalize_category_value("Project Tracker") == "implementation_schedule"
    assert normalize_category_value("parent>Setup Guide") == "installation_guide"


def test_purchase_order_key_value_line_item_block_is_extracted():
    text = """
    발주서

    공급업체: 대한정밀부품
    고객사: 한빛제조
    발주번호: PO-2026-0603
    발행일: 2026-06-03
    납기일: 2026-06-10

    품목명: M8 육각 볼트
    품목코드: BOLT-M8-20
    규격: M8x20
    수량: 500
    단위: EA
    단가: 120
    공급가액: 60000
    세액: 6000
    합계금액: 66000
    """

    parsed = DocumentParser().parse(text, "sample_po.txt")

    assert parsed.document_type == DocumentType.purchase_order
    assert parsed.vendor_name == "대한정밀부품"
    assert parsed.customer_name == "한빛제조"
    assert parsed.document_number == "PO-2026-0603"
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["item_name"] == "M8 육각 볼트"
    assert parsed.line_items[0]["item_code"] == "BOLT-M8-20"
    assert parsed.line_items[0]["quantity"] == 500
    assert parsed.line_items[0]["unit_price"] == 120
    assert parsed.line_items[0]["line_total"] == 66000


def test_purchase_order_table_line_item_row_is_extracted():
    text = """
    발주서
    공급업체: 대한정밀부품
    고객사: 한빛제조
    발주번호: PO-2026-0603
    품목명 | 품목코드 | 규격 | 수량 | 단위 | 단가 | 공급가액 | 세액 | 합계금액
    M8 육각 볼트 | BOLT-M8-20 | M8x20 | 500 | EA | 120 | 60000 | 6000 | 66000
    """

    parsed = DocumentParser().parse(text, "sample_po.txt")

    assert parsed.document_type == DocumentType.purchase_order
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["specification"] == "M8x20"
    assert parsed.line_items[0]["unit"] == "EA"
    assert parsed.line_items[0]["supply_amount"] == 60000
    assert parsed.line_items[0]["tax_amount"] == 6000
