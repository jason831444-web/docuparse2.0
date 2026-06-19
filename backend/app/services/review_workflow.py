from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.document import Document
from app.services.document_taxonomy import DocumentTaxonomyService


RESOLVED_ISSUE_STATUSES = {"resolved", "ignored"}
CRITICAL_ISSUE_CODES = {
    "missing_document_number",
    "missing_line_items",
    "missing_item_name",
    "missing_quantity",
    "missing_price_or_total",
    "amount_mismatch",
    "invalid_line_amount",
    "item_code_name_conflict",
}


@dataclass
class ApprovalValidation:
    blocking: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "blocking": self.blocking, "warnings": self.warnings}


ISSUE_LABELS_KO: dict[str, str] = {
    "missing_document_number": "문서번호를 확인해야 합니다.",
    "missing_vendor_name": "공급업체를 확인해야 합니다.",
    "missing_customer_name": "고객사를 확인해야 합니다.",
    "missing_line_items": "품목 정보가 없습니다.",
    "missing_item_name": "품목명이 비어 있습니다.",
    "missing_quantity": "수량이 비어 있습니다.",
    "missing_price_or_total": "단가 또는 금액을 확인해야 합니다.",
    "amount_mismatch": "문서 총액과 품목 합계가 맞지 않습니다.",
    "invalid_line_amount": "품목 금액 계산을 확인해야 합니다.",
    "item_code_name_conflict": "품목명과 내부 품목코드가 서로 맞지 않을 수 있습니다.",
    "internal_item_unmatched": "사내 품목마스터에서 맞는 내부 품목코드를 찾지 못했습니다.",
    "internal_item_ambiguous": "사내 품목마스터 후보가 여러 개라 선택이 필요합니다.",
    "item_matching_skipped": "품목마스터가 없어 내부 품목코드 매칭을 건너뛰었습니다.",
    "subtotal_tax_total_mismatch": "공급가액, 세액, 합계금액이 맞지 않습니다.",
    "tax_amount_fields_missing": "세금계산서 금액 필드를 확인해야 합니다.",
    "return_document_misclassified_as_delivery_note": "반품/차감 문서가 납품서로 분류되어 확인이 필요합니다.",
    "missing_total": "문서 총액을 확인해야 합니다.",
    "related_document_missing": "연결할 원문서 번호를 확인해야 합니다.",
    "amount_direction_requires_review": "반품/차감 금액 방향을 확인해야 합니다.",
    "missing_inventory_item_or_quantity": "수량 확인용 품목명 또는 수량을 확인해야 합니다.",
    "vl_candidate_review_required": "AI 추출 후보에 검토가 필요한 항목이 있습니다.",
    "review_required": "검토 항목을 확인해야 합니다.",
}


FIELD_LABELS_KO: dict[str, str] = {
    "document": "문서 전체",
    "extracted_amount": "총액",
    "total_amount": "총액",
    "subtotal": "공급가액",
    "tax": "세액",
    "statement_summary": "정산 요약",
    "line_items.item_name": "품목명",
    "line_items.item_code": "문서 품목코드",
    "line_items.document_item_code": "문서 품목코드",
    "line_items.internal_item_code": "내부 품목코드",
    "line_items.quantity": "수량",
    "line_items.unit": "단위",
    "line_items.unit_price": "단가",
    "line_items.supply_amount": "공급가액",
    "line_items.tax_amount": "세액",
    "line_items.line_total": "합계금액",
}


ACTION_LABELS_KO: dict[str, str] = {
    "internal_item_unmatched": "필요하면 내부 품목코드를 직접 입력하거나 후보를 선택하세요. 내부코드 없이 처리해도 되는 문서라면 그대로 확정할 수 있습니다.",
    "internal_item_ambiguous": "필요하면 원본과 품목 후보를 비교해 맞는 내부 품목코드를 선택하세요. 내부코드는 보조 정보라 확정 필수값은 아닙니다.",
    "item_matching_skipped": "품목마스터가 없으면 내부 품목코드 없이 처리됩니다. 필요할 때만 품목마스터를 등록하세요.",
    "missing_quantity": "원본 문서에서 수량을 확인해 입력하거나, 빈 칸이 맞다면 무시로 표시하세요.",
    "missing_item_name": "원본 문서에서 품목명을 확인해 입력하세요.",
    "missing_price_or_total": "원본 문서에 단가/금액이 있는지 확인하고, 금액 없는 문서라면 무시로 표시하세요.",
    "amount_mismatch": "수량, 단가, 공급가액, 세액, 합계금액 중 잘못 들어간 값을 수정하세요.",
    "invalid_line_amount": "품목 행의 금액 계산이 맞는지 원본과 대조해 수정하세요.",
    "subtotal_tax_total_mismatch": "공급가액, 세액, 합계금액을 원본과 대조해 수정하세요.",
    "tax_amount_fields_missing": "세금계산서의 공급가액, 세액, 청구금액을 확인해 입력하세요.",
    "missing_line_items": "품목을 하나 이상 추가하거나 이 문서가 품목 없는 문서인지 확인하세요.",
    "missing_document_number": "문서번호를 입력하거나 원본에 없으면 무시로 표시하세요.",
    "missing_vendor_name": "거래처/공급업체명을 확인해 입력하세요.",
    "missing_customer_name": "고객사/공급받는 자를 확인해 입력하세요.",
    "return_document_misclassified_as_delivery_note": "문서 유형을 반품/차감 문서로 바꾸거나 원본 유형을 다시 확인하세요.",
    "missing_inventory_item_or_quantity": "수량 중심 문서의 품목명과 수량을 원본과 대조해 입력하세요.",
    "review_required": "문서 검토 영역에서 값을 수정한 뒤 해결 또는 무시를 선택하세요.",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def issue_key(issue: dict[str, Any]) -> str:
    return ":".join(
        str(part)
        for part in [issue.get("code") or "review_required", issue.get("field") or "document", issue.get("item_index") if issue.get("item_index") is not None else ""]
    )


def review_metadata(document: Document) -> dict[str, Any]:
    metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
    review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    issue_states = review.get("issues") if isinstance(review.get("issues"), list) else []
    known = {str(item.get("key")): item for item in issue_states if isinstance(item, dict) and item.get("key")}
    for issue in metadata.get("normalized_review_issues") or []:
        if not isinstance(issue, dict):
            continue
        key = issue_key(issue)
        known.setdefault(key, {
            "key": key,
            "code": issue.get("code"),
            "field": issue.get("field"),
            "item_index": issue.get("item_index"),
            "status": "open",
            "message_ko": issue.get("message_ko"),
        })
    review["issues"] = list(known.values())
    return review


def merge_review_metadata(document: Document, review: dict[str, Any]) -> None:
    metadata = dict(document.workflow_metadata or {})
    metadata["review"] = review
    document.workflow_metadata = metadata


def update_issue_status(document: Document, key: str, status: str, note: str | None = None, reviewer: str = "manual") -> dict[str, Any]:
    if status not in {"open", "resolved", "ignored", "blocked"}:
        raise ValueError("Unsupported review issue status")
    review = review_metadata(document)
    issues = review.get("issues") if isinstance(review.get("issues"), list) else []
    target = None
    for issue in issues:
        if isinstance(issue, dict) and issue.get("key") == key:
            target = issue
            break
    if target is None:
        target = {"key": key, "code": key.split(":", 1)[0], "status": "open"}
        issues.append(target)
    target.update({"status": status, "note": note, "updated_by": reviewer, "updated_at": now_iso()})
    if status in RESOLVED_ISSUE_STATUSES:
        target["resolved_by"] = reviewer
        target["resolved_at"] = now_iso()
    review["issues"] = issues
    review["reviewed_at"] = now_iso()
    review["review_state"] = "in_review"
    merge_review_metadata(document, review)
    return target


def approve_document(document: Document, approval_note: str | None = None, reviewer: str = "manual") -> ApprovalValidation:
    validation = validate_approval(document)
    review = review_metadata(document)
    review["reviewed_at"] = now_iso()
    review["approval_validation"] = validation.to_dict()
    if validation.ok:
        review.update({
            "approved": True,
            "approved_at": now_iso(),
            "approved_by": reviewer,
            "approval_note": approval_note,
            "review_state": "approved",
        })
    else:
        review.update({"approved": False, "review_state": "blocked"})
    merge_review_metadata(document, review)
    return validation


def approval_error_payload(document: Document, validation: ApprovalValidation) -> dict[str, Any]:
    """Return a user-facing Korean error payload for approval blockers."""
    blocking_details = [_approval_issue_detail(document, raw, blocking=True) for raw in validation.blocking]
    warning_details = [_approval_issue_detail(document, raw, blocking=False) for raw in validation.warnings]
    blocking_messages = [item["message_ko"] for item in blocking_details]
    warning_messages = [item["message_ko"] for item in warning_details]
    return {
        "ok": False,
        "error_code": "approval_blocked_by_review_issues",
        "message_ko": "아직 해결되지 않은 검토 항목이 있어 확정할 수 없습니다.",
        "action_ko": "문서 검토 영역에서 값을 수정한 뒤 ‘해결’을 누르거나, 원본 확인 결과 업무상 문제 없으면 ‘무시’를 선택하세요.",
        "blocking_count": len(blocking_details),
        "warning_count": len(warning_details),
        "blocking": blocking_messages,
        "warnings": warning_messages,
        "blocking_details": blocking_details,
        "warning_details": warning_details,
    }


def reopen_document(document: Document, note: str | None = None, reviewer: str = "manual") -> None:
    review = review_metadata(document)
    review.update({
        "approved": False,
        "reopened_at": now_iso(),
        "reopened_by": reviewer,
        "reopen_note": note,
        "review_state": "in_review",
    })
    merge_review_metadata(document, review)


def validate_approval(document: Document) -> ApprovalValidation:
    taxonomy = _taxonomy(document)
    profiles = set(taxonomy.get("document_profiles") or [])
    amount_required = taxonomy.get("amount_required")
    if amount_required is None:
        amount_required = "no_price_document" not in profiles and "inventory_movement_document" not in profiles
    party_required = taxonomy.get("party_required")
    if party_required is None:
        party_required = "inventory_movement_document" not in profiles
    blocking: list[str] = []
    warnings: list[str] = []

    review = review_metadata(document)
    issue_status = {str(issue.get("key")): str(issue.get("status") or "open") for issue in review.get("issues", []) if isinstance(issue, dict)}
    for issue in (document.workflow_metadata or {}).get("normalized_review_issues") or []:
        if not isinstance(issue, dict):
            continue
        key = issue_key(issue)
        if issue_status.get(key, "open") in RESOLVED_ISSUE_STATUSES:
            continue
        code = str(issue.get("code") or "review_required")
        severity = str(issue.get("severity") or "warning")
        if severity in {"info", "low"}:
            continue
        if code == "missing_price_or_total" and not amount_required:
            continue
        if code in {"missing_vendor_name", "missing_customer_name"} and not party_required:
            continue
        if code in CRITICAL_ISSUE_CODES:
            blocking.append(f"unresolved:{key}")

    if not document.document_number:
        warnings.append("missing_document_number")
    if party_required and not (document.vendor_name or document.merchant_name):
        warnings.append("missing_vendor_name")
    if party_required and not document.customer_name:
        warnings.append("missing_customer_name")
    if _items_required(document, profiles) and not document.line_items:
        blocking.append("missing_line_items")
    if amount_required and not document.extracted_amount:
        warnings.append("missing_total")
    if "tax_document" in profiles:
        blocking.extend(_tax_blocks(document))
    if "return_document" in profiles:
        warnings.append("amount_direction_requires_review")
        if _doc_type(document) == "delivery_note":
            blocking.append("return_document_misclassified_as_delivery_note")
        if not _related_document_number(document):
            warnings.append("related_document_missing")
    if "inventory_movement_document" in profiles and document.line_items:
        for index, item in enumerate(document.line_items, start=1):
            if not item.get("item_name") or item.get("quantity") in (None, ""):
                blocking.append(f"missing_inventory_item_or_quantity:item_{index}")
    if _has_vl_candidate_issues(document):
        warnings.append("vl_candidate_review_required")
    return ApprovalValidation(blocking=list(dict.fromkeys(blocking)), warnings=list(dict.fromkeys(warnings)))


def _approval_issue_detail(document: Document, raw_value: str, *, blocking: bool) -> dict[str, Any]:
    parsed = _parse_approval_issue(raw_value)
    normalized_issue = _find_normalized_issue(document, parsed["key"])
    code = str(normalized_issue.get("code") or parsed["code"] or "review_required")
    field = str(normalized_issue.get("field") or parsed["field"] or "document")
    item_index = _coerce_item_index(normalized_issue.get("item_index")) if normalized_issue else parsed["item_index"]
    base_message = str(normalized_issue.get("message_ko") or ISSUE_LABELS_KO.get(code) or "검토 항목을 확인해야 합니다.")
    item_label = f"{item_index + 1}번째 품목" if item_index is not None else None
    field_label = FIELD_LABELS_KO.get(field)
    message = base_message
    if item_label and not message.startswith(item_label):
        message = f"{item_label} {message}"
    return {
        "message_ko": message,
        "field_label_ko": field_label,
        "item_label_ko": item_label,
        "action_ko": ACTION_LABELS_KO.get(code) or ACTION_LABELS_KO["review_required"],
        "severity_ko": "확정 차단" if blocking else "참고",
    }


def _parse_approval_issue(raw_value: str) -> dict[str, Any]:
    value = str(raw_value or "").replace("unresolved:", "", 1)
    parts = value.split(":")
    code = parts[0] if parts and parts[0] else "review_required"
    field = parts[1] if len(parts) > 1 and parts[1] else "document"
    item_index = _coerce_item_index(parts[2] if len(parts) > 2 else None)
    if code == "missing_inventory_item_or_quantity" and len(parts) > 1 and parts[1].startswith("item_"):
        field = "line_items.quantity"
        item_index = _coerce_item_index(parts[1].replace("item_", ""))
    return {"key": value, "code": code, "field": field, "item_index": item_index}


def _find_normalized_issue(document: Document, key: str) -> dict[str, Any]:
    for issue in (document.workflow_metadata or {}).get("normalized_review_issues") or []:
        if isinstance(issue, dict) and issue_key(issue) == key:
            return issue
    return {}


def _coerce_item_index(value: object) -> int | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.startswith("item_"):
        try:
            return max(int(text.replace("item_", "")) - 1, 0)
        except ValueError:
            return None
    try:
        index = int(text)
    except (TypeError, ValueError):
        return None
    return index


def _taxonomy(document: Document) -> dict[str, Any]:
    metadata = document.workflow_metadata or {}
    taxonomy = metadata.get("taxonomy") if isinstance(metadata.get("taxonomy"), dict) else {}
    if taxonomy:
        profiles = list(taxonomy.get("document_profiles") or metadata.get("document_profiles") or [])
        profile = taxonomy.get("document_profile") or metadata.get("document_profile")
        if profile and profile not in profiles:
            profiles.insert(0, profile)
        taxonomy = dict(taxonomy)
        taxonomy["document_profiles"] = profiles
        taxonomy.setdefault("document_profile", profile)
        return taxonomy
    return DocumentTaxonomyService().classify(document, document.raw_text or "", extraction_method=document.extraction_method).to_metadata()


def _doc_type(document: Document) -> str:
    return getattr(document.document_type, "value", str(document.document_type))


def _items_required(document: Document, profiles: set[str]) -> bool:
    return _doc_type(document) in {"purchase_order", "quotation", "transaction_statement", "delivery_note", "invoice", "inspection_report"} or bool(profiles & {"return_document", "inventory_movement_document", "quality_document"})


def _has_vl_candidate_issues(document: Document) -> bool:
    metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
    layout = metadata.get("layout_debug") if isinstance(metadata.get("layout_debug"), dict) else {}
    summary = metadata.get("vl_candidate_summary") if isinstance(metadata.get("vl_candidate_summary"), dict) else {}
    if not summary and isinstance(layout.get("vl_candidate_summary"), dict):
        summary = layout.get("vl_candidate_summary") or {}
    candidates = metadata.get("vl_candidates") if isinstance(metadata.get("vl_candidates"), list) else layout.get("vl_candidates")
    candidate_count = int(summary.get("candidate_count") or (len(candidates) if isinstance(candidates, list) else 0))
    issue_codes = summary.get("issue_codes")
    warning_count = int(summary.get("warning_count") or 0)
    failure_count = int(summary.get("failure_count") or 0)
    return bool(candidate_count and (issue_codes or warning_count or failure_count))


def _tax_blocks(document: Document) -> list[str]:
    blocks: list[str] = []
    subtotal = _to_decimal(document.subtotal)
    tax = _to_decimal(document.tax)
    total = _to_decimal(document.extracted_amount)
    if subtotal is None or tax is None or total is None:
        blocks.append("tax_amount_fields_missing")
    elif abs((subtotal + tax) - total) > Decimal("0.01"):
        blocks.append("subtotal_tax_total_mismatch")
    return blocks


def _to_decimal(value: object) -> Decimal | None:
    if value in (None, "", []):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


def _related_document_number(document: Document) -> str | None:
    metadata = document.workflow_metadata or {}
    business = metadata.get("business_fields") if isinstance(metadata.get("business_fields"), dict) else {}
    for key in ("related_document_number", "related_doc", "original_document_number", "source_document_number"):
        value = business.get(key) or metadata.get(key)
        if value:
            return str(value)
    return None
