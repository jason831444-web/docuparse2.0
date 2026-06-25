import io
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from app.models.document import Document, ExportTemplate
from app.services.export_templates import documents_to_template_rows
from app.services.document_taxonomy import DocumentTaxonomyService
from app.services.vl_candidate_validation import VLCandidateValidationGate


_taxonomy_service = DocumentTaxonomyService()
_vl_candidate_gate = VLCandidateValidationGate()


def export_blocked_documents(documents: list[Document]) -> list[dict]:
    blocked: list[dict] = []
    for document in documents:
        status = getattr(document.processing_status, "value", str(document.processing_status))
        if status == "confirmed":
            continue
        blocked.append(
            {
                "id": str(document.id),
                "title": document.title or document.original_filename,
                "processing_status": status,
            }
        )
    return blocked


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
    taxonomy = _export_taxonomy(document)
    policy = _export_policy(document, taxonomy)
    export_conflict_blocked = _return_credit_purchase_order_export_conflict(document, taxonomy)
    if export_conflict_blocked:
        data["line_items"] = []
        data["subtotal"] = None
        data["tax"] = None
        data["extracted_amount"] = None
        data["currency"] = None
    data["document_taxonomy"] = taxonomy
    data["export_policy"] = policy
    semantic = _semantic_mapping(document)
    semantic_fields = _semantic_fields(document)
    data["canonical_export"] = {
        "document": {
            "document_id": str(document.id) if document.id else None,
            "filename": document.original_filename,
            "document_type": semantic.get("document_type") or _doc_type(document),
            "category": semantic.get("category") or document.category,
            "document_subtype": taxonomy.get("document_subtype"),
            "document_profile": taxonomy.get("document_profile"),
            "document_profiles": taxonomy.get("document_profiles", []),
            "layout_profile": taxonomy.get("layout_profile"),
            "processing_status": getattr(document.processing_status, "value", str(document.processing_status)) if document.processing_status else None,
            "review_required": document.review_required,
            "document_number": semantic_fields.get("document_number") or document.document_number,
            "issue_date": str(semantic_fields.get("issue_date") or document.issue_date or document.extracted_date or "") or None,
            "due_date": str(document.due_date or "") or None,
            "vendor_name": semantic_fields.get("vendor_name") or document.vendor_name or document.merchant_name,
            "customer_name": semantic_fields.get("customer_name") or document.customer_name,
            "currency": None if export_conflict_blocked else semantic_fields.get("currency") or document.currency,
            "subtotal": None if export_conflict_blocked else _decimal_text(semantic_fields.get("supply_amount") or document.subtotal),
            "tax": None if export_conflict_blocked else _decimal_text(semantic_fields.get("tax_amount") or document.tax),
            "total": None if export_conflict_blocked else _decimal_text(_semantic_total(document) or document.extracted_amount),
        },
        "confirmed_semantic_mapping": semantic,
        "policy": policy,
        "line_items": _canonical_line_items(document),
        "review_candidates": {
            "bbox_table_candidates": _layout_debug(document).get("bbox_table_candidates", []),
            "bbox_candidate_summary": _layout_candidate_summary(document),
            "vl_candidates": _vl_candidates(document),
            "vl_candidate_summary": _vl_candidate_summary(document),
            "party_review_candidates": _workflow_list(document, "party_review_candidates"),
            "document_number_candidates": _workflow_list(document, "document_number_candidates"),
            "date_candidates": _workflow_list(document, "date_candidates"),
            "receipt_item_candidates": _workflow_list(document, "receipt_item_candidates"),
            "pos_settlement_summary": _workflow_dict(document, "pos_settlement_summary"),
        },
    }
    return data


def document_read_safety_overrides(document: Document) -> dict:
    """Return API read overrides for values that are unsafe to show as confirmed."""

    taxonomy = _export_taxonomy(document)
    if not _return_credit_purchase_order_export_conflict(document, taxonomy):
        return {}
    return {
        "line_items": [],
        "subtotal": None,
        "tax": None,
        "extracted_amount": None,
        "currency": None,
    }


def documents_to_csv(documents: list[Document], template: ExportTemplate | None = None) -> str:
    rows = documents_to_template_rows(documents, template) if template else documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows).drop(columns=["거래처 탭", "_party_tab"], errors="ignore")
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue()


def documents_to_excel(documents: list[Document], sheet_mode: str = "combined", template: ExportTemplate | None = None) -> bytes:
    rows = documents_to_template_rows(documents, template) if template else documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows)
    buffer = io.BytesIO()
    try:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            if sheet_mode == "party_tabs" and rows:
                party_column = "_party_tab" if "_party_tab" in frame.columns else "거래처 탭"
                grouped = frame.groupby(frame[party_column].fillna("미분류"), dropna=False)
                for name, group in grouped:
                    group.drop(columns=["거래처 탭", "_party_tab"], errors="ignore").to_excel(writer, index=False, sheet_name=_excel_sheet_name(str(name)))
            else:
                frame.drop(columns=["거래처 탭", "_party_tab"], errors="ignore").to_excel(writer, index=False, sheet_name="erp_ready_data")
        return buffer.getvalue()
    except ModuleNotFoundError:
        return _minimal_xlsx(rows, sheet_mode=sheet_mode)


def document_to_json(document: Document) -> str:
    return json.dumps(_json_safe(serialize_document(document)), indent=2)


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
        taxonomy = _export_taxonomy(document)
        policy = _export_policy(document, taxonomy)
        export_conflict_blocked = _return_credit_purchase_order_export_conflict(document, taxonomy)
        semantic = _semantic_mapping(document)
        semantic_fields = _semantic_fields(document)
        layout_summary = _layout_candidate_summary(document)
        vl_summary = _vl_candidate_summary(document)
        review_reasons = _review_reason_text(document)
        line_items = _exportable_line_items(document)
        has_line_items = bool(line_items)
        line_items = line_items or [{}]
        for index, item in enumerate(line_items, start=1):
            rows.append({
                "문서유형": semantic.get("category") or getattr(document.document_type, "value", str(document.document_type)),
                "공급업체": semantic_fields.get("vendor_name") or document.vendor_name or document.merchant_name,
                "고객사": semantic_fields.get("customer_name") or document.customer_name,
                "거래처 탭": semantic_fields.get("customer_name") or semantic_fields.get("vendor_name") or document.customer_name or document.vendor_name or document.merchant_name or "미분류",
                "문서번호": semantic_fields.get("document_number") or document.document_number,
                "발행일": str(semantic_fields.get("issue_date") or document.issue_date or document.extracted_date or "") or None,
                "납기일": str(document.due_date or "") or None,
                "품목명": item.get("item_name"),
                "품목코드": item.get("item_code"),
                "규격": item.get("specification"),
                "Lot No": item.get("lot_no"),
                "검사판정": item.get("inspection_result"),
                "수량": item.get("quantity"),
                "발주수량": item.get("ordered_quantity"),
                "요청수량": item.get("requested_quantity"),
                "입고수량": item.get("received_quantity"),
                "납품수량": item.get("delivered_quantity"),
                "잔량": item.get("remaining_quantity"),
                "합격수량": item.get("accepted_quantity"),
                "불량수량": item.get("rejected_quantity"),
                "단위": item.get("unit"),
                "단가": item.get("unit_price"),
                "공급가액": item.get("supply_amount"),
                "세액": item.get("tax_amount"),
                "합계금액": None if export_conflict_blocked else (item.get("line_total") if has_line_items else _semantic_total(document) or document.extracted_amount),
                "통화": None if export_conflict_blocked else semantic_fields.get("currency") or document.currency,
                "검토상태": "검토 필요" if document.review_required else "확정 가능",
                "document_id": str(document.id) if document.id else None,
                "filename": document.original_filename,
                "document_total": None if export_conflict_blocked else _decimal_text(_semantic_total(document) or document.extracted_amount),
                "document_subtype": taxonomy.get("document_subtype"),
                "document_profile": taxonomy.get("document_profile"),
                "document_profiles": ", ".join(taxonomy.get("document_profiles") or []),
                "layout_profile": taxonomy.get("layout_profile"),
                "processing_status": getattr(document.processing_status, "value", str(document.processing_status)) if document.processing_status else None,
                "review_required": document.review_required,
                "review_reasons": review_reasons,
                "line_index": index,
                "document_item_code": item.get("document_item_code") or item.get("item_code") or item.get("source_item_code"),
                "internal_item_code": item.get("internal_item_code"),
                "match_status": item.get("item_master_match_status"),
                "match_confidence": item.get("item_master_match_confidence"),
                "line_review_flags": _line_review_flags(document, index - 1),
                "amount_required": policy["amount_required"],
                "party_required": policy["party_required"],
                "export_policy": policy["export_policy"],
                "export_blocked": policy["export_blocked"],
                "export_warning": policy["export_warning"],
                "approved": policy["approved"],
                "review_state": policy["review_state"],
                "reviewed_at": policy["reviewed_at"],
                "approved_at": policy["approved_at"],
                "approval_note": policy["approval_note"],
                "taxonomy_evidence": ", ".join(taxonomy.get("evidence") or []),
                "bbox_candidate_count": layout_summary["candidate_count"],
                "bbox_uncertain_candidate_count": layout_summary["uncertain_count"],
                "bbox_review_flags": ", ".join(layout_summary["review_flags"]),
                "vl_candidate_count": vl_summary["candidate_count"],
                "vl_candidate_warning_count": vl_summary["warning_count"],
                "vl_candidate_failure_count": vl_summary["failure_count"],
                "vl_candidate_issue_codes": ", ".join(vl_summary["issue_codes"]),
                "vl_candidate_provider": vl_summary["provider"],
                "vl_candidate_gate_decision": vl_summary["gate_decision"],
                "vl_candidate_gate_reasons": ", ".join(vl_summary["gate_reasons"]),
            })
    return rows


def _doc_type(document: Document) -> str:
    return getattr(document.document_type, "value", str(document.document_type))


def _metadata_taxonomy(document: Document) -> dict:
    workflow = document.workflow_metadata or {}
    ingestion = document.ingestion_metadata or {}
    workflow_taxonomy = workflow.get("taxonomy") if isinstance(workflow.get("taxonomy"), dict) else {}
    ingestion_taxonomy = ingestion.get("taxonomy") if isinstance(ingestion.get("taxonomy"), dict) else {}
    profiles: list[str] = []
    for value in (workflow_taxonomy.get("document_profiles"), workflow.get("document_profiles"), ingestion_taxonomy.get("document_profiles")):
        if isinstance(value, list):
            profiles.extend(str(item) for item in value if item)
    profile = workflow_taxonomy.get("document_profile") or workflow.get("document_profile") or ingestion_taxonomy.get("document_profile")
    if profile and str(profile) not in profiles:
        profiles.insert(0, str(profile))
    return {
        "document_subtype": workflow_taxonomy.get("document_subtype") or workflow.get("document_subtype") or ingestion_taxonomy.get("document_subtype"),
        "document_profile": profile,
        "document_profiles": list(dict.fromkeys(profiles)),
        "layout_profile": workflow_taxonomy.get("layout_profile") or workflow.get("layout_profile") or ingestion_taxonomy.get("layout_profile"),
        "amount_required": _first_bool(workflow_taxonomy.get("amount_required"), workflow.get("amount_required"), ingestion_taxonomy.get("amount_required")),
        "party_required": _first_bool(workflow_taxonomy.get("party_required"), workflow.get("party_required"), ingestion_taxonomy.get("party_required")),
        "evidence": workflow_taxonomy.get("evidence") or ingestion_taxonomy.get("evidence") or [],
    }


def _export_taxonomy(document: Document) -> dict:
    metadata_taxonomy = _metadata_taxonomy(document)
    if metadata_taxonomy.get("document_subtype") or metadata_taxonomy.get("document_profile") or metadata_taxonomy.get("document_profiles"):
        return metadata_taxonomy
    classified = _taxonomy_service.classify(document, document.raw_text or "", extraction_method=document.extraction_method, file_metadata=(document.ingestion_metadata or {}).get("file_metadata"))
    return classified.to_metadata()


def _export_policy(document: Document, taxonomy: dict) -> dict:
    profiles = set(taxonomy.get("document_profiles") or [])
    review = _review_metadata(document)
    amount_required = taxonomy.get("amount_required")
    party_required = taxonomy.get("party_required")
    if amount_required is None:
        amount_required = "no_price_document" not in profiles and "inventory_movement_document" not in profiles and _doc_type(document) not in {"delivery_note", "inspection_report"}
    if party_required is None:
        party_required = "inventory_movement_document" not in profiles
    warnings: list[str] = []
    if "no_price_document" in profiles:
        warnings.append("amount_not_required")
    if "tax_document" in profiles:
        warnings.extend(_tax_consistency_warnings(document))
    if "return_document" in profiles:
        warnings.append("amount_direction_requires_review")
        if not _related_document_number(document):
            warnings.append("related_document_missing")
    if document.review_required and not review.get("approved"):
        warnings.append("review_required")
    if review.get("approval_validation", {}).get("blocking"):
        warnings.append("approval_validation_blocking")
    vl_summary = _vl_candidate_summary(document)
    if vl_summary["candidate_count"] and (vl_summary["issue_codes"] or vl_summary["warning_count"] or vl_summary["failure_count"]):
        warnings.append("vl_candidate_review_required")
    source_conflict_blocked = _return_credit_purchase_order_export_conflict(document, taxonomy)
    if source_conflict_blocked:
        warnings.append("return_credit_source_conflicts_with_priced_export_blocked")
    return {
        "amount_required": bool(amount_required),
        "party_required": bool(party_required),
        "review_required": bool(document.review_required and not review.get("approved")),
        "export_policy": _policy_name(taxonomy, amount_required=bool(amount_required)),
        "export_blocked": bool(review.get("approval_validation", {}).get("blocking")) or source_conflict_blocked,
        "export_warning": ", ".join(dict.fromkeys(warnings)),
        "related_document_number": _related_document_number(document),
        "approved": bool(review.get("approved")),
        "review_state": review.get("review_state"),
        "reviewed_at": review.get("reviewed_at"),
        "approved_at": review.get("approved_at"),
        "approval_note": review.get("approval_note"),
        "forced_approval": bool(review.get("forced_approval")),
    }


def _policy_name(taxonomy: dict, *, amount_required: bool) -> str:
    profiles = set(taxonomy.get("document_profiles") or [])
    if "inventory_movement_document" in profiles:
        return "inventory_movement_no_price"
    if "return_document" in profiles:
        return "return_or_credit_review"
    if "tax_document" in profiles:
        return "tax_document_consistency"
    if "foreign_currency_document" in profiles:
        return "foreign_currency_document"
    if not amount_required:
        return "no_price_document"
    return "priced_document"


def _tax_consistency_warnings(document: Document) -> list[str]:
    warnings: list[str] = []
    subtotal = _to_decimal(document.subtotal)
    tax = _to_decimal(document.tax)
    total = _to_decimal(document.extracted_amount)
    if subtotal is None or tax is None or total is None:
        warnings.append("tax_amount_fields_missing")
    elif abs((subtotal + tax) - total) > Decimal("0.01"):
        warnings.append("subtotal_tax_total_mismatch")
    line_total = _line_total_sum(document.line_items or [])
    if total is not None and line_total is not None and abs(line_total - total) > Decimal("0.01"):
        warnings.append("line_items_total_mismatch")
    return warnings


def _review_reason_text(document: Document) -> str:
    metadata = document.workflow_metadata or {}
    reasons = metadata.get("normalized_review_issues") or metadata.get("review_reasons") or []
    values: list[str] = []
    if isinstance(reasons, list):
        for reason in reasons:
            if isinstance(reason, dict):
                values.append(str(reason.get("code") or reason.get("message_ko") or "review_required"))
            elif reason:
                values.append(str(reason))
    for warning in document.warnings or []:
        values.append(str(warning))
    return ", ".join(dict.fromkeys(values))


def _review_metadata(document: Document) -> dict:
    metadata = document.workflow_metadata or {}
    review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    return review


def _layout_debug(document: Document) -> dict:
    metadata = document.workflow_metadata or {}
    layout = metadata.get("layout_debug") if isinstance(metadata.get("layout_debug"), dict) else {}
    return layout


def _layout_candidate_summary(document: Document) -> dict:
    layout = _layout_debug(document)
    return {
        "candidate_count": int(layout.get("candidate_count") or 0),
        "uncertain_count": int(layout.get("uncertain_count") or 0),
        "parser_integrated": bool(layout.get("parser_integrated")),
        "review_flags": list(layout.get("bbox_review_flags") or []),
    }


def _vl_candidate_source(document: Document) -> dict:
    metadata = document.workflow_metadata or {}
    layout = _layout_debug(document)
    if isinstance(metadata.get("vl_candidates"), list) or isinstance(metadata.get("vl_candidate_summary"), dict):
        return metadata
    if isinstance(layout.get("vl_candidates"), list) or isinstance(layout.get("vl_candidate_summary"), dict):
        return layout
    return {}


def _vl_candidates(document: Document) -> list[dict]:
    source = _vl_candidate_source(document)
    candidates = source.get("vl_candidates")
    if not isinstance(candidates, list):
        return []
    return [_compact_vl_candidate(document, candidate) for candidate in candidates if isinstance(candidate, dict)][:5]


def _compact_vl_candidate(document: Document, candidate: dict) -> dict:
    validation = candidate.get("manual_visual_check_validation")
    if not isinstance(validation, dict):
        validation = candidate.get("validation") if isinstance(candidate.get("validation"), dict) else {}
    issue_codes = _string_list(candidate.get("issue_codes")) or _string_list(validation.get("issue_codes"))
    text_preview = _sanitize_vl_text_preview(candidate.get("text_preview") or candidate.get("output_preview"))
    if text_preview and len(text_preview) > 1200:
        text_preview = text_preview[:1200] + "..."
    compact = {
        "source": candidate.get("source") or candidate.get("provider") or "paddleocr_vl_1_6_gguf",
        "provider": candidate.get("provider") or "paddleocr_vl_1_6_gguf",
        "candidate_only": bool(candidate.get("candidate_only", True)),
        "parser_integrated": bool(candidate.get("parser_integrated")),
        "parser_evaluated": bool(candidate.get("structured_candidate")) or bool(candidate.get("parser_evaluated")),
        "provider_available_candidate": bool(candidate.get("provider_available_candidate")),
        "validation_severity": candidate.get("validation_severity") or validation.get("severity"),
        "issue_codes": issue_codes,
        "issue_details": _compact_vl_issue_details(candidate.get("issue_details") or validation.get("issues")),
        "review_flags": _string_list(candidate.get("review_flags")) or issue_codes,
        "text_preview": text_preview,
        "matched_terms": _string_list(candidate.get("matched_terms") or (candidate.get("validation") or {}).get("matched_terms")),
        "missing_required_values": _json_safe(validation.get("missing_required_values") or {}),
        "inference_time_ms": _json_safe(candidate.get("inference_time_ms") or candidate.get("elapsed_ms")),
    }
    structured = _compact_vl_structured_candidate(candidate.get("structured_candidate"))
    if structured:
        compact["structured_candidate"] = structured
        compact["promotion_gate"] = _json_safe(_vl_candidate_gate.evaluate(document, candidate))
    return compact


def _compact_vl_structured_candidate(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    document = value.get("document") if isinstance(value.get("document"), dict) else {}
    line_items = value.get("line_items") if isinstance(value.get("line_items"), list) else []
    return {
        "candidate_only": bool(value.get("candidate_only", True)),
        "parser_integrated": bool(value.get("parser_integrated")),
        "parser_evaluated": bool(value.get("parser_evaluated", True)),
        "confirmed_promotion": bool(value.get("confirmed_promotion")),
        "document": _json_safe(document),
        "line_items": [_json_safe(item) for item in line_items[:25] if isinstance(item, dict)],
        "line_item_count": int(value.get("line_item_count") or len(line_items)),
        "issue_codes": _string_list(value.get("issue_codes")),
        "review_flags": _string_list(value.get("review_flags")),
        "issues": _compact_vl_issue_details(value.get("issues")),
    }


def _sanitize_vl_text_preview(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    lines: list[str] = []
    seen: set[str] = set()
    for raw in _strip_preview_html(value).splitlines():
        line = _normalize_vl_preview_line(raw)
        if not line:
            continue
        if _is_vl_artifact_path_line(line) or _is_vl_layout_label_line(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines) if lines else None


def _strip_preview_html(value: str) -> str:
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value)


def _normalize_vl_preview_line(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return value.strip(" |")


def _is_vl_artifact_path_line(value: str) -> bool:
    lowered = value.lower()
    if not lowered.endswith((".png", ".jpg", ".jpeg", ".pdf")):
        return False
    if lowered.startswith(("imgs/", "./imgs/")):
        return True
    return lowered.startswith(("/tmp/", "/var/tmp/", "/root/", "/app/")) or "/docuparse_e2e_logs/" in lowered


def _is_vl_layout_label_line(value: str) -> bool:
    return value.strip().casefold() in {
        "number",
        "footnote",
        "header",
        "header_image",
        "footer",
        "footer_image",
        "aside_text",
        "paragraph_title",
        "seal",
        "seal_image",
        "text",
        "table",
    }


def _compact_vl_issue_details(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    details: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        detail = {
            key: _json_safe(item.get(key))
            for key in (
                "code",
                "severity",
                "field",
                "expected_value",
                "row_contains",
                "label",
                "message",
                "line",
            )
            if item.get(key) not in (None, "")
        }
        if detail:
            details.append(detail)
        if len(details) >= 10:
            break
    return details


def _vl_candidate_summary(document: Document) -> dict:
    source = _vl_candidate_source(document)
    summary = source.get("vl_candidate_summary") if isinstance(source.get("vl_candidate_summary"), dict) else {}
    candidates = _vl_candidates(document)
    issue_codes = _string_list(summary.get("issue_codes"))
    severities: list[str] = []
    for candidate in candidates:
        issue_codes.extend(code for code in _string_list(candidate.get("issue_codes")) if code not in issue_codes)
        severity = candidate.get("validation_severity")
        if severity:
            severities.append(str(severity))
    warning_count = int(summary.get("warning_count") or sum(1 for value in severities if value == "warn"))
    failure_count = int(summary.get("failure_count") or sum(1 for value in severities if value == "fail"))
    gate_decisions = [
        str((candidate.get("promotion_gate") or {}).get("decision"))
        for candidate in candidates
        if isinstance(candidate.get("promotion_gate"), dict) and (candidate.get("promotion_gate") or {}).get("decision")
    ]
    gate_reasons: list[str] = []
    for candidate in candidates:
        gate = candidate.get("promotion_gate") if isinstance(candidate.get("promotion_gate"), dict) else {}
        for reason in gate.get("reasons") or []:
            if reason not in gate_reasons:
                gate_reasons.append(str(reason))
    return {
        "candidate_count": int(summary.get("candidate_count") or len(candidates)),
        "warning_count": warning_count,
        "failure_count": failure_count,
        "issue_codes": issue_codes,
        "parser_integrated": False,
        "parser_evaluated": bool(summary.get("parser_evaluated")) or any(candidate.get("parser_evaluated") for candidate in candidates),
        "parsed_line_item_count": _json_safe(summary.get("parsed_line_item_count")),
        "provider": summary.get("provider") or (candidates[0].get("provider") if candidates else None),
        "provider_available_candidate": bool(summary.get("provider_available_candidate")),
        "gate_decision": gate_decisions[0] if gate_decisions else None,
        "gate_reasons": gate_reasons,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _line_review_flags(document: Document, item_index: int) -> str:
    metadata = document.workflow_metadata or {}
    reasons = metadata.get("normalized_review_issues") or []
    flags: list[str] = []
    if isinstance(reasons, list):
        for reason in reasons:
            if isinstance(reason, dict) and reason.get("item_index") == item_index:
                flags.append(str(reason.get("code") or reason.get("message_ko") or "review_required"))
    return ", ".join(dict.fromkeys(flags))


def _canonical_line_items(document: Document) -> list[dict]:
    return [
        {
            "line_index": index,
            "item_name": item.get("item_name"),
            "document_item_code": item.get("document_item_code") or item.get("item_code") or item.get("source_item_code"),
            "internal_item_code": item.get("internal_item_code"),
            "specification": item.get("specification"),
            "lot_no": item.get("lot_no"),
            "inspection_result": item.get("inspection_result"),
            "quantity": _json_safe(item.get("quantity")),
            "ordered_quantity": _json_safe(item.get("ordered_quantity")),
            "requested_quantity": _json_safe(item.get("requested_quantity")),
            "received_quantity": _json_safe(item.get("received_quantity")),
            "delivered_quantity": _json_safe(item.get("delivered_quantity")),
            "remaining_quantity": _json_safe(item.get("remaining_quantity")),
            "accepted_quantity": _json_safe(item.get("accepted_quantity")),
            "rejected_quantity": _json_safe(item.get("rejected_quantity")),
            "unit": item.get("unit"),
            "unit_price": _json_safe(item.get("unit_price")),
            "supply_amount": _json_safe(item.get("supply_amount")),
            "tax_amount": _json_safe(item.get("tax_amount")),
            "line_total": _json_safe(item.get("line_total")),
            "match_status": item.get("item_master_match_status"),
            "match_confidence": _json_safe(item.get("item_master_match_confidence")),
            "line_review_flags": _line_review_flags(document, index - 1),
        }
        for index, item in enumerate(_exportable_line_items(document), start=1)
    ]


def _exportable_line_items(document: Document) -> list[dict]:
    cleaned: list[dict] = []
    seen: set[tuple] = set()
    for item in _semantic_line_items(document) or document.line_items or []:
        if not isinstance(item, dict):
            continue
        if _line_item_excluded_from_export(document, item):
            continue
        safe_item = _line_item_with_safe_amounts(item)
        key = _line_item_export_key(safe_item)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(safe_item)
    return cleaned


def _line_item_excluded_from_export(document: Document, item: dict) -> bool:
    if _return_credit_purchase_order_export_conflict(document, _export_taxonomy(document)):
        return True
    flags = _line_warning_codes(item)
    if "invalid_line_amount" in flags:
        return True
    doc_type = _doc_type(document)
    tags = {str(tag or "").casefold() for tag in (document.tags or [])}
    if doc_type == "receipt" or "receipt" in tags:
        dirty_receipt_flags = {
            "handwritten_requires_review",
            "receipt_row_review_required",
            "receipt_fragmented_row_reconstructed",
            "receipt_fragmented_row_preserved_for_review",
            "trailing_number_requires_review",
        }
        if flags & dirty_receipt_flags:
            return True
        if _looks_like_dirty_receipt_item_name(item.get("item_name")):
            return True
    return False


def _line_item_with_safe_amounts(item: dict) -> dict:
    safe_item = dict(item)
    flags = _line_warning_codes(item)
    invalid_amount_flags = {
        "invalid_tax_greater_than_total",
        "invalid_tax_greater_than_supply",
        "invalid_supply_greater_than_total",
        "invalid_line_total",
        "untrusted_ocr_amount",
        "hidden_amount_risk",
        "hidden_amount_confirmed_blocked",
    }
    if flags & invalid_amount_flags:
        for field in ("unit_price", "supply_amount", "tax_amount", "line_total"):
            safe_item.pop(field, None)
    return safe_item


def _line_warning_codes(item: dict) -> set[str]:
    codes: set[str] = set()
    for field in ("validation_warnings", "review_flags", "issue_codes"):
        values = item.get(field)
        if isinstance(values, list):
            codes.update(str(value) for value in values if value not in (None, ""))
        elif values:
            codes.add(str(values))
    return codes


def _line_item_export_key(item: dict) -> tuple:
    flags = _line_warning_codes(item)
    amount_sensitive_fields = ()
    if "ai_parsed_document_table_review_required" not in flags:
        amount_sensitive_fields = (
            _norm_export_key(item.get("unit_price")),
            _norm_export_key(item.get("supply_amount")),
            _norm_export_key(item.get("tax_amount")),
            _norm_export_key(item.get("line_total")),
        )
    return (
        _norm_export_key(item.get("item_name")),
        _norm_export_key(item.get("document_item_code") or item.get("item_code") or item.get("source_item_code")),
        _norm_export_key(item.get("specification")),
        _norm_export_key(item.get("quantity")),
        *amount_sensitive_fields,
    )


def _norm_export_key(value: object) -> str:
    numeric = _to_decimal(value)
    if numeric is not None:
        normalized = numeric.normalize()
        text = format(normalized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _return_credit_purchase_order_export_conflict(document: Document, taxonomy: dict | None = None) -> bool:
    if not _return_credit_source_signal(document):
        return False
    taxonomy = taxonomy or _export_taxonomy(document)
    profiles = {str(value or "").casefold() for value in (taxonomy.get("document_profiles") or [])}
    subtype = str(taxonomy.get("document_subtype") or "").casefold()
    profile = str(taxonomy.get("document_profile") or "").casefold()
    if "return_document" in profiles or subtype in {"return_note", "credit_note", "return_credit"} or profile in {"return_document"}:
        return False
    return _doc_type(document) in {"purchase_order", "quotation", "invoice", "transaction_statement"}


def _return_credit_source_signal(document: Document) -> bool:
    workflow = document.workflow_metadata or {}
    ingestion = document.ingestion_metadata or {}
    taxonomy = workflow.get("taxonomy") if isinstance(workflow.get("taxonomy"), dict) else {}
    ai_parsed = workflow.get("ai_parsed_document") if isinstance(workflow.get("ai_parsed_document"), dict) else {}
    values: list[object] = [
        document.original_filename,
        document.title,
        document.category,
        getattr(document.ai_document_type, "value", document.ai_document_type),
        taxonomy.get("document_subtype"),
        taxonomy.get("document_profile"),
        ai_parsed.get("document_type_hint"),
    ]
    values.extend(document.tags or [])
    values.extend(taxonomy.get("document_profiles") or [])
    file_metadata = ingestion.get("file_metadata") if isinstance(ingestion.get("file_metadata"), dict) else {}
    values.extend(file_metadata.get(key) for key in ("filename", "original_filename", "source_filename"))
    source = " ".join(str(value or "") for value in values)
    return bool(re.search(
        r"(?:return[_\s-]*(?:credit|note)|credit[_\s-]*note|return[_\s-]*document|반품\s*/?\s*크레딧|크레딧\s*메모|반품\s*(?:전표|요청서|문서|메모))",
        source,
        flags=re.IGNORECASE,
    ))


def _looks_like_dirty_receipt_item_name(value: object) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text).casefold()
    if re.fullmatch(r"\d+(?:ea|kg|box|개|봉|팩|세트)?x?", compact):
        return True
    if re.fullmatch(r"[s5]ea[x×]?", compact):
        return True
    return bool(re.search(r"(공급기액|공급가액|부가세|세액|합계|총액|결제금액|tax|vat|total)", text, flags=re.IGNORECASE))


def _workflow_list(document: Document, key: str) -> list:
    workflow = document.workflow_metadata or {}
    value = workflow.get(key)
    return value if isinstance(value, list) else []


def _workflow_dict(document: Document, key: str) -> dict:
    workflow = document.workflow_metadata or {}
    value = workflow.get(key)
    return value if isinstance(value, dict) else {}


def _semantic_mapping(document: Document) -> dict:
    if document.review_required:
        return {}
    workflow = document.workflow_metadata or {}
    value = workflow.get("confirmed_semantic_mapping")
    if not isinstance(value, dict):
        return {}
    confirmed = workflow.get("confirmed_raw_data") if isinstance(workflow.get("confirmed_raw_data"), dict) else {}
    confirmed_at = _parse_datetime(confirmed.get("confirmed_at"))
    mapping_at = _parse_datetime(value.get("created_at"))
    if confirmed_at is None or mapping_at is None or mapping_at < confirmed_at:
        return {}
    return value


def _semantic_fields(document: Document) -> dict:
    mapping = _semantic_mapping(document)
    value = mapping.get("fields")
    return value if isinstance(value, dict) else {}


def _semantic_line_items(document: Document) -> list[dict]:
    mapping = _semantic_mapping(document)
    values = mapping.get("line_items")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _semantic_total(document: Document) -> object | None:
    fields = _semantic_fields(document)
    for key in ("document_total", "payment_total", "estimated_total", "total_usd", "krw_converted"):
        if fields.get(key) not in (None, "", []):
            return fields.get(key)
    return None


def _related_document_number(document: Document) -> str | None:
    metadata = document.workflow_metadata or {}
    business = metadata.get("business_fields") if isinstance(metadata.get("business_fields"), dict) else {}
    for key in ("related_document_number", "related_doc", "original_document_number", "source_document_number"):
        value = business.get(key) or metadata.get(key)
        if value:
            return str(value)
    return None


def _first_bool(*values: object) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _json_safe(value: object) -> object:
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe(inner) for inner in value]
    return value


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _excel_sheet_name(value: str) -> str:
    cleaned = re.sub(r"[\[\]\*:/\\?]", " ", value).strip() or "미분류"
    return cleaned[:31]


def _minimal_xlsx(rows: list[dict], sheet_mode: str = "combined") -> bytes:
    sheets: list[tuple[str, list[dict]]] = []
    if sheet_mode == "party_tabs" and rows:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("_party_tab") or row.get("거래처 탭") or "미분류"), []).append(row)
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
    visible_rows = [{key: value for key, value in row.items() if key not in {"거래처 탭", "_party_tab"}} for row in rows]
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
