import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from app.models.document import Document


def serialize_document(document: Document) -> dict:
    data = {
        column.name: getattr(document, column.name)
        for column in document.__table__.columns
        if column.name not in {"stored_file_path"}
    }
    for key, value in data.items():
        if isinstance(value, (datetime, date, UUID, Decimal)):
            data[key] = str(value)
        elif hasattr(value, "value"):
            data[key] = value.value
    return data


def documents_to_csv(documents: list[Document]) -> str:
    rows = documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows).drop(columns=["거래처 탭"], errors="ignore")
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue()


def documents_to_excel(documents: list[Document], sheet_mode: str = "combined") -> bytes:
    rows = documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows)
    buffer = io.BytesIO()
    try:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            if sheet_mode == "party_tabs" and rows:
                grouped = frame.groupby(frame["거래처 탭"].fillna("미분류"), dropna=False)
                for name, group in grouped:
                    group.drop(columns=["거래처 탭"], errors="ignore").to_excel(writer, index=False, sheet_name=_excel_sheet_name(str(name)))
            else:
                frame.drop(columns=["거래처 탭"], errors="ignore").to_excel(writer, index=False, sheet_name="erp_ready_data")
        return buffer.getvalue()
    except ModuleNotFoundError:
        return _minimal_xlsx(rows, sheet_mode=sheet_mode)


def document_to_json(document: Document) -> str:
    return json.dumps(serialize_document(document), indent=2)


def tax_invoice_to_draft_xml(document: Document) -> bytes:
    errors = validate_tax_invoice_export(document)
    if errors:
        raise ValueError("; ".join(errors))
    root = ET.Element("TaxInvoiceDraft", {"version": "docuparse-draft-1"})
    header = ET.SubElement(root, "Header")
    _xml_text(header, "DocumentNumber", document.document_number)
    _xml_text(header, "IssueDate", str(document.issue_date or document.extracted_date or ""))
    _xml_text(header, "DocumentType", getattr(document.document_type, "value", str(document.document_type)))
    _xml_text(header, "Currency", document.currency or "KRW")
    supplier = ET.SubElement(root, "Supplier")
    _xml_text(supplier, "Name", document.vendor_name or document.merchant_name)
    customer = ET.SubElement(root, "Customer")
    _xml_text(customer, "Name", document.customer_name)
    amounts = ET.SubElement(root, "Amounts")
    _xml_text(amounts, "SupplyAmount", _decimal_text(document.subtotal))
    _xml_text(amounts, "TaxAmount", _decimal_text(document.tax))
    _xml_text(amounts, "TotalAmount", _decimal_text(document.extracted_amount))
    items = ET.SubElement(root, "LineItems")
    for index, item in enumerate(document.line_items or [], start=1):
        node = ET.SubElement(items, "LineItem", {"sequence": str(index)})
        _xml_text(node, "ItemName", item.get("item_name"))
        _xml_text(node, "DocumentItemCode", item.get("document_item_code") or item.get("item_code") or item.get("source_item_code"))
        _xml_text(node, "InternalItemCode", item.get("internal_item_code"))
        _xml_text(node, "Specification", item.get("specification"))
        _xml_text(node, "Quantity", _decimal_text(item.get("quantity")))
        _xml_text(node, "Unit", item.get("unit"))
        _xml_text(node, "UnitPrice", _decimal_text(item.get("unit_price")))
        _xml_text(node, "SupplyAmount", _decimal_text(item.get("supply_amount")))
        _xml_text(node, "TaxAmount", _decimal_text(item.get("tax_amount")))
        _xml_text(node, "LineTotal", _decimal_text(item.get("line_total")))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_tax_invoice_export(document: Document) -> list[str]:
    errors: list[str] = []
    if getattr(document.document_type, "value", str(document.document_type)) != "invoice":
        errors.append("전자세금계산서 XML 초안은 인보이스/세금계산서 문서만 지원합니다.")
    for label, value in {
        "공급업체": document.vendor_name or document.merchant_name,
        "고객사": document.customer_name,
        "계산서번호": document.document_number,
        "발행일": document.issue_date or document.extracted_date,
        "공급가액": document.subtotal,
        "세액": document.tax,
        "합계금액": document.extracted_amount,
    }.items():
        if value in (None, "", []):
            errors.append(f"{label} 필드가 필요합니다.")
    subtotal = _to_decimal(document.subtotal)
    tax = _to_decimal(document.tax)
    total = _to_decimal(document.extracted_amount)
    if subtotal is not None and tax is not None and total is not None and subtotal + tax != total:
        errors.append("공급가액 + 세액이 합계금액과 일치하지 않습니다.")
    line_total = _line_total_sum(document.line_items or [])
    if total is not None and line_total is not None and abs(line_total - total) > Decimal("0.01"):
        errors.append("품목 합계가 문서 총액과 일치하지 않습니다.")
    if not document.line_items:
        errors.append("품목 목록이 필요합니다.")
    return errors


def documents_to_erp_rows(documents: list[Document]) -> list[dict]:
    rows: list[dict] = []
    for document in documents:
        line_items = document.line_items or [{}]
        for item in line_items:
            rows.append({
                "문서유형": getattr(document.document_type, "value", str(document.document_type)),
                "공급업체": document.vendor_name or document.merchant_name,
                "고객사": document.customer_name,
                "거래처 탭": document.customer_name or document.vendor_name or document.merchant_name or "미분류",
                "문서번호": document.document_number,
                "발행일": str(document.issue_date or document.extracted_date or "") or None,
                "납기일": str(document.due_date or "") or None,
                "품목명": item.get("item_name"),
                "품목코드": item.get("item_code"),
                "규격": item.get("specification"),
                "수량": item.get("quantity"),
                "단위": item.get("unit"),
                "단가": item.get("unit_price"),
                "공급가액": item.get("supply_amount"),
                "세액": item.get("tax_amount"),
                "합계금액": item.get("line_total") or document.extracted_amount,
                "통화": document.currency,
                "검토상태": "검토 필요" if document.review_required else "확정 가능",
            })
    return rows


def _excel_sheet_name(value: str) -> str:
    cleaned = re.sub(r"[\[\]\*:/\\?]", " ", value).strip() or "미분류"
    return cleaned[:31]


def _minimal_xlsx(rows: list[dict], sheet_mode: str = "combined") -> bytes:
    sheets: list[tuple[str, list[dict]]] = []
    if sheet_mode == "party_tabs" and rows:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("거래처 탭") or "미분류"), []).append(row)
        sheets = [(_excel_sheet_name(name), group) for name, group in grouped.items()]
    else:
        sheets = [("erp_ready_data", rows)]
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types(len(sheets)))
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels(len(sheets)))
        archive.writestr("xl/workbook.xml", _xlsx_workbook_xml([name for name, _ in sheets]))
        archive.writestr("xl/styles.xml", _xlsx_styles())
        for index, (_, sheet_rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _xlsx_sheet_xml(sheet_rows))
    return output.getvalue()


def _xlsx_sheet_xml(rows: list[dict]) -> str:
    visible_rows = [{key: value for key, value in row.items() if key != "거래처 탭"} for row in rows]
    headers = list(visible_rows[0].keys()) if visible_rows else list(documents_to_erp_rows([])[0].keys()) if False else ["문서유형", "공급업체", "고객사", "문서번호"]
    table = [headers] + [[row.get(header, "") for header in headers] for row in visible_rows]
    row_xml = []
    for row_index, values in enumerate(table, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            ref = f"{_xlsx_col(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'


def _xlsx_col(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _xml_escape(value: object) -> str:
    return ("" if value is None else str(value)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _xlsx_content_types(sheet_count: int) -> str:
    sheets = "".join(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for index in range(1, sheet_count + 1))
    return f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{sheets}</Types>'


def _xlsx_root_rels() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'


def _xlsx_workbook_rels(sheet_count: int) -> str:
    rels = "".join(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>' for index in range(1, sheet_count + 1))
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'


def _xlsx_workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(f'<sheet name="{_xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>' for index, name in enumerate(sheet_names, start=1))
    return f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'


def _xlsx_styles() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf/></cellXfs></styleSheet>'


def _xml_text(parent: ET.Element, tag: str, value: object) -> None:
    node = ET.SubElement(parent, tag)
    node.text = "" if value is None else str(value)


def _decimal_text(value: object) -> str:
    decimal = _to_decimal(value)
    if decimal is None:
        return ""
    return format(decimal, "f")


def _to_decimal(value: object) -> Decimal | None:
    if value in (None, "", []):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


def _line_total_sum(line_items: list[dict]) -> Decimal | None:
    total = Decimal("0")
    seen = False
    for item in line_items:
        value = _to_decimal(item.get("line_total"))
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None
