from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest

from app.services.parser import DocumentParser


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "image_pdf_samples"


def _fixture_prefixes() -> list[str]:
    if not FIXTURE_DIR.exists():
        return []
    return sorted(path.name.split("_", 1)[0] for path in FIXTURE_DIR.glob("*_ocr_text.txt"))


PREFIXES = _fixture_prefixes()


def _load_text(prefix: str) -> str:
    return (FIXTURE_DIR / f"{prefix}_ocr_text.txt").read_text(encoding="utf-8")


def _load_json(prefix: str, suffix: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{prefix}_{suffix}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse(prefix: str):
    text = _load_text(prefix)
    assert text.strip(), f"{prefix} fixture has no real OCR text"
    return DocumentParser().parse(text, f"{prefix}_image_pdf_fixture.pdf")


def _doc_type(parsed) -> str:
    value = parsed.document_type
    return value.value if hasattr(value, "value") else str(value)


def _amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _line_total_sum(parsed) -> Decimal:
    total = Decimal("0")
    for item in parsed.line_items:
        value = _amount(item.get("line_total") or item.get("total_amount") or item.get("total"))
        if value is not None:
            total += value
    return total


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _item_names(parsed) -> list[str]:
    return [str(item.get("item_name") or "") for item in parsed.line_items]


def _assert_no_bad_structured_values(parsed) -> None:
    payload = _json_text(parsed)
    assert "3.3333333333333335" not in payload
    for item in parsed.line_items:
        for field in ["quantity", "unit_price", "supply_amount", "tax_amount", "line_total"]:
            value = item.get(field)
            assert not isinstance(value, str) or "확인" not in value
        name = str(item.get("item_name") or "")
        assert not re.match(r"^\s*(?:\d{4,}(?:\.\d+)?\s+){1,3}\S", name), name
        assert "Supply Total" not in name
        assert "Supply Tota" not in name


pytestmark = pytest.mark.skipif(not PREFIXES, reason="Real OCR fixtures are not extracted yet. Run app.scripts.extract_image_pdf_sample_fixtures.")


@pytest.mark.parametrize("prefix", PREFIXES)
def test_real_ocr_fixture_matches_expected_core_fields(prefix: str):
    expected = _load_json(prefix, "expected")
    parsed = _parse(prefix)

    assert _doc_type(parsed) == expected["document_type"]
    if expected.get("vendor_name"):
        assert parsed.vendor_name == expected["vendor_name"]
    if expected.get("customer_name"):
        assert parsed.customer_name == expected["customer_name"]
    if expected.get("document_number"):
        assert parsed.document_number == expected["document_number"]
    if expected.get("currency"):
        assert parsed.currency == expected["currency"]
    if expected.get("extracted_amount") is not None:
        assert _amount(parsed.extracted_amount) == _amount(expected["extracted_amount"])

    exact_count = expected.get("line_item_count")
    min_count = expected.get("line_item_count_min")
    if exact_count is not None:
        assert len(parsed.line_items) == exact_count
    if min_count is not None:
        assert len(parsed.line_items) >= min_count

    _assert_no_bad_structured_values(parsed)


def test_real_ocr_invoice_vendor_sku_rows_are_arithmetically_consistent():
    if "03" not in PREFIXES:
        pytest.skip("03 fixture missing")
    parsed = _parse("03")

    assert parsed.document_number == "INV-2026-0803-332"
    assert len(parsed.line_items) == 3
    assert _line_total_sum(parsed) == Decimal("1606000")
    assert _amount(parsed.subtotal) == Decimal("1460000")
    assert _amount(parsed.tax) == Decimal("146000")

    row1, row2, row3 = parsed.line_items
    assert _amount(row1.get("quantity")) == Decimal("1500")
    assert _amount(row1.get("unit_price")) == Decimal("300")
    assert _amount(row1.get("supply_amount")) == Decimal("450000")
    assert _amount(row1.get("tax_amount")) == Decimal("45000")
    assert _amount(row1.get("line_total")) == Decimal("495000")

    assert not str(row2.get("item_name") or "").startswith("495000")
    assert _amount(row2.get("quantity")) == Decimal("350")
    assert _amount(row2.get("unit_price")) == Decimal("2200")
    assert _amount(row2.get("supply_amount")) == Decimal("770000")
    assert _amount(row2.get("tax_amount")) == Decimal("77000")
    assert _amount(row2.get("line_total")) == Decimal("847000")

    assert not re.match(r"^(?:77000|847000)\b", str(row3.get("item_name") or ""))
    assert not row3.get("item_code")
    assert _amount(row3.get("quantity")) == Decimal("30")
    assert _amount(row3.get("unit_price")) == Decimal("8000")
    assert _amount(row3.get("supply_amount")) == Decimal("240000")
    assert _amount(row3.get("tax_amount")) == Decimal("24000")
    assert _amount(row3.get("line_total")) == Decimal("264000")


def test_real_ocr_transaction_statement_rejects_implausible_decimal_quantities():
    if "05" not in PREFIXES:
        pytest.skip("05 fixture missing")
    parsed = _parse("05")

    assert parsed.document_number == "TS-2026-0805-451"
    assert parsed.currency == "KRW"
    assert len(parsed.line_items) == 4
    assert _line_total_sum(parsed) == Decimal("517000")

    row2 = parsed.line_items[1]
    assert _amount(row2.get("quantity")) == Decimal("6")
    assert _amount(row2.get("unit_price")) == Decimal("25000")
    assert _amount(row2.get("supply_amount")) == Decimal("150000")
    assert _amount(row2.get("tax_amount")) == Decimal("15000")
    assert _amount(row2.get("line_total")) == Decimal("165000")

    row4 = parsed.line_items[3]
    assert "M8" in str(row4.get("item_name") or "").upper()
    assert _amount(row4.get("quantity")) == Decimal("500")
    assert _amount(row4.get("unit_price")) == Decimal("120")
    assert _amount(row4.get("supply_amount")) == Decimal("60000")
    assert _amount(row4.get("tax_amount")) == Decimal("6000")
    assert _amount(row4.get("line_total")) == Decimal("66000")


def test_real_ocr_usd_invoice_keeps_full_document_number_and_items():
    if "06" not in PREFIXES:
        pytest.skip("06 fixture missing")
    parsed = _parse("06")

    assert _doc_type(parsed) == "invoice"
    assert parsed.document_number == "INV-US-2026-0806-019"
    assert parsed.currency == "USD"
    assert _amount(parsed.extracted_amount) == Decimal("508")
    assert len(parsed.line_items) == 3
    assert _line_total_sum(parsed) == Decimal("508")
    row1, row2, row3 = parsed.line_items
    assert "O50C" not in str(row1.get("item_name") or "")
    assert "HGW20" in str(row1.get("item_name") or "")
    assert "880G" not in str(row2.get("item_name") or "")
    assert row1.get("item_code") == "HGW20-1000"
    assert _amount(row1.get("quantity")) == Decimal("8")
    assert _amount(row1.get("unit_price")) == Decimal("45")
    assert _amount(row1.get("line_total")) == Decimal("360")
    assert row2.get("item_code") == "CBL-HAR-500"
    assert _amount(row2.get("quantity")) == Decimal("40")
    assert _amount(row2.get("unit_price")) == Decimal("2.2")
    assert _amount(row2.get("line_total")) == Decimal("88")
    assert row3.get("item_code") == "CON-PCB-12P"
    assert _amount(row3.get("quantity")) == Decimal("200")
    assert _amount(row3.get("unit_price")) == Decimal("0.3")
    assert _amount(row3.get("line_total")) == Decimal("60")
    keys = {
        (
            str(item.get("item_code") or item.get("document_item_code") or ""),
            str(item.get("item_name") or ""),
            str(item.get("specification") or ""),
        )
        for item in parsed.line_items
    }
    assert len(keys) == len(parsed.line_items)


def test_real_ocr_noise_cases_preserve_item_candidates_without_header_or_amount_prefixes():
    for prefix in ["07", "08", "09", "10"]:
        if prefix not in PREFIXES:
            pytest.skip(f"{prefix} fixture missing")
        parsed = _parse(prefix)
        expected = _load_json(prefix, "expected")
        assert len(parsed.line_items) >= expected.get("line_item_count_min", expected.get("line_item_count", 1))
        _assert_no_bad_structured_values(parsed)
        if prefix == "09":
            assert parsed.extracted_amount == Decimal("403700")
            assert _line_total_sum(parsed) == Decimal("403700")
            assert _amount(parsed.line_items[1].get("quantity")) == Decimal("1200")
            assert _amount(parsed.line_items[2].get("quantity")) == Decimal("1200")
            for item in parsed.line_items:
                assert _amount(item.get("quantity")) != Decimal("7199")
        if prefix == "07":
            assert parsed.due_date and parsed.due_date.isoformat() == "2026-08-18"
            assert len(parsed.line_items) == 3
            for item in parsed.line_items:
                name = str(item.get("item_name") or "")
                assert "문서 총액" not in name
                assert "본문서는" not in name
                assert "담당/검토/승인" not in name
            assert _amount(parsed.line_items[1].get("quantity")) == Decimal("120")
            assert _amount(parsed.line_items[1].get("line_total")) == Decimal("132000")
            assert _amount(parsed.line_items[2].get("quantity")) == Decimal("300")
            assert _amount(parsed.line_items[2].get("line_total")) == Decimal("165000")
        if prefix == "08":
            first = parsed.line_items[0]
            assert parsed.document_number == "QT-2026-0808-009"
            assert _amount(parsed.extracted_amount) == Decimal("473000")
            assert first.get("quantity") is None
            assert first.get("unit_price") is None
            assert _amount(first.get("supply_amount")) == Decimal("280000")
            assert _amount(first.get("tax_amount")) == Decimal("28000")
            assert _amount(first.get("line_total")) == Decimal("308000")
            warnings = set(first.get("validation_warnings") or [])
            assert {"missing_quantity", "quantity_cell_blank"} <= warnings
            second = parsed.line_items[1]
            assert second.get("quantity") is None
            assert second.get("unit_price") is None
            assert _amount(second.get("supply_amount")) == Decimal("150000")
            assert _amount(second.get("tax_amount")) == Decimal("15000")
            assert _amount(second.get("line_total")) == Decimal("165000")
            second_warnings = set(second.get("validation_warnings") or [])
            assert {"missing_quantity", "ocr_quantity_price_unverified"} <= second_warnings
        if prefix == "10":
            first_name = str(parsed.line_items[0].get("item_name") or "")
            assert not re.match(r"^\s*(?:\d{1,3}\s+)?\d{4,}\s+\d{4,}\s+", first_name)
            assert first_name == "베어링 하우징"
            second = parsed.line_items[1]
            assert str(second.get("item_name") or "") == "S45C PIN 8X60"
            assert second.get("item_code") != "BOLT-M8-20"
