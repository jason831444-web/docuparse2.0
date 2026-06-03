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


DATE_PATTERNS = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
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
    "item_name": ["품목명", "품명", "제품명", "상품명", "자재명", "item name", "item"],
    "item_code": ["품목코드", "품번", "제품코드", "상품코드", "자재코드", "part no", "part number", "item code"],
    "specification": ["규격", "사양", "모델", "모델명", "size", "spec", "specification"],
    "quantity": ["수량", "주문수량", "납품수량", "qty", "quantity"],
    "unit": ["단위", "unit"],
    "unit_price": ["단가", "개당가격", "unit price"],
    "supply_amount": ["공급가액", "공급액", "supply amount"],
    "tax_amount": ["세액", "부가세", "vat", "tax"],
    "line_total": ["합계금액", "총액", "금액", "합계", "line total", "amount"],
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
        amount = self._extract_amount(joined) or self._line_items_total(line_items)
        category = self._guess_category(joined)
        issue_date = self._extract_labeled_date(joined, ["발행일", "작성일", "발주일", "견적일", "납품일", "거래일자", "일자"])
        due_date = self._extract_labeled_date(joined, ["납기일", "납품예정일", "납품 예정일", "due date", "delivery date"])
        vendor_name = self._extract_labeled_text(joined, ["공급업체", "공급자", "거래처", "매입처", "vendor", "supplier"])
        customer_name = self._extract_labeled_text(joined, ["고객사", "수요처", "발주처", "납품처", "구매자", "customer", "buyer"])
        return ParsedDocument(
            document_type=doc_type,
            title=self._guess_title(lines, doc_type, filename),
            extracted_date=issue_date or self._extract_date(joined),
            extracted_amount=amount,
            currency="KRW" if amount is not None else None,
            merchant_name=vendor_name or (self._guess_merchant(lines) if doc_type == DocumentType.receipt else None),
            vendor_name=vendor_name,
            customer_name=customer_name,
            document_number=self._extract_document_number(joined),
            issue_date=issue_date,
            due_date=due_date,
            line_items=line_items,
            category=category,
            tags=self._guess_tags(joined, category, doc_type),
        )

    def _guess_document_type(self, text: str, filename: str) -> DocumentType:
        haystack = f"{filename}\n{text}".lower()
        if self._score_korean_manufacturing(haystack, ["발주서", "발주 번호", "purchase order", "po no", "po번호"]) >= 1:
            return DocumentType.purchase_order
        if self._score_korean_manufacturing(haystack, ["견적서", "견적 번호", "quotation", "quote"]) >= 1:
            return DocumentType.quotation
        if self._score_korean_manufacturing(haystack, ["거래명세서", "거래 명세서", "transaction statement"]) >= 1:
            return DocumentType.transaction_statement
        if self._score_korean_manufacturing(haystack, ["납품서", "delivery note", "납품 번호"]) >= 1:
            return DocumentType.delivery_note
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

    def _extract_labeled_text(self, text: str, labels: list[str]) -> str | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        pattern = rf"(?:{label_pattern})\s*[:：]?\s*([^\n|]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group(1)).strip(" -:：")[:120] or None

    def _extract_labeled_date(self, text: str, labels: list[str]) -> date | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?:{label_pattern})\s*[:：]?\s*(\d{{4}}[.\-/년]\s*\d{{1,2}}[.\-/월]\s*\d{{1,2}}[일]?)",
            text,
            flags=re.IGNORECASE,
        )
        return self._extract_date(match.group(1)) if match else None

    def _extract_document_number(self, text: str) -> str | None:
        labels = ["발주번호", "발주 번호", "견적번호", "견적 번호", "거래명세서번호", "납품번호", "문서번호", "po no", "quote no", "invoice no"]
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_pattern})\s*[:：#]?\s*([A-Za-z0-9가-힣._/-]+)", text, flags=re.IGNORECASE)
        return match.group(1).strip()[:80] if match else None

    def _extract_line_items(self, lines: list[str]) -> list[dict]:
        items = self._extract_key_value_line_items(lines)
        items.extend(self._extract_table_line_items(lines))
        for line in lines:
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

    def _extract_key_value_line_items(self, lines: list[str]) -> list[dict]:
        current: dict = {}
        items: list[dict] = []
        seen_item_field = False
        for line in lines:
            parsed = self._parse_labeled_line(line)
            if not parsed:
                continue
            field, value = parsed
            if field not in LINE_ITEM_LABELS:
                continue
            if field == "item_name" and seen_item_field and self._line_item_has_identity(current):
                items.append(self._normalize_line_item(current))
                current = {}
            seen_item_field = True
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
            return [part.strip() for part in stripped.split("|") if part.strip()]
        if "\t" in stripped:
            return [part.strip() for part in stripped.split("\t") if part.strip()]
        if "," in stripped and len(stripped.split(",")) >= 4:
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return [part.strip() for part in re.split(r"\s{2,}", stripped) if part.strip()]

    def _parse_labeled_line(self, line: str) -> tuple[str, str] | None:
        match = re.match(r"\s*([^:：|]+?)\s*[:：]\s*(.+?)\s*$", line)
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
        normalized = {
            "item_name": self._clean_value(item.get("item_name")),
            "item_code": self._clean_value(item.get("item_code")),
            "specification": self._clean_value(item.get("specification")),
            "quantity": item.get("quantity"),
            "unit": self._clean_value(item.get("unit")),
            "unit_price": item.get("unit_price"),
            "supply_amount": item.get("supply_amount"),
            "tax_amount": item.get("tax_amount"),
            "line_total": item.get("line_total"),
        }
        if isinstance(normalized["quantity"], str):
            quantity, unit = self._parse_quantity_and_unit(normalized["quantity"])
            normalized["quantity"] = quantity
            normalized["unit"] = normalized["unit"] or unit
        for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]:
            if isinstance(normalized[field], str):
                normalized[field] = self._normalize_number(normalized[field])
        if normalized["quantity"] is None and normalized["unit"]:
            normalized["quantity"] = self._normalize_number(str(item.get("quantity") or ""))
        return {key: value for key, value in normalized.items() if value not in (None, "")}

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
        if "발주서" in lowered or "purchase order" in lowered:
            return "purchase_order"
        if "견적서" in lowered or "quotation" in lowered or "quote" in lowered:
            return "quotation"
        if "거래명세서" in lowered or "transaction statement" in lowered:
            return "transaction_statement"
        if "납품서" in lowered or "delivery note" in lowered:
            return "delivery_note"
        if "세금계산서" in lowered or "invoice" in lowered:
            return "invoice"
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
