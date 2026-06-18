from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.item_master_matcher import ItemMasterMatcher, normalize_item_text, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService
from app.models.document import Document
from app.schemas.item_master import ItemAliasCreate, ItemAliasUpdate, ItemMasterCreate, ItemMasterUpdate
from app.api.routes import item_master as item_master_routes


ROOT = Path(__file__).resolve().parents[2]
MANUFACTURING_ROOT = ROOT / "samples" / "manufacturing"


def _masters():
    rows, errors = parse_item_master_csv((ROOT / "samples" / "item_master_basic.csv").read_bytes())
    assert not errors
    return [SimpleNamespace(**row) for row in rows]


def _parsed_document(filename: str) -> tuple[str, object, Document]:
    text = (MANUFACTURING_ROOT / filename).read_text()
    parsed = DocumentParser().parse(text, filename)
    document = Document(
        original_filename=filename,
        stored_file_path=f"/tmp/{filename}",
        mime_type="text/plain",
        document_type=parsed.document_type,
        vendor_name=parsed.vendor_name,
        customer_name=parsed.customer_name,
        document_number=parsed.document_number,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        extracted_amount=parsed.extracted_amount,
        currency=parsed.currency,
        line_items=parsed.line_items,
    )
    return text, parsed, document


def test_item_master_csv_parse_sample_file():
    rows, errors = parse_item_master_csv((ROOT / "samples" / "item_master_basic.csv").read_bytes())

    assert errors == []
    assert len(rows) == 10
    assert rows[0]["internal_item_code"] == "M-001"
    assert rows[0]["normalized_item_name"]
    assert rows[2]["standard_price"] == 120


def test_no_item_master_matching_keeps_codes_empty_and_adds_single_review_issue():
    text, _, document = _parsed_document("09_item_matching_ambiguous.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, [])
    document.review_required = True

    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    messages = [issue["message_ko"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert all(item.get("item_master_match_status") == "skipped_no_item_master" for item in document.line_items)
    assert all(item.get("internal_item_code") in (None, "") for item in document.line_items)
    assert all("품목코드" not in str(item.get("internal_item_code") or "") for item in document.line_items)
    assert messages.count("내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.") == 1


def test_item_master_matching_auto_matches_known_purchase_order_items():
    _, _, document = _parsed_document("01_purchase_order_basic.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    assert document.line_items[0]["internal_item_code"] == "BOLT-M8-20"
    assert document.line_items[0]["item_master_match_status"] == "direct_code_match"
    assert document.line_items[1]["internal_item_code"] == "WASH-SUS-08"
    assert document.line_items[1]["item_master_match_status"] == "direct_code_match"


def test_item_master_matching_auto_matches_quotation_items():
    _, _, document = _parsed_document("03_quotation.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    assert document.line_items[0]["internal_item_code"] == "BRK-SUS-01"
    assert document.line_items[1]["internal_item_code"] == "PLT-FIX-02"


def test_item_master_normalization_handles_ocr_o_zero_material_grade_confusion():
    assert normalize_item_text("SUS3O4 2T PLATE") == normalize_item_text("SUS304 2T PLATE")
    assert normalize_item_text("STS3O4") == normalize_item_text("SUS304")


def test_item_master_matching_uses_code_prefix_with_explicit_thickness_to_resolve_ocr_truncated_code():
    masters = [
        SimpleNamespace(
            internal_item_code="M-PLT-SUS304-2T-1000X2000",
            item_name="SUS304 스테인리스 판재 2.0T 1000x2000",
            normalized_item_name=normalize_item_text("SUS304 스테인리스 판재 2.0T 1000x2000"),
            spec="2.0T 1000x2000",
            unit="EA",
            standard_price=Decimal("25000"),
            active=True,
            aliases=[],
        ),
        SimpleNamespace(
            internal_item_code="M-PLT-SUS304-3T-1000X2000",
            item_name="SUS304 스테인리스 판재 3.0T 1000x2000",
            normalized_item_name=normalize_item_text("SUS304 스테인리스 판재 3.0T 1000x2000"),
            spec="3.0T 1000x2000",
            unit="EA",
            standard_price=Decimal("37000"),
            active=True,
            aliases=[],
        ),
    ]
    matched = ItemMasterMatcher().match_line_items_against_masters(
        [
            {
                "item_name": "SUS3O4 2T PLATE",
                "item_code": "M-PLT-SUS304-",
                "specification": "M8x20",
            }
        ],
        masters,
    )[0]

    assert matched["internal_item_code"] == "M-PLT-SUS304-2T-1000X2000"
    assert matched["item_name"] == "SUS304 2T PLATE"
    assert "specification" not in matched
    assert matched["source_specification"] == "M8x20"
    assert matched["matched_master_spec"] == "2.0T 1000x2000"
    assert "specification_conflict_with_item_master" in matched["review_flags"]
    assert matched["item_master_match_status"] == "auto_matched"
    assert matched["item_master_match_reason"] == "PARTIAL_DOCUMENT_CODE_WITH_EXPLICIT_NAME_SPEC_MATCH"
    assert matched["item_master_candidates"][0]["prefix_code_match"] is True


def test_item_master_matching_preserves_visible_inspection_spec_when_master_differs():
    masters = [
        SimpleNamespace(
            id=uuid4(),
            internal_item_code="INSP-BH-200",
            item_name="베어링 하우징",
            normalized_item_name=normalize_item_text("베어링 하우징"),
            spec="STD-999",
            unit="EA",
            standard_price=None,
            active=True,
            aliases=[],
        )
    ]

    matched = ItemMasterMatcher().match_line_items_against_masters(
        [
            {
                "item_name": "베어링 하우징",
                "item_code": "INSP-BH-200",
                "specification": "BH-220",
                "quantity": 80,
                "received_quantity": 80,
                "accepted_quantity": 78,
                "defective_quantity": 2,
                "inspection_result": "조건부합격",
                "unit": "EA",
            }
        ],
        masters,
    )[0]

    assert matched["internal_item_code"] == "INSP-BH-200"
    assert matched["specification"] == "BH-220"
    assert matched["source_specification"] == "BH-220"
    assert matched["matched_master_spec"] == "STD-999"
    assert matched["specification_review_required"] is True
    assert "specification_conflict_with_item_master" in matched["review_flags"]


def test_ambiguous_sus_items_include_m001_candidate_without_forcing_code():
    _, _, document = _parsed_document("09_item_matching_ambiguous.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    for item in document.line_items:
        candidate_codes = [candidate["internal_item_code"] for candidate in item.get("item_master_candidates", [])]
        assert "M-001" in candidate_codes
        assert "품목코드" not in str(item.get("internal_item_code") or "")


def test_mixed_key_value_item_block_preserves_multiline_name_and_numeric_fields():
    text = """
    Purchase Order
    PO No: PO-2026-0728
    Issue Date: 2026.07.20
    Requested Delivery Date: 2026년 7월 28일
    Supplier: Alpha Parts
    Buyer: Hanbit Manufacturing

    [ITEM TABLE START]
    item name: SUS304
    2T
    PLATE
    vendor sku: M-001
    spec: 1000x2000
    qty: 1,200 EA
    unit price: 25,000 KRW
    subtotal: 30,000,000
    vat: 3,000,000
    total: 33,000,000
    [ITEM TABLE END]

    공급가액 합계: 30,000,000
    세액 합계: 3,000,000
    총 합계: 33,000,000
    """

    parsed = DocumentParser().parse(text, "po.txt")
    item = parsed.line_items[0]

    assert parsed.document_number == "PO-2026-0728"
    assert parsed.issue_date.isoformat() == "2026-07-20"
    assert parsed.due_date.isoformat() == "2026-07-28"
    assert parsed.extracted_amount == 33000000
    assert parsed.subtotal == 30000000
    assert parsed.tax == 3000000
    assert parsed.currency == "KRW"
    assert item["item_name"] == "SUS304 2T PLATE"
    assert item["item_code"] == "M-001"
    assert item["document_item_code"] == "M-001"
    assert item["quantity"] == 1200
    assert item["unit"] == "EA"
    assert item["unit_price"] == 25000
    assert item["supply_amount"] == 30000000
    assert item["tax_amount"] == 3000000
    assert item["line_total"] == 33000000


def test_invoice_total_uses_document_level_total_and_vendor_sku_is_document_item_code():
    text = """
    Invoice
    Invoice No: INV-10
    Date: 2026-07-01
    Due Date: 2026-07-31
    Vendor: Cable Inc
    Customer: Neo Factory

    [ITEM TABLE START]
    item name: Cable Harness
    vendor sku: CBL-HAR-500
    qty: 2 EA
    unit price: USD 200.00
    subtotal: USD 400.00
    vat: USD 40.00
    total: USD 440.00
    item name: Connector
    vendor sku: CON-PCB-12P
    qty: 4 EA
    unit price: USD 17.00
    subtotal: USD 68.00
    vat: USD 0.00
    total: USD 68.00
    [ITEM TABLE END]

    Invoice Total: USD 508.00
    """

    parsed = DocumentParser().parse(text, "invoice.txt")

    assert parsed.document_number == "INV-10"
    assert parsed.currency == "USD"
    assert parsed.extracted_amount == 508
    assert [item["item_code"] for item in parsed.line_items] == ["CBL-HAR-500", "CON-PCB-12P"]
    assert parsed.line_items[0]["line_total"] == 440
    assert parsed.line_items[1]["line_total"] == 68


def test_item_master_exact_code_alias_ambiguous_and_unmatched_statuses_are_exclusive():
    masters = [
        SimpleNamespace(
            internal_item_code="CANON-001",
            item_name="Canonical Cable",
            normalized_item_name="canonicalcable",
            spec="500mm",
            unit="EA",
            standard_price=100,
            aliases=["VENDOR-CABLE-001"],
        ),
        SimpleNamespace(
            internal_item_code="TIE-001",
            item_name="Tie Plate A",
            normalized_item_name="tieplate",
            spec="100",
            unit="EA",
            standard_price=10,
            aliases=[],
        ),
        SimpleNamespace(
            internal_item_code="TIE-002",
            item_name="Tie Plate B",
            normalized_item_name="tieplate",
            spec="100",
            unit="EA",
            standard_price=10,
            aliases=[],
        ),
    ]
    matcher = ItemMasterMatcher()

    direct, alias, ambiguous, unmatched = matcher.match_line_items_against_masters(
        [
            {"item_name": "Different", "item_code": "CANON-001", "quantity": 1, "unit": "EA", "unit_price": 100, "line_total": 100},
            {"item_name": "Different", "item_code": "VENDOR-CABLE-001", "quantity": 1, "unit": "EA", "unit_price": 100, "line_total": 100},
            {"item_name": "Tie Plate", "quantity": 1, "unit": "EA", "unit_price": 10, "line_total": 10},
            {"item_name": "No Possible Match", "quantity": 1, "unit": "EA", "unit_price": 999, "line_total": 999},
        ],
        masters,
    )

    assert direct["item_master_match_status"] == "direct_code_match"
    assert direct["internal_item_code"] == "CANON-001"
    assert alias["item_master_match_status"] == "alias_matched"
    assert alias["internal_item_code"] == "CANON-001"
    assert ambiguous["item_master_match_status"] == "ambiguous"
    assert ambiguous.get("internal_item_code") in (None, "")
    assert unmatched["item_master_match_status"] == "unmatched"
    assert unmatched.get("internal_item_code") in (None, "")


def test_active_alias_records_are_used_and_inactive_aliases_are_ignored():
    masters = [
        SimpleNamespace(
            internal_item_code="CANON-001",
            item_name="Canonical Cable",
            normalized_item_name="canonicalcable",
            spec="500mm",
            unit="EA",
            standard_price=100,
            aliases=[],
            alias_records=[
                SimpleNamespace(alias_name="거래처 케이블", alias_spec="500mm", active=True),
                SimpleNamespace(alias_name="비활성 케이블", alias_spec="500mm", active=False),
            ],
        )
    ]
    matcher = ItemMasterMatcher()

    active_match = matcher.match_line_items_against_masters(
        [{"item_name": "거래처 케이블", "specification": "500mm", "quantity": 1, "unit": "EA", "unit_price": 100, "line_total": 100}],
        masters,
    )[0]
    inactive_match = matcher.match_line_items_against_masters(
        [{"item_name": "비활성 케이블", "specification": "500mm", "quantity": 1, "unit": "EA", "unit_price": 100, "line_total": 100}],
        masters,
    )[0]

    assert active_match["item_master_match_status"] == "alias_matched"
    assert active_match["internal_item_code"] == "CANON-001"
    assert inactive_match["item_master_match_status"] in {"ambiguous", "unmatched"}
    assert inactive_match.get("internal_item_code") in (None, "")


def test_missing_document_item_code_is_not_blocking_when_internal_item_is_matched():
    document = Document(
        original_filename="matched_without_vendor_code.txt",
        stored_file_path="/tmp/matched_without_vendor_code.txt",
        mime_type="text/plain",
        document_type=DocumentParser().parse("발주서\n발주번호: PO-1\n발행일: 2026-07-01\n납기일: 2026-07-10\n공급업체: A\n고객사: B", "x.txt").document_type,
        vendor_name="A",
        customer_name="B",
        document_number="PO-1",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 10),
        extracted_amount=Decimal("100"),
        currency="KRW",
        line_items=[
            {
                "item_name": "M8 육각 볼트",
                "quantity": 1,
                "unit": "EA",
                "unit_price": 100,
                "line_total": 100,
                "internal_item_code": "BOLT-M8-20",
                "item_master_match_status": "auto_matched",
            }
        ],
    )

    workflow = DocumentWorkflowEnrichmentService().enrich(document, "발주서")
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert workflow.workflow_metadata["review_required"] is False
    assert any(issue["code"] == "missing_document_item_code" and issue["severity"] == "info" for issue in issues)
    assert not any(issue["code"] in {"internal_item_unmatched", "internal_item_ambiguous"} for issue in issues)


class FakeItemMasterSession:
    def __init__(self):
        self.items = {}
        self.aliases = {}
        self.added = []
        self.commits = 0
        self.scalar_calls = 0

    def scalar(self, statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return None
        return next(iter(self.items.values()), None)

    def add(self, value):
        self.added.append(value)
        if hasattr(value, "internal_item_code"):
            self.items[value.id] = value
        if hasattr(value, "alias_name"):
            self.aliases[value.id] = value

    def commit(self):
        self.commits += 1

    def refresh(self, value):
        now = datetime.now(timezone.utc)
        if getattr(value, "id", None) is None:
            value.id = uuid4()
        if getattr(value, "created_at", None) is None:
            value.created_at = now
        if getattr(value, "updated_at", None) is None:
            value.updated_at = now
        if hasattr(value, "alias_records") and value.alias_records is None:
            value.alias_records = []
        if hasattr(value, "internal_item_code"):
            self.items[value.id] = value
        if hasattr(value, "alias_name"):
            self.aliases[value.id] = value
        return None

    def get(self, model, object_id):
        if model.__name__ == "ItemAlias":
            return self.aliases.get(object_id)
        return self.items.get(object_id)


def test_item_master_crud_and_alias_api_handlers():
    db = FakeItemMasterSession()
    created = item_master_routes.create_item_master_item(
        ItemMasterCreate(
            internal_item_code="API-001",
            item_name="API 테스트 품목",
            spec="10mm",
            unit="EA",
            category="테스트",
            standard_price=Decimal("100"),
            active=True,
        ),
        db,
    )
    item = db.added[0]

    assert created.internal_item_code == "API-001"
    assert item.normalized_item_name

    updated = item_master_routes.update_item_master_item(
        item.id,
        ItemMasterUpdate(item_name="API 수정 품목", active=False),
        db,
    )
    assert updated.item_name == "API 수정 품목"
    assert updated.active is False

    alias = item_master_routes.create_item_alias(
        item.id,
        ItemAliasCreate(alias_name="거래처 별칭", alias_spec="10mm", vendor_name="거래처A", source="manual", confidence=Decimal("1")),
        db,
    )
    alias_model = db.added[-1]
    assert alias.alias_name == "거래처 별칭"
    assert alias.normalized_alias_name

    updated_alias = item_master_routes.update_item_alias(alias_model.id, ItemAliasUpdate(memo="확인 완료", active=False), db)
    assert updated_alias.memo == "확인 완료"
    assert updated_alias.active is False
