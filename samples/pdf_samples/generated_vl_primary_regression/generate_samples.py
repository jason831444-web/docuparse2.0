from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(__file__).resolve().parent
FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
FONT = ImageFont.truetype(str(FONT_PATH), 30)
FONT_BOLD = ImageFont.truetype(str(FONT_PATH), 42)
FONT_SMALL = ImageFont.truetype(str(FONT_PATH), 22)


@dataclass
class Sample:
    stem: str
    title: str
    document_type: str
    document_number: str
    issue_date: str
    vendor: str | None
    customer: str | None
    currency: str | None
    subtotal: int | float | None
    tax_amount: int | float | None
    total_amount: int | float | None
    line_items: list[dict[str, Any]]
    document_subtype: str | None = None
    document_profile: str | None = None
    no_price_document: bool = False
    visual_crop: bool = False
    crop_right_px: int = 0
    skew_degrees: float = 0
    blur_radius: float = 0
    text_layer: bool = False
    visible_columns: list[str] = field(default_factory=list)
    hidden_or_cropped_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary_rows: list[tuple[str, str]] = field(default_factory=list)


def money(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{int(value):,}"


def cell(value: Any) -> str:
    return "" if value is None else str(value)


def draw_page(sample: Sample) -> tuple[Image.Image, str]:
    full_width = 1900
    height = 2450
    image = Image.new("RGB", (full_width, height), "white")
    draw = ImageDraw.Draw(image)
    x0, y = 120, 120
    draw.text((x0, y), sample.title, fill=(20, 25, 35), font=FONT_BOLD)
    y += 90
    left_meta = [
        ("문서번호", sample.document_number),
        ("발행일", sample.issue_date),
        ("공급업체", sample.vendor or ""),
        ("고객사", sample.customer or ""),
    ]
    for idx, (label, value) in enumerate(left_meta):
        col = 0 if idx < 2 else 1
        row = idx if idx < 2 else idx - 2
        draw.text((x0 + col * 620, y + row * 42), label, fill=(30, 30, 30), font=FONT_SMALL)
        draw.text((x0 + col * 620 + 170, y + row * 42), value, fill=(30, 30, 30), font=FONT_SMALL)
    y += 135

    headers = infer_headers(sample)
    widths = [90, 350, 250, 210, 140, 120, 170, 190, 170, 190, 220]
    widths = widths[: len(headers)]
    table_x = x0
    if not sample.visual_crop:
        available_width = full_width - table_x - 120
        if sum(widths) > available_width:
            scale = available_width / sum(widths)
            widths = [max(54, int(width * scale)) for width in widths]
    row_h = 58
    draw.rectangle((table_x, y, table_x + sum(widths), y + row_h), outline=(95, 105, 120), fill=(232, 236, 242))
    cursor = table_x
    for header, width in zip(headers, widths):
        draw.text((cursor + 12, y + 16), header, fill=(20, 25, 35), font=FONT_SMALL)
        draw.line((cursor, y, cursor, y + row_h), fill=(130, 135, 145), width=1)
        cursor += width
    draw.line((cursor, y, cursor, y + row_h), fill=(130, 135, 145), width=1)
    y += row_h

    text_lines = [sample.title, f"문서번호 {sample.document_number}", f"발행일 {sample.issue_date}"]
    for idx, item in enumerate(sample.line_items, start=1):
        values = row_values(sample, item, idx)
        draw.rectangle((table_x, y, table_x + sum(widths), y + row_h), outline=(150, 155, 165), fill="white")
        cursor = table_x
        for value, width in zip(values, widths):
            draw.text((cursor + 10, y + 16), cell(value), fill=(30, 30, 30), font=FONT_SMALL)
            draw.line((cursor, y, cursor, y + row_h), fill=(160, 165, 175), width=1)
            cursor += width
        draw.line((cursor, y, cursor, y + row_h), fill=(160, 165, 175), width=1)
        text_lines.append(" ".join(cell(v) for v in values if cell(v)))
        y += row_h

    y += 70
    if sample.summary_rows:
        for label, value in sample.summary_rows:
            draw.text((1070, y), label, fill=(25, 25, 25), font=FONT)
            draw.text((1370, y), value, fill=(25, 25, 25), font=FONT)
            text_lines.append(f"{label} {value}")
            y += 48
    elif sample.total_amount is not None:
        if sample.subtotal is not None:
            draw.text((1070, y), "공급가액", fill=(25, 25, 25), font=FONT)
            draw.text((1370, y), money(sample.subtotal), fill=(25, 25, 25), font=FONT)
            text_lines.append(f"공급가액 {money(sample.subtotal)}")
            y += 48
        if sample.tax_amount is not None:
            draw.text((1070, y), "세액", fill=(25, 25, 25), font=FONT)
            draw.text((1370, y), money(sample.tax_amount), fill=(25, 25, 25), font=FONT)
            text_lines.append(f"세액 {money(sample.tax_amount)}")
            y += 48
        label = "Total USD" if sample.currency == "USD" else "총액"
        draw.text((1070, y), label, fill=(25, 25, 25), font=FONT)
        draw.text((1370, y), money(sample.total_amount), fill=(25, 25, 25), font=FONT)
        text_lines.append(f"{label} {money(sample.total_amount)}")
        y += 48

    y += 40
    for note in sample.notes:
        draw.text((x0, y), f"※ {note}", fill=(40, 40, 40), font=FONT_SMALL)
        text_lines.append(f"※ {note}")
        y += 40

    if sample.crop_right_px:
        image = image.crop((0, 0, full_width - sample.crop_right_px, height))
    if sample.skew_degrees:
        image = image.rotate(sample.skew_degrees, expand=True, fillcolor="white")
    if sample.blur_radius:
        image = image.filter(ImageFilter.GaussianBlur(sample.blur_radius))
    return image, "\n".join(text_lines)


def infer_headers(sample: Sample) -> list[str]:
    if sample.no_price_document and sample.document_type == "delivery_note":
        return ["No", "품목명", "문서품목코드", "규격", "발주수량", "납품수량", "잔량", "단위", "비고"]
    if sample.document_type == "inspection_report":
        return ["No", "품목명", "Lot No", "규격", "입고수량", "합격수량", "불량수량", "판정"]
    if sample.document_subtype == "internal_transfer":
        return ["No", "품목명", "내부품목코드", "규격", "요청수량", "단위", "비고"]
    if sample.currency == "USD":
        return ["No", "Description", "Vendor SKU", "Spec", "Qty", "Unit", "Unit Price", "Amount"]
    return ["No", "품목명", "품목코드", "규격", "수량", "단위", "단가", "공급가액", "세액", "합계금액"]


def row_values(sample: Sample, item: dict[str, Any], idx: int) -> list[Any]:
    if sample.no_price_document and sample.document_type == "delivery_note":
        return [
            idx,
            item["item_name"],
            item.get("document_item_code"),
            item.get("spec"),
            item.get("ordered_quantity"),
            item.get("delivered_quantity"),
            item.get("remaining_quantity"),
            item.get("unit"),
            item.get("note"),
        ]
    if sample.document_type == "inspection_report":
        return [
            idx,
            item["item_name"],
            item.get("lot_no"),
            item.get("spec"),
            item.get("received_quantity"),
            item.get("accepted_quantity"),
            item.get("rejected_quantity"),
            item.get("decision"),
        ]
    if sample.document_subtype == "internal_transfer":
        return [
            idx,
            item["item_name"],
            item.get("internal_item_code"),
            item.get("spec"),
            item.get("requested_quantity"),
            item.get("unit"),
            item.get("note"),
        ]
    if sample.currency == "USD":
        return [
            idx,
            item["item_name"],
            item.get("document_item_code"),
            item.get("spec"),
            item.get("quantity"),
            item.get("unit"),
            item.get("unit_price"),
            item.get("line_total"),
        ]
    return [
        idx,
        item["item_name"],
        item.get("document_item_code"),
        item.get("spec"),
        item.get("quantity"),
        item.get("unit"),
        item.get("unit_price"),
        item.get("supply_amount"),
        item.get("tax_amount"),
        item.get("line_total"),
    ]


def write_pdf(sample: Sample) -> None:
    image, text = draw_page(sample)
    image_path = ROOT / f"{sample.stem}.jpg"
    pdf_path = ROOT / f"{sample.stem}.pdf"
    image.save(image_path, quality=82, optimize=True)
    doc = fitz.open()
    page = doc.new_page(width=image.width * 0.5, height=image.height * 0.5)
    page.insert_image(page.rect, filename=str(image_path))
    if sample.text_layer:
        page.insert_text((24, 24), text, fontsize=1, render_mode=3)
    doc.save(pdf_path)
    image_path.unlink()
    expected = {
        "document_type": sample.document_type,
        "document_subtype": sample.document_subtype,
        "document_profile": sample.document_profile,
        "document_number": sample.document_number,
        "issue_date": sample.issue_date,
        "vendor": sample.vendor,
        "customer": sample.customer,
        "currency": sample.currency,
        "total_amount": sample.total_amount,
        "subtotal": sample.subtotal,
        "tax_amount": sample.tax_amount,
        "no_price_document": sample.no_price_document,
        "visual_crop": sample.visual_crop,
        "visible_columns": sample.visible_columns,
        "hidden_or_cropped_columns": sample.hidden_or_cropped_columns,
        "line_items": sample.line_items,
    }
    (ROOT / f"{sample.stem}.expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    visual = [
        f"# {sample.stem}",
        "",
        f"- PDF: `{sample.stem}.pdf`",
        f"- Text layer expected: `{sample.text_layer}`",
        f"- Visual crop: `{sample.visual_crop}`",
        f"- Visible columns: {', '.join(sample.visible_columns)}",
        f"- Hidden/cropped columns: {', '.join(sample.hidden_or_cropped_columns) or 'none'}",
        "",
        "## Visual Ground Truth",
        f"- document_number: {sample.document_number}",
        f"- total_amount: {sample.total_amount}",
        f"- no_price_document: {sample.no_price_document}",
        "",
        "## Notes",
        *[f"- {note}" for note in sample.notes],
    ]
    (ROOT / f"{sample.stem}.visual.md").write_text("\n".join(visual), encoding="utf-8")


def samples() -> list[Sample]:
    return [
        Sample(
            stem="001_po_clean_image",
            title="발주서",
            document_type="purchase_order",
            document_number="PO-GEN-2026-001",
            issue_date="2026-10-01",
            vendor="대성정공",
            customer="한빛제조",
            currency="KRW",
            subtotal=330000,
            tax_amount=33000,
            total_amount=363000,
            visible_columns=["item_name", "document_item_code", "spec", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total"],
            line_items=[
                {"item_name": "SUS304 2T PLATE", "document_item_code": "STS304-2T", "spec": "1000x2000", "quantity": 6, "unit": "EA", "unit_price": 25000, "supply_amount": 150000, "tax_amount": 15000, "line_total": 165000},
                {"item_name": "M8 육각볼트", "document_item_code": "BOLT-M8-20", "spec": "M8x20", "quantity": 1500, "unit": "EA", "unit_price": 120, "supply_amount": 180000, "tax_amount": 18000, "line_total": 198000},
            ],
        ),
        Sample(
            stem="002_quote_blank_qty_hidden_total",
            title="견적서",
            document_type="quotation",
            document_number="QT-GEN-2026-002",
            issue_date="2026-10-02",
            vendor="한성산업",
            customer="제일기계",
            currency="KRW",
            subtotal=430000,
            tax_amount=43000,
            total_amount=473000,
            visual_crop=True,
            crop_right_px=360,
            visible_columns=["item_name", "document_item_code", "spec", "quantity", "unit", "unit_price", "supply_amount"],
            hidden_or_cropped_columns=["tax_amount", "line_total"],
            notes=["첫 번째 품목 수량 공란. 값을 추정하지 말 것."],
            line_items=[
                {"item_name": "고정 플레이트", "document_item_code": "PLT-FIX-02", "spec": "120x60x5T", "quantity": None, "unit": "EA", "unit_price": 2800, "supply_amount": 280000, "tax_amount": None, "line_total": None, "expected_review_flags": ["missing_quantity", "row_amount_hidden_do_not_infer"]},
                {"item_name": "스테인리스 브라켓", "document_item_code": "BRK-SUS-01", "spec": "50x80x3T", "quantity": 100, "unit": "EA", "unit_price": 1500, "supply_amount": 150000, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
            ],
        ),
        Sample(
            stem="003_delivery_no_price_partial_crop",
            title="납품서",
            document_type="delivery_note",
            document_number="DN-GEN-2026-003",
            issue_date="2026-10-03",
            vendor="오성테크",
            customer="한빛제조",
            currency=None,
            subtotal=None,
            tax_amount=None,
            total_amount=None,
            no_price_document=True,
            visual_crop=True,
            crop_right_px=320,
            visible_columns=["item_name", "document_item_code", "spec", "ordered_quantity", "delivered_quantity"],
            hidden_or_cropped_columns=["remaining_quantity", "note"],
            line_items=[
                {"item_name": "베어링 하우징", "document_item_code": "BRG-H-100", "spec": "100mm", "ordered_quantity": 80, "delivered_quantity": 50, "remaining_quantity": None, "unit": "EA", "expected_review_flags": ["remaining_quantity_hidden"]},
                {"item_name": "S45C PIN 8X60", "document_item_code": "PIN-8X60", "spec": "8x60", "ordered_quantity": 300, "delivered_quantity": 300, "remaining_quantity": None, "unit": "EA"},
            ],
        ),
        Sample(
            stem="004_commercial_invoice_exchange_hidden_amount",
            title="COMMERCIAL INVOICE",
            document_type="invoice",
            document_subtype="commercial_invoice",
            document_profile="foreign_currency_document",
            document_number="INV-US-GEN-004",
            issue_date="2026-10-04",
            vendor="Global Motion Parts LLC",
            customer="NeoFactory Korea",
            currency="USD",
            subtotal=650.00,
            tax_amount=0.00,
            total_amount=650.00,
            visual_crop=True,
            crop_right_px=420,
            visible_columns=["item_name", "document_item_code", "spec", "quantity", "unit", "unit_price"],
            hidden_or_cropped_columns=["line_total"],
            notes=["Exchange Rate Note: USD = 1,370 KRW 참고. 1,370은 total/amount가 아님."],
            line_items=[
                {"item_name": "Linear Guide Rail HGW20", "document_item_code": "HGW20-1000", "spec": "1000mm", "quantity": 10, "unit": "EA", "unit_price": 45.00, "line_total": None, "expected_review_flags": ["amount_column_not_visible"]},
                {"item_name": "Cable Harness 500", "document_item_code": "CBL-HAR-500", "spec": "500mm", "quantity": 50, "unit": "EA", "unit_price": 2.20, "line_total": None, "expected_review_flags": ["amount_column_not_visible"]},
            ],
        ),
        Sample(
            stem="005_internal_transfer_quantity_only",
            title="사업장간 자재 이동 요청서",
            document_type="general_document",
            document_subtype="internal_transfer",
            document_profile="inventory_movement_document",
            document_number="TRF-GEN-2026-005",
            issue_date="2026-10-05",
            vendor=None,
            customer=None,
            currency=None,
            subtotal=None,
            tax_amount=None,
            total_amount=None,
            no_price_document=True,
            visual_crop=True,
            crop_right_px=260,
            visible_columns=["item_name", "internal_item_code", "spec", "requested_quantity", "unit"],
            hidden_or_cropped_columns=["note"],
            line_items=[
                {"item_name": "SUS304 2T PLATE", "internal_item_code": "M-PLT-SUS304-2T-1000X2000", "spec": "1000x2000", "requested_quantity": 2, "unit": "EA"},
                {"item_name": "M8 육각 볼트", "internal_item_code": "P-BOLT-M8-20-ZN", "spec": "M8x20", "requested_quantity": 500, "unit": "EA"},
            ],
        ),
        Sample(
            stem="006_return_credit_full_visible",
            title="반품 / 차감 요청서",
            document_type="general_document",
            document_subtype="credit_note",
            document_profile="return_document",
            document_number="RTN-GEN-2026-006",
            issue_date="2026-10-06",
            vendor="한빛제조",
            customer="오성테크",
            currency="KRW",
            subtotal=11000,
            tax_amount=1100,
            total_amount=12100,
            visible_columns=["item_name", "spec", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total"],
            notes=["관련납품서 DN-GEN-2026-003. 차감 부호 방향은 검토 필요."],
            line_items=[
                {"item_name": "베어링 하우징", "spec": "100mm", "quantity": 1, "unit": "EA", "unit_price": 8000, "supply_amount": 8000, "tax_amount": 800, "line_total": 8800},
                {"item_name": "S45C PIN", "spec": "8x60", "quantity": 5, "unit": "EA", "unit_price": 600, "supply_amount": 3000, "tax_amount": 300, "line_total": 3300},
            ],
        ),
        Sample(
            stem="007_inspection_report_no_price_crop_decision",
            title="입고검사성적서",
            document_type="inspection_report",
            document_subtype="incoming_inspection",
            document_profile="quality_document",
            document_number="IQC-GEN-2026-007",
            issue_date="2026-10-07",
            vendor="미래정밀",
            customer="정우금속",
            currency=None,
            subtotal=None,
            tax_amount=None,
            total_amount=None,
            no_price_document=True,
            visual_crop=True,
            crop_right_px=260,
            visible_columns=["item_name", "lot_no", "spec", "received_quantity", "accepted_quantity", "rejected_quantity"],
            hidden_or_cropped_columns=["decision"],
            notes=["불량 1EA는 반품 예정. 이 문구만으로 return/credit 문서가 아님."],
            line_items=[
                {"item_name": "베어링 하우징", "lot_no": "LOT-BRG-1007-A", "spec": "100mm", "received_quantity": 50, "accepted_quantity": 49, "rejected_quantity": 1, "decision": None},
                {"item_name": "S45C PIN 8X60", "lot_no": "LOT-PIN-1007-B", "spec": "8x60", "received_quantity": 300, "accepted_quantity": 300, "rejected_quantity": 0, "decision": None},
            ],
        ),
        Sample(
            stem="008_statement_text_layer_hidden_total",
            title="거래명세서",
            document_type="transaction_statement",
            document_number="TS-GEN-2026-008",
            issue_date="2026-10-08",
            vendor="한빛제조",
            customer="오성테크",
            currency="KRW",
            subtotal=641000,
            tax_amount=64100,
            total_amount=705100,
            visual_crop=True,
            crop_right_px=410,
            text_layer=True,
            visible_columns=["item_name", "spec", "quantity", "unit", "unit_price"],
            hidden_or_cropped_columns=["supply_amount", "tax_amount", "line_total"],
            notes=["Text layer contains hidden row totals; visual confirmed values must not pretend those columns are visible. Row supply amounts are partially clipped at the right edge."],
            line_items=[
                {"item_name": "SUS304 3T PLATE", "spec": "1000x2000", "quantity": 3, "unit": "EA", "unit_price": 35000, "supply_amount": None, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
                {"item_name": "M8 육각볼트", "spec": "M8x20", "quantity": 2000, "unit": "EA", "unit_price": 120, "supply_amount": None, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
            ],
        ),
        Sample(
            stem="009_tax_invoice_rounding_hidden_row_tax",
            title="전자 세금계산서",
            document_type="invoice",
            document_subtype="tax_invoice",
            document_profile="tax_document",
            document_number="TAX-GEN-2026-009",
            issue_date="2026-10-09",
            vendor="정우금속",
            customer="한빛제조",
            currency="KRW",
            subtotal=269709,
            tax_amount=26971,
            total_amount=296680,
            visual_crop=True,
            crop_right_px=390,
            visible_columns=["item_name", "document_item_code", "spec", "quantity", "unit", "unit_price"],
            hidden_or_cropped_columns=["tax_amount", "line_total"],
            notes=["Row supply amount for the first item is visually clipped; summary subtotal/tax/total remain visible."],
            line_items=[
                {"item_name": "PCB Connector 12P", "document_item_code": "CON-PCB-12P", "spec": "12P", "quantity": 333, "unit": "EA", "unit_price": 301, "supply_amount": None, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
                {"item_name": "조정금액", "document_item_code": "ROUND-ADJ", "spec": "원단위 조정", "quantity": 1, "unit": "식", "unit_price": -1, "supply_amount": -1, "tax_amount": None, "line_total": None},
            ],
        ),
        Sample(
            stem="010_fax_po_o0_row_boundary",
            title="FAX 발주서",
            document_type="purchase_order",
            document_number="FAX-PO-GEN-010",
            issue_date="2026-10-10",
            vendor="오성테크",
            customer="한빛제조",
            currency="KRW",
            subtotal=380000,
            tax_amount=38000,
            total_amount=418000,
            blur_radius=0.6,
            visible_columns=["item_name", "spec", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total"],
            notes=["팩스형 품질. 176,0OO처럼 O/0 혼동 가능. row boundary review 필요."],
            line_items=[
                {"item_name": "베어링 하우징", "spec": "100mm", "quantity": 20, "unit": "EA", "unit_price": 8000, "supply_amount": 160000, "tax_amount": 16000, "line_total": 176000, "expected_review_flags": ["fax_row_boundary_uncertain"]},
                {"item_name": "S45C PIN 8X60", "spec": "8x60", "quantity": 100, "unit": "EA", "unit_price": 600, "supply_amount": 60000, "tax_amount": 6000, "line_total": 66000, "expected_review_flags": ["fax_row_boundary_uncertain"]},
                {"item_name": "M8 볼트 / 와셔 SET", "spec": "M8", "quantity": 1000, "unit": "SET", "unit_price": 160, "supply_amount": 160000, "tax_amount": 16000, "line_total": 176000, "expected_review_flags": ["fax_row_boundary_uncertain"]},
            ],
        ),
        Sample(
            stem="011_vendor_sku_not_item_row",
            title="인보이스",
            document_type="invoice",
            document_number="INV-GEN-2026-011",
            issue_date="2026-10-11",
            vendor="미래정밀",
            customer="한빛제조",
            currency="KRW",
            subtotal=1220000,
            tax_amount=122000,
            total_amount=1342000,
            visible_columns=["item_name", "document_item_code", "spec", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total"],
            notes=["Vendor SKU column is an item code candidate, not a separate item row."],
            line_items=[
                {"item_name": "PCB Connector 12P", "document_item_code": "VSKU-CON-12P", "spec": "12P", "quantity": 1500, "unit": "EA", "unit_price": 300, "supply_amount": 450000, "tax_amount": 45000, "line_total": 495000},
                {"item_name": "Cable Harness 500", "document_item_code": "VSKU-CBL-500", "spec": "500mm", "quantity": 350, "unit": "EA", "unit_price": 2200, "supply_amount": 770000, "tax_amount": 77000, "line_total": 847000},
            ],
        ),
        Sample(
            stem="012_option_quote_no_final_total",
            title="옵션 견적서",
            document_type="quotation",
            document_number="QT-GEN-2026-012-ALT",
            issue_date="2026-10-12",
            vendor="미래정밀",
            customer="정우금속",
            currency=None,
            subtotal=None,
            tax_amount=None,
            total_amount=None,
            visual_crop=True,
            visible_columns=["item_name", "spec", "quantity", "unit", "unit_price", "supply_amount"],
            hidden_or_cropped_columns=["tax_amount", "line_total"],
            notes=["옵션 A/B/C 중 택일. 옵션별 공급가액은 있으나 최종 합계로 합산하지 말 것."],
            line_items=[
                {"item_name": "A1 스텐판", "spec": "SUS304 2T", "quantity": 10, "unit": "EA", "unit_price": 24500, "supply_amount": 245000, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
                {"item_name": "A2 스텐판", "spec": "SUS316 2T", "quantity": 10, "unit": "EA", "unit_price": 31000, "supply_amount": 310000, "tax_amount": None, "line_total": None, "expected_review_flags": ["row_amount_hidden_do_not_infer"]},
            ],
        ),
    ]


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for sample in samples():
        write_pdf(sample)
        manifest.append(
            {
                "filename": f"{sample.stem}.pdf",
                "expected": f"{sample.stem}.expected.json",
                "visual": f"{sample.stem}.visual.md",
                "document_type": sample.document_type,
                "visual_crop": sample.visual_crop,
                "text_layer": sample.text_layer,
            }
        )
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = [
        "# Generated VL Primary Regression Samples",
        "",
        "Synthetic manufacturing PDFs for VL-first parser and validation hardening.",
        "Model binaries are not stored here. These PDFs are small regression fixtures.",
        "",
        "Run `python3 samples/pdf_samples/generated_vl_primary_regression/generate_samples.py` to regenerate.",
    ]
    (ROOT / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(f"generated {len(manifest)} samples in {ROOT}")


if __name__ == "__main__":
    main()
