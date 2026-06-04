import base64
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from app.core.config import get_settings
from app.models.document import DocumentType
from app.services.parser import DocumentParser, ParsedDocument


logger = logging.getLogger(__name__)


class HTMLTableTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            cell = self._normalize_text(" ".join(self._current_cell))
            self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(value)).strip()


CATEGORY_MAP = {
    "purchase_order": ["발주서", "purchase order", "po no", "발주 번호", "납기일"],
    "quotation": ["견적서", "quotation", "quote", "견적 번호"],
    "transaction_statement": ["거래명세서", "transaction statement", "공급가액", "세액"],
    "delivery_note": ["납품서", "delivery note", "납품 번호", "납품일"],
    "invoice": ["세금계산서", "invoice", "청구서"],
    "packing_list": ["포장명세서", "packing list"],
    "inspection_report": ["검사성적서", "inspection report"],
    "contract": ["계약서", "contract"],
    "installation_guide": ["installation guide", "setup guide", "technical guide", "install", "setup", "configuration", "dependencies", "prerequisites"],
    "implementation_schedule": ["implementation", "schedule", "task", "feature", "status", "testing", "coverage", "pipeline", "claimed", "roadmap"],
    "food_drink": ["coffee", "cafe", "restaurant", "bakery", "pizza", "burger", "tea", "deli"],
    "groceries": ["grocery", "market", "supermarket", "foods", "produce"],
    "transport": ["gas", "fuel", "uber", "lyft", "taxi", "parking", "metro", "transit"],
    "education": ["school", "student", "tuition", "class", "campus", "pta", "teacher"],
    "utilities": ["electric", "water", "internet", "utility", "phone", "bill"],
    "health": ["pharmacy", "clinic", "doctor", "medical", "dental"],
    "retail": ["store", "shop", "target", "walmart", "costco", "purchase"],
    "office": ["office", "printing", "supplies", "stationery"],
}


@dataclass
class AIDocumentUnderstandingResult:
    document_type: DocumentType = DocumentType.general_document
    title: str | None = None
    merchant_name: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    document_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    line_items: list[dict] = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)
    extracted_date: date | None = None
    extracted_amount: Decimal | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    currency: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    cleaned_raw_text: str | None = None
    confidence_score: Decimal | None = None
    extraction_notes: list[str] = field(default_factory=list)
    review_required: bool = False
    provider: str = "heuristic_fallback"
    extraction_provider: str | None = None
    refinement_provider: str | None = None
    provider_chain: list[str] = field(default_factory=list)
    merge_strategy: str = "single_provider"
    field_sources: dict[str, str] = field(default_factory=dict)


class DocumentAIService:
    provider_name = "base"

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        raise NotImplementedError


class LocalDocumentAIService(DocumentAIService):
    provider_name = "heuristic_fallback"

    def __init__(self) -> None:
        self.parser = DocumentParser()

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        lines = self._clean_lines(raw_text)
        text = "\n".join(lines)
        quality_notes, image_quality = self._image_quality(image_path)
        document_type, type_confidence = self._classify(text, filename, parsed.document_type)
        subtotal = self._amount_near_label(lines, ["subtotal", "sub total"])
        tax = self._amount_near_label(lines, ["tax", "sales tax", "hst", "gst", "vat"])
        total = self._amount_near_label(lines, ["grand total", "total", "amount due", "balance"])
        if total is None and document_type == DocumentType.receipt:
            total = parsed.extracted_amount or self._largest_amount(text)

        parsed_category = self._normalize_category(parsed.category)
        inferred_category = self._infer_category(text, document_type)
        category = self._preferred_category(parsed_category, inferred_category, document_type)
        title = self._title(lines, document_type, parsed, filename)
        confidence = self._confidence(type_confidence, image_quality, raw_text, total, document_type)
        notes = quality_notes + self._field_notes(
            document_type,
            total,
            parsed.extracted_date or self.parser._extract_date(text),
            lines,
        )
        review_required = confidence < Decimal("0.62") or bool(notes)
        tags = self._tags(text, category, document_type, parsed.tags)

        field_sources = {
            "document_type": self.provider_name,
            "title": self.provider_name,
            "merchant_name": self.provider_name,
            "vendor_name": self.provider_name,
            "customer_name": self.provider_name,
            "document_number": self.provider_name,
            "issue_date": self.provider_name,
            "due_date": self.provider_name,
            "line_items": self.provider_name,
            "extracted_date": "ocr_parser",
            "extracted_amount": self.provider_name,
            "subtotal": self.provider_name,
            "tax": self.provider_name,
            "category": self.provider_name,
            "summary": self.provider_name,
        }
        return AIDocumentUnderstandingResult(
            document_type=document_type,
            title=title,
            merchant_name=self._merchant(lines, parsed) if document_type == DocumentType.receipt else parsed.merchant_name,
            vendor_name=parsed.vendor_name or parsed.merchant_name,
            customer_name=parsed.customer_name,
            document_number=parsed.document_number,
            issue_date=parsed.issue_date or parsed.extracted_date,
            due_date=parsed.due_date,
            line_items=parsed.line_items,
            low_confidence_fields=self._low_confidence_fields(parsed.line_items, document_type),
            extracted_date=parsed.extracted_date or self.parser._extract_date(text),
            extracted_amount=total,
            subtotal=subtotal,
            tax=tax,
            currency="USD" if any(value is not None for value in [total, subtotal, tax]) else parsed.currency,
            category=category,
            tags=tags,
            summary=self._summary(lines, document_type),
            cleaned_raw_text=text or raw_text,
            confidence_score=confidence,
            extraction_notes=notes,
            review_required=review_required,
            provider=self.provider_name,
            extraction_provider=self.provider_name,
            provider_chain=[self.provider_name],
            field_sources=field_sources,
        )

    def _low_confidence_fields(self, line_items: list[dict], document_type: DocumentType) -> list[str]:
        fields: list[str] = []
        manufacturing_types = {
            DocumentType.purchase_order,
            DocumentType.quotation,
            DocumentType.transaction_statement,
            DocumentType.delivery_note,
            DocumentType.invoice,
            DocumentType.packing_list,
        }
        if document_type not in manufacturing_types:
            return fields
        if not line_items:
            fields.append("missing_line_items")
            return fields
        for index, item in enumerate(line_items, start=1):
            if item.get("item_name") in (None, "", []):
                fields.append(f"missing_item_name:item_{index}")
            if item.get("quantity") in (None, "", []):
                fields.append(f"missing_quantity:item_{index}")
            if item.get("unit_price") in (None, "", []) and item.get("line_total") in (None, "", []):
                fields.append(f"missing_price_or_total:item_{index}")
        return fields[:30]

    def _clean_lines(self, raw_text: str) -> list[str]:
        lines = []
        for line in raw_text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        return lines

    def _image_quality(self, image_path: Path) -> tuple[list[str], Decimal]:
        if image_path.suffix.lower().lstrip(".") not in {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"}:
            return [], Decimal("0.86")
        notes: list[str] = []
        try:
            with Image.open(image_path) as image:
                grayscale = image.convert("L")
                width, height = image.size
                brightness = ImageStat.Stat(grayscale).mean[0]
                edge_strength = ImageStat.Stat(grayscale.filter(ImageFilter.FIND_EDGES)).mean[0]
        except Exception:
            return ["Image quality could not be evaluated."], Decimal("0.55")

        score = Decimal("0.86")
        if width < 700 or height < 700:
            notes.append("Image resolution is low; field extraction may be incomplete.")
            score -= Decimal("0.15")
        if brightness < 50:
            notes.append("Image is dark; review OCR and extracted fields.")
            score -= Decimal("0.12")
        if edge_strength < 3:
            notes.append("Image appears soft or low contrast; manual review is recommended.")
            score -= Decimal("0.14")
        return notes, max(Decimal("0.25"), min(Decimal("0.95"), score))

    def _classify(self, text: str, filename: str, fallback: DocumentType) -> tuple[DocumentType, Decimal]:
        content = text.lower()
        first_lines = "\n".join(line.strip().lower() for line in text.splitlines()[:6])
        priority_indicators = [
            (DocumentType.delivery_note, ["납품서", "납품번호", "납품일", "납품수량", "입고장소", "수령자", "delivery note"]),
            (DocumentType.quotation, ["견적서", "견적번호", "견적일", "유효기간", "견적유효기간", "납기조건", "결제조건", "quotation", "quote"]),
            (DocumentType.invoice, ["세금계산서", "계산서번호", "인보이스번호", "청구서번호", "지급기한", "결제기한", "사업자등록번호", "invoice"]),
            (DocumentType.purchase_order, ["발주서", "발주번호", "발주처", "납기일", "purchase order", "po no"]),
            (DocumentType.transaction_statement, ["거래명세서", "거래명세서번호", "거래일자", "transaction statement"]),
        ]
        for document_type, keywords in priority_indicators:
            strong_hits = sum(1 for keyword in keywords if keyword.lower() in first_lines)
            content_hits = sum(1 for keyword in keywords if keyword.lower() in content)
            threshold = 1 if document_type != DocumentType.transaction_statement else 2
            if strong_hits >= 1 or content_hits >= threshold:
                return document_type, Decimal(str(min(0.94, 0.68 + content_hits * 0.04)))
        haystack = f"{filename}\n{text}".lower()
        invoice_score = self._score(haystack, ["invoice", "invoice number", "invoice #", "vendor", "bill to", "amount due", "total due"])
        if invoice_score >= 4:
            return DocumentType.invoice, Decimal("0.84")
        guide_score = self._score(haystack, ["installation guide", "setup guide", "technical guide", "project setup", "install", "configuration", "dependencies", "prerequisites"])
        tracker_score = self._score(haystack, ["implementation schedule", "project tracker", "roadmap", "task", "feature", "status", "testing", "coverage", "pipeline", "claimed"])
        if guide_score >= 4 or tracker_score >= 6:
            return DocumentType.document, Decimal("0.84")
        scores = {
            DocumentType.receipt: self._score(haystack, ["receipt", "subtotal", "tax", "total", "change", "visa", "mastercard", "cashier"]),
            DocumentType.notice: self._score(haystack, ["notice", "announcement", "effective date", "deadline", "meeting", "reminder"]),
            DocumentType.memo: self._score(haystack, ["memo", "note", "minutes", "action item", "todo"]),
            DocumentType.presentation: self._score(haystack, ["presentation", "slide", "speaker notes", "speaking notes", "talk track", "rehearse", "script"]),
            DocumentType.document: self._score(haystack, ["invoice", "statement", "agreement", "policy", "form", "reference"]),
            DocumentType.other: 1,
        }
        winner, score = max(scores.items(), key=lambda item: item[1])
        if score < 3 and fallback != DocumentType.other:
            return fallback, Decimal("0.58")
        confidence = Decimal(str(min(0.93, 0.45 + (score * 0.08))))
        return winner, confidence

    def _score(self, haystack: str, keywords: list[str]) -> int:
        return sum(2 if keyword in haystack else 0 for keyword in keywords)

    def _amount_near_label(self, lines: list[str], labels: list[str]) -> Decimal | None:
        for line in lines:
            if any(self._has_label(line, label) for label in labels):
                amount = self._last_amount(line)
                if amount is not None:
                    return amount
        return None

    def _has_label(self, line: str, label: str) -> bool:
        escaped = re.escape(label).replace("\\ ", r"[\s_]+")
        return re.search(rf"(?<![A-Za-z]){escaped}(?![A-Za-z])", line, flags=re.IGNORECASE) is not None

    def _last_amount(self, text: str) -> Decimal | None:
        matches = re.findall(r"(?:USD|US\$|\$)?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{2}))", text, flags=re.IGNORECASE)
        return self._to_decimal(matches[-1]) if matches else None

    def _largest_amount(self, text: str) -> Decimal | None:
        amounts = [
            value
            for value in (self._to_decimal(match) for match in re.findall(r"([0-9]{1,6}(?:,[0-9]{3})*\.[0-9]{2})", text))
            if value is not None
        ]
        return max(amounts) if amounts else None

    def _to_decimal(self, value: str | None) -> Decimal | None:
        if not value:
            return None
        try:
            return Decimal(value.replace(",", "")).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    def _infer_category(self, text: str, document_type: DocumentType) -> str | None:
        lowered = text.lower()
        best_category = None
        best_score = 0
        for category, keywords in CATEGORY_MAP.items():
            score = sum(2 if keyword in lowered else 0 for keyword in keywords)
            if score > best_score:
                best_category = category
                best_score = score
        if best_category:
            return best_category
        if document_type == DocumentType.receipt:
            return "retail"
        if document_type == DocumentType.notice:
            return "notice"
        return "other"

    def _normalize_category(self, category: str | None) -> str | None:
        if not category:
            return None
        return {
            "dining": "food_drink",
            "transportation": "transport",
            "groceries": "groceries",
            "utilities": "utilities",
            "office": "office",
            "health": "health",
        }.get(category, category)

    def _preferred_category(
        self,
        parsed_category: str | None,
        inferred_category: str | None,
        document_type: DocumentType,
    ) -> str | None:
        specific_structured = {
            "invoice",
            "profile_record",
            "course_guide",
            "presentation_guide",
            "installation_guide",
            "implementation_schedule",
            "repair_service",
            "utilities",
        }
        if parsed_category in specific_structured:
            return parsed_category
        if document_type == DocumentType.receipt and parsed_category and parsed_category not in {"other", "notice"}:
            return parsed_category
        return inferred_category or parsed_category

    def _title(self, lines: list[str], document_type: DocumentType, parsed: ParsedDocument, filename: str) -> str:
        if document_type == DocumentType.receipt:
            merchant = self._merchant(lines, parsed)
            return f"{merchant} receipt" if merchant else "Receipt"
        if parsed.title:
            return parsed.title
        for line in lines:
            if not re.fullmatch(r"(Page|Slide)\s+\d+", line, flags=re.IGNORECASE):
                return line[:120]
        return filename.rsplit(".", 1)[0]

    def _merchant(self, lines: list[str], parsed: ParsedDocument) -> str | None:
        parsed_merchant = self._clean_merchant_candidate(parsed.merchant_name or "")
        if parsed_merchant:
            return parsed_merchant
        for line in lines[:7]:
            cleaned = self._clean_merchant_candidate(line)
            if len(cleaned) >= 3 and not re.search(r"\b(receipt|invoice|date|cashier|total|subtotal|tax)\b", cleaned, re.IGNORECASE):
                return cleaned[:120]
        return None

    def _clean_merchant_candidate(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9 &'.,:/|-]", " ", value or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-/|")
        cleaned = re.sub(r"\s*[-/|]\s*(?:work\s+order|service\s+receipt|receipt|invoice|statement)\b.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:work\s+order|service\s+receipt|receipt|invoice|statement)\b.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .,:;-/|")
        if re.match(r"^(?:acct|account|ticket|customer|date|bike|invoice\s+(?:number|#)|vendor|bill to)\b", cleaned, flags=re.IGNORECASE):
            return ""
        return cleaned

    def _summary(self, lines: list[str], document_type: DocumentType) -> str | None:
        if not lines:
            return None
        if document_type == DocumentType.receipt:
            return "영수증의 가맹점, 날짜, 금액, 세액 정보를 추출했습니다."
        if document_type in {
            DocumentType.purchase_order,
            DocumentType.quotation,
            DocumentType.transaction_statement,
            DocumentType.delivery_note,
            DocumentType.invoice,
            DocumentType.packing_list,
        }:
            return "제조업 문서의 문서 유형, 거래처, 문서번호, 날짜, 품목 및 금액 정보를 구조화했습니다."
        unique_lines = []
        seen = set()
        for line in lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            key = normalized.casefold()
            if not normalized or key in seen or re.fullmatch(r"(Page|Slide)\s+\d+", normalized, flags=re.IGNORECASE):
                continue
            seen.add(key)
            unique_lines.append(normalized)
        fact_lines = [line for line in unique_lines if ":" in line and len(line) <= 120]
        body = " ; ".join((fact_lines or unique_lines)[:4])
        return body[:500]

    def _field_notes(self, document_type: DocumentType, total: Decimal | None, extracted_date: date | None, lines: list[str]) -> list[str]:
        notes: list[str] = []
        if document_type == DocumentType.receipt and total is None:
            notes.append("No clear receipt total was found.")
        if extracted_date is None:
            notes.append("No reliable date was found.")
        if len(lines) < 3:
            notes.append("Very little text was detected.")
        return notes

    def _confidence(
        self,
        type_confidence: Decimal,
        image_quality: Decimal,
        raw_text: str,
        total: Decimal | None,
        document_type: DocumentType,
    ) -> Decimal:
        score = (type_confidence * Decimal("0.45")) + (image_quality * Decimal("0.35"))
        score += Decimal("0.12") if len(raw_text.strip()) > 80 else Decimal("0.03")
        if document_type != DocumentType.receipt or total is not None:
            score += Decimal("0.08")
        return max(Decimal("0.20"), min(Decimal("0.97"), score)).quantize(Decimal("0.001"))

    def _tags(self, text: str, category: str | None, document_type: DocumentType, fallback_tags: list[str]) -> list[str]:
        category_names = set(CATEGORY_MAP) | {"notice", "other"}
        tags = {tag for tag in fallback_tags if tag not in category_names}
        tags.add(document_type.value)
        if category:
            tags.add(category)
        if re.search(r"\b(due|deadline|expires|effective|urgent)\b", text, re.IGNORECASE):
            tags.add("review")
        return sorted(tags)


class OpenAIVisionDocumentAIService(DocumentAIService):
    provider_name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI package is not installed.") from exc
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.local_fallback = LocalDocumentAIService()

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        try:
            payload = self._call_model(image_path, raw_text, filename)
            result = self._normalize(payload, parsed, raw_text)
            result.provider = self.provider_name
            result.extraction_provider = self.provider_name
            result.provider_chain = [self.provider_name]
            return result
        except Exception as exc:
            fallback = self.local_fallback.analyze(image_path, raw_text, parsed, filename)
            fallback.extraction_notes.append(f"Vision AI provider failed; used local extraction fallback: {exc}")
            fallback.review_required = True
            return fallback

    def _call_model(self, image_path: Path, raw_text: str, filename: str) -> dict[str, Any]:
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        prompt = (
            "Analyze this document image directly. Return only JSON with: document_type, title, "
            "merchant_name, vendor_name, customer_name, document_number, issue_date, due_date, "
            "line_items, low_confidence_fields, extracted_date as YYYY-MM-DD or null, extracted_amount, subtotal, tax, "
            "currency, category, tags, summary, cleaned_raw_text, confidence_score between 0 and 1, "
            "review_required, and extraction_notes. Supported document_type values are receipt, "
            "purchase_order, quotation, transaction_statement, delivery_note, invoice, packing_list, "
            "inspection_report, contract, general_document, notice, document, memo, presentation, other. "
            "For Korean manufacturing documents, line_items must include item_name, item_code, specification, "
            "quantity, unit, unit_price, supply_amount, tax_amount, line_total. "
            "Write all user-facing summary and extraction_notes in Korean only. OCR text is supplied only "
            f"as auxiliary context. Filename: {filename}. OCR text:\n{raw_text[:6000]}"
        )
        response = self.client.chat.completions.create(
            model=self.settings.ai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def _normalize(self, payload: dict[str, Any], parsed: ParsedDocument, raw_text: str) -> AIDocumentUnderstandingResult:
        doc_type_value = payload.get("document_type") or parsed.document_type.value
        try:
            doc_type = DocumentType(doc_type_value)
        except ValueError:
            doc_type = parsed.document_type

        return AIDocumentUnderstandingResult(
            document_type=doc_type,
            title=payload.get("title") or parsed.title,
            merchant_name=payload.get("merchant_name") or parsed.merchant_name,
            vendor_name=payload.get("vendor_name") or parsed.vendor_name,
            customer_name=payload.get("customer_name") or parsed.customer_name,
            document_number=payload.get("document_number") or parsed.document_number,
            issue_date=self._parse_date(payload.get("issue_date")) or parsed.issue_date,
            due_date=self._parse_date(payload.get("due_date")) or parsed.due_date,
            line_items=payload.get("line_items") or parsed.line_items,
            low_confidence_fields=[str(field) for field in payload.get("low_confidence_fields", [])],
            extracted_date=self._parse_date(payload.get("extracted_date")) or parsed.extracted_date,
            extracted_amount=self._decimal(payload.get("extracted_amount")) or parsed.extracted_amount,
            subtotal=self._decimal(payload.get("subtotal")),
            tax=self._decimal(payload.get("tax")),
            currency=payload.get("currency") or parsed.currency,
            category=payload.get("category") or parsed.category,
            tags=[str(tag) for tag in payload.get("tags", [])] or parsed.tags,
            summary=payload.get("summary"),
            cleaned_raw_text=payload.get("cleaned_raw_text") or raw_text,
            confidence_score=self._decimal(payload.get("confidence_score")),
            extraction_notes=[str(note) for note in payload.get("extraction_notes", [])],
            review_required=bool(payload.get("review_required", False)),
            provider=self.provider_name,
            extraction_provider=self.provider_name,
            provider_chain=[self.provider_name],
        )

    def _decimal(self, value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.001"))
        except (InvalidOperation, ValueError):
            return None

    def _parse_date(self, value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


class PaddleOCRVLDocumentAIService(DocumentAIService):
    provider_name = "paddleocr_vl"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_normalizer = LocalDocumentAIService()

        logger.warning("PaddleOCR-VL provider initialization started")
        start_time = time.perf_counter()

        try:
            from paddleocr import PaddleOCRVL
        except Exception as exc:
            raise RuntimeError(
                "PaddleOCR-VL runtime is not installed. Install backend/requirements-ai.txt "
                "and configure PaddleOCR-VL model directories."
            ) from exc

        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        engine = self._clean_str(self.settings.paddleocr_vl_engine)
        model_dir = self._clean_path(self.settings.paddleocr_vl_model_dir)
        layout_model_dir = self._clean_path(self.settings.paddleocr_vl_layout_model_dir)

        kwargs: dict[str, Any] = {
            "pipeline_version": "v1.5",
            "device": self.settings.paddleocr_vl_device,
        }

        if engine:
            kwargs["engine"] = engine
        if model_dir:
            kwargs["vl_rec_model_dir"] = str(model_dir)
        if layout_model_dir:
            kwargs["layout_detection_model_dir"] = str(layout_model_dir)

        self.pipeline = PaddleOCRVL(**kwargs)

        elapsed = time.perf_counter() - start_time
        logger.warning("PaddleOCR-VL provider initialization finished in %.2fs", elapsed)

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        preprocess_start = time.perf_counter()
        analysis_image_path = self._prepare_image_for_inference(image_path)
        preprocess_elapsed = time.perf_counter() - preprocess_start

        inference_start = time.perf_counter()
        try:
            output = self.pipeline.predict(str(analysis_image_path))
        except Exception as exc:
            raise RuntimeError(f"PaddleOCR-VL inference failed: {exc}") from exc
        inference_elapsed = time.perf_counter() - inference_start

        provider_text = self._extract_text(output)
        merged_text = "\n".join(part for part in [provider_text, raw_text] if part.strip())

        logger.warning("PaddleOCR-VL raw output type: %s", type(output))

        first_item = None
        try:
            first_item = output[0] if output else None
        except Exception:
            first_item = None

        logger.warning(
            "PaddleOCR-VL first item type: %s",
            type(first_item) if first_item is not None else "no output",
        )
        logger.warning(
            "PaddleOCR-VL first item attrs preview: %s",
            dir(first_item)[:50] if first_item is not None else "no output",
        )
        first_json = self._coerce_payload(getattr(first_item, "json", None)) if first_item is not None else None
        parsing_preview = self._parsing_res_preview(first_json)
        logger.warning("PaddleOCR-VL parsing_res_list preview: %s", parsing_preview)
        logger.warning("PaddleOCR-VL extracted provider_text preview:\n%s", self._preview_text(provider_text))
        logger.warning("PaddleOCR-VL merged_text preview:\n%s", self._preview_text(merged_text))

        postprocess_start = time.perf_counter()
        provider_parsed = DocumentParser().parse(merged_text or raw_text, filename)
        result = self.local_normalizer.analyze(image_path, merged_text or raw_text, provider_parsed, filename)
        result.provider = self.provider_name
        result.extraction_provider = self.provider_name
        result.provider_chain = [self.provider_name]
        result.merge_strategy = "paddleocr_vl_structured_output_normalized"
        result.field_sources.update({key: self.provider_name for key in result.field_sources})
        result.extraction_notes.append("PaddleOCR-VL primary extraction completed.")
        if provider_text:
            result.cleaned_raw_text = provider_text
        postprocess_elapsed = time.perf_counter() - postprocess_start

        logger.warning(
            "PaddleOCR-VL timings | preprocess=%.2fs inference=%.2fs postprocess=%.2fs file=%s",
            preprocess_elapsed,
            inference_elapsed,
            postprocess_elapsed,
            image_path.name,
        )
        return result

    def _prepare_image_for_inference(self, image_path: Path) -> Path:
        max_dimension = 1800
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                longest = max(width, height)
                if longest <= max_dimension:
                    return image_path

                scale = max_dimension / float(longest)
                resized = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
                resized_path = image_path.with_name(f"{image_path.stem}_resized{image_path.suffix}")
                resized.save(resized_path, quality=90)
                return resized_path
        except Exception:
            return image_path

    def _extract_text(self, output: Any) -> str:
        parsing_lines: list[str] = []
        markdown_lines: list[str] = []
        parsing_blocks_seen = 0

        for item in output or []:
            json_payload = self._coerce_payload(getattr(item, "json", None))
            parsing_blocks = self._parsing_res_list(json_payload)
            if parsing_blocks:
                parsing_blocks_seen += len(parsing_blocks)
                parsing_lines.extend(self._lines_from_parsing_blocks(parsing_blocks))
            else:
                parsing_lines.extend(self._lines_from_generic_json(json_payload))

            markdown_payload = self._coerce_payload(getattr(item, "markdown", None))
            markdown_lines.extend(self._lines_from_markdown(markdown_payload))

        source = "json.res.parsing_res_list"
        lines = self._dedupe_lines(parsing_lines)
        if len(lines) < 3:
            source = "markdown_texts_fallback" if markdown_lines else "empty"
            lines = self._dedupe_lines(markdown_lines)

        logger.warning(
            "PaddleOCR-VL text normalization source=%s parsing_blocks=%s lines=%s markdown_fallback_lines=%s",
            source,
            parsing_blocks_seen,
            len(lines),
            len(markdown_lines),
        )
        return "\n".join(lines)

    def _coerce_payload(self, payload: Any) -> Any:
        if callable(payload):
            try:
                return payload()
            except TypeError:
                return payload
        return payload

    def _parsing_res_list(self, json_payload: Any) -> list[dict[str, Any]]:
        if not isinstance(json_payload, dict):
            return []
        res = json_payload.get("res")
        if not isinstance(res, dict):
            return []
        blocks = res.get("parsing_res_list")
        if not isinstance(blocks, list):
            return []
        return [block for block in blocks if isinstance(block, dict)]

    def _lines_from_parsing_blocks(self, blocks: list[dict[str, Any]]) -> list[str]:
        ordered_blocks = sorted(blocks, key=self._block_order)
        lines: list[str] = []
        for block in ordered_blocks:
            label = str(block.get("block_label") or "").lower()
            content = block.get("block_content")
            if not isinstance(content, str):
                continue
            lines.extend(self._lines_from_block_content(label, content))
        return lines

    def _block_order(self, block: dict[str, Any]) -> tuple[int, int]:
        raw_order = block.get("block_order")
        try:
            return int(raw_order), 0
        except (TypeError, ValueError):
            return 10_000, 0

    def _lines_from_block_content(self, label: str, content: str) -> list[str]:
        if self._is_table_block(label, content):
            table_lines = self._table_to_lines(content)
            if table_lines:
                return table_lines
        return self._text_to_lines(self._strip_html(content))

    def _is_table_block(self, label: str, content: str) -> bool:
        lowered = content.lower()
        return "table" in label or "<table" in lowered or "<tr" in lowered or "<td" in lowered

    def _table_to_lines(self, content: str) -> list[str]:
        if "<" not in content:
            return self._text_to_lines(content)

        parser = HTMLTableTextExtractor()
        try:
            parser.feed(content)
        except Exception:
            return self._text_to_lines(self._strip_html(content))

        lines: list[str] = []
        for row in parser.rows:
            cells = [self._normalize_line(cell) for cell in row if self._normalize_line(cell)]
            if not cells:
                continue
            line = self._join_table_cells(cells)
            if line:
                lines.append(line)

        return lines or self._text_to_lines(self._strip_html(content))

    def _join_table_cells(self, cells: list[str]) -> str:
        if len(cells) == 1:
            return cells[0]
        if len(cells) == 2:
            return f"{cells[0]} {cells[1]}"
        if self._looks_like_receipt_amount(cells[-1]):
            return f"{' '.join(cells[:-1])} {cells[-1]}"
        return " ".join(cells)

    def _looks_like_receipt_amount(self, value: str) -> bool:
        return re.search(r"(?:[$€£¥₩]\s*)?\d{1,6}(?:,\d{3})*(?:\.\d{2})?$", value.strip()) is not None

    def _lines_from_generic_json(self, value: Any) -> list[str]:
        fragments: list[str] = []
        self._walk_generic_json(value, fragments)
        lines: list[str] = []
        for fragment in fragments:
            lines.extend(self._lines_from_block_content("", fragment))
        return lines

    def _walk_generic_json(self, value: Any, fragments: list[str]) -> None:
        if isinstance(value, dict):
            for key in ["block_content", "rec_text", "text", "content"]:
                content = value.get(key)
                if isinstance(content, str):
                    fragments.append(content)
            for nested in value.values():
                self._walk_generic_json(nested, fragments)
        elif isinstance(value, list):
            for nested in value:
                self._walk_generic_json(nested, fragments)

    def _lines_from_markdown(self, markdown_payload: Any) -> list[str]:
        if not isinstance(markdown_payload, dict):
            return []
        texts = markdown_payload.get("markdown_texts")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list):
            return []
        lines: list[str] = []
        for text in texts:
            if isinstance(text, str):
                lines.extend(self._lines_from_block_content("markdown", text))
        return lines

    def _strip_html(self, value: str) -> str:
        value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", value)
        value = re.sub(r"<[^>]+>", " ", value)
        return html.unescape(value)

    def _text_to_lines(self, value: str) -> list[str]:
        return [
            normalized
            for line in value.splitlines()
            if (normalized := self._normalize_line(line))
        ]

    def _normalize_line(self, value: str) -> str:
        value = html.unescape(value)
        value = value.replace("\u00a0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\s+([:;,.])", r"\1", value)
        return value.strip(" |")

    def _dedupe_lines(self, lines: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = self._normalize_line(line)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    def _parsing_res_preview(self, json_payload: Any) -> list[dict[str, Any]]:
        preview = []
        for block in self._parsing_res_list(json_payload)[:8]:
            content = block.get("block_content")
            preview.append(
                {
                    "block_order": block.get("block_order"),
                    "block_label": block.get("block_label"),
                    "block_content_preview": self._preview_text(content if isinstance(content, str) else ""),
                }
            )
        return preview

    def _preview_text(self, value: str, limit: int = 1200) -> str:
        if not value:
            return ""
        cleaned = re.sub(r"(?is)<table.*?</table>", "[HTML_TABLE]", value)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
        return cleaned[:limit]

    def _clean_str(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def _clean_path(self, value: Path | str | None) -> Path | None:
        if value is None:
            return None
        if isinstance(value, Path):
            return value
        cleaned = str(value).strip()
        return Path(cleaned) if cleaned else None


class Qwen25VLDocumentAIService(DocumentAIService):
    provider_name = "qwen2_5_vl"

    def __init__(self) -> None:
        self.settings = get_settings()
        try:
            import torch
            from qwen_vl_utils import process_vision_info
            from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        except Exception as exc:
            raise RuntimeError(
                "Qwen2.5-VL runtime is not installed. Install backend/requirements-ai.txt "
                "and download Qwen2.5-VL weights."
            ) from exc

        self.torch = torch
        self.process_vision_info = process_vision_info
        model_ref = str(self.settings.qwen2_5_vl_model_dir or self.settings.qwen2_5_vl_model_name)
        device_map = "auto" if self.settings.qwen2_5_vl_device == "auto" else {"": self.settings.qwen2_5_vl_device}
        self.processor = AutoProcessor.from_pretrained(model_ref, min_pixels=256 * 28 * 28, max_pixels=1024 * 28 * 28)
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_ref,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map=device_map,
        )

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        prompt = self._prompt(raw_text, parsed, filename)
        messages = [{"role": "user", "content": [{"type": "image", "image": str(image_path)}, {"type": "text", "text": prompt}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = self.process_vision_info(messages)
        inputs = self.processor(text=[text], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt").to(self.model.device)
        generated_ids = self.model.generate(**inputs, max_new_tokens=512)
        generated_ids_trimmed = [
            out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids, strict=False)
        ]
        output_text = self.processor.batch_decode(generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        payload = self._extract_json(output_text)
        result = OpenAIVisionDocumentAIService._normalize(self, payload, parsed, raw_text)
        result.provider = self.provider_name
        result.extraction_provider = self.provider_name
        result.provider_chain = [self.provider_name]
        result.extraction_notes.append("Qwen2.5-VL refinement completed.")
        return result

    def _prompt(self, raw_text: str, parsed: ParsedDocument, filename: str) -> str:
        return (
            "Refine this document extraction. Return only JSON with keys: document_type, title, "
            "merchant_name, vendor_name, customer_name, document_number, issue_date, due_date, line_items, "
            "low_confidence_fields, extracted_date, extracted_amount, subtotal, tax, currency, category, "
            "tags, summary, cleaned_raw_text, confidence_score, review_required, extraction_notes. "
            "Use document_type purchase_order, quotation, transaction_statement, delivery_note, invoice, "
            "packing_list, inspection_report, contract, general_document, receipt, notice, document, memo, "
            "presentation, or other. For line_items include item_name, item_code, specification, quantity, "
            "unit, unit_price, supply_amount, tax_amount, line_total. Write all user-facing summary and extraction_notes in Korean only. "
            "Fill missing fields only when visible or strongly supported. "
            f"Filename: {filename}. Prior parser type: {parsed.document_type.value}. OCR text:\n{raw_text[:6000]}"
        )

    def _extract_json(self, output_text: str) -> dict[str, Any]:
        try:
            return json.loads(output_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", output_text, flags=re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def _decimal(self, value: Any) -> Decimal | None:
        return OpenAIVisionDocumentAIService._decimal(self, value)

    def _parse_date(self, value: Any) -> date | None:
        return OpenAIVisionDocumentAIService._parse_date(self, value)


class HybridOpenSourceDocumentAIService(DocumentAIService):
    provider_name = "hybrid_open_source"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_fallback = get_local_document_ai_service()

    def analyze(
        self,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str = "",
    ) -> AIDocumentUnderstandingResult:
        notes: list[str] = []
        primary = self._run_provider(self.settings.ai_primary_provider, image_path, raw_text, parsed, filename, notes)
        if primary is None:
            primary = self.local_fallback.analyze(image_path, raw_text, parsed, filename)
            primary.extraction_notes.extend(notes)
            primary.provider_chain = [self.settings.ai_primary_provider + "_unavailable", "heuristic_fallback"]
            primary.merge_strategy = "primary_unavailable_heuristic_fallback"

        final = primary
        should_refine, reasons = self._should_refine(primary)
        if self.settings.ai_enable_second_pass and should_refine:
            secondary = self._run_provider(self.settings.ai_secondary_provider, image_path, raw_text, parsed, filename, notes)
            if secondary:
                final = self._merge(primary, secondary, reasons)
            else:
                final.extraction_notes.extend(notes)
                final.provider_chain = self._dedupe(final.provider_chain + [self.settings.ai_secondary_provider + "_unavailable"])
        else:
            final.extraction_notes.extend(reasons)

        if not final.provider_chain:
            final.provider_chain = [final.provider or final.extraction_provider or "unknown"]
        final.extraction_provider = final.extraction_provider or final.provider_chain[0]
        final.review_required = final.review_required or self._requires_review(final)
        return final

    def _run_provider(
        self,
        provider_name: str,
        image_path: Path,
        raw_text: str,
        parsed: ParsedDocument,
        filename: str,
        notes: list[str],
    ) -> AIDocumentUnderstandingResult | None:
        try:
            provider = self._provider(provider_name)
            return provider.analyze(image_path, raw_text, parsed, filename)
        except Exception as exc:
            notes.append(f"{provider_name} unavailable or failed: {exc}")
            return None

    def _provider(self, provider_name: str) -> DocumentAIService:
        normalized = provider_name.lower()
        if normalized == "paddleocr_vl":
            return get_paddleocr_vl_document_ai_service()
        if normalized == "qwen2_5_vl":
            return get_qwen25_vl_document_ai_service()
        if normalized in {"heuristic", "heuristic_fallback", "local"}:
            return self.local_fallback
        if normalized == "openai":
            return get_openai_vision_document_ai_service()
        raise ValueError(f"Unsupported AI provider: {provider_name}")

    def _should_refine(self, result: AIDocumentUnderstandingResult) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        threshold = Decimal(str(self.settings.ai_second_pass_confidence_threshold))
        confidence = result.confidence_score or Decimal("0")
        if confidence < threshold:
            reasons.append(f"Primary confidence {confidence} is below second-pass threshold {threshold}.")
        if result.document_type == DocumentType.receipt:
            if result.extracted_amount is None:
                reasons.append("Receipt total is missing.")
            if result.extracted_date is None:
                reasons.append("Receipt date is missing.")
            if not result.merchant_name:
                reasons.append("Receipt merchant is missing.")
        elif result.document_type in {DocumentType.notice, DocumentType.document, DocumentType.memo, DocumentType.presentation}:
            if not result.title:
                reasons.append("Document title is missing.")
            if not result.summary:
                reasons.append("Document summary is missing.")
        elif result.document_type in {
            DocumentType.purchase_order,
            DocumentType.quotation,
            DocumentType.transaction_statement,
            DocumentType.delivery_note,
            DocumentType.invoice,
            DocumentType.packing_list,
        }:
            if not result.line_items:
                reasons.append("Manufacturing line items are missing.")
            if any(item.get(field) in (None, "", []) for item in result.line_items for field in ["quantity", "unit_price", "line_total"]):
                reasons.append("Manufacturing line item quantity, unit price, or line total is uncertain.")
        if not result.category or result.category == "other":
            reasons.append("Category is missing or weak.")
        if result.review_required:
            reasons.append("Primary extraction requested manual review.")
        if any("unavailable" in provider for provider in result.provider_chain):
            reasons.append("Configured primary provider was unavailable; second-pass fallback is warranted.")
        return bool(reasons), reasons

    def _requires_review(self, result: AIDocumentUnderstandingResult) -> bool:
        should_refine, _ = self._should_refine(result)
        return should_refine

    def _merge(
        self,
        primary: AIDocumentUnderstandingResult,
        secondary: AIDocumentUnderstandingResult,
        reasons: list[str],
    ) -> AIDocumentUnderstandingResult:
        merged = primary
        merged.provider_chain = self._dedupe(primary.provider_chain + secondary.provider_chain)
        merged.refinement_provider = secondary.extraction_provider or secondary.provider
        merged.merge_strategy = "primary_authoritative_secondary_fill_missing"
        merged.extraction_notes.extend(f"Second pass triggered: {reason}" for reason in reasons)
        merged.extraction_notes.extend(secondary.extraction_notes)

        for field_name in [
            "title",
            "merchant_name",
            "vendor_name",
            "customer_name",
            "document_number",
            "issue_date",
            "due_date",
            "line_items",
            "low_confidence_fields",
            "extracted_date",
            "extracted_amount",
            "subtotal",
            "tax",
            "currency",
            "category",
            "summary",
            "cleaned_raw_text",
        ]:
            primary_value = getattr(merged, field_name)
            secondary_value = getattr(secondary, field_name)
            if self._is_empty(primary_value) and not self._is_empty(secondary_value):
                setattr(merged, field_name, secondary_value)
                merged.field_sources[field_name] = secondary.extraction_provider or secondary.provider

        if (secondary.confidence_score or Decimal("0")) > (merged.confidence_score or Decimal("0")):
            merged.confidence_score = secondary.confidence_score
        if len(secondary.tags) > len(merged.tags):
            merged.tags = sorted(set(merged.tags) | set(secondary.tags))
        merged.review_required = self._requires_review(merged)
        return merged

    def _is_empty(self, value: Any) -> bool:
        return value is None or value == "" or value == []

    def _dedupe(self, providers: list[str]) -> list[str]:
        return list(dict.fromkeys(provider for provider in providers if provider))


@lru_cache(maxsize=1)
def get_local_document_ai_service() -> LocalDocumentAIService:
    logger.warning("Creating shared LocalDocumentAIService instance")
    return LocalDocumentAIService()


@lru_cache(maxsize=1)
def get_openai_vision_document_ai_service() -> OpenAIVisionDocumentAIService:
    logger.warning("Creating shared OpenAIVisionDocumentAIService instance")
    return OpenAIVisionDocumentAIService()


@lru_cache(maxsize=1)
def get_paddleocr_vl_document_ai_service() -> PaddleOCRVLDocumentAIService:
    logger.warning("Creating shared PaddleOCRVLDocumentAIService instance")
    return PaddleOCRVLDocumentAIService()


@lru_cache(maxsize=1)
def get_qwen25_vl_document_ai_service() -> Qwen25VLDocumentAIService:
    logger.warning("Creating shared Qwen25VLDocumentAIService instance")
    return Qwen25VLDocumentAIService()


@lru_cache(maxsize=1)
def get_document_ai_service() -> DocumentAIService:
    logger.warning("Creating shared HybridOpenSourceDocumentAIService instance")
    return HybridOpenSourceDocumentAIService()
