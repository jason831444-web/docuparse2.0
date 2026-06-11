import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

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
]

CATEGORY_KEYWORDS = {
    "purchase_order": ["발주서", "발주 번호", "po no", "purchase order", "납기일", "발주일"],
    "quotation": ["견적서", "견적 번호", "quotation", "quote", "유효기간", "견적금액"],
    "transaction_statement": ["거래명세서", "거래 명세서", "transaction statement", "공급가액", "세액"],
    "delivery_note": ["납품서", "납품 번호", "delivery note", "납품일", "인수자"],
    "packing_list": ["포장명세서", "packing list", "포장 수량", "box", "carton"],
    "inspection_report": ["검사성적서", "inspection report", "검사 결과", "합격", "불합격"],
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
    "item_name": ["품목명", "품명", "제품명", "상품명", "자재명", "item name", "item description", "description", "product name", "item"],
    "item_code": ["품목코드", "품번", "제품코드", "상품코드", "자재코드", "거래처코드", "거래처품목코드", "vendor sku", "customer item code", "sku", "part no", "part number", "item code"],
    "specification": ["규격", "사양", "모델", "모델명", "size", "spec", "specification", "dimension"],
    "quantity": ["수량", "주문수량", "납품수량", "delivery qty", "delivery quantity", "delivered qty", "qty", "quantity"],
    "unit": ["단위", "unit"],
    "unit_price": ["단가", "단 가", "개당가격", "unit price"],
    "supply_amount": ["공급가액", "공급액", "공급 금액", "supply amount", "subtotal", "amount"],
    "tax_amount": ["세액", "세 액", "부가세", "vat", "tax", "w세액"],
    "line_total": ["합계금액", "총액", "금액", "합계", "total", "line total"],
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
        line_items = self._extract_line_items(lines)
        document_scope_text = self._document_scope_text(lines)
        subtotal = self._extract_labeled_amount(document_scope_text, ["공급가액 합계", "공급가액합계", "공급가액", "공급액", "공급 금액", "subtotal total", "subtotal", "supply amount", "supply total"])
        tax = self._extract_labeled_amount(document_scope_text, ["세액 합계", "세액", "세 액", "부가세", "vat total", "vat", "tax", "w세액"])
        amount = self._extract_labeled_amount(document_scope_text, ["총 합계", "합계금액", "총액", "공급대가", "청구금액", "invoice total", "grand total", "total due", "total amount", "amount due", "total"]) or self._line_items_total(line_items)
        line_items = self._repair_line_items_against_document_totals(line_items, amount, subtotal, tax, lines)
        currency = self._extract_currency(document_scope_text) or self._extract_currency(joined) or ("KRW" if amount is not None else None)
        category = self._guess_category(joined)
        business_fields = self._extract_business_fields(joined, doc_type)
        issue_date = self._extract_issue_date(joined, doc_type)
        due_date = self._extract_due_date(joined, doc_type)
        vendor_name = self._extract_labeled_text(joined, ["공급업체", "공급자", "판매자", "매입처", "발행처", "청구처", "vendor", "supplier", "seller"])
        customer_name = self._extract_labeled_text(joined, ["공급받는자", "고객사", "구매처", "발주처", "수신처", "납품처", "수요처", "구매자", "customer", "buyer", "bill to"])
        if customer_name and vendor_name and customer_name == vendor_name:
            customer_name = self._extract_labeled_text(joined, ["공급받는자", "고객사", "구매처", "발주처", "수신처", "납품처", "customer", "buyer", "bill to"])
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
        first_lines = "\n".join(line.strip().lower() for line in text.splitlines()[:6])
        for document_type, keywords in MANUFACTURING_TYPE_INDICATORS:
            strong_hits = sum(1 for keyword in keywords if keyword.lower() in first_lines)
            content_hits = sum(1 for keyword in keywords if keyword.lower() in content)
            threshold = 1 if document_type != DocumentType.transaction_statement else 2
            if strong_hits >= 1 or content_hits >= threshold:
                return document_type
        if re.search(r"\bINV[-_ ]?\d{4}|\b(?:invoice|tax)\s*(?:no|number)", content, flags=re.IGNORECASE):
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
        candidates = re.findall(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|[A-Z][a-z]+ \d{1,2}, \d{4})\b", text)
        candidates.extend(re.findall(r"\b\d{4}[.년]\s*\d{1,2}[.월]\s*\d{1,2}[.일]?\b", text))
        for candidate in candidates:
            normalized = candidate
            if re.search(r"[년월일.]", normalized):
                parts = re.findall(r"\d{1,4}", normalized)
                if len(parts) >= 3:
                    normalized = f"{parts[0]}-{parts[1]}-{parts[2]}"
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
                rf"(?:^|\s)(?:{label_pattern})\s*[:：]?\s*(?:KRW|USD|₩|원|\$)?\s*([-+]?\d[\d,]*(?:\.\d+)?[A-Za-z]?)\s*(?:원|KRW|USD)?",
                line,
                flags=re.IGNORECASE,
            )
            if match:
                value = self._amount_from_labeled_match(match.group(1), line)
                if value is not None:
                    values.append(value)
                    continue
            line_key = re.sub(r"[\s:：]+", "", line.lower())
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
        key = re.sub(r"[\s:：]+", "", line.lower())
        return key in {
            "공급가액", "공급가액합계", "공급액", "공급금액", "subtotal", "supplyamount", "supplytotal",
            "세액", "부가세", "vat", "tax",
            "총액", "총합계", "합계", "합계금액", "invoicetotal", "grandtotal", "totaldue", "totalamount", "amountdue", "total",
        }

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
        if re.search(r"\bUSD\b|\$", text, flags=re.IGNORECASE):
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
        if self._looks_like_instruction_or_note(value):
            return None
        return value[:120] or None

    def _truncate_at_business_label_boundary(self, value: str) -> str:
        boundary_labels = [
            "공급업체", "공급자", "판매자", "매입처", "발행처", "청구처",
            "공급받는자", "고객사", "구매처", "발주처", "수신처", "납품처", "수요처", "구매자",
            "supplier", "vendor", "seller", "customer", "buyer", "bill to", "ship to",
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
        return self._extract_date(match.group(1)) if match else None

    def _extract_document_number(self, text: str) -> str | None:
        labels = [
            "발주번호", "발주 번호", "견적번호", "견적 번호", "거래명세서번호", "납품번호", "계산서번호", "인보이스번호", "청구서번호", "문서번호",
            "po no", "po number", "purchase order no", "qt no", "quote no", "quotation no", "statement no", "delivery note no", "dn no", "invoice no", "inv no",
        ]
        normalized_labels = {re.sub(r"[\s:：#]+", "", label.lower()) for label in labels}
        lines = [line.strip() for line in text.splitlines()]
        for index, line in enumerate(lines[:-1]):
            if re.sub(r"[\s:：#]+", "", line.lower()) not in normalized_labels:
                continue
            value = self._normalize_document_number_candidate(lines, index + 1)
            if value:
                return value
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_pattern})\s*[:：#]?\s*([A-Za-z0-9가-힣._/-]+)", text, flags=re.IGNORECASE)
        return self._normalize_document_number(match.group(1)) if match else None

    def _normalize_document_number(self, value: object) -> str | None:
        text = re.sub(r"\s+", "", str(value or "")).strip(" -:：[](){}")
        if not text:
            return None
        text = text.replace("_", "-")
        if re.match(r"^S-\d{4}-\d{4}-", text, flags=re.IGNORECASE):
            text = f"T{text}"
        if re.match(r"^OT-\d{4}-", text, flags=re.IGNORECASE):
            text = f"QT{text[2:]}"
        return text[:80] or None

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

    def _looks_like_document_number_continuation(self, value: str) -> bool:
        if re.fullmatch(r"\d{4}-\d{2,4}(?:-\d{2,5})?", value):
            return True
        if re.fullmatch(r"[A-Z]{1,4}-?\d{2,5}", value, flags=re.IGNORECASE):
            return True
        return False

    def _extract_business_fields(self, text: str, doc_type: DocumentType) -> dict:
        fields: dict[str, object] = {}
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
            DocumentType.invoice: ["발행일", "발행일자", "작성일", "계산서일자", "invoice date", "issue date", "date"],
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

    def _extract_line_items(self, lines: list[str]) -> list[dict]:
        item_block_lines = self._explicit_item_block_lines(lines)
        items = self._extract_key_value_line_items(item_block_lines or lines)
        items.extend(self._extract_table_line_items(lines))
        items.extend(self._normalize_line_item(candidate.item) for candidate in reconstruct_ocr_line_items(lines))
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
        return self._dedupe_line_items(items)[:80]

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
        item_code = self._clean_code_value(item.get("item_code"))
        normalized = {
            "item_name": self._clean_value(item.get("item_name")),
            "item_code": item_code,
            "document_item_code": item_code,
            "source_item_code": item_code,
            "specification": self._clean_value(item.get("specification")),
            "quantity": item.get("quantity"),
            "unit": self._clean_value(item.get("unit")),
            "unit_price": item.get("unit_price"),
            "supply_amount": item.get("supply_amount"),
            "tax_amount": item.get("tax_amount"),
            "line_total": item.get("line_total"),
        }
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
        normalized = self._repair_line_item_arithmetic(normalized)
        normalized = self._suppress_implausible_line_item_numbers(normalized)
        if normalized.get("quantity") is None and normalized.get("unit") and not normalized.get("_quantity_suppressed"):
            normalized["quantity"] = self._normalize_number(str(item.get("quantity") or ""))
        warnings = self._line_item_amount_warnings(normalized)
        if warnings:
            normalized["validation_warnings"] = warnings
        return {key: value for key, value in normalized.items() if value not in (None, "") and not str(key).startswith("_")}

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

    def _repair_line_item_arithmetic(self, item: dict) -> dict:
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
        if tax is not None and total is not None and tax > total:
            warnings.append("invalid_tax_greater_than_total")
        if supply is not None and tax is not None and supply > 0 and tax > supply:
            warnings.append("invalid_tax_greater_than_supply")
        if supply is not None and total is not None and supply > total:
            warnings.append("invalid_supply_greater_than_total")
        if supply is not None and tax is not None and total is not None and abs((supply + tax) - total) > max(Decimal("1"), abs(total) * Decimal("0.02")):
            warnings.append("invalid_line_total")
        return warnings

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
        if len(line_items) == 1 and self._line_item_amount_warnings(line_items[0]):
            return line_items
        if any("|" in line for line in lines) and any(self._line_item_amount_warnings(item) for item in line_items):
            return line_items
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
                    self._repair_quantity_price_from_ocr_context(item, supply_value, lines)
                    remaining_supply -= supply_value
                    remaining_tax -= tax_value
                    remaining_total -= total_value
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
                self._repair_quantity_price_from_ocr_context(item, supply_value, lines)
        return [self._normalize_line_item(item) for item in repaired]

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
        for item in items:
            key = (
                item.get("item_name"),
                item.get("item_code"),
                item.get("specification"),
                item.get("quantity"),
                item.get("line_total"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

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
