from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.document import Document, DocumentType


ROOT_LABELS = {
    "purchase_order": "발주서",
    "quotation": "견적서",
    "transaction_statement": "거래명세서",
    "delivery_note": "납품서",
    "invoice": "인보이스/세금계산서",
    "packing_list": "포장명세서",
    "inspection_report": "검사성적서",
    "return_note": "반품 문서",
    "credit_note": "차감/크레딧 문서",
    "internal_transfer": "내부 이동서",
    "pos_daily_settlement": "POS 일일정산",
    "purchase_memo": "구매 메모",
    "contract": "계약서",
    "general_document": "일반 문서",
    "receipt": "Receipt",
    "document": "Document",
    "memo": "Memo",
    "notice": "Notice",
    "presentation": "Presentation",
    "other": "Other",
}

ALIASES = {
    "repair_service_receipt": "repair_service",
    "receipt": "retail",
    "tax_invoice": "invoice",
    "commercial_invoice": "invoice",
    "incoming_inspection": "inspection_report",
    "return_credit": "credit_note",
    "return_credit_note": "credit_note",
    "utilities": "utility_bill",
    "utility": "utility_bill",
    "profile": "profile_record",
    "education_record": "profile_record",
    "setup_guide": "installation_guide",
    "technical_guide": "installation_guide",
    "technical_documentation": "installation_guide",
    "project_setup": "installation_guide",
    "project_tracker": "implementation_schedule",
    "engineering_planning": "implementation_schedule",
    "development_roadmap": "implementation_schedule",
}

LABEL_ALIASES = {
    "purchase_order": "발주서",
    "quotation": "견적서",
    "transaction_statement": "거래명세서",
    "delivery_note": "납품서",
    "invoice": "인보이스/세금계산서",
    "packing_list": "포장명세서",
    "inspection_report": "검사성적서",
    "return_note": "반품 문서",
    "credit_note": "차감/크레딧 문서",
    "internal_transfer": "내부 이동서",
    "pos_daily_settlement": "POS 일일정산",
    "purchase_memo": "구매 메모",
    "contract": "계약서",
    "general_document": "일반 문서",
    "retail": "소매",
    "repair_service": "수리 서비스",
    "utility_bill": "공과금",
    "syllabus": "강의계획서",
    "course_guide": "수업 안내",
    "presentation_guide": "발표 자료",
    "speaking_notes": "발표 노트",
    "resume_profile": "이력 프로필",
    "profile_record": "프로필 기록",
    "installation_guide": "설치 안내",
    "implementation_schedule": "구현 일정",
    "meeting_notice": "회의 공지",
    "instructional_memo": "업무 메모",
}

TIME_SENSITIVE_TAGS = {"time_sensitive", "time-sensitive", "urgent", "deadline"}

TAG_CONFLICTS = {
    "purchase_order": {"quotation", "transaction_statement", "delivery_note", "invoice", "packing_list", "general_document", "other"},
    "quotation": {"purchase_order", "transaction_statement", "delivery_note", "invoice", "packing_list", "general_document", "other"},
    "transaction_statement": {"purchase_order", "quotation", "delivery_note", "invoice", "packing_list", "general_document", "other"},
    "delivery_note": {"purchase_order", "quotation", "transaction_statement", "invoice", "packing_list", "general_document", "other"},
    "invoice": {"purchase_order", "quotation", "transaction_statement", "delivery_note", "packing_list", "general_document", "other"},
    "syllabus": {"memo", "notice", "office", "time_sensitive", "generic_document", "other"},
    "course_guide": {"memo", "notice", "office", "time_sensitive", "generic_document", "other"},
    "presentation_guide": {"receipt", "retail", "food_drink", "repair_service", "utility_bill", "notice", "memo", "generic_document", "other"},
    "speaking_notes": {"receipt", "retail", "food_drink", "repair_service", "utility_bill", "notice", "memo", "generic_document", "other"},
    "resume_profile": {"receipt", "retail", "food_drink", "utility_bill", "memo", "notice", "profile_record", "generic_document", "other", "time_sensitive"},
    "profile_record": {"receipt", "retail", "food_drink", "utility_bill", "memo", "notice", "generic_document", "other", "time_sensitive"},
    "installation_guide": {"receipt", "retail", "food_drink", "utility_bill", "memo", "notice", "profile_record", "generic_document", "other", "time_sensitive"},
    "implementation_schedule": {"receipt", "retail", "food_drink", "utility_bill", "memo", "notice", "profile_record", "generic_document", "other"},
    "repair_service": {"utility_bill", "notice", "memo", "time_sensitive", "generic_document", "other"},
    "repair_service_receipt": {"utility_bill", "notice", "memo", "time_sensitive", "generic_document", "other"},
    "utility_bill": {"invoice", "repair_service", "retail", "receipt", "notice", "memo", "time_sensitive", "generic_document", "other"},
    "invoice": {"retail", "food_drink", "utility_bill", "receipt", "notice", "memo", "time_sensitive", "generic_document", "other"},
    "meeting_notice": {"receipt", "retail", "food_drink", "utility_bill", "generic_document", "other"},
    "instructional_memo": {"receipt", "retail", "food_drink", "repair_service", "utility_bill", "notice", "generic_document", "other", "time_sensitive"},
}

CLEAR_TIME_SENSITIVE_CATEGORIES = {"meeting_notice", "policy_notice"}


@dataclass(frozen=True)
class CategoryPath:
    value: str
    label: str
    parent: str | None
    depth: int
    category: str | None


def normalize_category(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[\s/\-]+", "_", value.strip().lower())
    cleaned = re.sub(r"[^a-z0-9_]+", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return None
    return ALIASES.get(cleaned, cleaned)


def normalize_category_value(value: str | None) -> str | None:
    if not value:
        return None
    leaf = str(value).split(">")[-1]
    return normalize_category(leaf)


def display_label(value: str | None) -> str:
    normalized = normalize_category(value)
    if not normalized:
        return "Uncategorized"
    if normalized in ROOT_LABELS:
        return ROOT_LABELS[normalized]
    if normalized in LABEL_ALIASES:
        return LABEL_ALIASES[normalized]
    return " ".join(part.capitalize() for part in normalized.split("_"))


def normalize_tags(tags: list[str] | None) -> list[str]:
    cleaned = []
    for tag in tags or []:
        normalized = normalize_category(str(tag))
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def clean_tags_for_context(
    tags: list[str] | None,
    *,
    category: str | None = None,
    profile: str | None = None,
    document_type: str | None = None,
    key_dates: list | None = None,
    follow_up_required: bool | None = None,
    urgency_level: str | None = None,
) -> list[str]:
    cleaned = normalize_tags(tags)
    context = normalize_category(profile) or normalize_category(category)
    blocked = set(TAG_CONFLICTS.get(context or "", {"generic_document", "other"}))
    cleaned = [tag for tag in cleaned if tag not in blocked]

    has_explicit_timing = bool(key_dates) or bool(follow_up_required) or (urgency_level or "").lower() in {"medium", "high"}
    if "time_sensitive" in cleaned and not (has_explicit_timing or context in CLEAR_TIME_SENSITIVE_CATEGORIES):
        cleaned = [tag for tag in cleaned if tag != "time_sensitive"]

    broad_tag = normalize_category(document_type)
    if broad_tag in ROOT_LABELS and broad_tag not in cleaned:
        cleaned.insert(0, broad_tag)
    return cleaned


def category_path_for(document: Document) -> CategoryPath:
    leaf = normalize_category_value(document.category)
    value = leaf or "uncategorized"
    return CategoryPath(value=value, label=display_label(value), parent=None, depth=0, category=leaf)


def path_matches_document(document: Document, requested: str) -> bool:
    normalized = normalize_category_value(requested)
    path = category_path_for(document)
    if requested == path.value:
        return True
    return bool(normalized and (normalized == normalize_category_value(document.category) or normalized == normalize_category_value(path.value)))
