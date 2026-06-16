import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    from rapidfuzz import fuzz
except ImportError:
    class _FuzzFallback:
        @staticmethod
        def partial_ratio(needle: str, haystack: str) -> int:
            return 100 if needle.lower() in haystack.lower() else 0

    fuzz = _FuzzFallback()

from app.models.document import DocumentType
from app.services.ocr_table_reconstructor import reconstruct_ocr_line_items


DATE_PATTERNS = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
]

MANUFACTURING_TYPE_INDICATORS = [
    (DocumentType.delivery_note, ["납품서", "납품번호", "납품일", "납품수량", "입고장소", "수령자", "delivery note"]),
    (DocumentType.invoice, ["세금계산서", "계산서번호", "인보이스번호", "청구서번호", "청구금액", "지급기한", "결제기한", "사업자등록번호", "공급받는자", "invoice"]),
    (DocumentType.quotation, ["견적서", "견적번호", "견적일", "유효기간", "견적유효기간", "납기조건", "결제조건", "quotation", "quote"]),
    (DocumentType.purchase_order, ["발주서", "발주번호", "발주처", "납기일", "purchase order", "po no"]),
    (DocumentType.transaction_statement, ["거래명세서", "거래명세서번호", "거래일자", "transaction statement"]),
    (DocumentType.inspection_report, ["입고검사성적서", "검사성적서", "검사번호", "입고수량", "합격수량", "불량수량", "inspection report"]),
]

CATEGORY_KEYWORDS = {
    "purchase_order": ["발주서", "발주 번호", "po no", "purchase order", "납기일", "발주일"],
    "quotation": ["견적서", "견적 번호", "quotation", "quote", "유효기간", "견적금액"],
    "transaction_statement": ["거래명세서", "거래 명세서", "transaction statement", "공급가액", "세액"],
    "delivery_note": ["납품서", "납품 번호", "delivery note", "납품일", "인수자"],
    "packing_list": ["포장명세서", "packing list", "포장 수량", "box", "carton"],
    "inspection_report": ["검사성적서", "inspection report", "검사 결과", "합격", "불합격"],
    "return_note": ["반품", "차감", "return", "credit note", "rtn"],
    "internal_transfer": ["사업장간", "자재 이동", "내부 이동", "internal transfer", "trf"],
    "contract": ["계약서", "contract", "계약 기간", "계약 금액"],
    "profile_record": ["name:", "id:", "student id", "major:", "age:", "department:", "dob:"],
    "installation_guide": ["installation guide", "setup guide", "install", "installation", "setup", "configuration", "environment variables", "dependencies", "prerequisites"],
    "implementation_schedule": ["implementation", "schedule", "task", "feature", "status", "testing", "coverage", "pipeline", "claimed", "roadmap"],
    "invoice": ["invoice", "invoice number", "vendor", "bill to", "invoice date", "due date", "amount due", "total due"],
    "course_guide": ["syllabus", "course code", "office hours", "grading", "required materials", "instructor"],
    "presentation_guide": ["presentation guide", "speaker notes", "talk track", "slide guidance", "rehearse"],
    "repair_service": ["repair", "service work", "labor", "parts", "maintenance", "technician", "brake"],
    "groceries": ["grocery", "market", "foods", "supermarket", "trader", "whole foods"],
    "dining": ["restaurant", "cafe", "coffee", "pizza", "burger", "bar", "bakery"],
    "transportation": ["gas", "fuel", "uber", "lyft", "parking", "metro", "taxi"],
    "utilities": ["electric", "water", "internet", "phone", "utility", "bill"],
    "health": ["pharmacy", "clinic", "doctor", "medical", "dentist"],
    "office": ["office", "stationery", "printing", "supplies"],
    "notice": ["notice", "announcement", "meeting", "deadline", "reminder"],
}

LINE_ITEM_LABELS = {
    "item_name": ["품목명", "품명", "반품품목", "제품명", "상품명", "자재명", "item name", "item description", "description", "product name", "item"],
    "item_code": ["품목코드", "문서품목코드", "품번", "제품코드", "상품코드", "자재코드", "내부품목코드", "거래처코드", "거래처품목코드", "vendor sku", "customer item code", "sku", "part no", "part number", "item code"],
    "specification": ["규격", "사양", "모델", "모델명", "size", "spec", "specification", "dimension"],
    "quantity": ["수량", "주문수량", "발주수량", "요청수량", "요청수림", "납품수량", "delivery qty", "delivery quantity", "delivered qty", "qty", "quantity"],
    "unit": ["단위", "unit"],
    "unit_price": ["단가", "단 가", "개당가격", "unit price"],
    "supply_amount": ["공급가액", "공급액", "공급 금액", "supply amount", "subtotal", "amount"],
    "tax_amount": ["세액", "세 액", "부가세", "vat", "tax", "w세액"],
    "line_total": ["합계금액", "차감합계", "차감 합계", "총액", "금액", "합계", "total", "line total"],
}

LINE_ITEM_LABEL_LOOKUP = {
    re.sub(r"[\s_/-]+", "", label.lower()): field
    for field, labels in LINE_ITEM_LABELS.items()
    for label in labels
}

MANUFACTURING_TYPES = {
    DocumentType.purchase_order,
    DocumentType.quotation,
    DocumentType.transaction_statement,
    DocumentType.delivery_note,
    DocumentType.invoice,
    DocumentType.packing_list,
}


@dataclass
class ParsedDocument:
    document_type: DocumentType = DocumentType.general_document
    title: str | None = None
    extracted_date: date | None = None
    extracted_amount: Decimal | None = None
    currency: str | None = None
    merchant_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    document_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    business_fields: dict = field(default_factory=dict)
    line_items: list[dict] = field(default_factory=list)
    category: str | None = None
    tags: list[str] = field(default_factory=list)


class DocumentParser:
    """Heuristic parser. This is the extension point for an LLM or trained extractor later."""

    def parse(self, raw_text: str, filename: str = "") -> ParsedDocument:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        joined = "\n".join(lines)
        doc_type = self._guess_document_type(joined, filename)
        line_items = self._extract_line_items(lines, doc_type)
        document_scope_text = self._document_scope_text(lines)
        no_amount_quantity_document = self._is_no_amount_quantity_document(lines, doc_type)
        option_selection_quote = self._is_option_selection_quotation(lines, doc_type)
        if no_amount_quantity_document:
            special_quantity_items = self._extract_special_quantity_table_items(lines)
            if doc_type == DocumentType.inspection_report:
                special_quantity_items = self._suppress_incomplete_inspection_quantities(special_quantity_items)
            if special_quantity_items and (
                self._should_use_no_amount_special_items(line_items, special_quantity_items)
                or any(
                    any(
                        field in item
                        for field in [
                            "ordered_quantity",
                            "requested_quantity",
                            "received_quantity",
                            "delivered_quantity",
                            "remaining_quantity",
                            "accepted_quantity",
                            "rejected_quantity",
                        ]
                    )
                    for item in special_quantity_items
                )
            ):
                line_items = special_quantity_items
        if no_amount_quantity_document or option_selection_quote:
            subtotal = None
            tax = None
            amount = None
            currency = None
        else:
            subtotal = self._extract_labeled_amount(document_scope_text, ["차감 공급가액", "차감공급가액", "공급가액 합계", "공급가액합계", "공급가액", "공급액", "공급 금액", "금월공급가액", "subtotal total", "subtotal", "supply amount", "supply total"])
            tax = self._extract_labeled_amount(document_scope_text, ["차감 세액", "차감세액", "세액 합계", "세액", "세 액", "부가세", "금월세액", "vat total", "vat", "tax", "w세액"])
            amount = self._extract_labeled_amount(document_scope_text, ["차감 합계", "차감합계", "총 합계", "합계금액", "총액", "공급대가", "청구금액", "금월합계", "invoice total", "grand total", "total due", "total amount", "amount due", "total"]) or self._line_items_total(line_items)
            line_items = self._repair_line_items_against_document_totals(line_items, amount, subtotal, tax, lines)
            line_items = self._collapse_duplicate_line_item_sets(line_items, amount)
            currency = self._extract_currency(document_scope_text) or self._extract_currency(joined) or ("KRW" if amount is not None else None)
        line_items = self._repair_ocr_table_postprocess(line_items, amount, currency, lines)
        if not no_amount_quantity_document:
            line_items = self._suppress_untrusted_foreign_amounts(line_items, amount, currency)
        if no_amount_quantity_document:
            line_items = [self._strip_line_item_amount_fields(item) for item in line_items]
        line_items = self._apply_row_level_safety_overrides(line_items, lines, doc_type)
        if doc_type == DocumentType.inspection_report:
            line_items = self._suppress_incomplete_inspection_quantities(line_items)
        category = self._guess_category(joined)
        business_fields = self._extract_business_fields(joined, doc_type)
        issue_date = self._extract_issue_date(joined, doc_type)
        due_date = self._extract_due_date(joined, doc_type)
        vendor_name = self._extract_labeled_text(joined, ["공급업체", "공급엽체", "공급자", "판매자", "매입처", "발행처", "청구처", "업체", "거래처", "현장", "vendor", "supplier", "seller"])
        customer_name = self._extract_labeled_text(joined, ["공급받는자", "고객사", "고객시", "구매처", "발주처", "수신처", "납품처", "수요처", "구매자", "받는곳", "받는 곳", "customer", "buyer", "bill to"])
        if customer_name and vendor_name and customer_name == vendor_name:
            customer_name = self._extract_labeled_text(joined, ["공급받는자", "고객사", "구매처", "발주처", "수신처", "납품처", "받는곳", "받는 곳", "customer", "buyer", "bill to"])
        return ParsedDocument(
            document_type=doc_type,
            title=self._guess_title(lines, doc_type, filename),
            extracted_date=issue_date or self._extract_date(joined),
            extracted_amount=amount,
            currency=currency,
            merchant_name=vendor_name or (self._guess_merchant(lines) if doc_type == DocumentType.receipt else None),
            vendor_name=vendor_name,
            customer_name=customer_name,
            document_number=self._extract_document_number(joined),
            issue_date=issue_date,
            due_date=due_date,
            subtotal=subtotal,
            tax=tax,
            business_fields=business_fields,
            line_items=line_items,
            category=category,
            tags=self._guess_tags(joined, category, doc_type),
        )

    def _guess_document_type(self, text: str, filename: str) -> DocumentType:
        content = text.lower()
        first_lines_for_return = "\n".join(line.strip() for line in text.splitlines()[:8])
        if re.search(r"\bRTN[-_ ]?\d{4}|credit\s+(?:note|memo)|return\s+note", text, flags=re.IGNORECASE) or re.search(r"(반품\s*/?\s*차감|차감\s*요청|반품\s*요청)", first_lines_for_return, flags=re.IGNORECASE):
            return DocumentType.general_document
        handwritten_type_text = re.sub(r"\s+", "", text.casefold())
        if re.search(r"(간이검사기록|입고확인|검사수량|치수이상없음|수량확인완료)", handwritten_type_text):
            return DocumentType.inspection_report
        if re.search(r"(거래멈세서|거래명세서|거래명세)", handwritten_type_text):
            return DocumentType.transaction_statement
        if re.search(r"(납품서|납품메모)", handwritten_type_text):
            return DocumentType.delivery_note
        first_lines = "\n".join(line.strip().lower() for line in text.splitlines()[:6])
        scored_types: list[tuple[int, DocumentType]] = []
        for document_type, keywords in MANUFACTURING_TYPE_INDICATORS:
            strong_hits = sum(1 for keyword in keywords if keyword.lower() in first_lines)
            content_hits = sum(1 for keyword in keywords if keyword.lower() in content)
            score = strong_hits * 6 + content_hits
            if document_type == DocumentType.purchase_order and re.search(r"\b(?:PO|FAXx?-PO|FAX-PO)[-_ ]?\d{4}|발주서|발주번호", text, flags=re.IGNORECASE):
                score += 8
            if document_type == DocumentType.quotation and re.search(r"\bQT[-_ ]?\d{4}|견적서|견적번호", text, flags=re.IGNORECASE):
                score += 8
            if document_type == DocumentType.invoice and re.search(r"\bINV[-_ ]?(?:[A-Z0-9]+[-_ ]?)*\d{4}|계산서번호|세금계산서|invoice", text, flags=re.IGNORECASE):
                score += 8
            if document_type == DocumentType.delivery_note and re.search(r"\bDN[-_ ]?\d{4}|납품서|납품번호", text, flags=re.IGNORECASE):
                score += 8
            if document_type == DocumentType.transaction_statement and re.search(r"\bTS[-_ ]?\d{4}|거래명세서", text, flags=re.IGNORECASE):
                score += 8
            if document_type == DocumentType.inspection_report and re.search(r"\b(?:IQC|QC)[-_ ]?\d{4}|입고검사|검사번호|합격수량", text, flags=re.IGNORECASE):
                score += 10
            if score > 0:
                scored_types.append((score, document_type))
        if scored_types:
            scored_types.sort(key=lambda entry: -entry[0])
            if scored_types[0][0] >= 7:
                return scored_types[0][1]
        if re.search(r"\bINV[-_ ]?(?:[A-Z0-9]+[-_ ]?)*\d{4}|\b(?:invoice|tax)\s*(?:no|number)", content, flags=re.IGNORECASE):
            return DocumentType.invoice
        if re.search(r"\bQT[-_ ]?\d{4}|\b(?:quotation|quote)\s*(?:no|number)", content, flags=re.IGNORECASE):
            return DocumentType.quotation
        if re.search(r"\bPO[-_ ]?\d{4}|\b(?:po|purchase\s+order)\s*(?:no|number)", content, flags=re.IGNORECASE):
            return DocumentType.purchase_order
        if re.search(r"\bDN[-_ ]?\d{4}|\bdelivery\s+note\s*(?:no|number)", content, flags=re.IGNORECASE):
            return DocumentType.delivery_note
        if re.search(r"\bTS[-_ ]?\d{4}|\btransaction\s+statement\s*(?:no|number)", content, flags=re.IGNORECASE):
            return DocumentType.transaction_statement
        haystack = f"{filename}\n{text}".lower()
        if self._score_korean_manufacturing(haystack, ["포장명세서", "packing list"]) >= 1:
            return DocumentType.packing_list
        if self._score_korean_manufacturing(haystack, ["검사성적서", "inspection report"]) >= 1:
            return DocumentType.inspection_report
        if self._score_korean_manufacturing(haystack, ["계약서", "contract"]) >= 1:
            return DocumentType.contract
        if self._score_korean_manufacturing(haystack, ["세금계산서", "invoice", "청구서"]) >= 1:
            return DocumentType.invoice
        receipt_score = sum(keyword in haystack for keyword in ["receipt", "subtotal", "total", "tax", "change", "visa"])
        invoice_score = sum(keyword in haystack for keyword in ["invoice", "invoice number", "invoice #", "vendor", "bill to", "amount due", "total due"])
        presentation_score = sum(keyword in haystack for keyword in ["presentation", "slide", "speaker notes", "speaking notes", "talk track", "rehearse", "script"])
        guide_score = sum(keyword in haystack for keyword in ["installation guide", "setup guide", "technical guide", "project setup", "install", "configuration", "environment variables", "dependencies"])
        tracker_score = sum(keyword in haystack for keyword in ["implementation schedule", "project tracker", "roadmap", "task", "feature", "status", "testing", "coverage", "pipeline", "claimed"])
        notice_score = sum(keyword in haystack for keyword in ["notice", "announcement", "effective date", "deadline", "meeting"])
        memo_score = sum(keyword in haystack for keyword in ["memo", "note", "reminder", "todo"])
        if invoice_score >= 2:
            return DocumentType.document
        if guide_score >= 2 or tracker_score >= 3:
            return DocumentType.document
        if presentation_score >= 2:
            return DocumentType.presentation
        if receipt_score >= 2:
            return DocumentType.receipt
        if notice_score >= 1:
            return DocumentType.notice
        if memo_score >= 1:
            return DocumentType.memo
        return DocumentType.general_document if len(text) > 250 else DocumentType.other

    def _score_korean_manufacturing(self, haystack: str, keywords: list[str]) -> int:
        return sum(1 for keyword in keywords if keyword.lower() in haystack)

    def _extract_date(self, text: str) -> date | None:
        candidates = re.findall(r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|[A-Z][a-z]+ \d{1,2}, \d{4})\b", text)
        candidates.extend(re.findall(r"\b\d{4}[.년]\s*\d{1,2}[.월]\s*\d{1,2}[.일]?\b", text))
        for candidate in candidates:
            normalized = candidate
            if re.search(r"[년월일./-]", normalized):
                parts = re.findall(r"\d{1,4}", normalized)
                if len(parts) >= 3:
                    if len(parts[0]) <= 2 and int(parts[0]) > 12:
                        year = int(parts[0]) + 2000
                        normalized = f"{year}-{parts[1]}-{parts[2]}"
                    else:
                        year = int(parts[0])
                        if year < 100 and len(parts[2]) == 4:
                            normalized = f"{parts[2]}-{parts[0]}-{parts[1]}"
                        elif year < 100:
                            normalized = f"{year + 2000}-{parts[1]}-{parts[2]}"
                        else:
                            normalized = f"{year}-{parts[1]}-{parts[2]}"
            normalized = normalized.replace("-", "/") if re.match(r"\d{1,2}-\d{1,2}-", normalized) else normalized
            for pattern in DATE_PATTERNS:
                try:
                    return datetime.strptime(normalized, pattern).date()
                except ValueError:
                    continue
        return None

    def _extract_amount(self, text: str) -> Decimal | None:
        priority_lines = [
            line for line in text.splitlines()
            if re.search(r"\b(total|amount due|balance|grand total)\b|합계|총액|청구금액|공급대가", line, flags=re.IGNORECASE)
        ]
        for line in priority_lines + [text]:
            matches = re.findall(r"(?:USD|KRW|₩|\$)?\s*([0-9]{1,9}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", line)
            if matches:
                return max(Decimal(value.replace(",", "")) for value in matches)
        return None

    def _extract_labeled_amount(self, text: str, labels: list[str]) -> Decimal | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        values: list[Decimal] = []
        lines = text.splitlines()
        normalized_label_keys = {re.sub(r"[\s:：]+", "", label.lower()) for label in labels}
        for index, line in enumerate(lines):
            if self._looks_like_computed_or_note_amount(line):
                continue
            match = re.search(
                rf"(?:^|\s)(?:{label_pattern})\s*(?:KRW|USD|₩|원|\$)?\s*[:：]?\s*(?:KRW|USD|₩|원|\$)?\s*([-+]?\d[\d,]*(?:\.\d+)?[A-Za-z]?)\s*(?:원|KRW|USD)?",
                line,
                flags=re.IGNORECASE,
            )
            if match:
                value = self._amount_from_labeled_match(match.group(1), line)
                if value is not None:
                    values.append(value)
                    continue
            line_key = self._normalized_amount_label_key(line)
            if line_key in normalized_label_keys and index + 1 < len(lines):
                lookahead = " ".join(self._amount_label_lookahead(lines, index + 1))
                value = self._amount_from_labeled_match(lookahead, f"{line} {lookahead}")
                if value is not None:
                    values.append(value)
        values = [value for value in values if value is not None]
        return values[-1] if values else None

    def _amount_label_lookahead(self, lines: list[str], start_index: int) -> list[str]:
        collected: list[str] = []
        for line in lines[start_index:start_index + 4]:
            if not collected and self._looks_like_amount_label_line(line):
                break
            if collected and self._looks_like_amount_label_line(line):
                break
            collected.append(line)
            if re.search(r"\d", line) and not re.fullmatch(r"(?:KRW|USD|₩|\$)", line.strip(), flags=re.IGNORECASE):
                break
        return collected

    def _looks_like_amount_label_line(self, line: str) -> bool:
        key = self._normalized_amount_label_key(line)
        return key in {
            "공급가액", "공급가액합계", "공급액", "공급금액", "subtotal", "supplyamount", "supplytotal",
            "금월공급가액", "차감공급가액", "차감공급액", "세액", "부가세", "금월세액", "차감세액", "vat", "tax",
            "총액", "총합계", "합계", "합계금액", "금월합계", "차감합계", "invoicetotal", "grandtotal", "totaldue", "totalamount", "amountdue", "total",
        }

    def _looks_like_summary_amount_label_line(self, line: str) -> bool:
        key = self._normalized_amount_label_key(line)
        return key in {
            "공급가액합계", "공급액합계", "공급금액합계", "금월공급가액", "차감공급가액", "차감공급액",
            "세액합계", "금월세액", "차감세액", "총액", "총합계", "합계금액", "금월합계", "차감합계",
            "grandtotal", "totaldue", "totalamount", "amountdue",
        }

    def _normalized_amount_label_key(self, line: str) -> str:
        key = re.sub(r"[\s:：]+", "", str(line or "").lower())
        key = re.sub(r"(?:krw|usd|₩|원|\\$)$", "", key)
        return key

    def _amount_from_labeled_match(self, value_text: str, context: str) -> Decimal | None:
        candidates: list[Decimal] = []
        for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?[A-Za-z]?", value_text):
            cleaned = re.sub(r"[A-Za-z]$", "", token)
            value = self._to_decimal(cleaned)
            if value is not None and re.search(r"[CcGgLl]$", token):
                value *= Decimal("10")
            elif value is None:
                value = self._to_decimal(token)
            if value is not None:
                candidates.append(value)
        if not candidates:
            return None
        value = max(candidates)
        if re.search(r"\bUSD\b|\$", context, flags=re.IGNORECASE) and "." not in str(value_text) and value >= Decimal("10000"):
            cents = value / Decimal("100")
            if cents == cents.quantize(Decimal("0.01")):
                return cents
        return value

    def _looks_like_computed_or_note_amount(self, line: str) -> bool:
        lowered = line.lower()
        return bool(re.search(r"(실제\s*품목\s*합계|품목\s*합계는|line\s*item\s*(?:sum|total)|computed)", lowered, flags=re.IGNORECASE))

    def _extract_currency(self, text: str) -> str | None:
        if re.search(r"\bUSD\b|US\$|\$\s*\d|\d[\d,]*(?:\.\d+)?\s*(?:USD|US\$)", text, flags=re.IGNORECASE):
            return "USD"
        if re.search(r"\bKRW\b|₩|원", text, flags=re.IGNORECASE):
            return "KRW"
        return None

    def _document_scope_text(self, lines: list[str]) -> str:
        scoped: list[str] = []
        in_explicit_item_block = False
        for line in lines:
            lowered = line.lower()
            if "[item table start]" in lowered:
                in_explicit_item_block = True
                continue
            if "[item table end]" in lowered:
                in_explicit_item_block = False
                continue
            if in_explicit_item_block:
                continue
            scoped.append(line)
        return "\n".join(scoped)

    def _extract_labeled_text(self, text: str, labels: list[str]) -> str | None:
        lines = [line.strip() for line in text.splitlines()]
        normalized_labels = {re.sub(r"[\s:：]+", "", label.lower()) for label in labels}
        for index, line in enumerate(lines[:-1]):
            if re.sub(r"[\s:：]+", "", line.lower()) not in normalized_labels:
                continue
            for candidate in lines[index + 1:]:
                value = candidate.strip(" -:：")
                if not value:
                    continue
                value = self._truncate_at_business_label_boundary(value)
                if self._looks_like_business_label(value):
                    continue
                if self._looks_like_instruction_or_note(value):
                    continue
                return value[:120] or None

        label_pattern = "|".join(re.escape(label) for label in labels)
        pattern = rf"(?:{label_pattern})\s*[:：]?\s*([^\n|]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        value = re.sub(r"\s+", " ", match.group(1)).strip(" -:：")
        value = self._truncate_at_business_label_boundary(value)
        if self._looks_like_instruction_or_note(value) or self._looks_like_business_label(value):
            return None
        return value[:120] or None

    def _looks_like_business_label(self, value: str) -> bool:
        key = re.sub(r"[\s:：#/-]+", "", value.lower())
        labels = {
            "공급업체", "공급자", "공급받는자", "고객사", "고객시", "구매처", "발주처", "수신처",
            "납품처", "계산서번호", "발주번호", "견적번호", "납품번호", "거래명세서번호", "문서번호",
            "작성일자", "작성일", "발행일", "지급기한", "유효기간", "검사번호", "관련납품서",
            "vendor", "supplier", "customer", "buyer", "invoice no", "invoice number", "po no",
        }
        return key in {re.sub(r"[\s:：#/-]+", "", label.lower()) for label in labels}

    def _truncate_at_business_label_boundary(self, value: str) -> str:
        boundary_labels = [
            "공급업체", "공급자", "판매자", "매입처", "발행처", "청구처",
            "공급받는자", "고객사", "구매처", "발주처", "수신처", "납품처", "수요처", "구매자",
            "입고장소", "납품장소", "배송지", "수령", "수령자", "차량번호",
            "발행일", "작성일", "작성일자", "견적일", "납품일", "납기일", "지급기한", "유효기간", "통화",
            "문서번호", "발주번호", "견적번호", "납품번호", "거래명세서번호", "계산서번호", "인보이스번호",
            "Lot No", "검사번호", "판정", "비고",
            "supplier", "vendor", "seller", "customer", "buyer", "bill to", "ship to",
            "issue date", "due date", "payment due", "delivery date", "valid until", "currency",
            "item name", "vendor sku", "품목명", "품목코드",
        ]
        label_pattern = "|".join(re.escape(label) for label in boundary_labels)
        match = re.search(rf"\s+(?:{label_pattern})\s*[:：]?\s+", value, flags=re.IGNORECASE)
        return value[: match.start()].strip(" -:：") if match else value

    def _looks_like_instruction_or_note(self, value: str) -> bool:
        return bool(re.search(r"(must\s+not|should\s+not|do\s+not|주의|참고|note|warning|column)", value, flags=re.IGNORECASE))

    def _extract_labeled_date(self, text: str, labels: list[str]) -> date | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?:{label_pattern})\s*[:：]?\s*(\d{{4}}[.\-/년]\s*\d{{1,2}}[.\-/월]\s*\d{{1,2}}[일]?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return self._extract_date(match.group(1))
        normalized_labels = {re.sub(r"[\s:：]+", "", label.lower()) for label in labels}
        lines = [line.strip() for line in text.splitlines()]
        for index, line in enumerate(lines[:-1]):
            if re.sub(r"[\s:：]+", "", line.lower()) not in normalized_labels:
                continue
            for candidate in lines[index + 1 : min(len(lines), index + 5)]:
                parsed = self._extract_date(candidate)
                if parsed:
                    return parsed
                if candidate and not re.search(r"^(?:검토|확인|review|check)$", candidate, flags=re.IGNORECASE):
                    break
        return None

    def _extract_document_number(self, text: str) -> str | None:
        if self._has_return_or_credit_signal(text):
            return_number = self._first_document_number_for_prefix(text, "RTN")
            if return_number:
                return return_number
        if self._has_internal_transfer_signal(text):
            transfer_number = self._first_document_number_for_prefix(text, "TRF")
            if transfer_number:
                return transfer_number
        labels = [
            "발주번호", "발주 번호", "견적번호", "견적 번호", "거래명세서번호", "납품번호", "계산서번호", "인보이스번호", "청구서번호", "문서번호",
            "po no", "po number", "purchase order no", "qt no", "quote no", "quotation no", "statement no", "delivery note no", "dn no", "invoice no", "inv no",
        ]
        normalized_labels = {re.sub(r"[\s:：#]+", "", label.lower()) for label in labels}
        lines = [line.strip() for line in text.splitlines()]
        strong = self._best_document_number_from_text(text)
        best_labeled: str | None = None
        for index, line in enumerate(lines[:-1]):
            if re.sub(r"[\s:：#]+", "", line.lower()) not in normalized_labels:
                continue
            value = self._normalize_document_number_candidate(lines, index + 1)
            if value and self._document_number_score(value) >= 20:
                if best_labeled is None or self._document_number_score(value) > self._document_number_score(best_labeled):
                    best_labeled = value
        if strong and (
            best_labeled is None
            or self._document_number_score(strong) > self._document_number_score(best_labeled)
            or len(strong) > len(best_labeled) + 3
        ):
            return strong
        if best_labeled:
            return best_labeled
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_pattern})\s*[:：#]?\s*([A-Za-z0-9가-힣._/-]+)", text, flags=re.IGNORECASE)
        value = self._normalize_document_number(match.group(1)) if match else None
        if value and self._document_number_score(value) >= 20:
            return value
        return None

    def _has_return_or_credit_signal(self, text: str) -> bool:
        return bool(re.search(
            r"\bRTN[-_ ]?\d{4}|반품\s*/?\s*차감|반품\s*요청|차감\s*요청|credit\s+memo|credit\s+note|return\s+note",
            text,
            flags=re.IGNORECASE,
        ))

    def _has_internal_transfer_signal(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        if re.search(r"\bTRF[-_ ]?\d{4}", text, flags=re.IGNORECASE):
            return True
        if re.search(r"내부\s*(?:자재\s*)?이동|자재\s*이동|사업장\s*간|창고\s*이동|지점\s*이동", text, flags=re.IGNORECASE):
            return True
        has_from_to_warehouse = "출고창고" in normalized and "입고창고" in normalized
        has_inventory_rows = bool(re.search(r"내부품목코드|요청수량|요청수림", text, flags=re.IGNORECASE))
        return has_from_to_warehouse and has_inventory_rows

    def _first_document_number_for_prefix(self, text: str, prefix: str) -> str | None:
        match = re.search(rf"\b{re.escape(prefix)}[-_ ]?\d{{4}}[-_ ]?\d{{3,4}}(?:[-_ ][A-Z0-9]{{1,8}}){{0,2}}\b", text, flags=re.IGNORECASE)
        return self._normalize_document_number(match.group(0)) if match else None

    def _best_document_number_from_text(self, text: str) -> str | None:
        patterns = [
            r"\bFAX(?:[-_][A-Z][A-Z0-9]{1,9})*[-_]PO[-_]\d{4}(?:[-_][A-Z0-9]*\d[A-Z0-9]*)+(?:[-_][A-Z]{1,10}){0,2}\b",
            r"\b(?:FAXx?-)?PO0?[-_ ]?\d{4}[-_]?[0O]?\d{3,4}(?:[-_][A-Z0-9]{2,8}){0,2}\b",
            r"\bQT[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{2,8}){0,2}\b",
            r"\bINV[-_ ]?(?:US[-_ ]?)?\d{4}[-_ ]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\bDN[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\bTS[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\b(?:I?QC|QC)[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\bRTN[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\bTRF[-_ ]?\d{4}[-_]?\d{3,4}(?:[-_][A-Z0-9]{1,8}){0,2}\b",
            r"\b(?:PO|QT|INV|DN|TS|IQC|QC|RTN|TRF)(?:[-_][A-Z][A-Z0-9]{1,9})*[-_]\d{4}(?:[-_][A-Z0-9]*\d[A-Z0-9]*)+(?:[-_][A-Z]{1,10}){0,2}\b",
        ]
        candidates: list[str] = []
        compact_text = re.sub(r"(?<=\bINV-\d{4})-\s*-\s*(?=\d)", "-", text, flags=re.IGNORECASE)
        for pattern in patterns:
            candidates.extend(match.group(0) for match in re.finditer(pattern, compact_text, flags=re.IGNORECASE))
        normalized = [self._normalize_document_number(candidate) for candidate in candidates]
        normalized = [candidate for candidate in normalized if candidate]
        if not normalized:
            return None
        normalized.sort(key=lambda value: (-self._document_number_score(value), -len(value)))
        return normalized[0]

    def _normalize_document_number(self, value: object) -> str | None:
        text = re.sub(r"\s+", "", str(value or "")).strip(" -:：[](){}")
        if not text:
            return None
        text = text.replace("_", "-")
        text = re.sub(r"^FAXx-", "FAX-", text, flags=re.IGNORECASE)
        text = re.sub(r"^PO0-", "PO-", text, flags=re.IGNORECASE)
        text = re.sub(r"^(QC-)", "IQC-", text, flags=re.IGNORECASE)
        if re.match(r"^S-\d{4}-\d{4}-", text, flags=re.IGNORECASE):
            text = f"T{text}"
        if re.match(r"^OT-\d{4}-", text, flags=re.IGNORECASE):
            text = f"QT{text[2:]}"
        parts = text.split("-")
        fixed_parts: list[str] = []
        for index, part in enumerate(parts):
            if index >= 1 and re.fullmatch(r"[0-9O]{3,4}", part, flags=re.IGNORECASE):
                part = part.replace("O", "0").replace("o", "0")
            if index >= 2 and re.fullmatch(r"[0-9O]{2,5}", part, flags=re.IGNORECASE):
                part = part.replace("O", "0").replace("o", "0")
            fixed_parts.append(part)
        text = "-".join(fixed_parts)
        return text.upper()[:80] or None

    def _normalize_document_number_candidate(self, lines: list[str], start_index: int) -> str | None:
        first = self._normalize_document_number(lines[start_index] if start_index < len(lines) else "")
        if not first:
            return None
        parts = [first]
        for offset in range(start_index + 1, min(len(lines), start_index + 4)):
            candidate = re.sub(r"\s+", "", lines[offset]).strip(" -:：[](){}")
            if not candidate:
                continue
            if self._looks_like_document_number_continuation(candidate):
                parts.append(candidate)
                continue
            break
        return self._normalize_document_number("-".join(parts))

    def _document_number_score(self, value: str) -> int:
        text = str(value or "")
        if self._looks_like_business_label(text):
            return -100
        score = 0
        if re.match(r"^(?:PO|QT|INV|DN|TS|IQC|QC|RTN|TRF|FAX-PO)-", text, flags=re.IGNORECASE):
            score += 40
        if re.match(r"^FAX(?:-[A-Z0-9]{2,10})*-PO-", text, flags=re.IGNORECASE):
            score += 40
        if re.search(r"\d{4}", text):
            score += 10
        if text.count("-") >= 2:
            score += 8
        if re.search(r"[가-힣]", text):
            score -= 35
        return score

    def _looks_like_document_number_continuation(self, value: str) -> bool:
        if re.fullmatch(r"\d{2,5}", value):
            return True
        if re.fullmatch(r"\d{4}-\d{2,4}(?:-\d{2,5})?", value):
            return True
        if re.fullmatch(r"[A-Z]{1,4}-?\d{2,5}", value, flags=re.IGNORECASE):
            return True
        return False

    def _is_no_amount_quantity_document(self, lines: list[str], doc_type: DocumentType) -> bool:
        text = "\n".join(lines)
        if self._is_no_price_delivery_note(lines, doc_type):
            return True
        if doc_type == DocumentType.inspection_report:
            return True
        normalized = re.sub(r"\s+", "", text.lower())
        internal_transfer_signal = bool(re.search(r"(사업장간|자재이동|내부.*이동|요청수량|요청수림|내부품목코드|\bTRF[-_ ]?\d{4})", normalized, flags=re.IGNORECASE))
        amount_signal = bool(re.search(r"(단가|공급가액|공급액|세액|부가세|합계금액|총액|unit\s*price|subtotal|tax|total)", text, flags=re.IGNORECASE))
        return internal_transfer_signal and not amount_signal

    def _is_option_selection_quotation(self, lines: list[str], doc_type: DocumentType) -> bool:
        if doc_type != DocumentType.quotation:
            return False
        text = "\n".join(lines)
        return bool(re.search(r"(옵션|option).*?(하나\s*선택|선택\s*필요|모두\s*합산하면\s*안)|모두\s*합산하면\s*안", text, flags=re.IGNORECASE | re.DOTALL))

    def _is_no_price_delivery_note(self, lines: list[str], doc_type: DocumentType) -> bool:
        if doc_type != DocumentType.delivery_note:
            return False
        text = "\n".join(lines)
        if re.search(r"(단가\s*/?\s*금액\s*없이|금액\s*정보\s*없|no\s+price\s+columns|수량\s*확인용)", text, flags=re.IGNORECASE):
            return True
        has_delivery_quantity_headers = bool(re.search(r"(발주수량|납품수량|잔량|입고수량)", text))
        has_amount_headers = bool(re.search(r"(단가|공급가액|공급액|세액|부가세|합계금액|총액|unit\s*price|subtotal|tax|total)", text, flags=re.IGNORECASE))
        return has_delivery_quantity_headers and not has_amount_headers

    def _strip_line_item_amount_fields(self, item: dict) -> dict:
        stripped = dict(item)
        for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]:
            stripped.pop(field, None)
        warnings = [
            warning for warning in stripped.get("validation_warnings", [])
            if warning not in {"missing_price_or_total", "invalid_line_total", "amount_mismatch"}
        ]
        if warnings:
            stripped["validation_warnings"] = warnings
        else:
            stripped.pop("validation_warnings", None)
        return stripped

    def _extract_business_fields(self, text: str, doc_type: DocumentType) -> dict:
        fields: dict[str, object] = {}
        related_document_number = self._extract_labeled_text(text, ["관련납품서", "관련 원문서", "관련원문서", "관련 문서번호", "관련문서번호", "원 납품서", "원납품서", "related delivery note", "related document", "source document"])
        if related_document_number:
            fields["related_document_number"] = self._normalize_document_number(related_document_number) or related_document_number
        if doc_type == DocumentType.quotation:
            fields["quotation_date"] = self._date_string(self._extract_labeled_date(text, ["견적일", "quotation date", "quote date"]))
            fields["valid_until"] = self._date_string(self._extract_labeled_date(text, ["유효기간", "견적유효기간", "valid until", "expires"]))
            fields["delivery_terms"] = self._extract_labeled_text(text, ["납기조건", "delivery terms"])
            fields["payment_terms"] = self._extract_labeled_text(text, ["결제조건", "payment terms"])
        elif doc_type == DocumentType.transaction_statement:
            fields["transaction_date"] = self._date_string(self._extract_labeled_date(text, ["거래일자", "거래일", "transaction date"]))
        elif doc_type == DocumentType.delivery_note:
            fields["delivery_date"] = self._date_string(self._extract_labeled_date(text, ["납품일", "납품일자", "delivery date"]))
            fields["receiving_location"] = self._extract_labeled_text(text, ["입고장소", "납품장소", "receiving location"])
            fields["receiver_name"] = self._extract_labeled_text(text, ["수령자", "인수자", "receiver"])
        elif doc_type == DocumentType.invoice:
            fields["payment_due_date"] = self._date_string(self._extract_labeled_date(text, ["지급기한", "결제기한", "payment due date", "payment due", "due date"]))
            fields["business_registration_numbers"] = re.findall(r"\b\d{3}-\d{2}-\d{5}\b", text)
        return {key: value for key, value in fields.items() if value not in (None, "", [])}

    def _extract_issue_date(self, text: str, doc_type: DocumentType) -> date | None:
        labels_by_type = {
            DocumentType.purchase_order: ["발행일", "발행일자", "작성일", "발주일", "발주일자", "issue date", "date"],
            DocumentType.quotation: ["견적일", "발행일", "작성일", "quotation date", "quote date", "date"],
            DocumentType.transaction_statement: ["거래일자", "거래일", "발행일", "작성일", "transaction date", "issue date"],
            DocumentType.delivery_note: ["발행일", "작성일", "issue date"],
            DocumentType.invoice: ["작성일자", "작성일", "발행일", "발행일자", "계산서일자", "invoice date", "issue date", "date"],
        }
        labels = labels_by_type.get(doc_type, ["발행일", "작성일", "일자", "issue date"])
        return self._extract_labeled_date(text, labels)

    def _extract_due_date(self, text: str, doc_type: DocumentType) -> date | None:
        labels_by_type = {
            DocumentType.purchase_order: ["납기일", "납기요청일", "납기 요청", "납품요청일", "납품예정일", "납품 예정일", "requested delivery date", "due delivery", "delivery due", "delivery due date", "due date"],
            DocumentType.quotation: ["유효기간", "견적유효기간", "valid until", "expiration date"],
            DocumentType.delivery_note: ["납품일", "납품일자", "delivery date"],
            DocumentType.invoice: ["지급기한", "결제기한", "payment due date", "payment due", "due date"],
        }
        labels = labels_by_type.get(doc_type)
        return self._extract_labeled_date(text, labels) if labels else None

    def _date_string(self, value: date | None) -> str | None:
        return value.isoformat() if value else None

    def _extract_line_items(self, lines: list[str], doc_type: DocumentType | None = None) -> list[dict]:
        vl_inline_items = self._extract_vl_inline_table_items(lines, doc_type)
        if vl_inline_items:
            return self._dedupe_line_items(vl_inline_items)[:80]
        if doc_type == DocumentType.inspection_report:
            special_items = self._extract_special_quantity_table_items(lines)
            if special_items:
                return self._dedupe_line_items(self._suppress_incomplete_inspection_quantities(special_items))[:80]
        if doc_type == DocumentType.invoice:
            foreign_invoice_items = self._extract_foreign_invoice_vertical_line_items(lines)
            if foreign_invoice_items:
                return self._dedupe_line_items(foreign_invoice_items)[:80]
        numbered_items = self._extract_numbered_vertical_table_items(lines, doc_type)
        if self._should_prefer_numbered_vertical_items(numbered_items, lines, doc_type):
            return self._dedupe_line_items(numbered_items)[:80]
        item_block_lines = self._explicit_item_block_lines(lines)
        items = self._extract_key_value_line_items(item_block_lines or lines)
        items.extend(self._extract_table_line_items(lines))
        items.extend(self._normalize_line_item(candidate.item) for candidate in reconstruct_ocr_line_items(lines))
        special_items = self._extract_special_quantity_table_items(lines)
        if special_items and self._should_use_sparse_special_items(items):
            items.extend(special_items)
        repeated_amount_items = self._extract_sparse_repeated_amount_table_items(lines)
        if repeated_amount_items and self._should_use_repeated_amount_items(items, repeated_amount_items):
            items = repeated_amount_items
        fragmented_fax_items = self._extract_fragmented_fax_table_items(lines)
        if fragmented_fax_items and len(fragmented_fax_items) > len(items):
            items = fragmented_fax_items
        table_row_indexes = self._table_row_indexes(lines)
        for line_index, line in enumerate(lines):
            if line_index in table_row_indexes:
                continue
            normalized = re.sub(r"\s+", " ", line).strip()
            if not normalized or not self._looks_like_item_line(normalized):
                continue
            parts = [part.strip() for part in re.split(r"\s*\|\s*|\t+|,{2,}", normalized) if part.strip()]
            if len(parts) >= 6:
                item = self._line_item_from_parts(parts)
            else:
                item = self._line_item_from_free_text(normalized)
            if item and item.get("item_name"):
                items.append(self._normalize_line_item(item))
        if doc_type == DocumentType.inspection_report:
            items = self._suppress_incomplete_inspection_quantities(items)
        return self._dedupe_line_items(items)[:80]

    def _extract_vl_inline_table_items(self, lines: list[str], doc_type: DocumentType | None = None) -> list[dict]:
        """Extract rows from VL plain-text tables where columns are space-separated.

        PaddleOCR-VL often returns a readable table as one line per row instead of
        preserving pipe/tab delimiters. This path handles rows ending in
        qty/unit/unit_price/supply/tax/total without relying on filename-specific
        expectations.
        """
        header_index = next((
            index for index, line in enumerate(lines)
            if self._looks_like_vl_inline_table_header(line, doc_type)
        ), None)
        if header_index is None:
            return []
        items: list[dict] = []
        for row in lines[header_index + 1:]:
            if self._looks_like_numbered_table_footer(row) or self._looks_like_instruction_or_note(row):
                break
            item = self._vl_inline_table_item_from_row(row, doc_type)
            if item:
                items.append(self._normalize_line_item(item))
        if len(items) < 2:
            return []
        return items

    def _looks_like_vl_inline_table_header(self, line: str, doc_type: DocumentType | None = None) -> bool:
        text = str(line or "")
        if not re.search(r"(품목명|반품품목|품목\s*코드|item\s+name|item\s+code|description|vendor\s+sku)", text, flags=re.IGNORECASE):
            return False
        has_quantity = bool(re.search(r"(수량|qty|quantity)", text, flags=re.IGNORECASE))
        has_amount = bool(re.search(r"(단가|unit\s*price|공급가액|subtotal|supply|amount|total|세액|합계)", text, flags=re.IGNORECASE))
        has_no_price_quantity_columns = bool(
            re.search(r"(발주수량|납품수량|잔량|요청수량|입고수량|합격수량|불량수량)", text, flags=re.IGNORECASE)
        )
        if has_quantity and has_amount:
            return True
        if doc_type in {DocumentType.delivery_note, DocumentType.inspection_report, DocumentType.general_document, DocumentType.memo}:
            return has_no_price_quantity_columns or (has_quantity and not has_amount)
        return has_quantity and has_no_price_quantity_columns

    def _vl_inline_table_item_from_row(self, row: str, doc_type: DocumentType | None = None) -> dict | None:
        text = re.sub(r"\s+", " ", str(row or "")).strip(" |")
        if not text or re.search(r"^(?:total|subtotal|vat|tax|공급가액|부가세|총액|합계)", text, flags=re.IGNORECASE):
            return None
        if self._looks_like_line_item_header_text(text):
            return None
        text = self._strip_duplicate_trailing_amount(text)
        if self._vl_inline_no_price_context(doc_type) and not self._looks_like_priced_vl_inline_row(text):
            no_price_item = self._vl_inline_no_price_item_from_row(text, doc_type)
            if no_price_item:
                return no_price_item
        pattern = re.compile(
            r"^(?P<body>.+?)\s+"
            r"(?P<quantity>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
            r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
            r"(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
            r"(?P<supply_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
            r"(?P<tax_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
            r"(?P<line_total>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
            flags=re.IGNORECASE,
        )
        match = pattern.match(text)
        missing_supply_match = None
        missing_quantity_match = None
        missing_quantity_supply_only_match = None
        supply_only_match = None
        supply_without_unit_price_match = None
        if not match:
            missing_supply_match = re.match(
                r"^(?P<body>.+?)\s+"
                r"(?P<quantity>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
                r"(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<tax_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<line_total>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE,
            )
        if not match and not missing_supply_match:
            supply_only_match = re.match(
                r"^(?P<body>.+?)\s+"
                r"(?P<quantity>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
                r"(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<supply_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE,
            )
        if not match and not missing_supply_match and not supply_only_match:
            supply_without_unit_price_match = re.match(
                r"^(?P<body>.+?)\s+"
                r"(?P<quantity>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
                r"(?P<supply_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE,
            )
        if not match and not missing_supply_match and not supply_only_match and not supply_without_unit_price_match:
            missing_quantity_supply_only_match = re.match(
                r"^(?P<body>.+?)\s+"
                r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
                r"(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<supply_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE,
            )
        if not match and not missing_supply_match and not missing_quantity_supply_only_match:
            missing_quantity_match = re.match(
                r"^(?P<body>.+?)\s+"
                r"(?P<unit>[A-Za-z가-힣]{1,8})\s+"
                r"(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<supply_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<tax_amount>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<line_total>[-+]?\d[\d,]*(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE,
            )
        row_match = (
            match
            or missing_supply_match
            or supply_only_match
            or supply_without_unit_price_match
            or missing_quantity_supply_only_match
            or missing_quantity_match
        )
        if not row_match:
            if self._vl_inline_no_price_context(doc_type):
                return self._vl_inline_no_price_item_from_row(text, doc_type)
            return None
        body = re.sub(r"^\d+\s+", "", row_match.group("body")).strip(" -|")
        if not re.search(r"[A-Za-z가-힣]", body):
            return None
        item_name, item_code, specification = self._split_vl_inline_item_identity(body)
        if not item_name:
            return None
        item = {
            "item_name": item_name,
            "item_code": item_code,
            "specification": specification,
            "unit": row_match.group("unit"),
        }
        if "unit_price" in row_match.groupdict():
            item["unit_price"] = row_match.group("unit_price")
        warnings: list[str] = []
        if match:
            item["quantity"] = match.group("quantity")
            item["supply_amount"] = match.group("supply_amount")
            item["tax_amount"] = match.group("tax_amount")
            item["line_total"] = match.group("line_total")
        elif missing_supply_match:
            item["quantity"] = missing_supply_match.group("quantity")
            first_amount = self._to_decimal(missing_supply_match.group("tax_amount"))
            second_amount = self._to_decimal(missing_supply_match.group("line_total"))
            apparent_supply = self._to_decimal(missing_supply_match.group("unit_price"))
            if (
                doc_type == DocumentType.transaction_statement
                and apparent_supply is not None
                and first_amount is not None
                and second_amount is not None
                and apparent_supply > 0
                and first_amount >= 0
                and abs((apparent_supply + first_amount) - second_amount)
                <= max(Decimal("1"), abs(second_amount) * Decimal("0.02"))
            ):
                item.pop("unit_price", None)
                item["supply_amount"] = self._number_value(apparent_supply)
                item["tax_amount"] = self._number_value(first_amount)
                item["line_total"] = self._number_value(second_amount)
                warnings.append("unit_price_not_visible")
                item["validation_warnings"] = sorted(set(warnings))
                return {key: value for key, value in item.items() if value not in (None, "", [])}
            quantity = self._to_decimal(missing_supply_match.group("quantity"))
            unit_price = self._to_decimal(missing_supply_match.group("unit_price"))
            expected_supply = quantity * unit_price if quantity is not None and unit_price is not None else None
            if (
                expected_supply is not None
                and first_amount is not None
                and abs(first_amount - expected_supply) <= max(Decimal("1"), abs(expected_supply) * Decimal("0.02"))
            ):
                item["supply_amount"] = self._number_value(first_amount)
                if second_amount is not None and second_amount > first_amount:
                    item["line_total"] = self._number_value(second_amount)
                    warnings.append("line_total_without_tax_column")
                elif second_amount is not None:
                    warnings.append("trailing_fragment_ignored")
            inferred_supply = second_amount - first_amount if second_amount is not None and first_amount is not None else None
            if (
                "supply_amount" not in item
                and
                inferred_supply is not None
                and inferred_supply > 0
                and expected_supply is not None
                and abs(expected_supply - inferred_supply) <= max(Decimal("1"), abs(inferred_supply) * Decimal("0.02"))
            ):
                item["supply_amount"] = self._number_value(inferred_supply)
                item["tax_amount"] = self._number_value(first_amount) if first_amount is not None else missing_supply_match.group("tax_amount")
                warnings.append("supply_amount_recovered_from_line_total_tax")
            elif "supply_amount" not in item:
                warnings.append("supply_amount_missing_or_untrusted")
        elif missing_quantity_match:
            item["supply_amount"] = missing_quantity_match.group("supply_amount")
            item["tax_amount"] = missing_quantity_match.group("tax_amount")
            item["line_total"] = missing_quantity_match.group("line_total")
            warnings.extend(["missing_quantity", "quantity_cell_blank"])
        elif supply_only_match:
            item["quantity"] = supply_only_match.group("quantity")
            amount_value = supply_only_match.group("supply_amount")
            if doc_type == DocumentType.invoice and "." in str(amount_value):
                item["supply_amount"] = amount_value
                item["line_total"] = amount_value
            else:
                item["supply_amount"] = amount_value
                warnings.append("row_amount_hidden_do_not_infer")
        elif missing_quantity_supply_only_match:
            item["supply_amount"] = missing_quantity_supply_only_match.group("supply_amount")
            warnings.extend(["missing_quantity", "quantity_cell_blank", "row_amount_hidden_do_not_infer"])
        elif supply_without_unit_price_match:
            item["quantity"] = supply_without_unit_price_match.group("quantity")
            lone_amount = self._to_decimal(supply_without_unit_price_match.group("supply_amount"))
            quantity_value = self._to_decimal(supply_without_unit_price_match.group("quantity"))
            if (
                doc_type == DocumentType.invoice
                and lone_amount is not None
                and quantity_value is not None
                and quantity_value > 1
                and lone_amount < Decimal("1000")
                and "." in supply_without_unit_price_match.group("supply_amount")
            ):
                item["unit_price"] = supply_without_unit_price_match.group("supply_amount")
                warnings.append("missing_line_amount")
            else:
                item["supply_amount"] = supply_without_unit_price_match.group("supply_amount")

        quantity = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
        unit_price = self._to_decimal(str(item.get("unit_price"))) if item.get("unit_price") is not None else None
        supply_amount = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        if (
            quantity is not None
            and unit_price is not None
            and supply_amount is not None
            and quantity > 0
            and unit_price > 0
            and supply_amount > 0
            and abs((quantity * unit_price) - supply_amount) > max(Decimal("1"), abs(supply_amount) * Decimal("0.02"))
        ):
            warnings.append("explicit_quantity_price_amount_mismatch")
        if warnings:
            item["validation_warnings"] = sorted(set(warnings))
        return {key: value for key, value in item.items() if value not in (None, "", [])}

    def _vl_inline_no_price_context(self, doc_type: DocumentType | None) -> bool:
        return doc_type in {
            DocumentType.delivery_note,
            DocumentType.inspection_report,
            DocumentType.general_document,
            DocumentType.memo,
            DocumentType.other,
        }

    def _looks_like_priced_vl_inline_row(self, text: str) -> bool:
        tokens = str(text or "").split()
        numeric_count = sum(1 for token in tokens if self._to_decimal(token) is not None)
        has_unit = any(re.fullmatch(r"[A-Za-z가-힣]{1,8}", token or "") for token in tokens)
        return numeric_count >= 4 and has_unit

    def _vl_inline_no_price_item_from_row(self, text: str, doc_type: DocumentType | None) -> dict | None:
        if not re.match(r"^\d+\s+", text):
            return None
        if doc_type == DocumentType.inspection_report or re.search(r"\bLOT[-\w]+\b|합격수량|불량수량", text, flags=re.IGNORECASE):
            match = re.match(
                r"^\d+\s+(?P<body>.+?)\s+(?P<lot>LOT[-\w]+)\s+(?P<spec>\S+)\s+"
                r"(?P<received>[-+]?\d[\d,]*)\s+(?P<accepted>[-+]?\d[\d,]*)\s+(?P<rejected>[-+]?\d[\d,]*)"
                r"(?:\s+(?P<result>[A-Za-z가-힣][A-Za-z가-힣\s/-]*))?\s*$",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                item = {
                    "item_name": self._clean_value(match.group("body")),
                    "document_item_code": match.group("lot"),
                    "specification": match.group("spec"),
                    "quantity": match.group("received"),
                    "received_quantity": match.group("received"),
                    "accepted_quantity": match.group("accepted"),
                    "rejected_quantity": match.group("rejected"),
                }
                if match.group("result"):
                    item["inspection_result"] = match.group("result")
                return item
        if doc_type in {DocumentType.delivery_note, DocumentType.general_document, DocumentType.memo, DocumentType.other}:
            transfer_match = re.match(
                r"^\d+\s+(?P<body>.+?)\s+(?P<code>[A-Z][A-Z0-9-]*-[A-Z0-9-]+(?:\S*)?)\s+"
                r"(?P<spec>\S+)\s+(?P<quantity>[-+]?\d[\d,]*)\s+(?P<unit>[A-Za-z가-힣]{1,8})\s*$",
                text,
                flags=re.IGNORECASE,
            )
            if transfer_match:
                code = transfer_match.group("code")
                spec = transfer_match.group("spec")
                split_code, split_spec = self._split_compacted_code_spec(code)
                if split_spec:
                    code = split_code
                    spec = split_spec
                return {
                    "item_name": self._clean_value(transfer_match.group("body")),
                    "document_item_code": self._clean_code_value(code),
                    "specification": self._normalize_vl_inline_specification(spec),
                    "quantity": transfer_match.group("quantity"),
                    "requested_quantity": transfer_match.group("quantity"),
                    "unit": transfer_match.group("unit"),
                }
            match = re.match(
                r"^\d+\s+(?P<body>.+?)\s+(?P<code>[A-Z][A-Z0-9-]+)\s+(?P<spec>\S+)\s+"
                r"(?P<first>[-+]?\d[\d,]*)\s+(?P<second>[-+]?\d[\d,]*)(?:\s+(?P<third>[-+]?\d[\d,]*))?\s*(?P<unit>[A-Za-z가-힣]{1,8})?\s*$",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                item = {
                    "item_name": self._clean_value(match.group("body")),
                    "document_item_code": self._clean_code_value(match.group("code")),
                    "specification": self._normalize_vl_inline_specification(match.group("spec")),
                    "quantity": match.group("second"),
                    "ordered_quantity": match.group("first"),
                    "delivered_quantity": match.group("second"),
                }
                if match.group("third"):
                    item["remaining_quantity"] = match.group("third")
                if match.group("unit"):
                    item["unit"] = match.group("unit")
                return item
            transfer_match = re.match(
                r"^\d+\s+(?P<body>.+?)\s+(?P<code>[A-Z][A-Z0-9-]*-[A-Z0-9-]+(?:x\d+)?)(?P<spec>\d+x\d+(?:x\d+)?)?\s+"
                r"(?P<quantity>[-+]?\d[\d,]*)\s+(?P<unit>[A-Za-z가-힣]{1,8})\s*$",
                text,
                flags=re.IGNORECASE,
            )
            if transfer_match:
                code = transfer_match.group("code")
                spec = transfer_match.group("spec")
                if not spec:
                    code, spec = self._split_compacted_code_spec(code)
                return {
                    "item_name": self._clean_value(transfer_match.group("body")),
                    "document_item_code": self._clean_code_value(code),
                    "specification": self._normalize_vl_inline_specification(spec),
                    "quantity": transfer_match.group("quantity"),
                    "requested_quantity": transfer_match.group("quantity"),
                    "unit": transfer_match.group("unit"),
                }
        return None

    def _strip_duplicate_trailing_amount(self, text: str) -> str:
        tokens = str(text or "").split()
        if len(tokens) < 2:
            return str(text or "")
        last = self._to_decimal(tokens[-1])
        previous = self._to_decimal(tokens[-2])
        if last is not None and previous is not None and last == previous:
            return " ".join(tokens[:-1])
        return str(text or "")

    def _split_compacted_code_spec(self, code: str) -> tuple[str, str | None]:
        text = str(code or "")
        repeated = self._split_repeated_trailing_dimension(text)
        if repeated:
            return repeated
        dimension_matches = list(re.finditer(r"\d+[xX]\d+(?:[xX]\d+)?", text))
        if len(dimension_matches) >= 2:
            last = dimension_matches[-1]
            previous = dimension_matches[-2]
            if previous.end() == last.start() and previous.group(0).lower() == last.group(0).lower():
                return text[: last.start()], last.group(0)
        match = re.search(r"(\d+x\d+(?:x\d+)?)$", text, flags=re.IGNORECASE)
        if not match:
            return text, None
        spec = match.group(1)
        prefix = text[: match.start()].rstrip("-_ ")
        return prefix or text, spec

    def _split_repeated_trailing_dimension(self, text: str) -> tuple[str, str] | None:
        value = str(text or "")
        for start in range(len(value)):
            suffix = value[start:]
            if len(suffix) < 4 or len(suffix) % 2:
                continue
            midpoint = len(suffix) // 2
            first = suffix[:midpoint]
            second = suffix[midpoint:]
            if first.lower() != second.lower():
                continue
            if re.fullmatch(r"\d+[xX]\d+(?:[xX]\d+)?", first):
                return value[: start + midpoint], second
        return None

    def _split_vl_inline_item_identity(self, body: str) -> tuple[str | None, str | None, str | None]:
        tokens = body.split()
        tokens = self._strip_vl_inline_row_prefix_tokens(tokens)
        if not tokens:
            return None, None, None
        code_index = next((
            index for index, token in enumerate(tokens)
            if self._looks_like_item_code_token(token)
        ), None)
        if code_index is not None:
            item_name = self._clean_value(" ".join(tokens[:code_index]))
            item_code = self._clean_code_value(tokens[code_index])
            specification = self._clean_value(" ".join(tokens[code_index + 1:])) if code_index + 1 < len(tokens) else None
            return item_name, item_code, self._normalize_vl_inline_specification(specification)

        fused = self._split_fused_vl_inline_name_spec(" ".join(tokens))
        if fused:
            return fused

        spec_start = self._vl_inline_spec_start(tokens)
        if spec_start is not None and spec_start > 0:
            item_name = self._clean_value(" ".join(tokens[:spec_start]))
            specification = self._clean_value(" ".join(tokens[spec_start:]))
            return item_name, None, self._normalize_vl_inline_specification(specification)
        return self._clean_value(body), None, None

    def _strip_vl_inline_row_prefix_tokens(self, tokens: list[str]) -> list[str]:
        if len(tokens) >= 2 and re.fullmatch(r"\d{1,2}[-./]\d{1,2}", tokens[0]):
            return tokens[1:]
        return tokens

    def _split_fused_vl_inline_name_spec(self, value: str) -> tuple[str | None, str | None, str | None] | None:
        text = str(value or "").strip()
        if not text:
            return None
        match = re.match(r"^(?P<name>.+?)(?P<spec>M\d+x\d+)$", text, flags=re.IGNORECASE)
        if not match:
            match = re.match(r"^(?P<name>.+?[A-Za-z가-힣])(?P<spec>\d+x\d+(?:x\d+)?(?:T)?)$", text, flags=re.IGNORECASE)
        if not match:
            return None
        name = self._clean_value(match.group("name"))
        if name:
            name = re.sub(r"^(M\d+)([가-힣])", r"\1 \2", name, flags=re.IGNORECASE)
        spec = self._normalize_vl_inline_specification(match.group("spec"))
        return name, None, spec

    def _looks_like_item_code_token(self, token: str) -> bool:
        cleaned = str(token or "").strip(" ,|")
        if not cleaned or "-" not in cleaned:
            return False
        if re.search(r"[A-Za-z]", cleaned) and re.search(r"\d", cleaned):
            return True
        return bool(re.fullmatch(r"[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]{2,})+", cleaned))

    def _vl_inline_spec_start(self, tokens: list[str]) -> int | None:
        joined = " ".join(tokens)
        match = re.search(r"\b\d+(?:\.\d+)?\s*[xX]\s*\d+(?:\.\d+)?(?:\s*[xX]\s*\d+(?:\.\d+)?)?\b", joined)
        if match:
            prefix = joined[:match.start()].strip()
            return len(prefix.split()) if prefix else 0
        for index, token in enumerate(tokens):
            if index == 0:
                continue
            if re.fullmatch(r"M\d+(?:x\d+)?", token, flags=re.IGNORECASE):
                return index
            if re.fullmatch(r"[A-Z]{1,4}M\d+(?:x\d+)?", token, flags=re.IGNORECASE):
                return index
            if re.fullmatch(r"SEMI?\d+|SEM\d+", token, flags=re.IGNORECASE):
                return index
            if re.fullmatch(r"\d+(?:\.\d+)?(?:mm|T|P)", token, flags=re.IGNORECASE):
                return index
        return None

    def _normalize_vl_inline_specification(self, value: str | None) -> str | None:
        if not value:
            return None
        text = re.sub(r"\s*[xX×*]\s*", "x", value.strip())
        text = re.sub(r"^SEMI?(\d+)$", r"M\1", text, flags=re.IGNORECASE)
        text = re.sub(r"^SEM(\d+)$", r"M\1", text, flags=re.IGNORECASE)
        text = re.sub(r"^[A-Z]{1,4}(M\d+(?:x\d+)?)$", r"\1", text, flags=re.IGNORECASE)
        repeated = self._split_repeated_trailing_dimension(text)
        if repeated and repeated[0].lower() == repeated[1].lower():
            text = repeated[1]
        elif re.search(r"\d+\s*x\s*\d+", text, flags=re.IGNORECASE):
            compact_text = re.sub(r"\s+", "", text)
            repeated = self._split_repeated_trailing_dimension(compact_text)
            if repeated and repeated[0].lower() == repeated[1].lower():
                text = repeated[1]
        text = re.sub(r"\s+", "", text) if re.search(r"\d+\s*x\s*\d+", text, flags=re.IGNORECASE) else re.sub(r"\s+", " ", text)
        return self._clean_value(text)

    def _suppress_incomplete_inspection_quantities(self, items: list[dict]) -> list[dict]:
        safe_items: list[dict] = []
        for item in items:
            next_item = dict(item)
            if not next_item.get("lot_no"):
                lot_source = next_item.get("item_code") or next_item.get("document_item_code") or next_item.get("source_item_code")
                if re.match(r"^LOT[-\w]+$", str(lot_source or ""), flags=re.IGNORECASE):
                    next_item["lot_no"] = lot_source
            has_breakdown = any(
                next_item.get(field) not in (None, "", [])
                for field in ["received_quantity", "accepted_quantity", "rejected_quantity"]
            )
            if not has_breakdown and next_item.get("quantity") not in (None, "", []):
                next_item.pop("quantity", None)
                warnings = list(next_item.get("validation_warnings") or [])
                warnings.append("inspection_quantity_breakdown_missing")
                next_item["validation_warnings"] = sorted(set(warnings))
            safe_items.append(next_item)
        return safe_items

    def _should_prefer_numbered_vertical_items(
        self,
        items: list[dict],
        lines: list[str],
        doc_type: DocumentType | None = None,
    ) -> bool:
        if len(items) < 2:
            return False
        text = "\n".join(lines)
        has_no_header = any(re.fullmatch(r"No\.?", line.strip(), flags=re.IGNORECASE) for line in lines[:80])
        if not has_no_header:
            return False
        has_structured_amounts = any(item.get("line_total") or item.get("supply_amount") for item in items)
        has_quantity_table = any(item.get("quantity") for item in items) and re.search(r"(요청수량|납품수량|입고수량|합격수량)", text)
        option_quote = doc_type == DocumentType.quotation and re.search(r"(옵션|option|선택)", text, flags=re.IGNORECASE)
        return bool(has_structured_amounts or has_quantity_table or option_quote)

    def _has_quantity_bearing_items(self, items: list[dict]) -> bool:
        return any(item.get("quantity") is not None for item in items)

    def _should_use_no_amount_special_items(self, items: list[dict], special_items: list[dict]) -> bool:
        if not items:
            return True
        if not self._has_quantity_bearing_items(items):
            return True
        current_code_count = sum(1 for item in items if item.get("item_code") or item.get("document_item_code"))
        special_code_count = sum(1 for item in special_items if item.get("item_code") or item.get("document_item_code"))
        if special_code_count > current_code_count:
            return True
        if len(special_items) > len(items) and special_code_count >= current_code_count:
            return True
        current_has_quantity_leak = any(
            (
                (self._to_decimal(str(item.get("quantity"))) if item.get("quantity") not in (None, "") else None) == Decimal("0")
                or (
                    bool(item.get("item_code") or item.get("document_item_code"))
                    and bool(re.search(r"\s\d+(?:\.\d+)?$", str(item.get("item_name") or "")))
                )
            )
            for item in items
        )
        if current_has_quantity_leak and len(special_items) >= len(items) and special_code_count >= current_code_count:
            return True
        return False

    def _extract_numbered_vertical_table_items(
        self,
        lines: list[str],
        doc_type: DocumentType | None = None,
    ) -> list[dict]:
        items: list[dict] = []
        for index, line in enumerate(lines):
            if not re.fullmatch(r"No\.?", line.strip(), flags=re.IGNORECASE):
                continue
            fields: list[str | None] = ["row_no"]
            cursor = index + 1
            while cursor < len(lines) and len(fields) < 14:
                field = self._numbered_table_field_for_label(lines[cursor])
                if not field:
                    break
                fields.append(field)
                cursor += 1
            meaningful_fields = {field for field in fields if field and field not in {"row_no", "note"}}
            if "item_name" not in meaningful_fields or not ({"quantity", "supply_amount", "line_total"} & meaningful_fields):
                continue
            row_start = cursor
            parsed_rows: list[dict] = []
            while row_start < len(lines):
                marker = lines[row_start].strip()
                if self._looks_like_numbered_table_footer(marker):
                    break
                if not self._looks_like_numbered_table_row_marker(marker):
                    row_start += 1
                    continue
                cells = [marker]
                cursor = row_start + 1
                while cursor < len(lines) and len(cells) < len(fields):
                    value = lines[cursor].strip()
                    if self._looks_like_numbered_table_row_marker(value) and len(cells) <= 1:
                        break
                    if self._looks_like_numbered_table_footer(value):
                        break
                    cells.append(value)
                    cursor += 1
                if len(cells) < len(fields):
                    break
                item = self._line_item_from_numbered_vertical_cells(fields, cells)
                if item and item.get("item_name"):
                    parsed_rows.append(self._normalize_line_item(item))
                row_start = cursor
            if len(parsed_rows) >= 2:
                items.extend(parsed_rows)
                break
        return self._dedupe_line_items(items)

    def _numbered_table_field_for_label(self, label: str) -> str | None:
        key = re.sub(r"[\s_/-]+", "", str(label or "").strip().lower())
        mapping = {
            "품목명": "item_name",
            "품명": "item_name",
            "반품품목": "item_name",
            "description": "item_name",
            "itemdescription": "item_name",
            "itemname": "item_name",
            "품목코드": "item_code",
            "문서품목코드": "item_code",
            "내부품목코드": "item_code",
            "vendorsku": "item_code",
            "sku": "item_code",
            "규격": "specification",
            "spec": "specification",
            "specification": "specification",
            "수량": "quantity",
            "qty": "quantity",
            "quantity": "quantity",
            "요청수량": "quantity",
            "요청수림": "quantity",
            "발주수량": "quantity",
            "납품수량": "quantity",
            "입고수량": "quantity",
            "단위": "unit",
            "unit": "unit",
            "단가": "unit_price",
            "unitprice": "unit_price",
            "공급가액": "supply_amount",
            "공급액": "supply_amount",
            "subtotal": "supply_amount",
            "amount": "supply_amount",
            "세액": "tax_amount",
            "부가세": "tax_amount",
            "tax": "tax_amount",
            "vat": "tax_amount",
            "합계": "line_total",
            "합계금액": "line_total",
            "total": "line_total",
            "linetotal": "line_total",
            "거래일": "note",
            "거래일자": "note",
            "date": "note",
            "비고": "note",
            "note": "note",
            "remark": "note",
        }
        return mapping.get(key)

    def _looks_like_numbered_table_row_marker(self, value: str) -> bool:
        text = str(value or "").strip()
        return bool(re.fullmatch(r"\d{1,3}", text) or re.fullmatch(r"[A-Z]{1,2}\d{1,2}", text, flags=re.IGNORECASE))

    def _looks_like_numbered_table_footer(self, value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        return bool(re.search(
            r"(공급가액\s*합계|차감\s*공급가액|차감\s*세액|차감\s*합계|합계금액|총액|VAT|부가세|"
            r"선택시\s*합계|옵션라인|모두\s*합산|담당|검토|승인|DocuParse|synthetic data|"
            r"페이지\s*하단|전월이월|총\s*미수금)",
            text,
            flags=re.IGNORECASE,
        ))

    def _line_item_from_numbered_vertical_cells(self, fields: list[str | None], cells: list[str]) -> dict | None:
        item: dict = {}
        for field, value in zip(fields, cells):
            if not field or field in {"row_no", "note"}:
                continue
            if field == "quantity":
                quantity, unit = self._parse_quantity_and_unit(self._normalize_table_numeric_text(value))
                item[field] = quantity
                if unit and not item.get("unit"):
                    item["unit"] = unit
            elif field in {"unit_price", "supply_amount", "tax_amount", "line_total"}:
                item[field] = self._normalize_number(self._normalize_table_numeric_text(value))
            elif field == "item_code":
                item[field] = self._clean_code_value(value)
            else:
                item[field] = self._clean_value(value)
        if not item.get("item_name"):
            return None
        return item

    def _normalize_table_numeric_text(self, value: object) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"[-+]?[\d,.\sOo]+", text):
            text = text.replace("O", "0").replace("o", "0")
        return text

    def _should_use_sparse_special_items(self, items: list[dict]) -> bool:
        if not items:
            return True
        structured_count = sum(
            1 for item in items
            if item.get("item_code")
            or item.get("document_item_code")
            or item.get("specification")
            or item.get("quantity")
            or item.get("supply_amount")
            or item.get("line_total")
        )
        return structured_count <= 1

    def _should_use_repeated_amount_items(self, items: list[dict], repeated_items: list[dict]) -> bool:
        if len(repeated_items) < 4:
            return False
        if not items:
            return True
        current_named = sum(1 for item in items if item.get("item_name"))
        current_name_diversity = {
            self._normalized_item_key(item.get("item_name"))
            for item in items
            if item.get("item_name")
        }
        if current_named > 3 and (len(current_name_diversity) > 2 or len(repeated_items) < current_named * 2):
            return False
        if len(repeated_items) < current_named + 2:
            return False
        current_total = self._line_items_total(items)
        repeated_total = self._line_items_total(repeated_items)
        if current_total is None:
            return True
        if repeated_total is None:
            return True
        return repeated_total >= current_total

    def _extract_special_quantity_table_items(self, lines: list[str]) -> list[dict]:
        items: list[dict] = []
        items.extend(self._extract_delivery_quantity_line_items(lines))
        items.extend(self._extract_inspection_report_line_items(lines))
        items.extend(self._extract_internal_transfer_line_items(lines))
        items.extend(self._extract_option_quotation_line_items(lines))
        items.extend(self._extract_statement_date_line_items(lines))
        return [self._normalize_line_item(item) for item in items if item.get("item_name")]

    def _extract_delivery_quantity_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(납\s*품\s*서|delivery\s*note)", text, flags=re.IGNORECASE):
            return []
        vertical_items = self._extract_vertical_header_table_items(
            lines,
            self._delivery_quantity_field_for_label,
            required_fields={"item_name"},
            quantity_preference=("delivered_quantity", "received_quantity", "requested_quantity", "quantity", "ordered_quantity"),
            stop_on_amount_header=True,
        )
        if vertical_items and any(
            any(field in item for field in ("ordered_quantity", "delivered_quantity", "remaining_quantity", "received_quantity", "requested_quantity"))
            for item in vertical_items
        ):
            return vertical_items
        header_index = next((
            index for index, line in enumerate(lines)
            if re.search(r"품목명", line) and re.search(r"(납품수량|입고수량|발주수량|잔량|요청수량)", line)
        ), None)
        if header_index is None:
            return []

        header = lines[header_index]
        if re.search(r"(단가|공급가액|세액|합계금액|unit\s*price|amount|total)", header, flags=re.IGNORECASE):
            return []
        table_items = self._extract_delivery_quantity_items_from_table(lines, header_index)
        if table_items:
            return table_items
        quantity_labels = re.findall(r"(발주수량|납품수량|입고수량|요청수량|잔량|수량)", header)
        if not quantity_labels:
            return []
        preferred_label = next((label for label in ("납품수량", "입고수량", "요청수량", "수량", "발주수량") if label in quantity_labels), quantity_labels[0])
        quantity_position = quantity_labels.index(preferred_label)

        items: list[dict] = []
        for raw in lines[header_index + 1:]:
            line = self._clean_value(raw) or ""
            if not line:
                continue
            if self._looks_like_numbered_table_footer(line) or self._looks_like_instruction_or_note(line):
                break
            match = re.match(r"^\s*(\d{1,3})\s+(.+?)\s+([A-Za-z가-힣]{1,4})\s*$", line)
            if not match:
                continue
            body = match.group(2).strip()
            unit = match.group(3).upper()
            body_tokens = body.split()
            if len(body_tokens) <= len(quantity_labels):
                continue
            quantity_tokens = body_tokens[-len(quantity_labels):]
            if not all(re.fullmatch(r"\d+(?:\.\d+)?", token) for token in quantity_tokens):
                continue
            quantity = self._number_value(Decimal(quantity_tokens[quantity_position]))
            prefix = " ".join(body_tokens[:-len(quantity_labels)]).strip()
            if not prefix:
                continue
            item = self._delivery_quantity_item_from_prefix(prefix)
            if not item.get("item_name"):
                continue
            item["quantity"] = quantity
            for label, token in zip(quantity_labels, quantity_tokens):
                metadata_key = {
                    "발주수량": "ordered_quantity",
                    "요청수량": "requested_quantity",
                    "입고수량": "received_quantity",
                    "납품수량": "delivered_quantity",
                    "잔량": "remaining_quantity",
                    "수량": "quantity",
                }.get(label)
                if metadata_key and metadata_key != "quantity":
                    item[metadata_key] = self._number_value(Decimal(token))
            item["unit"] = unit
            items.append(item)
        return items

    def _extract_vertical_header_table_items(
        self,
        lines: list[str],
        field_mapper,
        required_fields: set[str],
        quantity_preference: tuple[str, ...] = ("quantity",),
        stop_on_amount_header: bool = False,
    ) -> list[dict]:
        items: list[dict] = []
        for index, line in enumerate(lines):
            if not re.fullmatch(r"No\.?", line.strip(), flags=re.IGNORECASE):
                continue
            fields: list[str | None] = ["line_no"]
            cursor = index + 1
            while cursor < len(lines) and len(fields) < 16:
                raw_label = lines[cursor].strip()
                if self._looks_like_numbered_table_row_marker(raw_label):
                    break
                field = field_mapper(raw_label)
                if field is None:
                    break
                if stop_on_amount_header and field in {"unit_price", "supply_amount", "tax_amount", "line_total"}:
                    fields = []
                    break
                fields.append(field)
                cursor += 1
            if not fields:
                continue
            meaningful = {field for field in fields if field and field != "line_no"}
            if not required_fields.issubset(meaningful):
                continue
            quantity_field = next((field for field in quantity_preference if field in fields), None)
            row_start = cursor
            parsed_rows: list[dict] = []
            while row_start < len(lines):
                marker = lines[row_start].strip()
                if self._looks_like_numbered_table_footer(marker) or self._looks_like_instruction_or_note(marker):
                    break
                if not self._looks_like_numbered_table_row_marker(marker):
                    row_start += 1
                    continue
                cells = [marker]
                cursor = row_start + 1
                while cursor < len(lines) and len(cells) < len(fields):
                    value = lines[cursor].strip()
                    if self._looks_like_numbered_table_footer(value) or self._looks_like_instruction_or_note(value):
                        break
                    cells.append(value)
                    cursor += 1
                if len(cells) < len(fields):
                    break
                item = self._line_item_from_vertical_fields(fields, cells, quantity_field)
                if item and item.get("item_name"):
                    parsed_rows.append(self._normalize_line_item(item))
                row_start = cursor
            if parsed_rows:
                items.extend(parsed_rows)
                break
        return self._dedupe_line_items(items)

    def _line_item_from_vertical_fields(
        self,
        fields: list[str | None],
        cells: list[str],
        quantity_field: str | None = None,
    ) -> dict | None:
        item: dict = {}
        for field, cell in zip(fields, cells):
            if not field or field == "line_no":
                continue
            if field in {
                "quantity",
                "ordered_quantity",
                "requested_quantity",
                "received_quantity",
                "delivered_quantity",
                "remaining_quantity",
                "accepted_quantity",
                "rejected_quantity",
            }:
                number = self._to_decimal(cell)
                if number is not None:
                    item[field] = self._number_value(number)
            elif field in {"unit_price", "supply_amount", "tax_amount", "line_total"}:
                number = self._normalize_number(self._normalize_table_numeric_text(cell))
                if number is not None:
                    item[field] = number
            elif field == "item_code":
                item[field] = self._clean_code_value(cell)
            else:
                item[field] = self._clean_value(cell)
        if quantity_field and item.get(quantity_field) is not None:
            item["quantity"] = item[quantity_field]
        elif item.get("quantity") is not None:
            item["quantity"] = item["quantity"]
        if item.get("quantity") is not None and not item.get("unit") and any(
            field in item for field in ("received_quantity", "delivered_quantity", "requested_quantity", "ordered_quantity", "accepted_quantity")
        ):
            item["unit"] = "EA"
        return item if item.get("item_name") else None

    def _extract_delivery_quantity_items_from_table(self, lines: list[str], header_index: int) -> list[dict]:
        headers = self._split_table_line(lines[header_index])
        mapped = [self._delivery_quantity_field_for_label(header) for header in headers]
        if "item_name" not in mapped:
            return []
        quantity_field = next((field for field in ("delivered_quantity", "received_quantity", "requested_quantity", "quantity", "ordered_quantity") if field in mapped), None)
        if quantity_field is None:
            return []
        items: list[dict] = []
        for row in lines[header_index + 1:]:
            if self._looks_like_numbered_table_footer(row) or self._looks_like_instruction_or_note(row):
                break
            cells = self._split_table_line(row)
            if len(cells) < 4:
                continue
            if len(cells) < len(mapped):
                cells = cells + [""] * (len(mapped) - len(cells))
            item: dict = {}
            for field, cell in zip(mapped, cells):
                if not field or field == "line_no":
                    continue
                if "quantity" in field:
                    number = self._to_decimal(cell)
                    if number is not None:
                        item[field] = self._number_value(number)
                else:
                    item[field] = self._clean_value(cell)
            if item.get(quantity_field) is not None:
                item["quantity"] = item[quantity_field]
            if item.get("item_name"):
                items.append(item)
        return items

    def _delivery_quantity_field_for_label(self, label: str) -> str | None:
        key = re.sub(r"[\s_/-]+", "", str(label or "").strip().lower())
        mapping = {
            "no": "line_no",
            "번호": "line_no",
            "순번": "line_no",
            "품목명": "item_name",
            "품명": "item_name",
            "문서품목코드": "item_code",
            "거래처품목코드": "item_code",
            "고객품목코드": "item_code",
            "vendoritemcode": "item_code",
            "customersku": "item_code",
            "품목코드": "item_code",
            "품번": "item_code",
            "규격": "specification",
            "spec": "specification",
            "발주수량": "ordered_quantity",
            "요청수량": "requested_quantity",
            "입고수량": "received_quantity",
            "납품수량": "delivered_quantity",
            "잔량": "remaining_quantity",
            "수량": "quantity",
            "단위": "unit",
        }
        return mapping.get(key)

    def _apply_row_level_safety_overrides(
        self,
        items: list[dict],
        lines: list[str],
        doc_type: DocumentType,
    ) -> list[dict]:
        if not items:
            return items
        text = "\n".join(lines)
        safe_items = [dict(item) for item in items]
        if doc_type == DocumentType.quotation and re.search(r"(수량\s*공란|수량.*빈\s*값|quantity\s*(?:blank|missing))", text, flags=re.IGNORECASE):
            target_index = 0
            ordinal = re.search(r"(첫\s*번째|1\s*번째|first)", text, flags=re.IGNORECASE)
            if ordinal:
                target_index = 0
            if target_index < len(safe_items):
                item = safe_items[target_index]
                item.pop("quantity", None)
                raw_window = self._item_context_window(item, lines)
                if item.get("unit_price") is not None and not self._numeric_value_appears_in_text(item["unit_price"], raw_window):
                    item.pop("unit_price", None)
                warnings = list(item.get("validation_warnings") or [])
                for warning in ["missing_quantity", "quantity_cell_blank"]:
                    if warning not in warnings:
                        warnings.append(warning)
                item["validation_warnings"] = warnings
            for item in safe_items:
                if item.get("quantity") in (None, "", []) or item.get("unit_price") in (None, "", []):
                    continue
                raw_window = self._item_context_window(item, lines)
                if self._numeric_value_appears_in_text(item["quantity"], raw_window) and self._numeric_value_appears_in_text(item["unit_price"], raw_window):
                    continue
                item.pop("quantity", None)
                item.pop("unit_price", None)
                warnings = list(item.get("validation_warnings") or [])
                for warning in ["missing_quantity", "ocr_quantity_price_unverified"]:
                    if warning not in warnings:
                        warnings.append(warning)
                item["validation_warnings"] = warnings
        return safe_items

    def _numeric_value_appears_in_text(self, value: Any, text: str) -> bool:
        expected = self._to_decimal(str(value))
        if expected is None:
            return False
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
            observed = self._to_decimal(token.replace(",", ""))
            if observed is not None and observed == expected:
                return True
        return False

    def _delivery_quantity_item_from_prefix(self, prefix: str) -> dict:
        tokens = prefix.split()
        code_index = next((
            index for index, token in enumerate(tokens)
            if "-" in token and re.search(r"[A-Za-z]", token) and re.search(r"\d", token)
        ), None)
        if code_index is None:
            return {"item_name": self._clean_value(prefix)}
        item_name = self._clean_value(" ".join(tokens[:code_index]))
        item_code = self._clean_code_value(tokens[code_index])
        specification = self._clean_value(" ".join(tokens[code_index + 1:])) if code_index + 1 < len(tokens) else None
        item: dict = {"item_name": item_name}
        if item_code:
            item["item_code"] = item_code
        if specification:
            item["specification"] = specification
        return item

    def _extract_sparse_repeated_amount_table_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(품목명|item\s*(?:name|description)|description)", text, flags=re.IGNORECASE):
            return []
        if not re.search(r"(단가|공급가액|합계금액|subtotal|amount|unit\s*price)", text, flags=re.IGNORECASE):
            return []

        starts: list[int] = []
        for index, line in enumerate(lines):
            if self._looks_like_sparse_repeated_item_name(line):
                starts.append(index)
        if len(starts) < 4:
            return []

        items: list[dict] = []
        for pos, start in enumerate(starts):
            end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
            segment = [line for line in lines[start:end] if str(line or "").strip()]
            item = self._parse_sparse_repeated_amount_segment(segment)
            if item:
                normalized = self._normalize_line_item(item)
                # Repeated photographed tables can contain legitimate rows
                # with the same name/spec after OCR drops the amount cells.
                # Keep source order available only for dedupe; it is stripped
                # before results leave the parser.
                normalized["_dedupe_token"] = start
                items.append(normalized)
        return items

    def _looks_like_sparse_repeated_item_name(self, line: str) -> bool:
        value = self._clean_value(line) or ""
        if not value:
            return False
        if self._looks_like_business_label(value) or self._looks_like_instruction_or_note(value):
            return False
        if self._looks_like_amount_label_line(value):
            return False
        if self._extract_unit(value) and len(value) <= 4:
            return False
        has_item_keyword = bool(re.search(
            r"(볼트|와셔|베어링|하우징|핀|브라켓|플레이트|스페이서|철판|환봉|하네스|커넥터|"
            r"bolt|washer|bearing|housing|pin|bracket|plate|spacer|harness|connector|rail|guide)",
            value,
            flags=re.IGNORECASE,
        ))
        if (self._internal_item_code_from_line(value) or self._extract_item_code(value)) and not has_item_keyword:
            return False
        if re.fullmatch(r"[-\d,.\[\]A-Za-z]+", value) and not re.search(r"(bolt|washer|pin|plate|rail|guide|cable|connector)", value, flags=re.IGNORECASE):
            return False
        if not re.search(r"[A-Za-z가-힣]", value):
            return False
        if has_item_keyword:
            return True
        return False

    def _extract_fragmented_fax_table_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(팩스|fax)", text, flags=re.IGNORECASE):
            return []
        if not re.search(r"(금액\s*검토|0\s*/\s*o|o\s*/\s*0|혼동|misalign|공급가액)", text, flags=re.IGNORECASE):
            return []
        anchors: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            value = self._clean_value(line) or ""
            if not value or self._looks_like_business_label(value) or self._looks_like_instruction_or_note(value):
                continue
            if self._looks_like_amount_label_line(value):
                continue
            if re.search(
                r"(베어링\s*하우징|베어링하우징|S45C\s*PIN|볼트\s*/\s*와셔|볼트.*와셔|"
                r"bearing\s*housing|s45c\s*pin|bolt.*washer)",
                value,
                flags=re.IGNORECASE,
            ):
                anchors.append((index, value))
        if len(anchors) < 2:
            return []

        items: list[dict] = []
        for pos, (start, name) in enumerate(anchors):
            end = anchors[pos + 1][0] if pos + 1 < len(anchors) else len(lines)
            segment: list[str] = []
            for raw in lines[start + 1:end]:
                value = self._clean_value(raw) or ""
                if not value:
                    continue
                if re.search(r"(공급가액|세액|합계금액|총액|담당|검토|승인|DocuParse|synthetic)", value, flags=re.IGNORECASE):
                    break
                segment.append(value)
            item: dict = {
                "item_name": self._clean_fragmented_fax_item_name(name),
                "validation_warnings": ["fax_row_boundary_uncertain"],
            }
            spec = self._spec_from_fragmented_fax_name(name)
            if spec:
                item["specification"] = spec
            amounts: list[Decimal] = []
            for value in segment:
                for token in re.findall(r"\d[\d,]*(?:\.\d+)?[A-Za-z]?", value):
                    amount = self._amount_from_labeled_match(self._normalize_ocr_numeric_token(token), value)
                    if amount is not None and amount > 0:
                        amounts.append(amount)
            raw_total = self._choose_untrusted_fragmented_line_total(amounts)
            if raw_total is not None:
                item["line_total"] = self._number_value(raw_total)
                if "untrusted_ocr_amount" not in item["validation_warnings"]:
                    item["validation_warnings"].append("untrusted_ocr_amount")
            normalized = self._normalize_line_item(item)
            normalized["_dedupe_token"] = start
            items.append(normalized)
        return items

    def _clean_fragmented_fax_item_name(self, value: str) -> str:
        text = self._clean_value(value) or ""
        text = re.sub(r"\b8X6[QO]\b", "8X60", text, flags=re.IGNORECASE)
        text = re.sub(r"\b8X6\s*C(?=\d{3,}\b)", "8X60 ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\d{3,}$", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if re.search(r"베어링\s*하우징|베어링하우징", text):
            return "베어링 하우징"
        return text

    def _spec_from_fragmented_fax_name(self, value: str) -> str | None:
        text = str(value or "")
        match = re.search(r"\b(\d+\s*[xX]\s*\d+|\d+\s*mm)\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        spec = match.group(1).replace(" ", "")
        spec = re.sub(r"8X6[QO]$", "8X60", spec, flags=re.IGNORECASE)
        return spec

    def _choose_untrusted_fragmented_line_total(self, amounts: list[Decimal]) -> Decimal | None:
        if not amounts:
            return None
        plausible = [amount for amount in amounts if amount >= Decimal("10000")]
        if not plausible:
            return None
        if len(amounts) >= 3 and amounts[1] <= amounts[0] * Decimal("0.2") and amounts[2] > amounts[0]:
            return amounts[2]
        for amount in plausible:
            if amount >= Decimal("50000"):
                return amount
        return plausible[-1]

    def _parse_sparse_repeated_amount_segment(self, segment: list[str]) -> dict | None:
        if not segment:
            return None
        name = self._clean_value(segment[0])
        if not name:
            return None
        spec = None
        item_code = None
        unit = None
        numeric_values: list[Decimal] = []
        untrusted_supply_only = False
        for raw in segment[1:]:
            value = self._clean_value(raw) or ""
            if not value:
                continue
            if self._looks_like_amount_label_line(value) or self._looks_like_instruction_or_note(value):
                break
            if item_code is None:
                item_code = self._extract_item_code(value) or self._internal_item_code_from_line(value)
            if spec is None and self._looks_like_sparse_spec_value(value):
                spec = value
                continue
            unit = unit or self._extract_unit(value)
            for token in re.findall(r"\[?\d[\d,]*(?:\.\d+)?[A-Za-z]?", value):
                amount = self._amount_from_labeled_match(self._normalize_ocr_numeric_token(token.strip("[]")), value)
                if amount is not None and amount > 0:
                    numeric_values.append(amount)
        if not numeric_values and not item_code and not spec:
            return None
        item: dict = {"item_name": name}
        if item_code:
            item["item_code"] = item_code
        if spec:
            item["specification"] = spec
        if unit:
            item["unit"] = unit
        elif numeric_values:
            item["unit"] = "EA"

        amount_values = [value for value in numeric_values if value >= Decimal("1000")]
        if amount_values:
            triples = self._valid_amount_triples_from_values(numeric_values)
            if triples:
                supply, tax, total = triples[-1]
                item["supply_amount"] = self._number_value(supply)
                item["tax_amount"] = self._number_value(tax)
                item["line_total"] = self._number_value(total)
            else:
                # In heavily fragmented long tables the OCR often preserves only
                # per-line supply amounts. Keep the row candidate for review but
                # do not pretend that a line total is known.
                item["supply_amount"] = self._number_value(amount_values[-1])
                item["validation_warnings"] = ["untrusted_ocr_amount"]
                untrusted_supply_only = True

        supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        if supply is not None and supply > 0 and not untrusted_supply_only:
            pair = self._choose_krw_quantity_price(
                " ".join(str(item.get(field) or "") for field in ["item_name", "specification", "item_code"]),
                supply,
                " ".join(segment),
            )
            if pair:
                item["quantity"] = self._number_value(pair[0])
                item["unit_price"] = self._number_value(pair[1])
        return item

    def _looks_like_sparse_spec_value(self, value: str) -> bool:
        return bool(re.search(r"\d+\s*[xX]\s*\d+|\d+(?:\.\d+)?\s*(?:mm|T)|\bM\d+\b", value, flags=re.IGNORECASE))

    def _valid_amount_triples_from_values(self, values: list[Decimal]) -> list[tuple[Decimal, Decimal, Decimal]]:
        triples: list[tuple[Decimal, Decimal, Decimal]] = []
        for i, supply_value in enumerate(values):
            for j in range(i + 1, min(i + 4, len(values))):
                tax_value = values[j]
                for k in range(j + 1, min(j + 4, len(values))):
                    total_value = values[k]
                    if abs(tax_value - supply_value * Decimal("0.1")) <= max(Decimal("1"), supply_value * Decimal("0.02")) and abs(total_value - (supply_value + tax_value)) <= max(Decimal("1"), total_value * Decimal("0.02")):
                        triples.append((supply_value, tax_value, total_value))
        return triples

    def _extract_inspection_report_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(입고검사|검사성적서|검사번호|Lot\s*No|합격수량|불량수량)", text, flags=re.IGNORECASE):
            return []
        table_items = self._extract_inspection_report_table_items(lines)
        if table_items:
            return table_items
        items: list[dict] = []
        for index, line in enumerate(lines):
            cleaned = self._clean_value(line) or ""
            if not cleaned or self._looks_like_business_label(cleaned) or self._looks_like_instruction_or_note(cleaned):
                continue
            if not re.search(r"[A-Za-z가-힣]", cleaned):
                continue
            if re.search(r"^(?:Page|Lot\s*No|입고수량|합격수량|불량수량|판정|담당|승인|검사자)$", cleaned, flags=re.IGNORECASE):
                continue
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if not re.search(r"\bLOT[-_A-Za-z0-9]+", next_line, flags=re.IGNORECASE):
                continue
            item: dict = {"item_name": cleaned, "lot_no": next_line.strip()}
            for lookahead in lines[index + 2:index + 6]:
                if re.search(r"\bLOT[-_A-Za-z0-9]+", lookahead, flags=re.IGNORECASE):
                    break
                number = self._to_decimal(lookahead)
                if number is not None and number > 0 and "quantity" not in item:
                    item["quantity"] = self._number_value(number)
                    item["unit"] = "EA"
            items.append(item)
        return items

    def _extract_inspection_report_table_items(self, lines: list[str]) -> list[dict]:
        vertical_items = self._extract_vertical_header_table_items(
            lines,
            self._inspection_field_for_label,
            required_fields={"item_name", "lot_no"},
            quantity_preference=("received_quantity", "quantity"),
        )
        if vertical_items:
            return vertical_items
        header_index = next((
            index for index, line in enumerate(lines)
            if re.search(r"품목명", line)
            and re.search(r"Lot\s*No|Lot", line, flags=re.IGNORECASE)
            and re.search(r"입고수량|합격수량|불량수량", line)
        ), None)
        if header_index is None:
            return []
        headers = self._split_table_line(lines[header_index])
        mapped = [self._inspection_field_for_label(header) for header in headers]
        if "item_name" not in mapped or "lot_no" not in mapped:
            return []
        items: list[dict] = []
        for row in lines[header_index + 1:]:
            if self._looks_like_numbered_table_footer(row) or self._looks_like_instruction_or_note(row):
                break
            cells = self._split_table_line(row)
            if len(cells) < 4:
                continue
            if len(cells) < len(mapped):
                cells = cells + [""] * (len(mapped) - len(cells))
            item: dict = {}
            for field, cell in zip(mapped, cells):
                if not field:
                    continue
                if field == "line_no":
                    continue
                if field in {"quantity", "received_quantity", "accepted_quantity", "rejected_quantity"}:
                    number = self._to_decimal(cell)
                    if number is not None:
                        item[field] = self._number_value(number)
                else:
                    item[field] = self._clean_value(cell)
            if item.get("received_quantity") is not None:
                item["quantity"] = item["received_quantity"]
                item["unit"] = item.get("unit") or "EA"
            if item.get("item_name"):
                items.append(item)
        return items

    def _inspection_field_for_label(self, label: str) -> str | None:
        key = re.sub(r"[\s_/-]+", "", str(label or "").strip().lower())
        mapping = {
            "no": "line_no",
            "번호": "line_no",
            "순번": "line_no",
            "품목명": "item_name",
            "품명": "item_name",
            "lotno": "lot_no",
            "lot": "lot_no",
            "lot번호": "lot_no",
            "규격": "specification",
            "spec": "specification",
            "입고수량": "received_quantity",
            "합격수량": "accepted_quantity",
            "불량수량": "rejected_quantity",
            "수량": "quantity",
            "판정": "inspection_result",
            "결과": "inspection_result",
        }
        return mapping.get(key)

    def _extract_foreign_invoice_vertical_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"\b(?:commercial\s+invoice|invoice)\b", text, flags=re.IGNORECASE):
            return []
        if not re.search(r"(?:^|\n)Description(?:\n|$)", text, flags=re.IGNORECASE):
            return []
        if not re.search(r"(Vendor\s+SKU|Unit\s+Price|Amount)", text, flags=re.IGNORECASE):
            return []
        return self._extract_vertical_header_table_items(
            lines,
            self._foreign_invoice_field_for_label,
            required_fields={"item_name", "quantity", "unit_price", "supply_amount"},
            quantity_preference=("quantity",),
        )

    def _foreign_invoice_field_for_label(self, label: str) -> str | None:
        key = re.sub(r"[\s_/-]+", "", str(label or "").strip().lower())
        mapping = {
            "no": "line_no",
            "description": "item_name",
            "itemdescription": "item_name",
            "itemname": "item_name",
            "vendorsku": "item_code",
            "sku": "item_code",
            "partno": "item_code",
            "partnumber": "item_code",
            "spec": "specification",
            "specification": "specification",
            "qty": "quantity",
            "quantity": "quantity",
            "unit": "unit",
            "unitprice": "unit_price",
            "amount": "supply_amount",
            "subtotal": "supply_amount",
        }
        return mapping.get(key)

    def _extract_internal_transfer_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(사업장간|자재\s*이동|내부품목코드|요청수량|요청수림|\bTRF[-_ ]?\d{4})", text, flags=re.IGNORECASE):
            return []
        items: list[dict] = []
        for index, line in enumerate(lines):
            code = self._internal_item_code_from_line(line)
            if not code:
                continue
            inline_item = self._parse_inline_internal_transfer_row(line, code)
            if inline_item:
                items.append(inline_item)
                continue
            previous_name = self._nearest_item_name_before(lines, index)
            next_spec = self._nearest_spec_after(lines, index)
            item = {
                "item_name": previous_name,
                "item_code": code,
                "specification": next_spec,
                "unit": "EA" if any(re.fullmatch(r"EA", value.strip(), flags=re.IGNORECASE) for value in lines[index + 1:index + 4]) else None,
            }
            items.append({key: value for key, value in item.items() if value})
        return items

    def _parse_inline_internal_transfer_row(self, line: str, code: str) -> dict | None:
        text = str(line or "").strip()
        if not text or re.search(r"^(?:No|번호)\b|품목명|내부품목코드|요청수량", text, flags=re.IGNORECASE):
            return None
        code_match = re.search(re.escape(code), text, flags=re.IGNORECASE)
        if not code_match:
            return None
        before = text[:code_match.start()].strip()
        after = text[code_match.end():].strip()
        item_name = re.sub(r"^\d+\s+", "", before).strip(" -:：|")
        if not item_name or not re.search(r"[A-Za-z가-힣]", item_name):
            return None
        tokens = after.split()
        spec: str | None = None
        quantity: Decimal | None = None
        unit: str | None = None
        for pos, token in enumerate(tokens):
            cleaned = token.strip(" ,|")
            if spec is None and re.search(r"\d+\s*[xX]\s*\d+|\d+(?:\.\d+)?\s*(?:mm|T)|\bM\d+\b", cleaned, flags=re.IGNORECASE):
                spec = cleaned
                continue
            if quantity is None and re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
                next_token = tokens[pos + 1].strip(" ,|") if pos + 1 < len(tokens) else ""
                if not next_token or self._extract_unit(next_token):
                    quantity = Decimal(cleaned)
                    unit = self._extract_unit(next_token) or unit
                    continue
            unit = unit or self._extract_unit(cleaned)
        item = {
            "item_name": item_name,
            "item_code": code,
            "document_item_code": code,
            "specification": spec,
            "quantity": self._number_value(quantity) if quantity is not None else None,
            "unit": unit,
        }
        return {key: value for key, value in item.items() if value not in (None, "", [])}

    def _internal_item_code_from_line(self, line: str) -> str | None:
        text = str(line or "").strip()
        match = re.search(r"\b[MP]-[A-Z0-9][A-Z0-9-]{4,}", text, flags=re.IGNORECASE)
        if not match:
            return None
        code = match.group(0)
        code = re.sub(r"(?<=\d)[Oo](?=\d|$|-)", "0", code)
        code = re.sub(r"(?<=\d)[Oo]{2}\b", "00", code)
        return code

    def _nearest_item_name_before(self, lines: list[str], index: int) -> str | None:
        for candidate in reversed(lines[max(0, index - 3):index]):
            value = self._clean_value(candidate) or ""
            if not value or self._looks_like_business_label(value) or self._looks_like_instruction_or_note(value):
                continue
            if self._internal_item_code_from_line(value) or self._extract_unit(value):
                continue
            if re.search(r"[A-Za-z가-힣]", value):
                return value
        return None

    def _nearest_spec_after(self, lines: list[str], index: int) -> str | None:
        for candidate in lines[index + 1:index + 4]:
            value = self._clean_value(candidate) or ""
            if not value or self._internal_item_code_from_line(value) or self._looks_like_business_label(value):
                continue
            if re.search(r"[가-힣]", value) and not re.search(r"\d+\s*[xX]\s*\d+|\d+(?:\.\d+)?\s*(?:mm|T)", value, flags=re.IGNORECASE):
                continue
            if re.search(r"^[A-Z]{1,5}\d|\d+(?:\.\d+)?\s*(?:mm|T)|\d+\s*[xX]\s*\d+", value, flags=re.IGNORECASE):
                return value
        return None

    def _extract_option_quotation_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(견\s*적|quotation|quote)", text, flags=re.IGNORECASE) or not re.search(r"(옵션|option|선택)", text, flags=re.IGNORECASE):
            return []
        items: list[dict] = []
        pending_amounts: list[Decimal] = []
        table_body = False
        for index, line in enumerate(lines):
            if self._field_for_quotation_option_header(line):
                table_body = True
                continue
            if not table_body:
                continue
            if re.search(r"(VAT|부가세|선택시합계|옵션라인|모두합산|DocuParse|Synthetic)", line, flags=re.IGNORECASE):
                break
            value = self._to_decimal(line) if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", line.strip()) else None
            if value is not None and value > 0:
                pending_amounts.append(value)
                continue
            if not re.search(r"[A-Za-z가-힣]", line):
                continue
            if self._looks_like_business_label(line) or self._looks_like_instruction_or_note(line):
                continue
            if not re.search(r"(션\s*[A-Z0-9]|옵션|bracket|브라켓|판|plate)", line, flags=re.IGNORECASE):
                continue
            spec = None
            for candidate in lines[index + 1:index + 4]:
                if re.search(r"(VAT|부가세|선택시합계|옵션라인)", candidate, flags=re.IGNORECASE):
                    break
                if re.search(r"[A-Za-z가-힣]", candidate) and not self._looks_like_business_label(candidate):
                    spec = candidate
                    break
            item: dict = {"item_name": line.strip(), "specification": spec, "unit": "EA"}
            if len(pending_amounts) >= 2:
                item["unit_price"] = self._number_value(pending_amounts[-2])
                item["supply_amount"] = self._number_value(pending_amounts[-1])
            items.append(item)
            pending_amounts = []
        return items

    def _field_for_quotation_option_header(self, line: str) -> bool:
        key = re.sub(r"[^0-9a-z가-힣]+", "", str(line or "").lower())
        return key in {"품목명", "규격", "단위", "단가", "공급가액", "견적품목", "description", "itemdescription"}

    def _extract_statement_date_line_items(self, lines: list[str]) -> list[dict]:
        text = "\n".join(lines)
        if not re.search(r"(거래명세서|명세서번호|전월이월|금월합계)", text):
            return []
        starts = [index for index, line in enumerate(lines) if re.fullmatch(r"[\]\[]?\d{1,2}-\d{1,2}", line.strip()) or re.fullmatch(r"\d{1,2}-\d", line.strip())]
        items: list[dict] = []
        for pos, start in enumerate(starts):
            end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
            segment = [line for line in lines[start + 1:end] if line.strip()]
            if not segment:
                continue
            truncated: list[str] = []
            for line in segment:
                if re.search(r"(금월|총미수금|전월이월|담당|승인)", line):
                    break
                truncated.append(line)
            segment = truncated
            if not segment:
                continue
            name = next((line for line in segment if re.search(r"[A-Za-z가-힣]", line) and not self._extract_unit(line)), None)
            if not name:
                continue
            spec = next((line for line in segment if line != name and re.search(r"\\d", line) and re.search(r"[A-Za-z가-힣xX]", line)), None)
            amounts = [self._to_decimal(line) for line in segment]
            amounts = [amount for amount in amounts if amount is not None and amount >= Decimal("1000")]
            item: dict = {"item_name": name, "specification": spec, "unit": "EA"}
            if amounts:
                item["supply_amount"] = self._number_value(amounts[-1])
            items.append(item)
        return items

    def _table_row_indexes(self, lines: list[str]) -> set[int]:
        row_indexes: set[int] = set()
        for index, line in enumerate(lines):
            headers = self._split_table_line(line)
            mapped_headers = [self._line_item_field_for_label(header) for header in headers]
            if len(headers) < 3 or sum(bool(header) for header in mapped_headers) < 3:
                continue
            for row_index, row in enumerate(lines[index + 1:], start=index + 1):
                cells = self._split_table_line(row)
                if len(cells) < 3:
                    break
                if sum(bool(self._line_item_field_for_label(cell)) for cell in cells) >= 3:
                    break
                if self._looks_like_table_data_row(cells, mapped_headers):
                    row_indexes.add(row_index)
                    continue
                break
            if row_indexes:
                break
        return row_indexes

    def _looks_like_table_data_row(self, cells: list[str], mapped_headers: list[str | None]) -> bool:
        if len(cells) < 3:
            return False
        mapped_count = sum(bool(header) for header in mapped_headers)
        if mapped_count < 3:
            return False
        normalized = self._normalize_line_item({
            field: cell
            for field, cell in zip(mapped_headers, cells + [""] * max(0, len(mapped_headers) - len(cells)))
            if field
        })
        return bool(normalized.get("item_name") or normalized.get("item_code"))

    def _explicit_item_block_lines(self, lines: list[str]) -> list[str]:
        blocks: list[str] = []
        in_block = False
        for line in lines:
            lowered = line.lower()
            if "[item table start]" in lowered:
                in_block = True
                continue
            if "[item table end]" in lowered:
                in_block = False
                continue
            if in_block:
                blocks.append(line)
        return blocks

    def _extract_key_value_line_items(self, lines: list[str]) -> list[dict]:
        current: dict = {}
        items: list[dict] = []
        seen_item_field = False
        last_field: str | None = None
        for line in lines:
            parsed = self._parse_labeled_line(line)
            if not parsed:
                if last_field == "item_name" and current.get("item_name") and not self._looks_like_item_block_boundary(line):
                    current["item_name"] = f"{current['item_name']} {self._clean_value(line) or ''}".strip()
                continue
            field, value = parsed
            if field not in LINE_ITEM_LABELS:
                last_field = None
                continue
            if field == "item_name" and seen_item_field and self._line_item_has_identity(current):
                items.append(self._normalize_line_item(current))
                current = {}
            seen_item_field = True
            last_field = field
            if field == "quantity":
                quantity, unit = self._parse_quantity_and_unit(value)
                current[field] = quantity
                if unit and not current.get("unit"):
                    current["unit"] = unit
            elif field in {"unit_price", "supply_amount", "tax_amount", "line_total"}:
                current[field] = self._normalize_number(value)
            else:
                current[field] = self._clean_value(value)
        if self._line_item_has_identity(current):
            items.append(self._normalize_line_item(current))
        return items

    def _looks_like_item_block_boundary(self, line: str) -> bool:
        lowered = line.lower().strip()
        if not lowered:
            return True
        if "[item table" in lowered:
            return True
        return bool(re.search(r"(공급가액\s*합계|세액\s*합계|총\s*합계|grand total|invoice total|subtotal total|vat total)", lowered, flags=re.IGNORECASE))

    def _extract_table_line_items(self, lines: list[str]) -> list[dict]:
        items: list[dict] = []
        for index, line in enumerate(lines):
            headers = self._split_table_line(line)
            mapped_headers = [self._line_item_field_for_label(header) for header in headers]
            if len(headers) < 3 or sum(bool(header) for header in mapped_headers) < 3:
                continue
            for row in lines[index + 1:]:
                cells = self._split_table_line(row)
                if len(cells) < 3:
                    break
                if sum(bool(self._line_item_field_for_label(cell)) for cell in cells) >= 3:
                    break
                if len(cells) < len(mapped_headers):
                    cells = cells + [""] * (len(mapped_headers) - len(cells))
                elif len(cells) > len(mapped_headers):
                    cells = cells[:len(mapped_headers)]
                item: dict = {}
                for field, cell in zip(mapped_headers, cells):
                    if not field:
                        continue
                    item[field] = cell
                normalized = self._normalize_line_item(item)
                if normalized.get("item_name"):
                    items.append(normalized)
            if items:
                break
        return items

    def _split_table_line(self, line: str) -> list[str]:
        stripped = line.strip()
        if "|" in stripped:
            return [part.strip() for part in stripped.split("|")]
        if "\t" in stripped:
            return [part.strip() for part in stripped.split("\t")]
        if "," in stripped and len(stripped.split(",")) >= 4:
            return [part.strip() for part in stripped.split(",")]
        if " / " in stripped and len(stripped.split(" / ")) >= 3:
            return [part.strip() for part in stripped.split(" / ")]
        return [part.strip() for part in re.split(r"\s{2,}", stripped) if part.strip()]

    def _parse_labeled_line(self, line: str) -> tuple[str, str] | None:
        match = re.match(r"\s*([^:：|]+?)\s*[:：]\s*(.*?)\s*$", line)
        if not match:
            return None
        field = self._line_item_field_for_label(match.group(1))
        if not field:
            return None
        return field, match.group(2).strip()

    def _line_item_field_for_label(self, label: str) -> str | None:
        key = re.sub(r"[\s_/-]+", "", label.strip().lower())
        return LINE_ITEM_LABEL_LOOKUP.get(key)

    def _line_item_has_identity(self, item: dict) -> bool:
        return bool(item.get("item_name") or item.get("item_code"))

    def _normalize_line_item(self, item: dict) -> dict:
        item_code = self._clean_code_value(
            item.get("item_code") or item.get("document_item_code") or item.get("source_item_code")
        )
        normalized = {
            "item_name": self._normalize_item_name_value(item.get("item_name")),
            "item_code": item_code,
            "document_item_code": item_code,
            "source_item_code": item_code,
            "specification": self._normalize_specification_value(item.get("specification")),
            "quantity": item.get("quantity"),
            "unit": self._clean_value(item.get("unit")),
            "unit_price": item.get("unit_price"),
            "supply_amount": item.get("supply_amount"),
            "tax_amount": item.get("tax_amount"),
            "line_total": item.get("line_total"),
        }
        normalized = self._split_trailing_material_grade_from_item_name(normalized)
        item_warnings = list(item.get("validation_warnings") or [])
        if item_warnings:
            normalized["validation_warnings"] = item_warnings
        if not normalized["unit"] and isinstance(normalized["quantity"], str):
            _, unit = self._parse_quantity_and_unit(normalized["quantity"])
            normalized["unit"] = unit
        if isinstance(normalized["quantity"], str):
            quantity, unit = self._parse_quantity_and_unit(normalized["quantity"])
            normalized["quantity"] = quantity
            normalized["unit"] = normalized["unit"] or unit
        if item.get("_quantity_inferred_without_cell"):
            normalized["quantity"] = None
            normalized["_quantity_suppressed"] = True
        for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]:
            normalized[field] = self._normalize_number(normalized[field])
        for field in [
            "ordered_quantity",
            "requested_quantity",
            "received_quantity",
            "delivered_quantity",
            "remaining_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "lot_no",
            "inspection_result",
        ]:
            if item.get(field) not in (None, ""):
                normalized[field] = self._normalize_number(item[field]) if "quantity" in field else self._clean_value(item[field])
        normalized = self._repair_line_item_arithmetic(normalized)
        normalized = self._suppress_implausible_line_item_numbers(normalized)
        if normalized.get("quantity") is None and normalized.get("unit") and not normalized.get("_quantity_suppressed"):
            normalized["quantity"] = self._normalize_number(str(item.get("quantity") or ""))
        warnings = self._line_item_amount_warnings(normalized)
        for warning in item_warnings:
            if warning in {
                "invalid_tax_greater_than_total",
                "invalid_tax_greater_than_supply",
                "invalid_supply_greater_than_total",
                "invalid_line_total",
            } and warning not in warnings:
                continue
            if warning == "explicit_quantity_price_amount_mismatch" and self._quantity_price_matches_supply(normalized):
                if "malformed_amount_columns_repaired" not in warnings:
                    warnings.append("malformed_amount_columns_repaired")
                continue
            if warning not in warnings:
                warnings.append(warning)
        if warnings:
            normalized["validation_warnings"] = warnings
        return {key: value for key, value in normalized.items() if value not in (None, "") and not str(key).startswith("_")}

    def _normalize_item_name_value(self, value: Any) -> str | None:
        name = self._clean_value(value)
        if not name:
            return None
        return self._strip_leading_row_number_from_item_name(name)

    def _strip_leading_row_number_from_item_name(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return text
        # Strip only a standalone table row number. Model numbers such as
        # 2PIN, 8X60, 100R, and 10mm are kept because the digit is fused to
        # the product token rather than separated as its own cell.
        return re.sub(r"^\s*\d{1,3}\s+(?=[A-Za-z가-힣])", "", text, count=1).strip()

    def _split_trailing_material_grade_from_item_name(self, item: dict) -> dict:
        name = self._clean_value(item.get("item_name"))
        spec = self._clean_value(item.get("specification"))
        if not name or not spec:
            return item
        match = re.match(
            r"^(?P<base>.+?)\s+(?P<grade>SUS\s*3[O0]4|SUS\s*316|STS\s*3[O0]4|STS\s*316|SS\s*400|S45C)$",
            name,
            flags=re.IGNORECASE,
        )
        if not match:
            return item
        grade = match.group("grade").upper().replace(" ", "").replace("O", "0")
        if grade.startswith("STS"):
            grade = "SUS" + grade[3:]
        if grade in spec.upper().replace(" ", ""):
            item["item_name"] = self._clean_value(match.group("base"))
            return item
        if re.search(r"\d+(?:\.\d+)?T\b|^\d+(?:\.\d+)?T$", spec, flags=re.IGNORECASE):
            item["item_name"] = self._clean_value(match.group("base"))
            item["specification"] = self._clean_value(f"{grade} {spec}")
        return item

    def _suppress_implausible_line_item_numbers(self, item: dict) -> dict:
        quantity = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
        supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        tax = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
        total = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
        if quantity is not None and quantity > 5000:
            tax_ok = supply is not None and tax is not None and abs(tax - supply * Decimal("0.1")) <= max(Decimal("1"), abs(supply) * Decimal("0.02"))
            total_ok = supply is not None and tax is not None and total is not None and abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.02"))
            if not (tax_ok and total_ok):
                item.pop("quantity", None)
                item.pop("unit_price", None)
                item["_quantity_suppressed"] = True
        return item

    def _should_preserve_quantity_price_from_row(self, item: dict) -> bool:
        warnings = set(item.get("validation_warnings") or [])
        return bool(
            warnings
            & {
                "explicit_quantity_price_amount_mismatch",
                "supply_amount_recovered_from_line_total_tax",
                "missing_quantity",
                "quantity_cell_blank",
                "ocr_quantity_price_unverified",
                "unit_price_not_visible",
            }
        )

    def _repair_line_item_arithmetic(self, item: dict) -> dict:
        if self._should_preserve_quantity_price_from_row(item):
            return item
        quantity = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
        unit_price = self._to_decimal(str(item.get("unit_price"))) if item.get("unit_price") is not None else None
        supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        if quantity is None or quantity <= 0 or supply is None or supply <= 0:
            return item
        if unit_price is not None and abs((quantity * unit_price) - supply) <= max(Decimal("1"), abs(supply) * Decimal("0.02")):
            return item
        if unit_price is not None and unit_price > supply:
            return item
        repaired = supply / quantity
        if repaired > 0 and repaired == repaired.to_integral_value() and Decimal("10") <= repaired <= Decimal("1000000"):
            item["unit_price"] = int(repaired)
        return item

    def _line_item_amount_warnings(self, item: dict) -> list[str]:
        warnings: list[str] = []
        supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        tax = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
        total = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
        if tax is not None and total is not None and total >= 0 and tax > total:
            warnings.append("invalid_tax_greater_than_total")
        if supply is not None and tax is not None and supply > 0 and tax > supply:
            warnings.append("invalid_tax_greater_than_supply")
        if supply is not None and total is not None and supply > total:
            warnings.append("invalid_supply_greater_than_total")
        if supply is not None and tax is not None and total is not None and abs((supply + tax) - total) > max(Decimal("1"), abs(total) * Decimal("0.02")):
            warnings.append("invalid_line_total")
        return warnings

    def _quantity_price_matches_supply(self, item: dict) -> bool:
        quantity = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
        unit_price = self._to_decimal(str(item.get("unit_price"))) if item.get("unit_price") is not None else None
        supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
        if quantity is None or unit_price is None or supply is None or supply <= 0:
            return False
        return abs((quantity * unit_price) - supply) <= max(Decimal("1"), abs(supply) * Decimal("0.02"))

    def _repair_line_items_against_document_totals(
        self,
        line_items: list[dict],
        amount: Decimal | None,
        subtotal: Decimal | None,
        tax: Decimal | None,
        lines: list[str],
    ) -> list[dict]:
        if not line_items or amount is None:
            return line_items
        if any("untrusted_ocr_amount" in (item.get("validation_warnings") or []) for item in line_items):
            return line_items
        if len(line_items) == 1 and self._line_item_amount_warnings(line_items[0]):
            return line_items
        if any("|" in line for line in lines) and any(self._line_item_amount_warnings(item) for item in line_items):
            return line_items
        line_items = self._suppress_unsafe_line_totals_when_supply_matches_document_total(line_items, amount, tax, lines)
        current_total = self._line_items_total(line_items)
        if current_total is None or abs(current_total - amount) <= max(Decimal("1"), abs(amount) * Decimal("0.02")):
            return line_items
        repaired = [dict(item) for item in line_items]
        triples = self._valid_amount_triples_from_lines(lines)
        used: set[tuple[Decimal, Decimal, Decimal]] = set()
        for item in repaired:
            item_total = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
            item_tax = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
            for supply_value, tax_value, total_value in triples:
                if (supply_value, tax_value, total_value) in used:
                    continue
                if item_total is not None and item_total == supply_value:
                    item["supply_amount"] = self._number_value(supply_value)
                    item["tax_amount"] = self._number_value(tax_value)
                    item["line_total"] = self._number_value(total_value)
                    used.add((supply_value, tax_value, total_value))
                    break
                if item_tax is not None and item_tax == tax_value:
                    item["supply_amount"] = self._number_value(supply_value)
                    item["tax_amount"] = self._number_value(tax_value)
                    item["line_total"] = self._number_value(total_value)
                    used.add((supply_value, tax_value, total_value))
                    break
        effective_subtotal = subtotal
        effective_tax = tax
        if amount is not None and (
            effective_subtotal is None
            or effective_tax is None
            or effective_subtotal < amount * Decimal("0.2")
            or effective_tax < amount * Decimal("0.02")
        ):
            inferred_subtotal = (amount / Decimal("1.1")).quantize(Decimal("1"))
            inferred_tax = amount - inferred_subtotal
            if inferred_subtotal > 0 and inferred_tax >= 0 and abs((inferred_subtotal + inferred_tax) - amount) <= max(Decimal("1"), amount * Decimal("0.02")):
                effective_subtotal = inferred_subtotal
                effective_tax = inferred_tax

        if effective_subtotal is not None and effective_tax is not None:
            good_supply = Decimal("0")
            good_tax = Decimal("0")
            good_total = Decimal("0")
            bad_indexes: list[int] = []
            for index, item in enumerate(repaired):
                supply_value = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
                tax_value = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
                total_value = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
                line_total_ok = (
                    supply_value is not None
                    and tax_value is not None
                    and total_value is not None
                    and abs((supply_value + tax_value) - total_value) <= max(Decimal("1"), abs(total_value) * Decimal("0.02"))
                )
                tax_ok = (
                    supply_value is not None
                    and tax_value is not None
                    and abs(tax_value - supply_value * Decimal("0.1")) <= max(Decimal("1"), abs(supply_value) * Decimal("0.02"))
                )
                if line_total_ok and tax_ok and total_value > amount * Decimal("0.05"):
                    good_supply += supply_value
                    good_tax += tax_value
                    good_total += total_value
                else:
                    bad_indexes.append(index)
            if len(bad_indexes) == 1:
                index = bad_indexes[0]
                residual_supply = effective_subtotal - good_supply
                residual_tax = effective_tax - good_tax
                residual_total = amount - good_total
                if residual_supply > 0 and residual_tax >= 0 and residual_total > 0 and abs((residual_supply + residual_tax) - residual_total) <= max(Decimal("1"), residual_total * Decimal("0.02")):
                    item = repaired[index]
                    item["supply_amount"] = self._number_value(residual_supply)
                    item["tax_amount"] = self._number_value(residual_tax)
                    item["line_total"] = self._number_value(residual_total)
                    if not self._should_preserve_quantity_price_from_row(item):
                        self._repair_quantity_price_from_ocr_context(item, residual_supply, lines)
            elif len(bad_indexes) > 1:
                remaining_supply = effective_subtotal - good_supply
                remaining_tax = effective_tax - good_tax
                remaining_total = amount - good_total
                for order, index in enumerate(bad_indexes):
                    item = repaired[index]
                    if order == len(bad_indexes) - 1:
                        supply_value = remaining_supply
                    else:
                        supply_value = self._best_supply_from_item_context(item, remaining_supply, lines)
                    if supply_value is None or supply_value <= 0:
                        continue
                    tax_value = supply_value * Decimal("0.1")
                    total_value = supply_value + tax_value
                    if total_value > remaining_total + max(Decimal("1"), remaining_total * Decimal("0.02")):
                        continue
                    item["supply_amount"] = self._number_value(supply_value)
                    item["tax_amount"] = self._number_value(tax_value)
                    item["line_total"] = self._number_value(total_value)
                    if not self._should_preserve_quantity_price_from_row(item):
                        self._repair_quantity_price_from_ocr_context(item, supply_value, lines)
                    remaining_supply -= supply_value
                    remaining_tax -= tax_value
                    remaining_total -= total_value
        repaired = self._suppress_unsafe_line_totals_when_supply_matches_document_total(repaired, amount, tax, lines)
        for item in repaired:
            supply_value = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
            quantity_value = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
            unit_price_value = self._to_decimal(str(item.get("unit_price"))) if item.get("unit_price") is not None else None
            if supply_value is None or supply_value <= 0:
                continue
            if (
                quantity_value is None
                or unit_price_value is None
                or quantity_value <= 0
                or unit_price_value <= 0
                or abs((quantity_value * unit_price_value) - supply_value) > max(Decimal("1"), supply_value * Decimal("0.02"))
            ):
                if not self._should_preserve_quantity_price_from_row(item):
                    self._repair_quantity_price_from_ocr_context(item, supply_value, lines)
        return [self._normalize_line_item(item) for item in repaired]

    def _suppress_unsafe_line_totals_when_supply_matches_document_total(
        self,
        line_items: list[dict],
        amount: Decimal | None,
        document_tax: Decimal | None,
        lines: list[str],
    ) -> list[dict]:
        if not line_items or amount is None:
            return line_items
        if document_tax is not None and abs(document_tax) > Decimal("0.01"):
            return line_items
        context = "\n".join(lines).lower()
        if re.search(r"(거\s*래\s*명\s*세\s*서|transaction\s+statement|전월\s*이월|총\s*미수금|금월\s*합계)", context, flags=re.IGNORECASE):
            return line_items
        supply_sum = Decimal("0")
        line_total_sum = Decimal("0")
        supply_count = 0
        line_total_count = 0
        tax_count = 0
        implied_tax_total_count = 0
        for item in line_items:
            supply_value = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
            tax_value = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
            line_total_value = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
            if supply_value is not None:
                supply_sum += supply_value
                supply_count += 1
            if tax_value is not None:
                tax_count += 1
            if line_total_value is not None:
                line_total_sum += line_total_value
                line_total_count += 1
                if supply_value is not None and abs(line_total_value - supply_value * Decimal("1.1")) <= max(Decimal("1"), abs(line_total_value) * Decimal("0.02")):
                    implied_tax_total_count += 1
        if supply_count != len(line_items) or line_total_count == 0:
            return line_items
        tolerance = max(Decimal("1"), abs(amount) * Decimal("0.02"))
        document_total_matches_supply = abs(supply_sum - amount) <= tolerance and abs(line_total_sum - amount) > tolerance
        line_totals_look_tax_inferred = (
            tax_count == 0
            and implied_tax_total_count == line_total_count
            and abs(line_total_sum - amount) <= tolerance
        )
        if not (document_total_matches_supply or line_totals_look_tax_inferred):
            return line_items
        cleaned: list[dict] = []
        for item in line_items:
            next_item = dict(item)
            supply_value = self._to_decimal(str(next_item.get("supply_amount"))) if next_item.get("supply_amount") is not None else None
            line_total_value = self._to_decimal(str(next_item.get("line_total"))) if next_item.get("line_total") is not None else None
            if supply_value is not None and line_total_value is not None and line_total_value != supply_value:
                next_item.pop("tax_amount", None)
                next_item.pop("line_total", None)
            cleaned.append(next_item)
        return cleaned

    def _valid_amount_triples_from_lines(self, lines: list[str]) -> list[tuple[Decimal, Decimal, Decimal]]:
        values: list[Decimal] = []
        for line in lines:
            for token in re.findall(r"\d[\d,]*(?:\.\d+)?[A-Za-z]?", line):
                value = self._amount_from_labeled_match(token, line)
                if value is not None and value > 0:
                    values.append(value)
        triples: list[tuple[Decimal, Decimal, Decimal]] = []
        for i, supply_value in enumerate(values):
            for j in range(i + 1, min(i + 5, len(values))):
                tax_value = values[j]
                for k in range(j + 1, min(j + 4, len(values))):
                    total_value = values[k]
                    if abs(tax_value - supply_value * Decimal("0.1")) <= max(Decimal("1"), supply_value * Decimal("0.02")) and abs(total_value - (supply_value + tax_value)) <= max(Decimal("1"), total_value * Decimal("0.02")):
                        triple = (supply_value, tax_value, total_value)
                        if triple not in triples:
                            triples.append(triple)
        return triples

    def _repair_quantity_price_from_ocr_context(self, item: dict, supply: Decimal, lines: list[str]) -> None:
        name = str(item.get("item_name") or "").split()[0]
        if not name:
            return
        normalized_name = re.sub(r"[^0-9a-z가-힣]+", "", name.lower())
        for index, line in enumerate(lines):
            normalized_line = re.sub(r"[^0-9a-z가-힣]+", "", line.lower())
            if name.lower() not in line.lower() and (not normalized_name or normalized_name not in normalized_line):
                continue
            window = " ".join(lines[index:index + 8])
            raw_tokens = re.findall(r"(?:[BO])?\d[\d,]*(?:\.\d+)?[A-Za-z가-힣]?", window, flags=re.IGNORECASE)
            numbers: list[tuple[int, Decimal, str]] = []
            for position, token in enumerate(raw_tokens):
                number = self._amount_from_labeled_match(self._normalize_ocr_numeric_token(token), window)
                if number is not None and number > 0:
                    numbers.append((position, number, token))
            scored: list[tuple[int, Decimal, Decimal]] = []
            identity = " ".join(str(item.get(field) or "") for field in ["item_code", "item_name", "specification"])
            had_quantity = item.get("quantity") not in (None, "")
            for position, number, token in numbers:
                if number <= 0:
                    continue
                code_text = str(item.get("item_code") or item.get("document_item_code") or "")
                if code_text and token.lower() in code_text.lower():
                    continue
                token_has_ocr_amount_suffix = bool(re.search(r"[CcGgLl]$", token))
                token_has_spec_suffix = bool(re.search(r"[A-Za-z가-힣]$", token)) and not token_has_ocr_amount_suffix
                price = supply / number
                if (
                    not token_has_spec_suffix
                    and price > 0
                    and price == price.to_integral_value()
                    and Decimal("10") <= price <= Decimal("1000000")
                ):
                    score = 0
                    if number == number.to_integral_value():
                        score += 5
                    if number <= Decimal("5000"):
                        score += 3
                    if re.search(r"(connector|pcb|cable|harness|커넥터|하네스)", identity, flags=re.IGNORECASE) and number >= 300 and price <= 5000:
                        score += 8
                    if re.search(r"(bolt|washer|볼트|와셔)", identity, flags=re.IGNORECASE) and number >= 100 and price <= 1000:
                        score += 8
                    if re.search(r"(plate|plt|bracket|철판|판|브라켓|플레이트)", identity, flags=re.IGNORECASE) and number <= 100 and price >= 1000:
                        score += 8
                    if str(number) in str(item.get("specification") or "") and len(numbers) > 1:
                        score -= 6
                    if number > Decimal("3000"):
                        score -= 6
                    if price < Decimal("100") and not re.search(r"(bolt|washer|볼트|와셔)", identity, flags=re.IGNORECASE):
                        score -= 8
                    score -= min(position, 6)
                    scored.append((score, number, price))
                if token_has_spec_suffix and not token_has_ocr_amount_suffix:
                    continue
                scales = [Decimal("1")] if token_has_ocr_amount_suffix else [Decimal("1"), Decimal("10"), Decimal("100")]
                for scale in scales:
                    if not had_quantity and not token_has_ocr_amount_suffix:
                        continue
                    if str(number) in str(item.get("specification") or "") and len(numbers) > 1 and not token_has_ocr_amount_suffix:
                        continue
                    price_candidate = number * scale
                    if not (Decimal("10") <= price_candidate <= Decimal("1000000")):
                        continue
                    quantity = supply / price_candidate
                    if quantity <= 0 or quantity != quantity.to_integral_value() or quantity > Decimal("5000"):
                        continue
                    score = 6
                    if scale != 1:
                        score += 5
                    if token_has_ocr_amount_suffix:
                        score += 10
                    if re.search(r"(connector|pcb|cable|harness|커넥터|하네스)", identity, flags=re.IGNORECASE) and quantity >= 100 and price_candidate <= 5000:
                        score += 8
                    if re.search(r"(plate|plt|bracket|철판|판|브라켓|플레이트)", identity, flags=re.IGNORECASE) and quantity <= 100 and price_candidate >= 1000:
                        score += 8
                    if quantity > Decimal("3000"):
                        score -= 6
                    score -= min(position, 6)
                    scored.append((score, quantity, price_candidate))
            if scored:
                scored.sort(key=lambda entry: (-entry[0], -entry[1]))
                _, quantity, price = scored[0]
                item["quantity"] = self._number_value(quantity)
                item["unit_price"] = self._number_value(price)
            return

    def _best_supply_from_item_context(self, item: dict, remaining_supply: Decimal, lines: list[str]) -> Decimal | None:
        name = str(item.get("item_name") or "").split()[0]
        if not name:
            return None
        windows: list[tuple[int, str]] = []
        identity_text = " ".join(str(item.get(field) or "") for field in ["item_name", "specification", "item_code"])
        identity_terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z가-힣0-9]+", identity_text)
            if len(term) >= 2 or re.fullmatch(r"[가-힣]", term)
        ]
        normalized_identity = re.sub(r"[^0-9a-z가-힣]+", "", identity_text.lower())
        normalized_name = re.sub(r"[^0-9a-z가-힣]+", "", name.lower())
        for index, line in enumerate(lines):
            normalized_line = re.sub(r"[^0-9a-z가-힣]+", "", line.lower())
            if name.lower() not in line.lower() and (not normalized_name or normalized_name not in normalized_line):
                continue
            window = " ".join(lines[index:index + 8])
            normalized_window = re.sub(r"[^0-9a-z가-힣]+", "", window.lower())
            score = sum(1 for term in identity_terms if term in window.lower() or term in normalized_window)
            score += 2 * sum(1 for term in identity_terms if term in line.lower() or term in normalized_line)
            if normalized_identity and normalized_window:
                score += int(fuzz.partial_ratio(normalized_identity, normalized_window) // 10)
            windows.append((score, window))
        if not windows:
            return None
        windows.sort(key=lambda entry: -entry[0])
        candidates: list[Decimal] = []
        for _, window in windows[:1]:
            for token in re.findall(r"\d[\d,]*(?:\.\d+)?[A-Za-z]?", window):
                value = self._amount_from_labeled_match(token, window)
                if value is not None and Decimal("1000") <= value <= remaining_supply:
                    candidates.append(value)
        if not candidates:
            return None
        candidates = sorted(set(candidates), reverse=True)
        return candidates[0]

    def _normalize_ocr_numeric_token(self, token: str) -> str:
        value = str(token or "")
        if re.match(r"^[Bb]\d", value):
            value = f"8{value[1:]}"
        elif re.match(r"^[Oo]\d", value):
            value = f"0{value[1:]}"
        return value

    def _clean_code_value(self, value: object) -> str | None:
        cleaned = self._clean_value(value)
        if not cleaned:
            return None
        if cleaned in {"-", "—", "–", "N/A", "n/a", "없음"}:
            return None
        if re.search(r"(미확인|신뢰도|검토|비어|missing|required)", cleaned, flags=re.IGNORECASE):
            return None
        return cleaned

    def _dedupe_line_items(self, items: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[tuple] = set()
        preserve_sparse_review_duplicates = len(items) >= 8
        for item in items:
            if self._looks_like_line_item_header_text(str(item.get("item_name") or "")):
                continue
            has_numeric_evidence = any(
                item.get(field) is not None
                for field in ["quantity", "unit_price", "supply_amount", "tax_amount", "line_total"]
            )
            preserve_duplicate = (
                preserve_sparse_review_duplicates
                and not has_numeric_evidence
                and bool(item.get("item_name"))
                and self._looks_like_sparse_repeated_item_name(str(item.get("item_name") or ""))
            )
            key = (
                self._normalized_item_key(item.get("item_name")),
                self._normalized_item_key(item.get("item_code")),
                self._normalized_item_key(item.get("specification")),
                item.get("quantity"),
                item.get("supply_amount"),
                item.get("line_total"),
                item.get("_dedupe_token"),
            )
            if key in seen and not preserve_duplicate:
                continue
            seen.add(key)
            deduped.append({key: value for key, value in item.items() if not str(key).startswith("_")})
        return deduped

    def _looks_like_line_item_header_text(self, value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return False
        compact = re.sub(r"[\s_./|:-]+", "", text).lower()
        header_terms = [
            "no",
            "번호",
            "순번",
            "품목명",
            "품명",
            "item",
            "itemname",
            "itemdescription",
            "description",
            "vendorsku",
            "품목코드",
            "문서품목코드",
            "내부품목코드",
            "lotno",
            "규격",
            "spec",
            "material",
            "수량",
            "qty",
            "quantity",
            "입고수량",
            "합격수량",
            "불량수량",
            "단위",
            "unit",
            "단가",
            "unitprice",
            "공급가액",
            "subtotal",
            "세액",
            "tax",
            "vat",
            "합계",
            "total",
            "판정",
            "result",
            "remark",
        ]
        hits = sum(1 for term in header_terms if term in compact)
        has_real_item_signal = bool(re.search(r"\bLOT[-A-Z0-9]+|[A-Z]{2,}[-A-Z0-9]*\d|[가-힣A-Za-z]+\d+[A-Za-z]*", text, flags=re.IGNORECASE))
        return hits >= 3 and not has_real_item_signal

    def _collapse_duplicate_line_item_sets(self, items: list[dict], document_total: Decimal | None) -> list[dict]:
        if not items:
            return items
        collapsed = self._dedupe_line_items(items)
        if document_total is None or document_total <= 0:
            return collapsed
        line_sum = self._line_items_total(collapsed)
        if line_sum is None or line_sum <= 0:
            return collapsed
        ratio = line_sum / document_total
        nearest = ratio.to_integral_value()
        if nearest < 2 or abs(ratio - nearest) > Decimal("0.03"):
            return collapsed
        grouped: dict[tuple[str, str, str, str], dict] = {}
        for item in collapsed:
            key = (
                self._normalized_item_key(item.get("item_code") or item.get("document_item_code") or item.get("source_item_code")),
                self._normalized_item_key(item.get("item_name")),
                self._normalized_item_key(item.get("specification")),
                str(item.get("line_total") or item.get("supply_amount") or ""),
            )
            existing = grouped.get(key)
            if existing is None or self._line_item_quality_score(item) > self._line_item_quality_score(existing):
                grouped[key] = item
        reduced = list(grouped.values())
        reduced_sum = self._line_items_total(reduced)
        if reduced_sum is not None and reduced_sum < line_sum:
            return reduced
        return collapsed

    def _suppress_untrusted_foreign_amounts(self, items: list[dict], document_total: Decimal | None, currency: str | None) -> list[dict]:
        if currency != "USD" or document_total is None or document_total <= 0 or not items:
            return items
        line_sum = self._line_items_total(items)
        if line_sum is None or line_sum <= document_total * Decimal("1.5"):
            return items
        cleaned: list[dict] = []
        for item in items:
            next_item = dict(item)
            for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]:
                next_item.pop(field, None)
            next_item.setdefault("validation_warnings", [])
            if "untrusted_ocr_amount" not in next_item["validation_warnings"]:
                next_item["validation_warnings"].append("untrusted_ocr_amount")
            cleaned.append(next_item)
        return cleaned

    def _repair_ocr_table_postprocess(
        self,
        items: list[dict],
        document_total: Decimal | None,
        currency: str | None,
        lines: list[str],
    ) -> list[dict]:
        if not items:
            return items
        repaired = [self._clean_ocr_line_item_artifacts(dict(item)) for item in items]
        repaired = [item for item in repaired if not self._looks_like_footer_note_item(item)]
        repaired = self._drop_identity_only_noise_items(repaired)
        repaired = self._repair_usd_vertical_invoice_rows(repaired, document_total, currency, lines)
        repaired = self._repair_krw_vertical_amount_rows(repaired, document_total, lines)
        repaired = [self._clean_ocr_line_item_artifacts(dict(item)) for item in repaired]
        return self._dedupe_line_items(repaired)

    def _drop_identity_only_noise_items(self, items: list[dict]) -> list[dict]:
        if len(items) <= 1:
            return items
        has_structured_items = any(
            item.get("item_code") or item.get("document_item_code") or item.get("specification") or item.get("quantity") or item.get("supply_amount") or item.get("line_total")
            for item in items
        )
        if not has_structured_items:
            return items
        return [
            item for item in items
            if item.get("item_code")
            or item.get("document_item_code")
            or item.get("specification")
            or item.get("quantity")
            or item.get("supply_amount")
            or item.get("line_total")
            or (
                len(items) >= 8
                and item.get("item_name")
                and self._looks_like_sparse_repeated_item_name(str(item.get("item_name") or ""))
            )
        ]

    def _clean_ocr_line_item_artifacts(self, item: dict) -> dict:
        name = str(item.get("item_name") or "")
        if name:
            # Remove OCR-leaked amount cells that were prepended to the next item name.
            name = re.sub(r"^\s*(?:\d{1,3}\s+)?(?:\d{4,}(?:\.\d+)?\s+){1,3}(?=\S*[A-Za-z가-힣])", "", name).strip()
            name = re.sub(r"^(?:공급가[액악]|합계|금액)\s+", "", name).strip()
            name = re.sub(r"^(?:[B8]\d{2,4}C?|C\d{3,5}|OOC|0OC|00C)\s+", "", name, flags=re.IGNORECASE).strip()
            name = re.sub(r"\b(?:OOC|0OC|00C|[O0]?\d{2,4}[CG]|P?\d{4,})(?:\s+|$)", " ", name, flags=re.IGNORECASE)
            name = re.sub(r"\b([A-Z]{2,}\d+)\s+O\b", r"\g<1>0", name)
            name = re.sub(r"\bS45C\s+PIN\s+8X60\s+S45\[$", "S45C PIN 8X60", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+", " ", name).strip()
            code_hint = str(item.get("item_code") or item.get("document_item_code") or item.get("source_item_code") or "")
            if re.search(r"\bBRG-H-?100\b", code_hint, flags=re.IGNORECASE) and re.search(r"베어.*하", name):
                name = "베어링 하우징"
            if re.search(r"\bBRG-H-?100\b", code_hint, flags=re.IGNORECASE) and re.search(r"(베어린|베어리|한읙|하을)", name):
                name = "베어링 하우징"
            if re.search(r"\bBRK-SUS-[O0]?1\b", code_hint, flags=re.IGNORECASE) and re.search(r"(브라젯|브라겟)", name):
                name = "스테인리스 브라켓" if re.search(r"(스테인리스|스테인레스|스텐|sus)", name, flags=re.IGNORECASE) else re.sub(r"브라[젯겟]", "브라켓", name)
            if name:
                item["item_name"] = name
        code = item.get("item_code") or item.get("document_item_code") or item.get("source_item_code")
        if code:
            normalized_code = self._normalize_document_item_code(str(code))
            for field in ["item_code", "document_item_code", "source_item_code"]:
                if item.get(field):
                    item[field] = normalized_code
            if item.get("item_name"):
                prefix = normalized_code.split("-", 1)[0]
                if re.fullmatch(r"[A-Z]{2,}\d+", prefix, flags=re.IGNORECASE):
                    item["item_name"] = re.sub(
                        rf"\b{re.escape(prefix[:-1])}\b",
                        prefix,
                        str(item["item_name"]),
                        flags=re.IGNORECASE,
                    )
        identity = " ".join(str(item.get(field) or "") for field in ["item_name", "specification"])
        code_text = str(item.get("item_code") or "")
        if re.search(r"\b(?:pin|s45c)\b|8\s*x\s*60", identity, flags=re.IGNORECASE) and re.search(r"bolt|BOLT-M8", code_text, flags=re.IGNORECASE):
            for field in ["item_code", "document_item_code", "source_item_code"]:
                item.pop(field, None)
            item.setdefault("validation_warnings", [])
            if "item_code_name_conflict" not in item["validation_warnings"]:
                item["validation_warnings"].append("item_code_name_conflict")
        return item

    def _normalize_document_item_code(self, value: str) -> str:
        code = value.strip()
        if not code:
            return code
        if re.search(r"\d", code) and re.search(r"[-_]", code):
            code = re.sub(r"(?<=\d)[Oo]{2}C\b", "000", code)
            code = re.sub(r"(?<=-)[Oo](?=\d)", "0", code)
            code = re.sub(r"(?<=\d)[Oo](?=\d|$|-)", "0", code)
            code = re.sub(r"(?<=\d)[Oo]{2}\b", "00", code)
        return code

    def _looks_like_footer_note_item(self, item: dict) -> bool:
        text = " ".join(str(item.get(field) or "") for field in ["item_name", "specification"])
        return bool(re.search(
            r"(문서\s*총액|주의|검토\s*필요|본문서는|ERP\s*입력용|담당|검토|승인|총액\s*:|"
            r"DocuParse\s+realistic|synthetic\s+data|옵션.*선택|모두\s*합산하면\s*안|"
            r"전월이월|품목\s*합계에\s*포함하지|통관/회계|거래처\s*문서가\s*아니라|"
            r"반품\s*문서|품질\s*담당|페이지\s*하단|금액\s*정보\s*없는\s*수량\s*확인용)",
            text,
            flags=re.IGNORECASE,
        ))

    def _repair_usd_vertical_invoice_rows(
        self,
        items: list[dict],
        document_total: Decimal | None,
        currency: str | None,
        lines: list[str],
    ) -> list[dict]:
        if currency != "USD" or document_total is None or document_total <= 0 or len(items) < 2:
            return items
        joined = "\n".join(lines).lower()
        has_description_header = "item description" in joined or re.search(r"(?:^|\n)description(?:\n|$)", joined)
        if not has_description_header or "vendor sku" not in joined or "unit price" not in joined:
            return items
        segments = self._vertical_segments_for_items(items, lines)
        if len(segments) != len(items):
            return items
        amount_choices: list[list[Decimal]] = []
        for segment in segments:
            choices = self._usd_amount_choices_from_segment(segment)
            if not choices:
                return items
            amount_choices.append(choices)
        best = self._choose_amounts_matching_total(amount_choices, document_total)
        if not best:
            return items
        repaired: list[dict] = []
        for item, segment, line_total in zip(items, segments, best):
            next_item = dict(item)
            next_item["line_total"] = self._number_value(line_total)
            next_item["supply_amount"] = self._number_value(line_total)
            next_item["tax_amount"] = self._number_value(Decimal("0"))
            quantity, price = self._infer_usd_quantity_price(next_item, line_total, segment)
            if quantity is not None and price is not None:
                next_item["quantity"] = self._number_value(quantity)
                next_item["unit_price"] = self._number_value(price)
            else:
                next_item.pop("quantity", None)
                next_item.pop("unit_price", None)
            next_item["unit"] = next_item.get("unit") or "EA"
            warnings = [warning for warning in next_item.get("validation_warnings", []) if warning != "untrusted_ocr_amount"]
            if quantity is None or price is None:
                warnings.append("untrusted_ocr_amount")
            if warnings:
                next_item["validation_warnings"] = sorted(set(warnings))
            elif "validation_warnings" in next_item:
                next_item.pop("validation_warnings", None)
            repaired.append(next_item)
        return repaired

    def _vertical_segments_for_items(self, items: list[dict], lines: list[str]) -> list[list[str]]:
        starts: list[int] = []
        normalized_lines = [re.sub(r"[^0-9a-z가-힣]+", "", line.lower()) for line in lines]
        for item in items:
            name = str(item.get("item_name") or "").split()[0]
            code = str(item.get("item_code") or item.get("document_item_code") or "")
            probes = [name, code]
            start = None
            for probe in probes:
                normalized_probe = re.sub(r"[^0-9a-z가-힣]+", "", probe.lower())
                if not normalized_probe:
                    continue
                for index, normalized_line in enumerate(normalized_lines):
                    if normalized_probe and normalized_probe in normalized_line:
                        start = index
                        break
                if start is not None:
                    break
            if start is None:
                return []
            starts.append(start)
        segments: list[list[str]] = []
        for pos, start in enumerate(starts):
            end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
            tail_end = end
            for index in range(start + 1, end):
                if self._looks_like_summary_amount_label_line(lines[index]):
                    tail_end = index
                    break
                if (
                    index > start + 2
                    and self._looks_like_amount_label_line(lines[index])
                    and re.search(r"total|amount|총액|합계", lines[index], flags=re.IGNORECASE)
                ):
                    tail_end = index
                    break
            segments.append(lines[start:tail_end])
        return segments

    def _usd_amount_choices_from_segment(self, segment: list[str]) -> list[Decimal]:
        choices: list[Decimal] = []
        for raw in segment:
            text = raw.strip()
            if re.fullmatch(r"(?:O|0){2}C?", text, flags=re.IGNORECASE):
                continue
            compact = text.replace(",", "").replace("O", "0").replace("o", "0")
            compact = re.sub(r"[GgCcLl]$", "0", compact)
            if not re.fullmatch(r"\d{2,6}", compact):
                continue
            value = Decimal(compact)
            for candidate in [value / Decimal("10"), value / Decimal("100")]:
                if Decimal("0.01") <= candidate <= Decimal("100000") and candidate not in choices:
                    choices.append(candidate)
        choices.sort(reverse=True)
        return choices

    def _choose_amounts_matching_total(self, choices: list[list[Decimal]], document_total: Decimal) -> list[Decimal] | None:
        best: tuple[Decimal, list[Decimal]] | None = None
        def walk(index: int, selected: list[Decimal]) -> None:
            nonlocal best
            if index == len(choices):
                total = sum(selected, Decimal("0"))
                diff = abs(total - document_total)
                if best is None or diff < best[0]:
                    best = (diff, list(selected))
                return
            for candidate in choices[index][:8]:
                walk(index + 1, selected + [candidate])
        walk(0, [])
        if best and best[0] <= max(Decimal("0.01"), document_total * Decimal("0.01")):
            return best[1]
        return None

    def _infer_usd_quantity_price(self, item: dict, line_total: Decimal, segment: list[str]) -> tuple[Decimal | None, Decimal | None]:
        identity = " ".join(str(item.get(field) or "") for field in ["item_name", "item_code", "specification"])
        factors = self._factor_quantity_price_pairs(line_total)
        if not factors:
            return None, None
        scored: list[tuple[int, Decimal, Decimal]] = []
        for quantity, price in factors:
            score = 0
            if quantity == quantity.to_integral_value():
                score += 10
            if price == price.quantize(Decimal("0.01")):
                score += 8
            if re.search(r"(rail|guide)", identity, flags=re.IGNORECASE):
                if quantity <= 20 and Decimal("20") <= price <= Decimal("100"):
                    score += 30
                if quantity == 8 and Decimal("40") <= price <= Decimal("50"):
                    score += 20
            if re.search(r"(cable|harness)", identity, flags=re.IGNORECASE):
                if Decimal("20") <= quantity <= Decimal("100") and Decimal("1") <= price <= Decimal("10"):
                    score += 30
                if quantity == 40:
                    score += 20
            if re.search(r"(connector|pcb)", identity, flags=re.IGNORECASE):
                if quantity >= 100 and price <= Decimal("1"):
                    score += 30
                if quantity == 200:
                    score += 20
            if quantity > 5000 or quantity <= 0 or price <= 0:
                score -= 100
            scored.append((score, quantity, price))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        score, quantity, price = scored[0]
        if score < 25:
            return None, None
        return quantity, price

    def _factor_quantity_price_pairs(self, supply: Decimal) -> list[tuple[Decimal, Decimal]]:
        pairs: list[tuple[Decimal, Decimal]] = []
        cents = int((supply * Decimal("100")).to_integral_value())
        for quantity in range(1, 5001):
            if cents % quantity != 0:
                continue
            price = Decimal(cents // quantity) / Decimal("100")
            if Decimal("0.01") <= price <= Decimal("100000"):
                pairs.append((Decimal(quantity), price))
        return pairs

    def _repair_krw_vertical_amount_rows(self, items: list[dict], document_total: Decimal | None, lines: list[str]) -> list[dict]:
        repaired = [dict(item) for item in items]
        current_total = self._line_items_total(repaired)
        amount_mismatch = (
            document_total is not None
            and current_total is not None
            and abs(current_total - document_total) > max(Decimal("1"), abs(document_total) * Decimal("0.02"))
        )
        segments = self._vertical_segments_for_items(repaired, lines)
        single_malformed_row = len(repaired) == 1 and bool(self._line_item_amount_warnings(repaired[0]))
        if single_malformed_row:
            return repaired
        if amount_mismatch and not single_malformed_row and len(segments) == len(repaired):
            segment_repaired: list[dict] = []
            changed = False
            for item, segment in zip(repaired, segments):
                if "untrusted_ocr_amount" in (item.get("validation_warnings") or []):
                    segment_repaired.append(item)
                    continue
                recovered = self._recover_krw_segment_amount_row(item, segment)
                if recovered:
                    segment_repaired.append(recovered)
                    changed = True
                else:
                    segment_repaired.append(item)
            if changed:
                repaired = segment_repaired
        for item in repaired:
            if "untrusted_ocr_amount" in (item.get("validation_warnings") or []) or self._should_preserve_quantity_price_from_row(item):
                continue
            supply = self._to_decimal(str(item.get("supply_amount"))) if item.get("supply_amount") is not None else None
            tax = self._to_decimal(str(item.get("tax_amount"))) if item.get("tax_amount") is not None else None
            total = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
            if supply is None or supply <= 0:
                continue
            quantity = self._to_decimal(str(item.get("quantity"))) if item.get("quantity") is not None else None
            unit_price = self._to_decimal(str(item.get("unit_price"))) if item.get("unit_price") is not None else None
            identity = " ".join(str(item.get(field) or "") for field in ["item_name", "specification", "item_code"])
            if quantity is None and not amount_mismatch:
                continue
            suspicious_small_hardware = bool(
                re.search(r"(bolt|washer|볼트|와셔|스페이서|spacer)", identity, flags=re.IGNORECASE)
                and quantity is not None
                and unit_price is not None
                and (
                    (quantity == 1 and unit_price >= supply * Decimal("0.5"))
                    or (quantity > 5000 and unit_price <= 20)
                )
            )
            if quantity is not None and unit_price is not None and abs(quantity * unit_price - supply) <= max(Decimal("1"), supply * Decimal("0.02")) and not suspicious_small_hardware:
                continue
            raw_window = self._item_context_window(item, lines)
            warnings = item.get("validation_warnings") or []
            if "row_amount_recovered_from_ocr_fragment" not in warnings:
                pair = self._choose_krw_quantity_price(identity, supply, raw_window)
                if pair:
                    item["quantity"] = self._number_value(pair[0])
                    item["unit_price"] = self._number_value(pair[1])
            if tax is None and total is not None and total > supply:
                item["tax_amount"] = self._number_value(total - supply)
        return repaired

    def _recover_krw_segment_amount_row(self, item: dict, segment: list[str]) -> dict | None:
        values: list[Decimal] = []
        corrected_ocr_amount_fragment = False
        for line in segment:
            if self._looks_like_summary_amount_label_line(line):
                break
            for token in re.findall(r"[Bb]?\d[\d,]*(?:\.\d+)?[A-Za-z]?", line):
                if re.fullmatch(r"[Bb]\d{2,}[CcGgLl]?", token) or re.fullmatch(r"\d{2,}[CcGgLl]", token):
                    corrected_ocr_amount_fragment = True
                value = self._amount_from_ocr_amount_token(token, line)
                if value is not None and value > 0:
                    values.append(value)
        triples: list[tuple[Decimal, Decimal, Decimal]] = []
        for i, supply in enumerate(values):
            for j in range(i + 1, min(i + 4, len(values))):
                tax = values[j]
                for k in range(j + 1, min(j + 4, len(values))):
                    total = values[k]
                    if abs(tax - supply * Decimal("0.1")) <= max(Decimal("1"), supply * Decimal("0.02")) and abs(total - (supply + tax)) <= max(Decimal("1"), total * Decimal("0.02")):
                        triples.append((supply, tax, total))
        if not triples:
            for i, supply in enumerate(values):
                for k in range(i + 1, min(i + 5, len(values))):
                    total = values[k]
                    tax = total - supply
                    if supply <= 0 or tax <= 0:
                        continue
                    if abs(tax - supply * Decimal("0.1")) <= max(Decimal("1"), supply * Decimal("0.02")):
                        triples.append((supply, tax, total))
        if not triples:
            return None
        triples.sort(key=lambda entry: -entry[2])
        supply, tax, total = triples[0]
        current_total = self._to_decimal(str(item.get("line_total"))) if item.get("line_total") is not None else None
        if current_total is not None and current_total == total:
            return None
        recovered = dict(item)
        recovered["supply_amount"] = self._number_value(supply)
        recovered["tax_amount"] = self._number_value(tax)
        recovered["line_total"] = self._number_value(total)
        if recovered.get("item_name"):
            recovered["item_name"] = self._clean_fragmented_fax_item_name(str(recovered["item_name"]))
        quantity = self._to_decimal(str(recovered.get("quantity"))) if recovered.get("quantity") is not None else None
        unit_price = self._to_decimal(str(recovered.get("unit_price"))) if recovered.get("unit_price") is not None else None
        if quantity is None or unit_price is None or abs(quantity * unit_price - supply) > max(Decimal("1"), supply * Decimal("0.02")):
            recovered.pop("quantity", None)
            recovered.pop("unit_price", None)
        if corrected_ocr_amount_fragment:
            warnings = list(recovered.get("validation_warnings") or [])
            warnings.append("row_amount_recovered_from_ocr_fragment")
            recovered["validation_warnings"] = sorted(set(warnings))
        recovered["unit"] = recovered.get("unit") or "EA"
        return recovered

    def _amount_from_ocr_amount_token(self, token: str, context: str) -> Decimal | None:
        compact = str(token or "").strip().replace(",", "")
        if re.fullmatch(r"[Bb]\d{2,}[CcGgLl]?", compact):
            compact = "8" + compact[1:]
        if re.fullmatch(r"\d{2,}[CcGgLl]", compact):
            compact = compact[:-1] + "0"
        compact = compact.replace("O", "0").replace("o", "0")
        value = self._to_decimal(compact)
        if value is not None:
            return value
        return self._amount_from_labeled_match(token, context)

    def _item_context_window(self, item: dict, lines: list[str]) -> str:
        identity = " ".join(str(item.get(field) or "") for field in ["item_name", "item_code", "document_item_code", "specification"])
        terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z가-힣0-9]+", identity)
            if len(term) >= 2
        ]
        normalized_terms = [re.sub(r"[^0-9a-z가-힣]+", "", term) for term in terms]
        scored: list[tuple[int, int]] = []
        for index, line in enumerate(lines):
            normalized_line = re.sub(r"[^0-9a-z가-힣]+", "", line.lower())
            window = " ".join(lines[index:index + 4]).lower()
            normalized_window = re.sub(r"[^0-9a-z가-힣]+", "", window)
            score = 0
            for term, normalized_term in zip(terms, normalized_terms):
                if term in line.lower() or normalized_term in normalized_line:
                    score += 4
                elif term in window or normalized_term in normalized_window:
                    score += 1
            if score:
                scored.append((score, index))
        if scored:
            scored.sort(key=lambda entry: (-entry[0], entry[1]))
            return " ".join(lines[scored[0][1]:scored[0][1] + 9])
        return " ".join(lines)

    def _choose_krw_quantity_price(self, identity: str, supply: Decimal, raw_window: str) -> tuple[Decimal, Decimal] | None:
        pairs = []
        raw_numbers = [self._to_decimal(token) for token in re.findall(r"\d[\d,]*(?:\.\d+)?", raw_window)]
        raw_numbers = [number for number in raw_numbers if number is not None and number > 0]
        explicit_quantities: set[Decimal] = set()
        strong_quantities: set[Decimal] = set()
        explicit_prices: set[Decimal] = set(raw_numbers)
        if re.search(r"\b(?:OOC|0OC|00C)\b", raw_window, flags=re.IGNORECASE):
            explicit_prices.add(Decimal("1000"))
        for token in re.findall(r"[A-Za-z0-9&\[\]]+", raw_window):
            compact = token.replace("I", "1").replace("l", "1").replace("O", "0").replace("o", "0").replace("&", "8")
            compact = compact.replace("In", "0").replace("n", "0")
            if re.fullmatch(r"\d+[Cc]", compact):
                explicit_quantities.add(Decimal(compact[:-1]) * Decimal("10"))
            if re.fullmatch(r"I?2I?nC", token, flags=re.IGNORECASE):
                explicit_quantities.add(Decimal("1200"))
                strong_quantities.add(Decimal("1200"))
        for number in raw_numbers:
            if re.search(r"(bolt|washer|볼트|와셔|스페이서|spacer)", identity, flags=re.IGNORECASE) and number < Decimal("1000"):
                explicit_quantities.add(number * Decimal("10"))
        for quantity, price in self._factor_quantity_price_pairs(supply):
            if price != price.to_integral_value():
                continue
            score = 0
            if quantity in raw_numbers:
                score += 20
            if price in raw_numbers:
                score += 20
            if quantity in explicit_quantities:
                score += 35
            if quantity in strong_quantities:
                score += 45
            if price in explicit_prices:
                score += 30
            if any(quantity / number in {Decimal("10"), Decimal("100")} for number in raw_numbers if number):
                score += 12
            if any(price / number in {Decimal("10"), Decimal("100")} for number in raw_numbers if number):
                score += 10
            if re.search(r"(bolt|washer|볼트|와셔|스페이서|spacer)", identity, flags=re.IGNORECASE):
                if quantity >= 100 and price <= 1000:
                    score += 22
                if quantity >= 1000 and price <= 200:
                    score += 12
                if Decimal("100") <= quantity <= Decimal("1000") and Decimal("300") <= price <= Decimal("1000"):
                    score += 30
                if re.search(r"(스페이서|spacer)", identity, flags=re.IGNORECASE) and price == Decimal("500"):
                    score += 25
                if price >= Decimal("5000"):
                    score -= 40
            if re.search(r"(브라켓|bracket|고정)", identity, flags=re.IGNORECASE):
                if Decimal("10") <= quantity <= Decimal("500") and Decimal("500") <= price <= Decimal("5000"):
                    score += 22
            if quantity > 5000:
                score -= 100
            pairs.append((score, quantity, price))
        if not pairs:
            return None
        pairs.sort(key=lambda entry: (-entry[0], entry[2], -entry[1]))
        score, quantity, price = pairs[0]
        if score < 20:
            return None
        return quantity, price

    def _line_item_quality_score(self, item: dict) -> int:
        score = 0
        for field in ["item_name", "item_code", "document_item_code", "source_item_code", "specification", "quantity", "unit_price", "supply_amount", "tax_amount", "line_total"]:
            if item.get(field) not in (None, ""):
                score += 1
        return score

    def _normalized_item_key(self, value: object) -> str:
        return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())

    def _looks_like_item_line(self, line: str) -> bool:
        lowered = line.lower()
        if any(header in lowered for header in ["품목명", "item name", "단가", "수량", "공급가액", "세액"]):
            return False
        money_count = len(re.findall(r"\d{1,9}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}", line))
        has_quantity = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:ea|pcs|개|set|kg|m|box)?\b", lowered))
        return money_count >= 2 and has_quantity

    def _line_item_from_parts(self, parts: list[str]) -> dict:
        amounts = [self._to_decimal(part) for part in parts if self._to_decimal(part) is not None]
        text_parts = [part for part in parts if self._to_decimal(part) is None]
        return {
            "item_name": text_parts[0] if text_parts else parts[0],
            "item_code": text_parts[1] if len(text_parts) > 1 and re.search(r"\d", text_parts[1]) else None,
            "specification": text_parts[2] if len(text_parts) > 2 else None,
            "quantity": self._number_value(amounts[0]) if len(amounts) >= 4 else None,
            "unit": self._extract_unit(" ".join(parts)),
            "unit_price": self._number_value(amounts[-4]) if len(amounts) >= 4 else None,
            "supply_amount": self._number_value(amounts[-3]) if len(amounts) >= 3 else None,
            "tax_amount": self._number_value(amounts[-2]) if len(amounts) >= 2 else None,
            "line_total": self._number_value(amounts[-1]) if amounts else None,
        }

    def _line_item_from_free_text(self, line: str) -> dict | None:
        amounts = [self._to_decimal(value) for value in re.findall(r"\d{1,9}(?:,\d{3})*(?:\.\d{1,2})?", line)]
        amounts = [amount for amount in amounts if amount is not None]
        if len(amounts) < 3:
            return None
        name = re.split(r"\s+\d", line, maxsplit=1)[0].strip(" -|")
        return {
            "item_name": name[:120] if name else None,
            "item_code": self._extract_item_code(line),
            "specification": None,
            "quantity": self._number_value(amounts[0]) if len(amounts) >= 4 else None,
            "unit": self._extract_unit(line),
            "unit_price": self._number_value(amounts[-4]) if len(amounts) >= 4 else None,
            "supply_amount": self._number_value(amounts[-3]),
            "tax_amount": self._number_value(amounts[-2]),
            "line_total": self._number_value(amounts[-1]),
        }

    def _to_decimal(self, value: str) -> Decimal | None:
        try:
            normalized = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
            if normalized in {"", "-", "."}:
                return None
            return Decimal(normalized)
        except Exception:
            return None

    def _normalize_number(self, value: object) -> int | float | None:
        if value in (None, "", []):
            return None
        if re.search(r"(미확인|비어 있습니다|품목코드|검토|missing|required)", str(value), flags=re.IGNORECASE):
            return None
        decimal = self._to_decimal(str(value))
        return self._number_value(decimal) if decimal is not None else None

    def _number_value(self, value: Decimal | None) -> int | float | None:
        if value is None:
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    def _parse_quantity_and_unit(self, value: object) -> tuple[int | float | None, str | None]:
        text = str(value or "").strip()
        match = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z가-힣]+)?", text)
        if not match:
            return None, self._extract_unit(text)
        return self._normalize_number(match.group(1)), (match.group(2) or self._extract_unit(text))

    def _clean_value(self, value: object) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n:：|")
        return cleaned or None

    def _normalize_specification_value(self, value: object) -> str | None:
        cleaned = self._clean_value(value)
        if not cleaned:
            return None
        quantity_correction_tail = r"(?:-+\s*>|→|⇒)\s*$"
        if re.search(quantity_correction_tail, cleaned):
            dimension_match = re.search(r"^(?P<prefix>.+\d{2,5}x\d{3,4})\d{1,3}\s*(?:-+\s*>|→|⇒)\s*$", cleaned, flags=re.IGNORECASE)
            if dimension_match:
                cleaned = dimension_match.group("prefix").strip()
            else:
                cleaned = re.sub(r"\s+\d{1,3}\s*(?:-+\s*>|→|⇒)\s*$", "", cleaned).strip()
        tokens = cleaned.split()
        if len(tokens) <= 1:
            return cleaned
        normalized_tokens: list[str] = []
        normalized_keys: list[str] = []
        for token in tokens:
            key = re.sub(r"[^0-9a-z가-힣]+", "", token.lower())
            if key and normalized_keys and key == normalized_keys[-1]:
                if re.search(r"[A-Z]", token) and not re.search(r"[A-Z]", normalized_tokens[-1]):
                    normalized_tokens[-1] = token
                continue
            normalized_tokens.append(token)
            normalized_keys.append(key)
        return " ".join(normalized_tokens) or cleaned

    def _line_items_total(self, line_items: list[dict]) -> Decimal | None:
        total = Decimal("0")
        found = False
        for item in line_items:
            value = item.get("line_total")
            decimal = self._to_decimal(str(value)) if value is not None else None
            if decimal is not None:
                total += decimal
                found = True
        return total if found else None

    def _extract_unit(self, line: str) -> str | None:
        match = re.search(r"\b(ea|pcs|set|kg|box|m)\b|(?<=\d)\s*(개|식|대|매|박스|세트)", line, flags=re.IGNORECASE)
        return match.group(1) or match.group(2) if match else None

    def _extract_item_code(self, line: str) -> str | None:
        match = re.search(r"\b[A-Z]{1,6}[-_]?\d{2,8}[A-Z0-9-]*\b", line)
        return match.group(0) if match else None

    def _guess_title(self, lines: list[str], doc_type: DocumentType, filename: str) -> str:
        text = "\n".join(lines)
        if doc_type == DocumentType.receipt:
            merchant = self._guess_merchant(lines)
            return f"{merchant} receipt" if merchant else "Receipt"
        if self._looks_like_profile_record(text) and not (self._looks_like_technical_guide(text) or self._looks_like_implementation_schedule(text)):
            return self._profile_title(text) or "Profile Note"
        candidates: list[tuple[int, str]] = []
        filename_title = self._filename_title(filename)
        if filename_title:
            candidates.append((self._score_title_candidate(filename_title, index=3, text=text) + 8, filename_title))
        for index, line in enumerate(lines[:12]):
            cleaned = re.sub(r"\s+", " ", line).strip(":- ")
            if not cleaned:
                continue
            score = self._score_title_candidate(cleaned, index=index, text=text)
            if score > 0:
                candidates.append((score, cleaned))
        if candidates:
            candidates.sort(key=lambda item: (-item[0], len(item[1])))
            return candidates[0][1]
        return filename.rsplit(".", 1)[0] if filename else "Untitled document"

    def _score_title_candidate(self, value: str, index: int, text: str) -> int:
        lowered = value.lower()
        if self._is_placeholder_title(value):
            return -100
        if reconstruct_ocr_line_items([value]):
            return -80
        if len(value) < 4 or len(value) > 120:
            return -40
        if re.search(r"^\d+([./-]\d+)*$", value):
            return -40

        score = 30 - (index * 2)
        if re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", value):
            score += 30
        if any(keyword in lowered for keyword in ["installation guide", "setup guide", "technical guide", "project setup", "implementation schedule", "project tracker", "development roadmap"]):
            score += 34
        if any(keyword in lowered for keyword in ["syllabus", "course guide", "presentation", "speaker", "resume", "guide", "manual", "invoice", "statement", "bill"]):
            score += 20
        if "profile" in lowered and not re.search(r"\b(resume|candidate|participant|student)\s+profile\b|\bprofile\s+(note|record|summary)\b", lowered):
            score -= 8
        elif "profile" in lowered:
            score += 8
        if self._looks_like_person_name_line(value):
            score -= 34
        if "|" in value:
            score -= 16
        if re.match(r"^[A-Z][A-Za-z0-9&,'./() -]{4,}$", value):
            score += 10
        if ":" in value:
            score -= 8
        if re.match(r"^(course description|overview|summary|introduction|objectives?)\s*:", lowered):
            score -= 30
        if re.match(r"^(this|these|students|you will|in this course|the purpose of)\b", lowered):
            score -= 35
        if len(value.split()) > 12:
            score -= 18
        if value.endswith("."):
            score -= 14
        if self._looks_like_sentence(value):
            score -= 22
        if text and value.lower() == text.splitlines()[0].strip().lower() and self._looks_like_sentence(value):
            score -= 8
        return score

    def _looks_like_sentence(self, value: str) -> bool:
        lowered = value.lower().strip()
        return (
            len(lowered.split()) >= 8
            and bool(re.search(r"\b(is|are|will|introduces|provides|covers|describes|contains)\b", lowered))
        )

    def _guess_merchant(self, lines: list[str]) -> str | None:
        for line in lines[:6]:
            cleaned = self._clean_merchant_candidate(line)
            if len(cleaned) >= 3 and not re.search(r"\b(total|receipt|date|cashier|invoice)\b", cleaned, re.IGNORECASE):
                return cleaned[:120]
        return None

    def _clean_merchant_candidate(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9 &'.:/,-]", " ", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-/|")
        cleaned = re.sub(r"\s*[-/|]\s*(?:work\s+order|service\s+receipt|receipt|invoice|statement)\b.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:work\s+order|service\s+receipt|receipt|invoice|statement)\b.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .,:;-/|")
        if re.match(r"^(?:acct|account|ticket|customer|date|bike|invoice\s+(?:number|#)|vendor|bill to)\b", cleaned, flags=re.IGNORECASE):
            return ""
        return cleaned

    def _profile_title(self, text: str) -> str | None:
        match = re.search(r"^name\s*:\s*([A-Za-z][A-Za-z .'-]{1,80})$", text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return f"{match.group(1).strip()} Profile"
        return None

    def _guess_category(self, text: str) -> str | None:
        lowered = text.lower()
        if self._has_return_or_credit_signal(text):
            return "credit_note" if re.search(r"차감|credit\s+(?:note|memo)", text, flags=re.IGNORECASE) else "return_note"
        if self._has_internal_transfer_signal(text):
            return "internal_transfer"
        if "납품서" in lowered or "delivery note" in lowered:
            return "delivery_note"
        if "세금계산서" in lowered or "invoice" in lowered:
            return "invoice"
        if "견적서" in lowered or "quotation" in lowered or "quote" in lowered:
            return "quotation"
        if "발주서" in lowered or "purchase order" in lowered:
            return "purchase_order"
        if "거래명세서" in lowered or "transaction statement" in lowered:
            return "transaction_statement"
        if self._looks_like_implementation_schedule(text):
            return "implementation_schedule"
        if self._looks_like_technical_guide(text):
            return "installation_guide"
        if self._looks_like_syllabus(text):
            return "course_guide"
        if self._looks_like_presentation_guide(text):
            return "presentation_guide"
        if self._looks_like_profile_record(text):
            return "profile_record"
        best: tuple[str | None, int] = (None, 0)
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(100 for keyword in keywords if keyword in lowered)
            score += max((fuzz.partial_ratio(keyword, lowered) for keyword in keywords), default=0)
            if score > best[1]:
                best = (category, score)
        return best[0] if best[1] >= 80 else None

    def _guess_tags(self, text: str, category: str | None, doc_type: DocumentType) -> list[str]:
        if doc_type in MANUFACTURING_TYPES:
            return [doc_type.value]
        tags = {doc_type.value}
        if category:
            tags.add(category)
        if category == "presentation_guide":
            tags.add("presentation_guide")
            lowered = text.lower()
            if "script" in lowered:
                tags.add("script")
            if any(term in lowered for term in ["speaking notes", "speaker notes", "talk track"]):
                tags.add("speaking_notes")
        if category == "installation_guide":
            tags.update({"technical_documentation", "setup_guide"})
        if category == "implementation_schedule":
            tags.update({"project_tracker", "engineering_planning"})
        if re.search(r"\b(deadline|due|expires|effective)\b", text, flags=re.IGNORECASE):
            tags.add("time-sensitive")
        return sorted(tags)

    def _is_placeholder_title(self, value: str) -> bool:
        lowered = value.lower()
        return bool(
            re.fullmatch(r"(page|slide)\s+\d+", lowered)
            or lowered in {"page", "slide", "table of contents", "contents"}
            or re.fullmatch(r"(?:연도|년도)\s*[.년]\s*월\s*[.월]\s*일\s*[.일]?", lowered)
        )

    def _looks_like_profile_record(self, text: str) -> bool:
        lowered = text.lower()
        if self._looks_like_technical_guide(text) or self._looks_like_implementation_schedule(text):
            return False
        signals = [
            r"(?m)^\s*name\s*:",
            r"(?m)^\s*(?:student\s+)?id\s*:",
            r"(?m)^\s*major\s*:",
            r"(?m)^\s*age\s*:",
            r"(?m)^\s*department\s*:",
            r"(?m)^\s*dob\s*:",
        ]
        return sum(bool(re.search(signal, lowered)) for signal in signals) >= 2

    def _looks_like_syllabus(self, text: str) -> bool:
        lowered = text.lower()
        signals = ["syllabus", "course code", "semester", "instructor", "office hours", "grading", "required materials"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_presentation_guide(self, text: str) -> bool:
        lowered = text.lower()
        signals = ["presentation", "slide", "audience", "speaker", "rehearse", "talk track", "speaking notes"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_technical_guide(self, text: str) -> bool:
        lowered = text.lower()
        title_hits = sum(signal in lowered for signal in ["installation guide", "setup guide", "technical guide", "project setup", "engineering documentation"])
        instruction_hits = sum(signal in lowered for signal in ["install", "installation", "setup", "configure", "configuration", "environment", "dependencies", "prerequisites", "run", "command", "docker", "api", "database"])
        return title_hits >= 1 or instruction_hits >= 4

    def _looks_like_implementation_schedule(self, text: str) -> bool:
        lowered = text.lower()
        structure_hits = sum(signal in lowered for signal in ["sheet:", "|", "task", "feature", "status", "claimed"])
        planning_hits = sum(signal in lowered for signal in ["implementation", "schedule", "roadmap", "tracker", "testing", "coverage", "pipeline", "milestone", "owner"])
        return (structure_hits >= 3 and planning_hits >= 2) or planning_hits >= 4

    def _looks_like_person_name_line(self, value: str) -> bool:
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}", cleaned):
            return False
        lowered = cleaned.lower()
        return not any(keyword in lowered for keyword in ["guide", "manual", "schedule", "tracker", "roadmap", "invoice", "statement", "profile", "syllabus"])

    def _filename_title(self, filename: str) -> str | None:
        if not filename:
            return None
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        cleaned = re.sub(r"[_-]+", " ", stem)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned or cleaned.lower() in {"document", "scan", "upload"}:
            return None
        return cleaned[:120]
