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
    "internal_item_unmatched",
    "internal_item_ambiguous",
    "item_matching_skipped",
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
    return ApprovalValidation(blocking=list(dict.fromkeys(blocking)), warnings=list(dict.fromkeys(warnings)))


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
