import re
from dataclasses import dataclass, field
from typing import Any

from app.models.document import Document, DocumentType


@dataclass
class CategoryInterpretation:
    category: str | None = None
    profile: str = "generic_document"
    subtype: str | None = None
    title_hint: str | None = None
    summary_hint: str | None = None
    key_fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    workflow_hints: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.6
    provider: str = "heuristic_interpretation"
    provider_chain: list[str] = field(default_factory=lambda: ["heuristic_interpretation"])
    refinement_status: str = "heuristic_only"
    diagnostics: list[str] = field(default_factory=list)
    ai_assisted: bool = False


class CategoryInterpretationService:
    """Cross-format interpretation layer.

    File type decides how a document is read. This service decides how the
    extracted content should be interpreted across PDF, DOCX, TXT, JSON, and images.
    """

    def interpret(self, document: Document, text: str) -> CategoryInterpretation:
        lowered = text.lower()
        title_hint = self._meaningful_title(document.title, text)
        explicit_category = self._specific_existing_category(document.category)
        manufacturing_types = {
            DocumentType.purchase_order,
            DocumentType.quotation,
            DocumentType.transaction_statement,
            DocumentType.delivery_note,
            DocumentType.invoice,
            DocumentType.packing_list,
            DocumentType.inspection_report,
            DocumentType.contract,
            DocumentType.general_document,
        }

        if document.document_type in manufacturing_types and self._looks_like_manufacturing_document(lowered, document):
            doc_type = document.document_type.value
            line_items = getattr(document, "line_items", None) or []
            missing_items = not line_items
            return CategoryInterpretation(
                category=explicit_category or document.category or doc_type,
                profile=doc_type,
                subtype=doc_type,
                title_hint=title_hint or self._manufacturing_title(document, text),
                summary_hint=self._manufacturing_summary(document, doc_type),
                key_fields={
                    "vendor_name": getattr(document, "vendor_name", None) or document.merchant_name,
                    "customer_name": getattr(document, "customer_name", None),
                    "document_number": getattr(document, "document_number", None),
                    "line_item_count": len(line_items),
                },
                warnings=["Line items were not confidently extracted."] if missing_items else [],
                workflow_hints={
                    "review_focus": [
                        "Verify document type, vendor, customer, document number, dates, quantities, unit prices, tax, and line totals."
                    ],
                    "important_points": [
                        f"{len(line_items)} line item rows extracted.",
                    ],
                },
                reasons=["Detected Korean manufacturing purchase, quotation, delivery, transaction, or invoice document signals."],
                confidence=0.86 if line_items else 0.68,
            )

        if document.document_type == DocumentType.receipt:
            if self._looks_like_repair_service_receipt(lowered):
                return CategoryInterpretation(
                    category="repair_service",
                    profile="repair_service_receipt",
                    subtype="repair_service_receipt",
                    title_hint=title_hint or self._receipt_title(document),
                    summary_hint=self._receipt_summary(document, "repair-service", lowered),
                    key_fields={
                        "item_style": "parts_and_labor",
                        "service_keywords": self._keyword_hits(lowered, ["repair", "service", "labor", "parts", "maintenance"]),
                    },
                    reasons=["Receipt text includes service/labor/parts signals."],
                    confidence=0.89,
                )
            if self._looks_like_invoice(lowered):
                return CategoryInterpretation(
                    category="invoice",
                    profile="invoice",
                    subtype="invoice",
                    title_hint=title_hint or self._invoice_title(text),
                    summary_hint="Invoice document with vendor, billing details, amount due, and payment timing information.",
                    key_fields={
                        "invoice_number": self._invoice_number(text),
                        "billing_keywords": self._keyword_hits(
                            lowered,
                            ["invoice", "vendor", "bill to", "invoice date", "due date", "amount due", "total due"],
                        ),
                    },
                    reasons=["Receipt-style parsing found invoice-specific billing fields such as invoice number, vendor, or amount due."],
                    confidence=0.85,
                )
            if self._looks_like_utility_bill(lowered):
                return CategoryInterpretation(
                    category="utilities",
                    profile="utility_bill",
                    subtype="utility_bill",
                    title_hint=title_hint or self._receipt_title(document),
                    summary_hint=self._receipt_summary(document, "utility-bill", lowered),
                    reasons=["Document text includes provider, due-date, or bill signals."],
                    confidence=0.86,
                )
            return CategoryInterpretation(
                category=document.category or "retail",
                profile="receipt",
                subtype="receipt",
                title_hint=title_hint or self._receipt_title(document),
                summary_hint=self._receipt_summary(document, "receipt", lowered),
                confidence=0.78,
            )

        if self._looks_like_resume_profile(lowered):
            return CategoryInterpretation(
                category="resume_profile",
                profile="resume_profile",
                subtype="resume_profile",
                title_hint=title_hint or "Resume Profile",
                summary_hint="Resume-style document highlighting education, experience, projects, and skills.",
                key_fields={
                    "contact_links": self._contact_links(text),
                    "section_hints": self._section_hints(
                        lowered,
                        ["education", "experience", "projects", "skills", "technical skills", "gpa"],
                    ),
                },
                reasons=["Content is organized like a resume or portfolio profile."],
                confidence=0.9,
            )

        if self._looks_like_utility_bill(lowered):
            return CategoryInterpretation(
                category="utility_bill",
                profile="utility_bill",
                subtype="utility_bill",
                title_hint=title_hint or self._bill_title(text),
                summary_hint="Utility bill or account statement showing provider, billing period, due date, and amount due.",
                key_fields={
                    "statement_style": "utility_bill",
                    "billing_keywords": self._keyword_hits(
                        lowered,
                        ["provider", "billing period", "service period", "amount due", "due date", "account number"],
                    ),
                },
                reasons=["Detected provider, billing-period, due-date, or amount-due signals consistent with a utility bill."],
                confidence=0.86,
            )

        if self._looks_like_invoice(lowered):
            return CategoryInterpretation(
                category="invoice",
                profile="invoice",
                subtype="invoice",
                title_hint=title_hint or self._invoice_title(text),
                summary_hint="Invoice document with vendor, billing details, amount due, and payment timing information.",
                key_fields={
                    "invoice_number": self._invoice_number(text),
                    "billing_keywords": self._keyword_hits(
                        lowered,
                        ["invoice", "vendor", "bill to", "invoice date", "due date", "amount due", "total due"],
                    ),
                },
                reasons=["Detected invoice-specific billing fields such as invoice number, vendor, due date, or amount due."],
                confidence=0.87,
            )

        if self._looks_like_syllabus(lowered):
            course_title = self._course_title(text) or title_hint or "Course Guide"
            return CategoryInterpretation(
                category="course_guide",
                profile="syllabus",
                subtype="syllabus",
                title_hint=course_title,
                summary_hint="Course guide with title, semester, instructor, materials, policies, and exam dates.",
                reasons=["Detected course, semester, instructor, and policy/material signals."],
                confidence=0.88,
            )

        if self._looks_like_presentation_guide(lowered):
            subtype = "speaking_notes" if any(term in lowered for term in ["script", "speaker notes", "speaking notes", "talk track", "say:"]) else "presentation_guide"
            return CategoryInterpretation(
                category="presentation_guide",
                profile="presentation_guide",
                subtype=subtype,
                title_hint=title_hint or "Presentation Guide",
                summary_hint="Presentation guide with audience, slide flow, speaking notes, and rehearsal guidance.",
                reasons=["Detected presentation structure, audience, and speaking-note signals."],
                confidence=0.87,
            )

        if self._looks_like_technical_guide(text):
            title = self._technical_guide_title(text) or title_hint or self._filename_title(document.original_filename) or "Setup Guide"
            return CategoryInterpretation(
                category="installation_guide",
                profile="installation_guide",
                subtype="technical_documentation",
                title_hint=title,
                summary_hint="Technical setup guide with installation, configuration, dependency, and runbook-style instructions.",
                key_fields={
                    "guide_keywords": self._keyword_hits(
                        lowered,
                        ["installation", "setup", "configuration", "environment", "dependencies", "prerequisites", "docker", "api", "database"],
                    ),
                },
                workflow_hints={
                    "review_focus": [
                        "Verify prerequisites, environment variables, commands, and deployment steps before using the guide."
                    ],
                    "action_items": [
                        "Review prerequisites and dependencies before starting setup.",
                        "Confirm configuration and environment values for the target setup.",
                    ],
                },
                reasons=["Detected installation/setup vocabulary and procedural technical-documentation structure."],
                confidence=0.88,
            )

        if self._looks_like_implementation_schedule(text, document):
            title = self._tracker_title(text, document) or title_hint or self._filename_title(document.original_filename) or "Implementation Schedule"
            return CategoryInterpretation(
                category="implementation_schedule",
                profile="implementation_schedule",
                subtype="project_tracker",
                title_hint=title,
                summary_hint="Engineering planning spreadsheet with implementation tasks, status, testing, coverage, and ownership signals.",
                key_fields={
                    "sheet_names": self._sheet_names(text),
                    "tracker_keywords": self._keyword_hits(
                        lowered,
                        ["task", "feature", "schedule", "implementation", "testing", "coverage", "pipeline", "claimed", "status"],
                    ),
                    "header_hints": self._table_header_hints(text),
                },
                workflow_hints={
                    "review_focus": [
                        "Review task ownership, claimed status, implementation progress, testing, and coverage gaps."
                    ],
                    "action_items": [
                        "Review open implementation tasks, status, and claimed ownership.",
                        "Check testing, coverage, and pipeline status before the next milestone.",
                    ],
                },
                reasons=["Detected spreadsheet-like rows with planning, task, status, testing, and implementation vocabulary."],
                confidence=0.9,
            )

        if self._looks_like_profile_record(lowered):
            subtype = "education_record" if any(term in lowered for term in ["major:", "student id", "department:"]) else "profile_record"
            return CategoryInterpretation(
                category="profile_record",
                profile="profile_record",
                subtype=subtype,
                title_hint=self._profile_title(text) or "Profile Note",
                summary_hint="Profile-like text containing identity or affiliation facts.",
                reasons=["Detected multiple labeled identity fields."],
                confidence=0.85,
            )

        if self._looks_like_meeting_notice(lowered):
            return CategoryInterpretation(
                category="meeting_notice",
                profile="meeting_notice",
                subtype="meeting_notice",
                title_hint=title_hint,
                summary_hint="Meeting notice with time, location, purpose, and follow-up details.",
                reasons=["Detected meeting/date/location language."],
                confidence=0.76,
            )

        if self._looks_like_instructional_memo(lowered):
            return CategoryInterpretation(
                category="instructional_memo",
                profile="instructional_memo",
                subtype="instructional_memo",
                title_hint=title_hint or "Instructional Memo",
                summary_hint="Instructional memo with process guidance, requirements, deadlines, and follow-up details.",
                key_fields={
                    "process_keywords": self._keyword_hits(
                        lowered,
                        ["purpose", "scope", "required", "approval", "documentation", "deadline", "audit", "revocation", "follow-up"],
                    ),
                },
                reasons=["Detected memo-style process guidance with requirements, deadlines, or follow-up procedures."],
                confidence=0.82,
            )

        return CategoryInterpretation(
            category=explicit_category or document.category or ("notice" if document.document_type == DocumentType.notice else "other"),
            profile="generic_document",
            subtype="generic_document",
            title_hint=title_hint,
            summary_hint=None,
            confidence=0.55,
        )

    def _looks_like_manufacturing_document(self, lowered: str, document: Document) -> bool:
        if getattr(document, "line_items", None):
            return True
        signals = [
            "발주서",
            "견적서",
            "거래명세서",
            "납품서",
            "세금계산서",
            "포장명세서",
            "검사성적서",
            "품목명",
            "공급가액",
            "납기일",
            "수량",
            "단가",
        ]
        return sum(signal in lowered for signal in signals) >= 2

    def _manufacturing_title(self, document: Document, text: str) -> str:
        label = {
            "purchase_order": "발주서",
            "quotation": "견적서",
            "transaction_statement": "거래명세서",
            "delivery_note": "납품서",
            "invoice": "인보이스/세금계산서",
            "packing_list": "포장명세서",
            "inspection_report": "검사성적서",
            "contract": "계약서",
        }.get(getattr(document.document_type, "value", ""), "일반 문서")
        number = getattr(document, "document_number", None)
        vendor = getattr(document, "vendor_name", None) or document.merchant_name
        if number and vendor:
            return f"{label} {number} - {vendor}"
        if number:
            return f"{label} {number}"
        return self._filename_title(document.original_filename) or label

    def _manufacturing_summary(self, document: Document, doc_type: str) -> str:
        label = {
            "purchase_order": "발주서",
            "quotation": "견적서",
            "transaction_statement": "거래명세서",
            "delivery_note": "납품서",
            "invoice": "인보이스/세금계산서",
            "packing_list": "포장명세서",
            "inspection_report": "검사성적서",
            "contract": "계약서",
        }.get(doc_type, "제조업 문서")
        vendor = getattr(document, "vendor_name", None) or document.merchant_name or "공급업체 미확인"
        customer = getattr(document, "customer_name", None) or "고객사 미확인"
        count = len(getattr(document, "line_items", None) or [])
        return f"{label}에서 {vendor}와 {customer} 간 거래 정보와 품목 {count}건을 업무데이터/엑셀 입력용 데이터로 추출했습니다."

    def _meaningful_title(self, current: str | None, text: str) -> str | None:
        cleaned_current = self._clean_title_candidate(current)
        if cleaned_current and self._score_title_candidate(cleaned_current, 0, text) > 0:
            return cleaned_current
        candidates: list[tuple[int, str]] = []
        for index, line in enumerate(text.splitlines()[:12]):
            cleaned = self._clean_title_candidate(line)
            if not cleaned:
                continue
            score = self._score_title_candidate(cleaned, index, text)
            if score > 0:
                candidates.append((score, cleaned))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    def _course_title(self, text: str) -> str | None:
        lines = [self._clean_title_candidate(line) for line in text.splitlines()[:12]]
        candidates: list[tuple[int, str]] = []
        for index, cleaned in enumerate(lines):
            lowered = cleaned.lower()
            if not cleaned or re.fullmatch(r"(page|slide)\s+\d+", cleaned, flags=re.IGNORECASE):
                continue
            if lowered == "syllabus" and index > 0 and lines[index - 1]:
                previous = lines[index - 1]
                if not re.fullmatch(r"(page|slide)\s+\d+", previous, flags=re.IGNORECASE):
                    candidates.append((95, f"{previous} Syllabus"))
            if "syllabus" in lowered and len(cleaned) > len("syllabus"):
                candidates.append((90, cleaned))
            if any(keyword in lowered for keyword in ["course guide", "course overview", "course fundamentals"]):
                candidates.append((85, cleaned))
            score = self._score_title_candidate(cleaned, index, text)
            if score > 0 and (self._course_code(cleaned) or "course" in lowered):
                candidates.append((score + 10, cleaned))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    def _clean_title_candidate(self, value: str | None) -> str:
        cleaned = re.sub(r"\s+", " ", value or "").strip(" :-")
        cleaned = re.sub(
            r"^(?:title|document title|heading|subject|name|line)\s*:\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" :-")

    def _course_code(self, text: str) -> str | None:
        match = re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", text)
        return match.group(0) if match else None

    def _score_title_candidate(self, value: str, index: int, text: str) -> int:
        lowered = value.lower()
        if re.fullmatch(r"(page|slide)\s+\d+", value.strip(), flags=re.IGNORECASE):
            return -100
        if re.fullmatch(r"(?:연도|년도)\s*[.년]\s*월\s*[.월]\s*일\s*[.일]?", value.strip().lower()):
            return -100
        if len(value) < 4 or len(value) > 120:
            return -40
        score = 30 - (index * 2)
        if re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", value):
            score += 30
        if any(keyword in lowered for keyword in ["syllabus", "course", "guide", "presentation", "resume", "profile"]):
            score += 18
        if any(keyword in lowered for keyword in ["installation guide", "setup guide", "technical guide", "implementation schedule", "project tracker", "development roadmap"]):
            score += 30
        if self._looks_like_person_name_line(value):
            score -= 32
        if "|" in value:
            score -= 14
        if re.match(r"^[A-Z][A-Za-z0-9&,'./() -]{4,}$", value):
            score += 8
        if ":" in value:
            score -= 8
        if re.match(r"^(course description|overview|summary|introduction|objectives?)\s*:", lowered):
            score -= 28
        if re.match(r"^(this|these|students|you will|in this course|the purpose of)\b", lowered):
            score -= 35
        if len(value.split()) > 12:
            score -= 18
        if value.endswith("."):
            score -= 12
        if self._looks_like_sentence(value):
            score -= 22
        return score

    def _looks_like_sentence(self, value: str) -> bool:
        lowered = value.lower().strip()
        return len(lowered.split()) >= 8 and bool(re.search(r"\b(is|are|will|introduces|provides|covers|describes|contains)\b", lowered))

    def _specific_existing_category(self, category: str | None) -> str | None:
        if not category:
            return None
        lowered = category.lower().strip()
        if lowered in {"other", "document", "generic_document", "notice"}:
            return None
        return category

    def _receipt_title(self, document: Document) -> str:
        return f"{document.merchant_name} receipt" if document.merchant_name else "Receipt"

    def _receipt_summary(self, document: Document, style: str, lowered: str) -> str | None:
        merchant = document.merchant_name or "Unknown merchant"
        amount = f"${document.extracted_amount}" if document.extracted_amount is not None else "unknown amount"
        if style == "repair-service":
            return f"Repair-service receipt from {merchant} totaling {amount} with parts and labor charges."
        if style == "utility-bill":
            return f"Utility bill from {merchant} with an extracted amount of {amount}."
        return f"Receipt from {merchant} totaling {amount}."

    def _keyword_hits(self, lowered: str, keywords: list[str]) -> list[str]:
        return [keyword for keyword in keywords if keyword in lowered][:6]

    def _section_hints(self, lowered: str, section_names: list[str]) -> list[str]:
        return [name for name in section_names if name in lowered][:8]

    def _contact_links(self, text: str) -> list[str]:
        return re.findall(r"(https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|linkedin\.com/\S+|github\.com/\S+)", text, flags=re.IGNORECASE)[:6]

    def _looks_like_repair_service_receipt(self, lowered: str) -> bool:
        signals = ["repair", "service", "labor", "parts", "maintenance", "technician", "brake", "pedal", "cable"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_utility_bill(self, lowered: str) -> bool:
        utility_signals = [
            "utility",
            "billing period",
            "service period",
            "account number",
            "electric",
            "electricity",
            "water",
            "internet",
            "gas service",
            "power",
        ]
        billing_signals = [
            "amount due",
            "balance due",
            "pay by",
            "due date",
            "provider",
            "statement",
        ]
        utility_hits = sum(signal in lowered for signal in utility_signals)
        billing_hits = sum(signal in lowered for signal in billing_signals)
        return utility_hits >= 1 and (utility_hits + billing_hits) >= 2

    def _looks_like_invoice(self, lowered: str) -> bool:
        signals = [
            "invoice",
            "invoice number",
            "invoice #",
            "vendor",
            "bill to",
            "invoice date",
            "due date",
            "amount due",
            "total due",
        ]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_syllabus(self, lowered: str) -> bool:
        signals = ["syllabus", "course code", "semester", "instructor", "office hours", "grading", "required materials"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_presentation_guide(self, lowered: str) -> bool:
        signals = ["presentation", "slide", "audience", "speaker", "rehearse", "talk track", "speaking notes"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_profile_record(self, lowered: str) -> bool:
        if self._looks_like_technical_guide(lowered) or self._looks_like_implementation_schedule(lowered, None):
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

    def _looks_like_resume_profile(self, lowered: str) -> bool:
        signals = ["education", "experience", "projects", "skills", "technical skills", "gpa", "linkedin", "github", "resume"]
        return sum(signal in lowered for signal in signals) >= 3

    def _looks_like_meeting_notice(self, lowered: str) -> bool:
        patterns = [
            r"\bmeeting\b",
            r"\bagenda\b",
            r"\bmeeting date\b",
            r"\blocation\b",
            r"\broom\b",
            r"\bjoin us\b",
            r"\bzoom\b",
            r"\bteams\b",
        ]
        hits = sum(bool(re.search(pattern, lowered)) for pattern in patterns)
        return hits >= 2 or (
            bool(re.search(r"\bmeeting\b", lowered))
            and bool(re.search(r"\b(location|room|agenda|date|zoom|teams)\b", lowered))
        )

    def _looks_like_instructional_memo(self, lowered: str) -> bool:
        if (
            self._looks_like_presentation_guide(lowered)
            or self._looks_like_meeting_notice(lowered)
            or self._looks_like_technical_guide(lowered)
            or self._looks_like_implementation_schedule(lowered, None)
        ):
            return False
        signals = [
            "memo",
            "purpose:",
            "scope:",
            "guidance",
            "instructions",
            "please follow",
            "review the following",
            "steps",
            "prepare",
            "required prerequisites",
            "approval workflow",
            "documentation:",
            "deadlines:",
            "audit procedure",
            "revocation:",
            "arrival",
            "materials",
            "follow-up",
            "workshop",
            "facilitator",
        ]
        return sum(signal in lowered for signal in signals) >= 2 and not self._looks_like_invoice(lowered)

    def _bill_title(self, text: str) -> str | None:
        for line in text.splitlines()[:8]:
            cleaned = self._clean_title_candidate(line)
            lowered = cleaned.lower()
            if cleaned and any(keyword in lowered for keyword in ["statement", "bill", "utility", "power", "electric", "water", "internet"]):
                return cleaned
        return "Utility Bill"

    def _invoice_title(self, text: str) -> str | None:
        invoice_number = self._invoice_number(text)
        vendor = None
        for line in text.splitlines()[:10]:
            cleaned = self._clean_title_candidate(line)
            lowered = cleaned.lower()
            if lowered.startswith("vendor"):
                vendor = re.split(r"[:|,]", cleaned, maxsplit=1)[-1].strip(" |:-")
                break
        if invoice_number and vendor:
            return f"Invoice {invoice_number} from {vendor}"
        if invoice_number:
            return f"Invoice {invoice_number}"
        if vendor:
            return f"{vendor} Invoice"
        return "Invoice"

    def _invoice_number(self, text: str) -> str | None:
        match = re.search(r"\b(?:invoice(?:\s+number)?|invoice\s*#)\s*[:|,]?\s*([A-Z0-9-]{4,})", text, flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _profile_title(self, text: str) -> str | None:
        match = re.search(r"^name\s*:\s*([A-Za-z][A-Za-z .'-]{1,80})$", text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return f"{match.group(1).strip()} Profile"
        return None

    def _looks_like_technical_guide(self, text: str) -> bool:
        lowered = text.lower()
        title_hits = sum(signal in lowered for signal in ["installation guide", "setup guide", "technical guide", "project setup", "engineering documentation"])
        instruction_hits = sum(
            bool(re.search(pattern, lowered))
            for pattern in [
                r"\binstall(?:ation)?\b",
                r"\bsetup\b",
                r"\bconfigure|configuration\b",
                r"\benvironment(?: variables?)?\b",
                r"\bdependencies|prerequisites\b",
                r"\brun\b",
                r"\bcommand\b",
                r"\bdocker|api|database|server\b",
            ]
        )
        return title_hits >= 1 or instruction_hits >= 4

    def _looks_like_implementation_schedule(self, text: str, document: Document | None) -> bool:
        lowered = text.lower()
        is_spreadsheet = bool(document and document.source_file_type in {"xlsx", "csv"})
        sheet_hits = len(self._sheet_names(text))
        structure_hits = sum(signal in lowered for signal in ["|", "task", "feature", "status", "claimed", "owner"])
        planning_hits = sum(signal in lowered for signal in ["implementation", "schedule", "roadmap", "tracker", "testing", "coverage", "pipeline", "milestone"])
        header_hits = len(self._table_header_hints(text))
        return (
            (is_spreadsheet and planning_hits >= 2 and (structure_hits >= 2 or header_hits >= 2))
            or (sheet_hits >= 1 and planning_hits >= 2 and structure_hits >= 2)
            or planning_hits >= 4
        )

    def _technical_guide_title(self, text: str) -> str | None:
        candidates: list[tuple[int, str]] = []
        for index, line in enumerate(text.splitlines()[:18]):
            cleaned = self._clean_title_candidate(line)
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if self._looks_like_person_name_line(cleaned):
                continue
            score = self._score_title_candidate(cleaned, index, text)
            if any(keyword in lowered for keyword in ["installation guide", "setup guide", "technical guide", "manual", "project setup"]):
                score += 50
            elif any(keyword in lowered for keyword in ["installation", "setup", "configure", "deployment"]):
                score += 20
            if score > 0:
                candidates.append((score, cleaned))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    def _tracker_title(self, text: str, document: Document | None) -> str | None:
        sheet_names = self._sheet_names(text)
        for sheet_name in sheet_names:
            if re.search(r"\b(implementation|schedule|tracker|roadmap|plan|planning)\b", sheet_name, flags=re.IGNORECASE):
                return sheet_name
        for line in text.splitlines()[:14]:
            cleaned = self._clean_title_candidate(line)
            lowered = cleaned.lower()
            if cleaned and re.search(r"\b(implementation|schedule|tracker|roadmap|planning)\b", lowered) and "|" not in cleaned:
                return cleaned
        return self._filename_title(document.original_filename if document else "")

    def _sheet_names(self, text: str) -> list[str]:
        names = []
        for line in text.splitlines():
            match = re.match(r"\s*sheet\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
            if match:
                cleaned = self._clean_title_candidate(match.group(1))
                if cleaned:
                    names.append(cleaned)
        return list(dict.fromkeys(names))[:6]

    def _table_header_hints(self, text: str) -> list[str]:
        wanted = {"task", "feature", "schedule", "implementation", "testing", "coverage", "pipeline", "claimed", "status", "owner", "milestone"}
        hits: list[str] = []
        for line in text.splitlines()[:40]:
            if "|" not in line:
                continue
            cells = [cell.strip().lower() for cell in line.split("|") if cell.strip()]
            hits.extend(cell for cell in cells if cell in wanted)
        return list(dict.fromkeys(hits))[:8]

    def _looks_like_person_name_line(self, value: str) -> bool:
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}", cleaned):
            return False
        lowered = cleaned.lower()
        return not any(keyword in lowered for keyword in ["guide", "manual", "schedule", "tracker", "roadmap", "invoice", "statement", "profile", "syllabus"])

    def _filename_title(self, filename: str | None) -> str | None:
        if not filename:
            return None
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        cleaned = re.sub(r"[_-]+", " ", stem)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:120] if cleaned else None
