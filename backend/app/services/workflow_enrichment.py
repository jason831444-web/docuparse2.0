import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.document import Document, DocumentType
from app.services.category_interpretation import CategoryInterpretation
from app.services.document_taxonomy import DocumentTaxonomy, DocumentTaxonomyService


@dataclass
class WorkflowEnrichment:
    workflow_summary: str | None = None
    summary_short: str | None = None
    summary_detailed: str | None = None
    action_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    key_dates: list[str] = field(default_factory=list)
    urgency_level: str = "low"
    follow_up_required: bool = False
    workflow_metadata: dict[str, Any] = field(default_factory=dict)


class DocumentWorkflowEnrichmentService:
    """Post-extraction workflow layer.

    This turns extracted document data into conservative, type-aware assistance.
    It does not replace extraction or editing; it adds workflow context on top.
    """

    def __init__(self) -> None:
        self.taxonomy = DocumentTaxonomyService()

    def enrich(
        self,
        document: Document,
        normalized_text: str | None = None,
        interpretation: CategoryInterpretation | None = None,
    ) -> WorkflowEnrichment:
        text = self._classification_text(normalized_text, document.raw_text)
        mode = self._workflow_mode(document, interpretation)
        profile = interpretation.profile if interpretation and interpretation.profile else self._content_profile(document, text, mode)
        if self._is_manufacturing_document(document, profile):
            return self._manufacturing_business_data(document, text, interpretation)
        if document.document_type == DocumentType.receipt:
            result = self._receipt(document, text, mode)
        elif profile in {"syllabus", "course_guide"}:
            result = self._syllabus(document, text, mode)
        elif profile == "resume_profile":
            result = self._resume_profile(document, text, mode)
        elif profile in {"presentation_guide", "speaking_notes"} or document.document_type == DocumentType.presentation:
            result = self._presentation_guide(document, text, mode)
        elif profile == "installation_guide":
            result = self._installation_guide(document, text, mode)
        elif profile == "implementation_schedule":
            result = self._implementation_schedule(document, text, mode)
        elif profile == "invoice":
            result = self._invoice(document, text, mode)
        elif profile == "meeting_notice":
            result = self._meeting_notice(document, text, mode)
        elif profile == "profile_record":
            result = self._profile_record(document, text, mode)
        elif mode in {"utilities", "utility_bill"}:
            result = self._utilities(document, text, mode)
        elif mode in {"education", "notice"} or document.document_type == DocumentType.notice:
            result = self._education_notice(document, text, mode)
        elif mode == "health":
            result = self._health(document, text, mode)
        elif mode == "office":
            result = self._office(document, text, mode)
        elif mode in {"food_drink", "groceries", "retail", "transport"}:
            result = self._spend_category(document, text, mode)
        else:
            result = self._generic(document, text, mode)

        if interpretation:
            result = self._apply_interpretation_hints(result, interpretation)
        result.action_items = self._finalize_action_items(result.action_items, text, mode, profile)
        result.warnings = self._dedupe(result.warnings)
        result.key_dates = self._normalize_date_list(result.key_dates)
        summary_short, summary_detailed = self._finalize_summaries(document, text, mode, profile, result, interpretation)
        result.summary_short = summary_short
        result.summary_detailed = summary_detailed
        if summary_detailed:
            result.workflow_summary = summary_detailed
        result.workflow_metadata["summaries"] = {
            "short": summary_short,
            "detailed": summary_detailed,
        }
        if interpretation and interpretation.workflow_hints.get("review_focus"):
            result.workflow_metadata["review_focus"] = self._string_list(interpretation.workflow_hints.get("review_focus"))
        result.workflow_metadata["workflow_mode"] = mode
        result.workflow_metadata["content_profile"] = profile
        result.workflow_metadata["source"] = "deterministic_workflow_enrichment"
        if interpretation:
            result.workflow_metadata["category_interpretation"] = {
                "category": interpretation.category,
                "profile": interpretation.profile,
                "subtype": interpretation.subtype,
                "title_hint": interpretation.title_hint,
                "summary_hint": interpretation.summary_hint,
                "key_fields": interpretation.key_fields,
                "warnings": interpretation.warnings,
                "workflow_hints": interpretation.workflow_hints,
                "reasons": interpretation.reasons,
                "confidence": interpretation.confidence,
                "provider": interpretation.provider,
                "provider_chain": interpretation.provider_chain,
                "refinement_status": interpretation.refinement_status,
                "diagnostics": interpretation.diagnostics,
                "ai_assisted": interpretation.ai_assisted,
            }
        return result

    def _classification_text(self, normalized_text: str | None, raw_text: str | None) -> str:
        normalized = str(normalized_text or "").strip()
        raw = str(raw_text or "").strip()
        if normalized and raw and normalized != raw:
            return f"{normalized}\n{raw}"
        return normalized or raw

    def _is_manufacturing_document(self, document: Document, profile: str | None) -> bool:
        manufacturing_profiles = {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
            "inspection_report",
            "contract",
            "general_document",
        }
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        category = str(getattr(document, "category", "") or "")
        tags = " ".join(str(tag or "") for tag in (getattr(document, "tags", None) or []))
        has_manufacturing_taxonomy_signal = bool(
            re.search(r"\b(?:internal_transfer|return_note|credit_note)\b", f"{category} {tags}", flags=re.IGNORECASE)
        )
        return doc_type in manufacturing_profiles or (profile or "") in manufacturing_profiles or has_manufacturing_taxonomy_signal

    def _manufacturing_business_data(
        self,
        document: Document,
        text: str,
        interpretation: CategoryInterpretation | None,
    ) -> WorkflowEnrichment:
        doc_type = getattr(document.document_type, "value", str(document.document_type or "general_document"))
        taxonomy = self.taxonomy.classify(document, text)
        label = self._manufacturing_label(doc_type)
        vendor = document.vendor_name or document.merchant_name or "공급업체 미확인"
        customer = document.customer_name or "고객사 미확인"
        number = document.document_number or "문서번호 미확인"
        issue_date = self._korean_date(document.issue_date or document.extracted_date)
        business_fields = self._manufacturing_business_fields(document, text, doc_type)
        line_item_count = len(document.line_items or [])
        total = self._korean_money(document.extracted_amount, document.currency or "KRW")
        review_reasons = self._manufacturing_review_reasons(document, business_fields, taxonomy)
        review_issues = self._normalized_review_issues(document, review_reasons)
        warnings = self._dedupe([issue["message_ko"] for issue in review_issues if self._is_blocking_review_issue(issue)])
        review_required = any(self._is_blocking_review_issue(issue) for issue in review_issues)
        export_ready = not review_required
        summary = self._manufacturing_summary(
            doc_type,
            label,
            vendor,
            customer,
            number,
            business_fields,
            line_item_count,
            total,
            taxonomy,
        )
        action_items = self._manufacturing_action_items(doc_type, warnings)
        if warnings:
            action_items.insert(0, "검토 필요 항목을 수정한 뒤 확정 처리하세요.")
        key_dates = self._manufacturing_key_dates(doc_type, issue_date, business_fields)
        metadata = {
            "business_summary": summary,
            "business_fields": business_fields,
            "action_items": action_items,
            "validation_warnings": warnings,
            "normalized_review_issues": review_issues,
            "review_reasons": review_reasons,
            "missing_required_fields": [issue["field"] for issue in review_issues if str(issue.get("code", "")).startswith("missing_")],
            "export_ready": export_ready,
            "review_required": review_required,
            "line_item_count": line_item_count,
            "low_confidence_fields": list(document.low_confidence_fields or []),
            "workflow_mode": doc_type,
            "content_profile": doc_type,
            "taxonomy": taxonomy.to_metadata(),
            "document_subtype": taxonomy.document_subtype,
            "document_profile": taxonomy.document_profile,
            "document_profiles": taxonomy.document_profiles,
            "layout_profile": taxonomy.layout_profile,
            "source": "deterministic_manufacturing_business_data",
            "summaries": {
                "short": summary,
                "detailed": summary,
            },
        }
        if interpretation:
            metadata["category_interpretation"] = {
                "category": interpretation.category,
                "profile": interpretation.profile,
                "subtype": interpretation.subtype,
                "title_hint": interpretation.title_hint,
                "summary_hint": interpretation.summary_hint,
                "key_fields": interpretation.key_fields,
                "warnings": interpretation.warnings,
                "workflow_hints": interpretation.workflow_hints,
                "reasons": interpretation.reasons,
                "confidence": interpretation.confidence,
                "provider": interpretation.provider,
                "provider_chain": interpretation.provider_chain,
                "refinement_status": interpretation.refinement_status,
                "diagnostics": interpretation.diagnostics,
                "ai_assisted": interpretation.ai_assisted,
            }
        return WorkflowEnrichment(
            workflow_summary=summary,
            summary_short=summary,
            summary_detailed=summary,
            action_items=action_items,
            warnings=warnings,
            key_dates=key_dates,
            urgency_level="medium" if review_required else "low",
            follow_up_required=review_required,
            workflow_metadata=metadata,
        )

    def _manufacturing_business_fields(self, document: Document, text: str, doc_type: str) -> dict[str, str | list[str]]:
        fields: dict[str, str | list[str]] = {}
        related_document_number = self._extract_labeled_text_multiline(text, ["관련납품서", "관련 원문서", "관련원문서", "관련 원 납품서", "관련원납품서", "관련 문서번호", "관련문서번호", "원문서", "원 문서", "원 납품서", "원납품서", "original document", "related delivery note", "related document", "source document"])
        if related_document_number:
            fields["related_document_number"] = related_document_number
        if doc_type == "purchase_order":
            fields["due_date"] = self._date_iso(document.due_date)
        elif doc_type == "quotation":
            fields["quotation_date"] = self._date_iso(self._extract_labeled_date(text, ["견적일", "quotation date"]) or document.issue_date or document.extracted_date)
            fields["valid_until"] = self._date_iso(self._extract_labeled_date(text, ["유효기간", "견적유효기간", "valid until", "expiration date", "expires"]) or document.due_date)
            fields["delivery_terms"] = self._extract_labeled_text(text, ["납기조건", "delivery terms"])
            fields["payment_terms"] = self._extract_labeled_text(text, ["결제조건", "payment terms"])
        elif doc_type == "transaction_statement":
            fields["transaction_date"] = self._date_iso(self._extract_labeled_date(text, ["거래일자", "거래일", "transaction date"]) or document.issue_date or document.extracted_date)
            if re.search(r"(전월\s*이월|총\s*미수금|미수\s*잔액|전월잔액|금월\s*합계|입금액)", text):
                fields["statement_balance_summary_present"] = "true"
        elif doc_type == "delivery_note":
            fields["delivery_date"] = self._date_iso(self._extract_labeled_date(text, ["납품일", "납품일자", "delivery date"]) or document.due_date)
            fields["receiving_location"] = self._extract_labeled_text(text, ["입고장소", "납품장소", "receiving location"])
            fields["receiver_name"] = self._extract_labeled_text(text, ["수령자", "인수자", "receiver"])
        elif doc_type == "invoice":
            fields["payment_due_date"] = self._date_iso(document.due_date or self._extract_labeled_date(text, ["지급기한", "결제기한", "payment due date", "due date"]))
            supplier_brn = self._extract_labeled_text(text, ["공급자 사업자등록번호", "공급업체 사업자등록번호", "supplier business registration number"])
            customer_brn = self._extract_labeled_text(text, ["공급받는자 사업자등록번호", "고객사 사업자등록번호", "customer business registration number"])
            fields["supplier_business_registration_number"] = supplier_brn
            fields["customer_business_registration_number"] = customer_brn
            fields["business_registration_numbers"] = re.findall(r"\b\d{3}-\d{2}-\d{5}\b", text)
        return {key: value for key, value in fields.items() if value not in (None, "", [])}

    def _manufacturing_review_reasons(self, document: Document, business_fields: dict[str, str | list[str]], taxonomy: DocumentTaxonomy | None = None) -> list[dict[str, Any]]:
        reasons: list[dict[str, Any]] = []
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        taxonomy = taxonomy or self.taxonomy.classify(document, "")
        profile_values = {
            str(value)
            for value in [
                taxonomy.document_profile,
                *(taxonomy.document_profiles or []),
            ]
            if value
        }
        no_price_quantity_doc = self._is_no_price_quantity_document(document) or taxonomy.amount_required is False
        option_quote_doc = self._is_option_quote_document(document, business_fields, taxonomy)
        party_optional_doc = no_price_quantity_doc or taxonomy.party_required is False
        document_has_item_code_evidence = self._document_has_item_code_evidence(document)
        if "return_document" in set(taxonomy.document_profiles or []):
            reasons.append(self._review_reason(
                "amount_direction_requires_review",
                "반품/차감 문서는 금액의 차감 방향과 원문서 반영 방식을 확인해야 합니다.",
                "total_amount",
            ))
            if not business_fields.get("related_document_number"):
                reasons.append(self._review_reason(
                    "related_document_missing",
                    "반품/차감 문서의 관련 원문서 번호를 확인해야 합니다.",
                    "related_document_number",
                    severity="info",
                ))
        if not party_optional_doc and not (document.vendor_name or document.merchant_name):
            reasons.append(self._review_reason("missing_vendor_name", "공급업체가 추출되지 않았습니다.", "vendor_name"))
        if not party_optional_doc and not document.customer_name:
            reasons.append(self._review_reason("missing_customer_name", "고객사가 추출되지 않았습니다.", "customer_name"))
        if not document.document_number:
            reasons.append(self._review_reason("missing_document_number", f"{self._manufacturing_number_label(doc_type)} 미확인", "document_number"))
        if not (document.issue_date or document.extracted_date):
            reasons.append(self._review_reason("missing_issue_date", f"{self._manufacturing_issue_label(doc_type)} 미확인", "issue_date"))
        if doc_type == "purchase_order" and not document.due_date:
            reasons.append(self._review_reason("missing_due_date", "납기일 미확인", "due_date"))
        if doc_type == "invoice" and "tax_document" not in profile_values and not business_fields.get("payment_due_date"):
            reasons.append(self._review_reason("missing_payment_due_date", "지급기한 미확인", "due_date"))
        if doc_type == "transaction_statement" and business_fields.get("statement_balance_summary_present"):
            reasons.append(self._review_reason(
                "statement_balance_summary_requires_review",
                "거래명세서에 전월이월/입금액/미수잔액 등 정산 요약이 포함되어 있어 품목 합계와 잔액 구분을 확인해야 합니다.",
                "statement_summary",
            ))
        if option_quote_doc and document.extracted_amount is None:
            reasons.append(self._review_reason(
                "option_quote_total_requires_selection",
                "옵션 견적서: 최종 합계는 옵션 선택 후 확정 필요",
                "total_amount",
            ))
        if not document.line_items and not self._line_items_optional_document(document, taxonomy):
            reasons.append(self._review_reason("missing_line_items", "품목 정보가 추출되지 않았습니다.", "line_items"))
        for index, item in enumerate(document.line_items or [], start=1):
            if item.get("item_name") in (None, "", []):
                reasons.append(self._review_reason("missing_item_name", f"{index}번째 품목의 품목명이 비어 있습니다.", "line_items.item_name", index - 1))
            if item.get("quantity") in (None, "", []):
                reasons.append(self._review_reason("missing_quantity", f"{index}번째 품목의 수량이 비어 있습니다.", "line_items.quantity", index - 1))
            if doc_type == "inspection_report" and not any(
                item.get(field) not in (None, "", [])
                for field in ["received_quantity", "accepted_quantity", "rejected_quantity"]
            ):
                reasons.append(self._review_reason(
                    "inspection_quantities_require_review",
                    f"{index}번째 검사 품목의 입고/합격/불량 수량을 확인해야 합니다.",
                    "line_items.received_quantity",
                    index - 1,
                ))
            if not no_price_quantity_doc and not option_quote_doc and doc_type != "delivery_note" and item.get("unit_price") in (None, "", []) and item.get("line_total") in (None, "", []):
                reasons.append(self._review_reason("missing_price_or_total", f"{index}번째 품목의 단가 또는 합계금액을 확인해야 합니다.", "line_items.unit_price", index - 1))
            for warning in item.get("validation_warnings") or []:
                if self._suppress_user_facing_amount_warning(warning, no_price_quantity_doc=no_price_quantity_doc, option_quote_doc=option_quote_doc):
                    continue
                if warning == "invalid_tax_greater_than_total":
                    reasons.append(self._review_reason("invalid_line_amount", f"{index}번째 품목의 세액이 합계금액보다 큽니다.", "line_items.tax_amount", index - 1))
                elif warning == "invalid_tax_greater_than_supply":
                    reasons.append(self._review_reason("invalid_line_amount", f"{index}번째 품목의 세액이 공급가액보다 큽니다.", "line_items.tax_amount", index - 1))
                elif warning == "invalid_supply_greater_than_total":
                    reasons.append(self._review_reason("invalid_line_amount", f"{index}번째 품목의 공급가액이 합계금액보다 큽니다.", "line_items.supply_amount", index - 1))
                elif warning == "invalid_line_total":
                    reasons.append(self._review_reason("invalid_line_amount", f"{index}번째 품목의 공급가액, 세액, 합계금액 계산이 맞지 않습니다.", "line_items.line_total", index - 1))
                elif warning == "item_code_name_conflict":
                    reasons.append(self._review_reason("item_code_name_conflict", f"{index}번째 품목명과 품목코드 매칭이 충돌합니다.", "line_items.internal_item_code", index - 1))
            internal_code = item.get("internal_item_code")
            if item.get("item_code") in (None, "", []):
                severity = "warning" if document_has_item_code_evidence and internal_code in (None, "", []) and not no_price_quantity_doc else "info"
                reasons.append(self._review_reason("missing_document_item_code", f"{index}번째 품목 문서 품목코드 미확인", "line_items.item_code", index - 1, severity=severity))
            match_status = item.get("item_master_match_status")
            if match_status == "ambiguous":
                reasons.append(self._review_reason("internal_item_ambiguous", f"{index}번째 품목 내부 품목코드 후보 확인 필요", "line_items.internal_item_code", index - 1))
            elif match_status == "unmatched":
                severity = "warning" if document_has_item_code_evidence and item.get("item_code") not in (None, "", []) and not no_price_quantity_doc else "info"
                reasons.append(self._review_reason("internal_item_unmatched", f"{index}번째 품목 내부 품목코드 미매칭", "line_items.internal_item_code", index - 1, severity=severity))
        if any(
            item.get("item_master_match_status") == "skipped_no_item_master" and item.get("item_code") in (None, "", [])
            for item in document.line_items or []
        ):
            reasons.append(self._review_reason("item_matching_skipped", "내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.", "line_items.internal_item_code", severity="info"))
        if self._manufacturing_total_mismatch(document):
            line_total_sum = self._line_items_total(document)
            document_total = document.extracted_amount
            difference = abs(document_total - line_total_sum) if document_total is not None and line_total_sum is not None else None
            currency = document.currency or "KRW"
            reasons.append(self._review_reason(
                "amount_mismatch",
                self._amount_mismatch_message(document_total, line_total_sum, difference, currency),
                "total_amount",
                expected=line_total_sum,
                actual=document_total,
                extra={
                    "document_total": str(document_total) if document_total is not None else None,
                    "line_total_sum": str(line_total_sum) if line_total_sum is not None else None,
                    "difference": str(difference) if difference is not None else None,
                    "currency": currency,
                },
            ))
        elif any(reason.get("code") == "invalid_line_amount" for reason in reasons):
            reasons.append(self._review_reason(
                "amount_mismatch",
                "품목 금액 계산 오류가 있어 문서 합계금액도 함께 확인해야 합니다.",
                "total_amount",
            ))
        return self._dedupe_review_reasons(reasons)

    def _is_option_quote_document(
        self,
        document: Document,
        business_fields: dict[str, str | list[str]],
        taxonomy: DocumentTaxonomy,
    ) -> bool:
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        metadata = document.workflow_metadata or {}
        profile_values = {
            str(value)
            for value in [
                taxonomy.document_profile,
                metadata.get("document_profile"),
                (metadata.get("taxonomy") or {}).get("document_profile") if isinstance(metadata.get("taxonomy"), dict) else None,
                *list(taxonomy.document_profiles or []),
                *list(metadata.get("document_profiles") or []),
                *((metadata.get("taxonomy") or {}).get("document_profiles") or [] if isinstance(metadata.get("taxonomy"), dict) else []),
            ]
            if value
        }
        text = " ".join(
            str(value or "")
            for value in [
                document.document_number,
                document.original_filename,
                document.category,
                " ".join(document.tags or []),
                " ".join(str(value or "") for value in business_fields.values()),
            ]
        )
        return (
            doc_type == "quotation"
            and (
                "option_quote_document" in profile_values
                or bool(re.search(r"(옵션|option).*?(선택|확정|합산하면\s*안)|선택\s*후\s*확정", text, flags=re.IGNORECASE | re.DOTALL))
            )
        )

    def _line_items_optional_document(self, document: Document, taxonomy: DocumentTaxonomy) -> bool:
        values = {
            str(value).casefold()
            for value in [
                document.category,
                *(document.tags or []),
                taxonomy.document_subtype,
                taxonomy.document_profile,
                *(taxonomy.document_profiles or []),
            ]
            if value
        }
        if values.intersection({"pos_daily_settlement", "settlement_summary", "daily_sales_settlement"}):
            return True
        text = " ".join(values)
        return bool(re.search(r"(pos[_ -]?daily[_ -]?settlement|settlement[_ -]?summary)", text, flags=re.IGNORECASE))

    def _document_has_item_code_evidence(self, document: Document) -> bool:
        for item in document.line_items or []:
            if not isinstance(item, dict):
                continue
            if item.get("item_code") not in (None, "", []) or item.get("document_item_code") not in (None, "", []) or item.get("source_item_code") not in (None, "", []):
                return True
        text = str(document.raw_text or "")
        if re.search(r"(?:문서\s*)?품목\s*코드|item\s*code|part\s*(?:no|number)|부품\s*번호", text, flags=re.IGNORECASE):
            return True
        return False

    def _suppress_user_facing_amount_warning(
        self,
        warning: object,
        *,
        no_price_quantity_doc: bool,
        option_quote_doc: bool,
    ) -> bool:
        if not (no_price_quantity_doc or option_quote_doc):
            return False
        return str(warning) in {
            "invalid_tax_greater_than_total",
            "invalid_tax_greater_than_supply",
            "invalid_supply_greater_than_total",
            "invalid_line_total",
            "missing_price_or_total",
            "missing_line_amount",
            "amount_mismatch",
        }

    def _is_internal_transfer_document(self, document: Document) -> bool:
        values = [
            getattr(document, "category", None),
            *(getattr(document, "tags", None) or []),
            getattr(document, "document_number", None),
        ]
        text = " ".join(str(value or "") for value in values)
        return bool(re.search(r"internal[_ -]?transfer|\bTRF[-_ ]?\d{4}|사업장|자재\s*이동", text, flags=re.IGNORECASE))

    def _is_no_price_quantity_document(self, document: Document) -> bool:
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        if doc_type in {"delivery_note", "inspection_report"}:
            return True
        if self._is_internal_transfer_document(document):
            return True
        if document.extracted_amount is not None or document.subtotal is not None or document.tax is not None:
            return False
        return bool(document.line_items) and any(
            item.get("quantity") not in (None, "", []) or item.get("item_code") not in (None, "", []) or item.get("document_item_code") not in (None, "", [])
            for item in document.line_items or []
        )

    def _normalized_review_issues(self, document: Document, review_reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        issues = self._dedupe_review_reasons(list(review_reasons), by_message=True)
        known_messages = {self._normalize_review_message(str(issue.get("message_ko"))) for issue in issues if issue.get("message_ko")}
        metadata = document.workflow_metadata or {}
        for message in self._string_list(metadata.get("validation_warnings")):
            normalized_message = self._normalize_review_message(message)
            if self._is_legacy_amount_mismatch_message(message) and any(issue.get("code") == "amount_mismatch" for issue in issues):
                continue
            if normalized_message not in known_messages:
                issues.append(self._review_reason("validation_warning", message, "document"))
                known_messages.add(normalized_message)
        for field in list(document.low_confidence_fields or []):
            code = field.split(":", 1)[0]
            item_index = self._low_confidence_item_index(field)
            if code == "amount_mismatch" and any(issue.get("code") == "amount_mismatch" for issue in issues):
                continue
            if code == "missing_item_code":
                if any(issue.get("code") == "missing_document_item_code" and issue.get("item_index") == item_index for issue in issues):
                    continue
                field = field.replace("missing_item_code", "missing_document_item_code", 1)
                code = "missing_document_item_code"
            if code == "item_master_match_required" and any(
                issue.get("code") in {"internal_item_ambiguous", "internal_item_unmatched"} and issue.get("item_index") == item_index
                for issue in issues
            ):
                continue
            message = self._low_confidence_message(field)
            normalized_message = self._normalize_review_message(message)
            if normalized_message not in known_messages:
                issues.append(self._review_reason(code, message, self._low_confidence_field(field), item_index=item_index))
                known_messages.add(normalized_message)
        return self._collapse_repeated_item_issues(self._dedupe_review_reasons(issues, by_message=True))

    def _collapse_repeated_item_issues(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collapsible = {
            "missing_quantity": ("여러 품목의 수량 확인이 필요합니다.", "line_items.quantity"),
            "missing_price_or_total": ("여러 품목의 단가 또는 합계금액 확인이 필요합니다.", "line_items.unit_price"),
            "missing_document_item_code": ("여러 품목의 문서 품목코드 확인이 필요합니다.", "line_items.item_code"),
        }
        counts: dict[str, int] = {}
        for issue in issues:
            code = str(issue.get("code") or "")
            if code in collapsible and issue.get("item_index") is not None:
                counts[code] = counts.get(code, 0) + 1
        collapsed_codes = {code for code, count in counts.items() if count >= 4}
        if not collapsed_codes:
            return issues
        collapsed: list[dict[str, Any]] = []
        emitted: set[str] = set()
        for issue in issues:
            code = str(issue.get("code") or "")
            if code not in collapsed_codes or issue.get("item_index") is None:
                collapsed.append(issue)
                continue
            if code in emitted:
                continue
            message, field = collapsible[code]
            collapsed.append(self._review_reason(code, f"{message} ({counts[code]}건)", field))
            emitted.add(code)
        return self._dedupe_review_reasons(collapsed, by_message=True)

    def _is_legacy_amount_mismatch_message(self, message: str) -> bool:
        return "문서 합계금액" in message and "품목 합계금액" in message and "일치하지 않습니다" in message

    def _low_confidence_item_index(self, value: str) -> int | None:
        _, _, item_token = value.partition(":")
        item_number = item_token.replace("item_", "")
        return int(item_number) - 1 if item_number.isdigit() else None

    def _low_confidence_message(self, value: str) -> str:
        code, _, item_token = value.partition(":")
        item_number = item_token.replace("item_", "")
        prefix = f"{item_number}번째 품목 " if item_number.isdigit() else ""
        return {
            "missing_line_items": "품목 정보가 추출되지 않았습니다.",
            "missing_item_name": f"{prefix}품목명이 비어 있습니다.",
            "missing_quantity": f"{prefix}수량이 비어 있습니다.",
            "missing_price_or_total": f"{prefix}단가 또는 합계금액을 확인해야 합니다.",
            "missing_item_code": f"{prefix}품목코드 미확인",
            "missing_document_item_code": f"{prefix}문서 품목코드 미확인",
            "item_master_match_required": f"{prefix}내부 품목코드 후보 확인 필요" if prefix else "내부 품목 장부 매칭 필요",
            "internal_item_ambiguous": f"{prefix}내부 품목코드 후보 확인 필요" if prefix else "내부 품목코드 후보 확인 필요",
            "item_master_unmatched": f"{prefix}내부 품목코드 미매칭" if prefix else "내부 품목코드 미매칭",
            "internal_item_unmatched": f"{prefix}내부 품목코드 미매칭" if prefix else "내부 품목코드 미매칭",
            "item_matching_skipped": "내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.",
            "amount_mismatch": "문서 합계금액과 품목 합계금액이 일치하지 않습니다.",
            "missing_document_number": "문서번호 미확인",
            "missing_issue_date": "날짜 미확인",
            "missing_due_date": "납기일 미확인",
            "missing_payment_due_date": "지급기한 미확인",
        }.get(code, value)

    def _low_confidence_field(self, value: str) -> str:
        code = value.split(":", 1)[0]
        return {
            "missing_quantity": "line_items.quantity",
            "missing_item_name": "line_items.item_name",
            "missing_item_code": "line_items.item_code",
            "missing_document_item_code": "line_items.item_code",
            "item_master_match_required": "line_items.internal_item_code",
            "internal_item_ambiguous": "line_items.internal_item_code",
            "item_master_unmatched": "line_items.internal_item_code",
            "internal_item_unmatched": "line_items.internal_item_code",
            "item_matching_skipped": "line_items.internal_item_code",
            "missing_price_or_total": "line_items.unit_price",
            "amount_mismatch": "total_amount",
        }.get(code, code)

    def _review_reason(
        self,
        code: str,
        message_ko: str,
        field: str,
        item_index: int | None = None,
        severity: str = "warning",
        expected: Decimal | None = None,
        actual: Decimal | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reason: dict[str, Any] = {
            "code": code,
            "message_ko": message_ko,
            "field": field,
            "severity": severity,
        }
        if item_index is not None:
            reason["item_index"] = item_index
        if expected is not None:
            reason["expected"] = str(expected)
        if actual is not None:
            reason["actual"] = str(actual)
        if extra:
            reason.update({key: value for key, value in extra.items() if value is not None})
        return reason

    def _is_blocking_review_issue(self, issue: dict[str, Any]) -> bool:
        if issue.get("severity") in {"info", "low"}:
            return False
        return issue.get("code") in {
            "missing_vendor_name",
            "missing_customer_name",
            "missing_document_number",
            "missing_issue_date",
            "missing_due_date",
            "missing_payment_due_date",
            "missing_line_items",
            "missing_item_name",
            "missing_quantity",
            "missing_price_or_total",
            "amount_mismatch",
            "invalid_line_amount",
            "option_quote_total_requires_selection",
            "item_code_name_conflict",
            "amount_direction_requires_review",
            "statement_balance_summary_requires_review",
            "inspection_quantities_require_review",
            "internal_item_unmatched",
            "internal_item_ambiguous",
            "item_matching_skipped",
        }

    def _dedupe_review_reasons(self, reasons: list[dict[str, Any]], by_message: bool = False) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        seen_messages: set[str] = set()
        for reason in reasons:
            message_key = self._normalize_review_message(str(reason.get("message_ko") or ""))
            if by_message and message_key:
                if message_key in seen_messages:
                    continue
                seen_messages.add(message_key)
            key = (
                reason.get("code"),
                reason.get("field"),
                reason.get("item_index"),
                message_key if reason.get("code") in {None, "", "validation_warning", "review_required"} else "",
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(reason)
        return deduped

    def _normalize_review_message(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _manufacturing_total_mismatch(self, document: Document) -> bool:
        if document.extracted_amount is None or not document.line_items:
            return False
        line_total = Decimal("0")
        found = False
        for item in document.line_items:
            value = item.get("line_total")
            if value in (None, "", []):
                continue
            try:
                line_total += Decimal(str(value).replace(",", ""))
                found = True
            except Exception:
                return True
        if not found:
            return False
        tolerance = self._amount_tolerance(document.currency)
        return abs(document.extracted_amount - line_total) > tolerance

    def _amount_tolerance(self, currency: str | None) -> Decimal:
        normalized = (currency or "KRW").upper()
        if normalized == "USD":
            return Decimal("0.05")
        if normalized == "KRW":
            return Decimal("10")
        return Decimal("1")

    def _line_items_total(self, document: Document) -> Decimal | None:
        total = Decimal("0")
        found = False
        for item in document.line_items or []:
            value = item.get("line_total")
            if value in (None, "", []):
                continue
            try:
                total += Decimal(str(value).replace(",", ""))
                found = True
            except Exception:
                return None
        return total if found else None

    def _manufacturing_summary(
        self,
        doc_type: str,
        label: str,
        vendor: str,
        customer: str,
        number: str,
        fields: dict[str, str | list[str]],
        line_item_count: int,
        total: str | None,
        taxonomy: DocumentTaxonomy | None = None,
    ) -> str:
        profiles = set(taxonomy.document_profiles or []) if taxonomy else set()
        subtype = taxonomy.document_subtype if taxonomy else None
        amount_not_required = bool(taxonomy and taxonomy.amount_required is False) or "no_price_document" in profiles
        number_label = self._manufacturing_number_label(doc_type)
        detail = ""
        if subtype == "internal_transfer" or "inventory_movement_document" in profiles:
            item_summary = f"품목 {line_item_count}건이 추출되었습니다. 금액/통화 정보 없이 수량 중심 문서로 처리되었습니다."
            return (
                f"이 문서는 내부 사업장 또는 창고 간 자재 이동 문서입니다. "
                f"{number_label}는 {number}이며, "
                f"{item_summary}"
            )
        if doc_type == "quotation":
            valid_until = self._korean_date_from_iso(fields.get("valid_until"))
            terms = []
            if fields.get("delivery_terms"):
                terms.append(f"납기조건은 {fields['delivery_terms']}")
            if fields.get("payment_terms"):
                terms.append(f"결제조건은 {fields['payment_terms']}")
            detail = f"유효기간은 {valid_until or '미확인'}이며, " + (", ".join(terms) + "입니다. " if terms else "")
        elif doc_type == "transaction_statement":
            transaction_date = self._korean_date_from_iso(fields.get("transaction_date"))
            detail = f"거래일자는 {transaction_date or '미확인'}입니다. "
        elif doc_type == "delivery_note":
            delivery_date = self._korean_date_from_iso(fields.get("delivery_date"))
            location = fields.get("receiving_location") or "입고장소 미확인"
            receiver = fields.get("receiver_name") or "수령자 미확인"
            detail = f"납품일은 {delivery_date or '미확인'}이며, 입고장소는 {location}, 수령자는 {receiver}입니다. "
        elif doc_type == "invoice":
            payment_due = self._korean_date_from_iso(fields.get("payment_due_date"))
            detail = f"지급기한은 {payment_due or '미확인'}입니다. "
        else:
            due_date = self._korean_date_from_iso(fields.get("due_date"))
            detail = f"납기일은 {due_date or '미확인'}입니다. "
        if amount_not_required and not total:
            if doc_type == "inspection_report":
                item_summary = (
                    f"품목 {line_item_count}건이 추출되었습니다. "
                    "이 검사성적서는 금액 정보 없이 입고/합격/불량 수량 확인용 문서로 처리되었습니다."
                )
            elif doc_type == "delivery_note":
                item_summary = (
                    f"품목 {line_item_count}건이 추출되었습니다. "
                    "이 납품서는 금액 정보 없이 수량 확인용 문서로 처리되었습니다."
                )
            else:
                item_summary = (
                    f"품목 {line_item_count}건이 추출되었습니다. "
                    "금액 정보 없이 수량 중심 문서로 처리되었습니다."
                )
        else:
            item_summary = f"품목 {line_item_count}건과 합계금액 {total or '미확인'}이 추출되었습니다."
        return (
            f"이 {label}는 {vendor}{self._with_particle(vendor)} {customer} 간의 거래 문서입니다. "
            f"{number_label}는 {number}이며, {detail}"
            f"{item_summary}"
        )

    def _manufacturing_action_items(self, doc_type: str, warnings: list[str]) -> list[str]:
        items = ["품목명, 수량, 단가, 합계금액이 실제 원본 문서와 일치하는지 확인하세요."]
        if doc_type == "purchase_order":
            items.append("납기일이 실제 요청 일정과 맞는지 확인하세요.")
        elif doc_type == "quotation":
            items.append("유효기간, 납기조건, 결제조건이 견적 조건과 맞는지 확인하세요.")
        elif doc_type == "delivery_note":
            items.append("납품일, 입고장소, 수령자가 실제 납품 정보와 맞는지 확인하세요.")
        elif doc_type == "invoice":
            items.append("지급기한과 사업자등록번호가 원본과 일치하는지 확인하세요.")
        else:
            items.append("문서번호와 날짜가 원본 문서와 일치하는지 확인하세요.")
        return items

    def _manufacturing_key_dates(self, doc_type: str, issue_date: str | None, fields: dict[str, str | list[str]]) -> list[str]:
        values: list[tuple[str, str | None]] = []
        if issue_date and doc_type not in {"transaction_statement"}:
            values.append((self._manufacturing_issue_label(doc_type), issue_date))
        if doc_type == "purchase_order":
            values.append(("납기일", self._korean_date_from_iso(fields.get("due_date"))))
        elif doc_type == "quotation":
            values.append(("견적일", issue_date))
            values.append(("유효기간", self._korean_date_from_iso(fields.get("valid_until"))))
        elif doc_type == "transaction_statement":
            transaction_date = self._korean_date_from_iso(fields.get("transaction_date"))
            values.append(("거래일자", transaction_date or issue_date))
            if issue_date and issue_date != transaction_date:
                values.append(("발행일", issue_date))
        elif doc_type == "delivery_note":
            values.append(("납품일", self._korean_date_from_iso(fields.get("delivery_date"))))
        elif doc_type == "invoice":
            values.append(("지급기한", self._korean_date_from_iso(fields.get("payment_due_date"))))
        return self._dedupe_key_dates(values)

    def _dedupe_key_dates(self, values: list[tuple[str, str | None]]) -> list[str]:
        result: list[str] = []
        seen_pairs: set[tuple[str, str]] = set()
        seen_dates: set[str] = set()
        for label, value in values:
            if not value:
                continue
            pair = (label, value)
            if pair in seen_pairs:
                continue
            if value in seen_dates and label in {"거래일자", "발행일", "견적일"}:
                continue
            seen_pairs.add(pair)
            seen_dates.add(value)
            result.append(f"{label}: {value}")
        return result

    def _manufacturing_label(self, doc_type: str) -> str:
        return {
            "purchase_order": "발주서",
            "quotation": "견적서",
            "transaction_statement": "거래명세서",
            "delivery_note": "납품서",
            "invoice": "인보이스/세금계산서",
            "packing_list": "포장명세서",
            "inspection_report": "검사성적서",
            "contract": "계약서",
        }.get(doc_type, "제조업 문서")

    def _manufacturing_number_label(self, doc_type: str) -> str:
        return {
            "purchase_order": "발주번호",
            "quotation": "견적번호",
            "transaction_statement": "거래명세서번호",
            "delivery_note": "납품번호",
            "invoice": "계산서번호",
        }.get(doc_type, "문서번호")

    def _manufacturing_issue_label(self, doc_type: str) -> str:
        return {
            "quotation": "견적일",
            "transaction_statement": "거래일자",
        }.get(doc_type, "발행일")

    def _extract_labeled_text(self, text: str, labels: list[str]) -> str | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*([^\n|]+)", text, flags=re.IGNORECASE)
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group(1)).strip(" -:：")[:120] or None

    def _extract_labeled_text_multiline(self, text: str, labels: list[str]) -> str | None:
        same_line = self._extract_labeled_text(text, labels)
        if same_line:
            return same_line
        lines = [line.strip() for line in text.splitlines()]
        normalized_labels = {re.sub(r"[\s:：]+", "", label.lower()) for label in labels}
        for index, line in enumerate(lines[:-1]):
            if re.sub(r"[\s:：]+", "", line.lower()) not in normalized_labels:
                continue
            for candidate in lines[index + 1:index + 4]:
                value = re.sub(r"\s+", " ", candidate).strip(" -:：")
                if value and not re.fullmatch(r"[가-힣A-Za-z ]{1,20}", value):
                    return value[:120]
        return None

    def _extract_labeled_date(self, text: str, labels: list[str]) -> date | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?:{label_pattern})\s*[:：]?\s*(\d{{4}}[.\-/년]\s*\d{{1,2}}[.\-/월]\s*\d{{1,2}}[일]?)",
            text,
            flags=re.IGNORECASE,
        )
        return self._parse_date(match.group(1)) if match else None

    def _parse_date(self, value: str) -> date | None:
        parts = re.findall(r"\d{1,4}", value)
        if len(parts) < 3:
            return None
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None

    def _date_iso(self, value: date | None) -> str | None:
        return value.isoformat() if value else None

    def _korean_date_from_iso(self, value: object) -> str | None:
        if not value or not isinstance(value, str):
            return None
        parsed = self._parse_date(value)
        return self._korean_date(parsed)

    def _korean_date(self, value: date | None) -> str | None:
        if not value:
            return None
        return f"{value.year}년 {value.month}월 {value.day}일"

    def _korean_money(self, value: Decimal | None, currency: str) -> str | None:
        if value is None:
            return None
        amount = f"{int(value):,}" if value == value.to_integral_value() else f"{float(value):,.2f}"
        suffix = "원" if currency.upper() == "KRW" else f" {currency.upper()}"
        return f"{amount}{suffix}"

    def _amount_mismatch_message(self, document_total: Decimal | None, line_total_sum: Decimal | None, difference: Decimal | None, currency: str) -> str:
        document_total_text = self._korean_money(document_total, currency) or "미확인"
        line_total_text = self._korean_money(line_total_sum, currency) or "미확인"
        difference_text = self._korean_money(difference, currency) or "미확인"
        return f"문서 총액 {document_total_text}과 품목 합계 {line_total_text}이 일치하지 않습니다. 차이 {difference_text}."

    def _with_particle(self, value: str) -> str:
        last = value[-1] if value else ""
        if not ("\uac00" <= last <= "\ud7a3"):
            return "와"
        return "과" if (ord(last) - ord("\uac00")) % 28 else "와"

    def _finalize_summaries(
        self,
        document: Document,
        text: str,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        interpretation: CategoryInterpretation | None,
    ) -> tuple[str | None, str | None]:
        important_points = self._important_points(document, text, mode, profile, result, interpretation)
        result.workflow_metadata["important_points"] = important_points
        short = self._build_summary_short(document, text, mode, profile, result, interpretation, important_points)
        detailed = self._build_summary_detailed(document, text, mode, profile, result, interpretation, short, important_points)
        return short, detailed

    def _build_summary_short(
        self,
        document: Document,
        text: str,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        interpretation: CategoryInterpretation | None,
        important_points: list[str],
    ) -> str | None:
        lead = self._importance_lead(document, mode, profile)
        top_points = self._summary_points(important_points, lead, interpretation, limit=2)
        if top_points:
            if len(top_points) == 1:
                return self._join_summary_sentences(lead, top_points[0])
            return self._join_summary_sentences(lead, f"Key details: {top_points[0]}; {top_points[1]}")
        if interpretation and interpretation.summary_hint and not self._summary_is_generic(interpretation.summary_hint):
            return interpretation.summary_hint
        return result.workflow_summary or document.summary or self._importance_lead(document, mode, profile)

    def _build_summary_detailed(
        self,
        document: Document,
        text: str,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        interpretation: CategoryInterpretation | None,
        summary_short: str | None,
        important_points: list[str],
    ) -> str | None:
        lead = self._importance_lead(document, mode, profile)
        top_points = self._summary_points(important_points, lead, interpretation, limit=5)
        highlight_sentence = self._natural_highlight_sentence(top_points, mode, profile)
        purpose_sentence = self._importance_purpose_sentence(document, mode, profile, result, important_points)
        if interpretation and interpretation.summary_hint and not self._summary_is_generic(interpretation.summary_hint) and not self._summary_hint_is_template(interpretation.summary_hint):
            highlight_sentence = self._natural_highlight_sentence([interpretation.summary_hint] + top_points[:3], mode, profile) or highlight_sentence
        label = self._category_display_name(profile if profile != "standard" else mode or document.document_type.value)
        return self._join_summary_sentences(
            self._summary_opening(document, profile, mode, label),
            highlight_sentence or summary_short,
            purpose_sentence,
        )

    def _summary_opening(self, document: Document, profile: str, mode: str, label: str) -> str:
        title = self._clean_text_fragment(document.title)
        key = profile if profile != "standard" else mode
        if key in {"receipt", "repair_service_receipt"}:
            merchant = self._merchant_display(document) or title or "This receipt"
            return f"{merchant} records a receipt transaction."
        if key == "invoice":
            return f"{title or 'This invoice'} summarizes billing and payment details."
        if key == "utility_bill":
            return f"{title or 'This utility bill'} summarizes service charges and payment timing."
        if key in {"meeting_notice"}:
            return f"{title or 'This notice'} sets out meeting timing, location, and preparation details."
        if key == "instructional_memo":
            return f"{title or 'This memo'} lays out process guidance and follow-up expectations."
        if key in {"syllabus", "course_guide"}:
            return f"{title or 'This course guide'} outlines course expectations and key academic details."
        if key in {"presentation_guide", "speaking_notes"}:
            return f"{title or 'This presentation guide'} organizes audience, slide flow, and preparation guidance."
        if key == "installation_guide":
            return f"{title or 'This setup guide'} explains installation, configuration, dependencies, and technical setup steps."
        if key == "implementation_schedule":
            return f"{title or 'This implementation schedule'} tracks engineering work, status, testing, and ownership."
        if key == "resume_profile":
            return f"{title or 'This resume profile'} highlights background, skills, and experience."
        if key == "profile_record":
            return f"{title or 'This profile record'} captures identity and affiliation details."
        return f"This {label}{self._title_phrase(document.title)}."

    def _summary_points(
        self,
        points: list[str],
        lead: str | None,
        interpretation: CategoryInterpretation | None,
        limit: int,
    ) -> list[str]:
        cleaned: list[str] = []
        blocked = {self._summary_key(lead)}
        if interpretation and interpretation.summary_hint and self._summary_hint_is_template(interpretation.summary_hint):
            blocked.add(self._summary_key(interpretation.summary_hint))
        for point in points:
            normalized = self._normalize_importance_point(point)
            if not normalized:
                continue
            key = self._summary_key(normalized)
            if key in blocked:
                continue
            if key and any(key == existing or key in existing or existing in key for existing in blocked if existing):
                continue
            cleaned.append(normalized)
            blocked.add(key)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _summary_key(self, value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _summary_hint_is_template(self, value: str | None) -> bool:
        lowered = (value or "").lower().strip()
        templates = [
            "invoice document with vendor",
            "meeting notice with time",
            "course guide with title",
            "presentation guide with audience",
            "instructional memo with guidance",
            "instructional memo with process guidance",
            "resume-style document highlighting",
            "profile-like text containing identity",
            "utility bill or account statement",
        ]
        return any(lowered.startswith(template) for template in templates)

    def _important_points(
        self,
        document: Document,
        text: str,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        interpretation: CategoryInterpretation | None,
    ) -> list[str]:
        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()

        def add(point: str | None, score: int) -> None:
            cleaned = self._normalize_importance_point(point)
            if not cleaned:
                return
            if not self._is_useful_importance_point(cleaned):
                return
            key = cleaned.casefold()
            if key in seen:
                return
            seen.add(key)
            candidates.append((score + self._importance_score_adjustment(cleaned, mode, profile), cleaned))

        add(self._importance_lead(document, mode, profile), 110)

        for point in self._string_list((interpretation.workflow_hints if interpretation else {}).get("review_focus"))[:4]:
            add(point, 90)
        for point in self._string_list((interpretation.workflow_hints if interpretation else {}).get("important_points"))[:6]:
            add(point, 86)

        facts = self._core_fact_points(document, mode, profile, result, interpretation)
        for point in facts:
            add(point, 88)

        for point in self._key_field_points(interpretation.key_fields if interpretation else {}):
            add(point, 76)

        for point in result.warnings[:4]:
            add(point, 72)

        for point in result.action_items[:4]:
            add(point, 66)

        for point in self._text_importance_points(text, mode, profile):
            add(point, 58)

        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return [point for _, point in candidates[:8]]

    def _core_fact_points(
        self,
        document: Document,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        interpretation: CategoryInterpretation | None,
    ) -> list[str]:
        receipt_meta = self._metadata_section(result, "receipt")
        syllabus_meta = self._metadata_section(result, "syllabus")
        guide_meta = self._metadata_section(result, "guide")
        resume_meta = self._metadata_section(result, "resume")
        profile_meta = self._metadata_section(result, "profile")
        utilities_meta = self._metadata_section(result, "utilities")
        meeting_meta = self._metadata_section(result, "meeting_notice")
        points: list[str] = []

        points.append(self._importance_lead(document, mode, profile))
        if document.document_type == DocumentType.receipt:
            points.extend([
                self._receipt_spend_summary(document, document.raw_text or ""),
                self._list_preview(receipt_meta.get("top_item_lines"), label="itemized lines"),
            ])
        points.extend([
            self._string_value(syllabus_meta.get("course_title")),
            self._string_value(syllabus_meta.get("course_code")),
            self._string_value(syllabus_meta.get("semester")),
            self._string_value(syllabus_meta.get("instructor")),
            self._list_preview(syllabus_meta.get("required_materials"), label="required materials"),
            self._list_preview(syllabus_meta.get("key_policies"), label="policies"),
            self._list_preview(syllabus_meta.get("exam_dates"), label="exam details"),
            self._string_value(guide_meta.get("purpose")),
            self._string_value(guide_meta.get("audience")),
            self._list_preview(guide_meta.get("slide_guidance"), label="slide flow guidance"),
            self._list_preview(guide_meta.get("speaking_notes"), label="speaking notes"),
            self._list_preview(guide_meta.get("preparation_actions"), label="preparation guidance"),
            self._string_value(resume_meta.get("person_name")),
            self._list_preview(resume_meta.get("education"), label="education"),
            self._list_preview(resume_meta.get("work_experience"), label="experience"),
            self._list_preview(resume_meta.get("projects"), label="projects"),
            self._list_preview(resume_meta.get("technical_skills"), label="technical skills"),
            self._list_preview(profile_meta.get("identity_facts"), label="identity facts"),
        ])
        if profile == "utility_bill":
            points.extend([
                self._label_value("provider", utilities_meta.get("provider")),
                self._label_value("amount due", self._money_string(utilities_meta.get("amount_due"), document.currency)),
                self._label_value("due date", utilities_meta.get("due_date")),
                self._label_value("billing period", utilities_meta.get("billing_period")),
            ])
        if profile == "meeting_notice":
            points.extend([
                self._label_value("purpose", meeting_meta.get("purpose")),
                self._label_value("meeting date", meeting_meta.get("meeting_date")),
                self._label_value("location", meeting_meta.get("location")),
            ])
        if interpretation and interpretation.summary_hint and not self._summary_is_generic(interpretation.summary_hint) and not self._summary_hint_is_template(interpretation.summary_hint):
            points.append(interpretation.summary_hint)
        return [point for point in points if point]

    def _key_field_points(self, key_fields: dict[str, Any]) -> list[str]:
        points: list[str] = []
        for key, value in key_fields.items():
            label = self._category_display_name(str(key))
            if isinstance(value, list):
                preview = ", ".join(str(item) for item in value[:3] if item)
                if preview:
                    points.append(f"{label}: {preview}")
            elif isinstance(value, dict):
                nested = ", ".join(f"{self._category_display_name(str(k))}: {v}" for k, v in list(value.items())[:3] if v)
                if nested:
                    points.append(f"{label}: {nested}")
            else:
                cleaned = self._clean_text_fragment(str(value)) if value not in (None, "") else None
                if cleaned:
                    points.append(f"{label}: {cleaned}")
        return points[:6]

    def _text_importance_points(self, text: str, mode: str, profile: str) -> list[str]:
        lines = self._unique_content_lines(text)
        candidates: list[tuple[int, str]] = []
        context_terms = self._importance_terms(mode, profile)
        for index, line in enumerate(lines[:40]):
            lowered = line.lower()
            score = 0
            if ":" in line and len(line) <= 100:
                score += 16
            if any(term in lowered for term in context_terms):
                score += 18
            if re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", line):
                score += 18
            if re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", line):
                score += 8
            if re.search(r"\b\d{1,6}(?:,\d{3})*\.\d{2}\b", line):
                score += 10
            if index < 8:
                score += 8 - index
            if self._looks_like_body_fragment(line):
                score -= 24
            if len(line) > 140:
                score -= 20
            if any(keyword in lowered for keyword in ["policy", "regulation", "academic integrity", "attendance"]) and not any(
                term in lowered for term in ["deadline", "exam", "required materials", "grading", "due"]
            ):
                score -= 10
            normalized = self._normalize_importance_point(line)
            if score > 6 and normalized and self._is_useful_importance_point(normalized):
                candidates.append((score, normalized))
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return self._dedupe([point for _, point in candidates[:6]])

    def _importance_terms(self, mode: str, profile: str) -> list[str]:
        terms = [
            "deadline", "due", "important", "required", "policy", "summary", "instructions",
            "meeting", "exam", "materials", "skills", "experience", "project", "audience",
            "speaker", "rehearse", "total", "tax", "subtotal", "service", "labor", "parts",
            "installation", "setup", "configuration", "dependencies", "task", "feature",
            "status", "testing", "coverage", "pipeline", "claimed", "roadmap",
        ]
        if mode:
            terms.extend(mode.replace("-", "_").split("_"))
        if profile and profile != "standard":
            terms.extend(profile.replace("-", "_").split("_"))
        return self._dedupe(terms)

    def _importance_lead(self, document: Document, mode: str, profile: str) -> str:
        label = self._category_display_name(profile if profile != "standard" else mode or document.document_type.value)
        title = self._clean_text_fragment(document.title)
        merchant = self._merchant_display(document) if document.document_type == DocumentType.receipt else None
        subject = merchant or title
        lowered_label = label.lower()
        if document.document_type == DocumentType.receipt and subject:
            return f"Receipt from {subject}"
        if "course" in lowered_label or "syllabus" in lowered_label:
            return f"{label.capitalize()} for {subject}" if subject else label.capitalize()
        if "presentation" in lowered_label or "speaking" in lowered_label:
            return f"{label.capitalize()} for {subject}" if subject else label.capitalize()
        if "resume" in lowered_label or "profile" in lowered_label:
            return f"{label.capitalize()} for {subject}" if subject else label.capitalize()
        if "meeting" in lowered_label:
            return f"Meeting notice for {subject}" if subject else "Meeting notice"
        if "utility" in lowered_label:
            return f"Utility bill from {subject}" if subject else "Utility bill"
        if subject:
            return f"{label.capitalize()} for {subject}"
        return f"{label.capitalize()} document"

    def _importance_purpose_sentence(
        self,
        document: Document,
        mode: str,
        profile: str,
        result: WorkflowEnrichment,
        important_points: list[str],
    ) -> str:
        if result.follow_up_required or result.action_items:
            if profile in {"syllabus", "course_guide"}:
                return "Use it to track expectations, deadlines, materials, and course work that need attention."
            if profile == "instructional_memo":
                return "Use it to follow the stated procedure, deadlines, documentation rules, and ownership expectations."
            if profile == "meeting_notice":
                return "Use it to prepare for the meeting, note the location or timing, and complete any follow-up."
            if profile in {"invoice", "utility_bill"}:
                return "Use it to verify charges, due dates, account details, and payment or filing needs."
            if profile in {"receipt", "repair_service_receipt"} or document.document_type == DocumentType.receipt:
                return "Use it to verify the merchant, date, total, and any itemized charges before filing."
            if profile in {"presentation_guide", "speaking_notes"}:
                return "Use it to prepare the presentation flow, speaking points, timing, and materials."
            if profile == "installation_guide":
                return "Use it to verify prerequisites, commands, configuration, and deployment steps before implementation."
            if profile == "implementation_schedule":
                return "Use it to track task ownership, implementation progress, testing, coverage, and next planning steps."
            if profile == "resume_profile":
                return "Use it to review qualifications, experience, and any follow-up details."
            if profile == "profile_record":
                return "Use it to verify identity, affiliation, support notes, and any follow-up details in the record."
            return "Use it to confirm timing, required follow-up, and the details most relevant to the reader."
        if document.document_type == DocumentType.receipt:
            return "It mainly matters as a transaction record, especially for tracking, reimbursement, or understanding the purchase or service context."
        if profile in {"syllabus", "course_guide"}:
            return "It is most useful for understanding the course structure, expectations, dates, and materials or policies that guide participation."
        if profile in {"presentation_guide", "speaking_notes"}:
            return "It is most useful for understanding how the presentation should be structured, delivered, and prepared."
        if profile == "installation_guide":
            return "It is most useful as technical documentation for setting up, configuring, and verifying a system or project."
        if profile == "implementation_schedule":
            return "It is most useful as a planning tracker for implementation status, testing work, ownership, and roadmap progress."
        if profile == "resume_profile":
            return "It is most useful for quickly understanding the candidate's background, skills, and evidence of experience."
        if profile == "profile_record":
            return "It is most useful for quick reference to the key identity or affiliation details captured in the record."
        if important_points:
            return "It gives the reader a clearer view of the document's main purpose and the details that are most worth noticing first."
        return "It provides a clearer view of the document's main purpose and preserves the most useful details from the extracted content."

    def _normalize_importance_point(self, point: str | None) -> str | None:
        cleaned = self._clean_text_fragment(point)
        if not cleaned:
            return None
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ;,.-")
        cleaned = re.sub(r"^(this document|this file|this course|this receipt)\s+(is|contains|includes)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:line|title|field)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(important details?|key details?)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^([A-Za-z][A-Za-z ]{2,40})\s*:\s*(?:line|title|field)\s*:\s*",
            r"\1: ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^([A-Za-z][A-Za-z ]{2,40})\s*:\s*\1\s*:\s*", r"\1: ", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        profile_record_match = re.fullmatch(r"([A-Z][A-Za-z .'-]{1,80})\s+is\s+(?:a\s+)?(?:profile record|profile)", cleaned)
        if profile_record_match:
            cleaned = f"Name: {profile_record_match.group(1).strip()}"
        return self._truncate_text(cleaned, 220) if cleaned else None

    def _is_useful_importance_point(self, point: str) -> bool:
        lowered = point.lower()
        if len(point) < 4:
            return False
        if self._is_placeholder_title(point):
            return False
        if re.fullmatch(r"(general notice|generic document|document|memo|notice)", lowered):
            return False
        if lowered.startswith("important date detected"):
            return False
        if lowered.startswith("review the document for deadlines"):
            return False
        if lowered.startswith(("review ", "pay or schedule ", "file this receipt ", "confirm ", "handle this notice ")):
            return False
        if re.match(r"^(it|this|these)\b", lowered) and len(point.split()) > 12:
            return False
        if len(point.split()) > 28:
            return False
        if re.search(r"(lorem ipsum|table of contents)", lowered):
            return False
        return True

    def _importance_score_adjustment(self, point: str, mode: str, profile: str) -> int:
        lowered = point.lower()
        score = 0
        if any(term in lowered for term in self._importance_terms(mode, profile)):
            score += 6
        if re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", point):
            score += 6
        if re.search(r"\b\d{1,6}(?:,\d{3})*\.\d{2}\b", point):
            score += 4
        if self._looks_like_body_fragment(point):
            score -= 10
        if ":" in point and len(point) <= 80:
            score += 4
        return score

    def _looks_like_body_fragment(self, value: str) -> bool:
        lowered = value.lower().strip()
        return (
            len(lowered.split()) >= 10
            and bool(re.search(r"\b(is|are|will|introduces|provides|covers|describes|contains|should)\b", lowered))
        ) or bool(re.match(r"^(course description|overview|summary|introduction|objectives?)\s*:", lowered))

    def _natural_highlight_sentence(self, points: list[str], mode: str, profile: str) -> str | None:
        cleaned = [self._normalize_importance_point(point) for point in points]
        cleaned = [point for point in cleaned if point]
        if not cleaned:
            return None
        cleaned = self._compress_neighboring_points(cleaned)
        if not cleaned:
            return None
        lead = self._highlight_intro(mode, profile)
        if len(cleaned) == 1:
            return self._safe_sentence(f"{lead} {cleaned[0]}")
        if len(cleaned) == 2:
            return self._safe_sentence(f"{lead} {cleaned[0]} and {cleaned[1]}")
        joined = self._join_phrases(cleaned[:3])
        return self._safe_sentence(f"{lead} {joined}")

    def _highlight_intro(self, mode: str, profile: str) -> str:
        key = profile if profile != "standard" else mode
        if key in {"syllabus", "course_guide"}:
            return "It mainly covers"
        if key in {"presentation_guide", "speaking_notes"}:
            return "It mainly highlights"
        if key in {"resume_profile", "profile_record"}:
            return "It brings together"
        if key in {"receipt", "repair_service_receipt", "utility_bill", "invoice"}:
            return "Key details include"
        return "Key details include"

    def _finalize_action_items(self, items: list[str], text: str, mode: str, profile: str) -> list[str]:
        normalized = [self._normalize_action_item(item, mode, profile) for item in items]
        normalized = [item for item in normalized if item]
        key = profile if profile != "standard" else mode
        if key in {"presentation_guide", "speaking_notes"}:
            normalized = [item for item in normalized if self._is_presentation_action(item)]
            if len(normalized) > 1:
                normalized = [item for item in normalized if item != "Review presentation preparation guidance."]
        if not normalized:
            fallback = self._action_fallback(mode, profile)
            return [fallback] if fallback else []
        return self._dedupe(normalized)[:5]

    def _normalize_action_item(self, item: str | None, mode: str, profile: str) -> str | None:
        cleaned = self._clean_text_fragment(item)
        if not cleaned:
            return None
        cleaned = re.sub(r"^(?:line:\s*)+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:action|next step|next steps)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        lowered = cleaned.lower()
        key = profile if profile != "standard" else mode
        if key in {"presentation_guide", "speaking_notes"} and re.fullmatch(r"slide\s+\d+\s+(script|notes?)\.?", lowered):
            return None
        if key in {"presentation_guide", "speaking_notes"} and any(term in lowered for term in ["grading", "exam", "quiz", "course policy"]):
            return "Review presentation preparation guidance."
        receipt_like = {"receipt", "repair_service_receipt", "utility_bill", "invoice"}
        if "receipt" in lowered and key not in receipt_like:
            return self._action_fallback(mode, profile)
        if "required attendees" in lowered:
            return "Confirm required attendees and preparation before the meeting."
        if lowered.rstrip(".:") == "communication":
            return "Review course communication expectations."
        if "contact:" in lowered or lowered.startswith("contact "):
            return "Confirm the right contact channel for questions or exceptions."
        if key == "invoice" and any(term in lowered for term in ["deadline", "due", "submit", "pay", "payment"]):
            return "Review the invoice due date and payment timing."
        if key == "utility_bill" and any(term in lowered for term in ["deadline", "due", "pay", "payment", "amount due", "balance due"]):
            return "Review the bill due date and payment timing."
        if key == "utility_bill" and "follow-up" in lowered:
            return "Review the bill due date and payment timing."
        if key == "instructional_memo" and lowered.startswith("handle this notice"):
            return re.sub(r"^handle this notice", "Handle this memo", cleaned, flags=re.IGNORECASE)
        if key == "instructional_memo" and "follow-up" in lowered:
            return "Review memo follow-up steps and required materials."
        if key == "profile_record" and "support notes" in lowered:
            return "Review support notes and follow-up needs."
        if key == "profile_record" and any(term in lowered for term in ["risk indicator", "risk indicators", "support need"]):
            return "Review risk indicators and support needs."
        if len(cleaned.split()) > 12 or self._looks_like_body_fragment(cleaned):
            if any(term in lowered for term in ["deadline", "due", "submit", "register", "rsvp"]):
                if key == "instructional_memo":
                    return "Review memo deadlines and required submission steps."
                return "Review the document for deadlines or required submission steps."
            if any(term in lowered for term in ["pay", "amount due", "balance due"]):
                return "Review the payment details and timing before taking action."
            if any(term in lowered for term in ["policy", "attendance", "grading", "materials"]):
                return self._policy_review_prompt(lowered, mode, profile)
            if any(term in lowered for term in ["rehearse", "practice", "slide", "speaker", "talk"]):
                return "Review the preparation and delivery guidance before presenting."
            return None
        if profile == "meeting_notice" and any(term in lowered for term in ["bring", "prepare", "questions", "feedback", "materials"]):
            return "Review the requested preparation details before the meeting."
        if any(term in lowered for term in ["policy", "attendance", "missed work", "late work", "grading", "materials", "exam", "quiz"]):
            return self._policy_review_prompt(lowered, mode, profile)
        if any(term in lowered for term in ["deadline", "due", "submit", "register", "rsvp"]):
            if key == "instructional_memo":
                return "Review memo deadlines and required submission steps."
            return "Review deadlines and required submission details."
        cleaned = cleaned[0].upper() + cleaned[1:]
        if cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned

    def _action_fallback(self, mode: str, profile: str) -> str | None:
        key = profile if profile != "standard" else mode
        if key in {"syllabus", "course_guide"}:
            return "Review the key course expectations, dates, and materials."
        if key in {"presentation_guide", "speaking_notes"}:
            return "Review the presentation flow and preparation guidance."
        if key == "installation_guide":
            return "Review prerequisites, setup commands, and configuration values."
        if key == "implementation_schedule":
            return "Review open tasks, ownership, testing, and coverage status."
        if key in {"resume_profile"}:
            return "Review the most important qualifications and experience details."
        if key in {"profile_record"}:
            return "Review the key identity details for accuracy."
        if key in {"receipt", "repair_service_receipt", "utility_bill", "invoice"}:
            return "Review the main transaction details before filing or exporting."
        if key in {"meeting_notice", "instructional_memo"}:
            return "Review the key next steps and follow-up details."
        return None

    def _policy_review_prompt(self, lowered: str, mode: str, profile: str) -> str:
        if any(term in lowered for term in ["attendance", "missed work", "late work"]):
            return "Review attendance and missed-work expectations."
        if any(term in lowered for term in ["grading", "exam", "quiz"]):
            return "Review grading and exam-related requirements."
        if "materials" in lowered:
            return "Review material and resource requirements."
        if "policy" in lowered:
            return "Review key policy requirements."
        if profile in {"presentation_guide", "speaking_notes"}:
            return "Review presentation preparation guidance."
        return "Review the key policy and requirement details."

    def _compress_neighboring_points(self, points: list[str]) -> list[str]:
        result: list[str] = []
        seen_tokens: list[set[str]] = []
        for point in points:
            tokens = {token for token in re.findall(r"[a-z0-9]+", point.lower()) if len(token) > 2}
            if any(len(tokens & existing) >= max(2, min(len(tokens), len(existing)) // 2) for existing in seen_tokens if tokens and existing):
                continue
            seen_tokens.append(tokens)
            result.append(point)
        return result

    def _join_phrases(self, phrases: list[str]) -> str:
        cleaned = [self._strip_trailing_conjunction_noise(phrase) for phrase in phrases]
        cleaned = [phrase for phrase in cleaned if phrase]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"

    def _strip_trailing_conjunction_noise(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"\s+", " ", value).strip(" ,;")
        cleaned = re.sub(r"(?:,?\s+(?:and|or))+$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" ,;")
        return cleaned or None

    def _safe_sentence(self, value: str | None) -> str | None:
        cleaned = self._strip_trailing_conjunction_noise(value)
        if not cleaned:
            return None
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r",\s*,", ",", cleaned)
        cleaned = re.sub(r",\s+and\.$", ".", cleaned, flags=re.IGNORECASE)
        cleaned = self._truncate_text(cleaned, 320)
        if cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned

    def _metadata_section(self, result: WorkflowEnrichment, key: str) -> dict[str, Any]:
        value = result.workflow_metadata.get(key)
        return value if isinstance(value, dict) else {}

    def _string_value(self, value: Any) -> str | None:
        if isinstance(value, str):
            return self._clean_text_fragment(value)
        if isinstance(value, (int, float, Decimal)):
            return self._clean_text_fragment(str(value))
        return None

    def _label_value(self, label: str, value: Any) -> str | None:
        cleaned = self._string_value(value)
        if not cleaned:
            return None
        if cleaned.lower().startswith(f"{label.lower()}:"):
            return cleaned
        return f"{label}: {cleaned}"

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [self._clean_text_fragment(str(item)) for item in value if item]
        return [item for item in items if item]

    def _dict_value(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _list_preview(self, value: Any, label: str | None = None, max_items: int = 3) -> str | None:
        items = self._string_list(value)
        if not items:
            return None
        preview = ", ".join(items[:max_items])
        if label:
            return f"{label}: {preview}"
        return preview

    def _money_string(self, value: Any, currency: str | None = None) -> str | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            amount = value
        else:
            try:
                amount = Decimal(str(value))
            except Exception:
                return None
        code = currency or "USD"
        return f"{self._money(amount)} {code}".strip()

    def _category_display_name(self, value: str | None) -> str:
        if not value:
            return "document"
        return str(value).replace("_", " ").replace("-", " ")

    def _article(self, label: str) -> str:
        first_word = (label or "").split(maxsplit=1)[0].lower()
        consonant_sound_vowels = ("uni", "use", "user", "utility", "euro", "one")
        if first_word.startswith(consonant_sound_vowels):
            return "a"
        return "an" if first_word[:1] in {"a", "e", "i", "o", "u"} else "a"

    def _title_phrase(self, title: str | None) -> str:
        cleaned = self._clean_text_fragment(title)
        return f" titled {cleaned}" if cleaned else ""

    def _date_phrase(self, document: Document) -> str:
        return f" dated {document.extracted_date.isoformat()}" if document.extracted_date else ""

    def _describe_contains(self, parts: list[str | None]) -> str | None:
        values = [self._clean_text_fragment(part) for part in parts if part]
        values = [value for value in values if value]
        if not values:
            return None
        joined = ", ".join(values[:5])
        return f"It contains {joined}."

    def _join_summary_sentences(self, *parts: str | None) -> str | None:
        sentences = []
        for part in parts:
            cleaned = self._clean_sentence_part(part)
            if not cleaned:
                continue
            if cleaned[-1] not in ".!?":
                cleaned = f"{cleaned}."
            sentences.append(cleaned)
        if not sentences:
            return None
        return " ".join(sentences)[:900]

    def _generic_importance_sentence(self, result: WorkflowEnrichment) -> str:
        if result.follow_up_required or result.action_items:
            return "It is useful for understanding the main content and any follow-up or review steps."
        return "It is useful for understanding the document's main content and preserving a clear record."

    def _workflow_mode(self, document: Document, interpretation: CategoryInterpretation | None = None) -> str:
        category = ((interpretation.category if interpretation else None) or document.category or "").lower()
        if category:
            return category
        if document.document_type == DocumentType.receipt:
            return "receipt"
        if document.document_type == DocumentType.notice:
            return "notice"
        if document.document_type == DocumentType.memo:
            return "memo"
        if document.document_type == DocumentType.presentation:
            return "presentation"
        if document.document_type == DocumentType.document:
            return "document"
        return "other"

    def _receipt(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        item_lines = self._item_lines(text)
        category_context = self._category_context(text, mode)
        expected_total = self._expected_total(document)
        suspicious_total = False
        validation_notes: list[str] = []
        if expected_total is not None and document.extracted_amount is not None:
            difference = abs(expected_total - document.extracted_amount)
            suspicious_total = difference > Decimal("0.03")
            if suspicious_total:
                validation_notes.append(
                    f"Subtotal plus tax ({expected_total}) does not match extracted total ({document.extracted_amount})."
                )
        elif document.extracted_amount is None:
            validation_notes.append("No receipt total was detected.")

        warnings = list(validation_notes)
        if not document.merchant_name:
            warnings.append("Merchant is missing; review before export.")
        if not document.extracted_date:
            warnings.append("Receipt date is missing; review before reimbursement or expense tracking.")

        action_items = []
        if warnings:
            action_items.append("Review receipt merchant, date, and total.")
        else:
            action_items.append("File this receipt for expense export or reimbursement records.")

        spend_summary = self._receipt_spend_summary(document, text)
        return WorkflowEnrichment(
            workflow_summary=spend_summary,
            action_items=action_items,
            warnings=warnings,
            key_dates=self._key_dates(document, text),
            urgency_level="medium" if warnings else "low",
            follow_up_required=bool(warnings),
            workflow_metadata={
                "receipt": {
                    "merchant_confidence": self._merchant_confidence(document),
                    "expected_total": str(expected_total) if expected_total is not None else None,
                    "suspicious_total": suspicious_total,
                    "top_item_lines": item_lines[:6],
                    "spend_summary": spend_summary,
                    "category_context": category_context,
                    "receipt_quality_flags": warnings,
                }
            },
        )

    def _utilities(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        due_date = self._date_near_label(text, ["due date", "payment due", "due by", "pay by"])
        billing_period = self._billing_period(text)
        amount_due = document.extracted_amount or self._amount_near_label(text, ["amount due", "total due", "balance due", "new charges"])
        provider = document.merchant_name or self._first_meaningful_line(text)
        warnings = []
        action_items = []
        if due_date:
            action_items.append(f"Pay or schedule this bill by {due_date}.")
        else:
            warnings.append("No clear due date was detected.")
            action_items.append("Review the bill for a payment deadline.")
        if amount_due is None:
            warnings.append("No clear amount due was detected.")
        urgency = "high" if self._has_urgent_language(text) else ("medium" if due_date else "low")
        return WorkflowEnrichment(
            workflow_summary=self._sentence([provider, "utility bill", self._money(amount_due), f"due {due_date}" if due_date else None]),
            action_items=action_items,
            warnings=warnings,
            key_dates=self._dedupe([due_date] + self._key_dates(document, text)),
            urgency_level=urgency,
            follow_up_required=True,
            workflow_metadata={
                "utilities": {
                    "provider": provider,
                    "amount_due": str(amount_due) if amount_due is not None else None,
                    "due_date": due_date,
                    "billing_period": billing_period,
                    "payment_urgency": urgency,
                    "comparison_ready": bool(amount_due and billing_period),
                }
            },
        )

    def _invoice(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        due_date = self._date_near_label(text, ["due date", "payment due", "pay by"])
        invoice_date = self._date_near_label(text, ["invoice date", "date"])
        total_due = document.extracted_amount or self._amount_near_label(text, ["amount due", "total due", "balance due", "subtotal", "total"])
        vendor = self._line_after_label(text, ["vendor", "from", "provider"]) or self._first_meaningful_line(text)
        invoice_number = self._line_after_label(text, ["invoice number", "invoice #", "invoice"])
        summary = self._sentence(
            [
                vendor,
                "invoice",
                invoice_number,
                self._money(total_due),
                f"due {due_date}" if due_date else None,
            ]
        )
        action_items = []
        if due_date:
            action_items.append("Review the invoice due date and payment timing.")
        if total_due is not None:
            action_items.append("Review billed amounts and line-item accuracy before paying.")
        if not action_items:
            action_items.append("Review the invoice details before filing or approving payment.")
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=action_items,
            warnings=[] if total_due is not None else ["No clear total due was detected."],
            key_dates=self._dedupe(([invoice_date] if invoice_date else []) + ([due_date] if due_date else []) + self._key_dates(document, text)),
            urgency_level="medium" if due_date else "low",
            follow_up_required=True,
            workflow_metadata={
                "invoice": {
                    "vendor": vendor,
                    "invoice_number": invoice_number,
                    "invoice_date": invoice_date,
                    "due_date": due_date,
                    "amount_due": str(total_due) if total_due is not None else None,
                }
            },
        )

    def _education_notice(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        deadline = self._date_near_label(text, ["deadline", "due", "register by", "submit by", "rsvp by"])
        deadline = deadline or self._deadline_phrase_near_label(text, ["deadline", "deadlines", "due", "register by", "submit by", "rsvp by"])
        key_dates = self._key_dates(document, text)
        action_items = self._action_lines(text)
        if deadline:
            action_items.insert(0, f"Handle this notice by {deadline}.")
        elif not action_items:
            action_items.append("Review the notice for required actions.")
        warning = "Important date detected; confirm it before relying on the reminder." if deadline or key_dates else None
        urgency = "high" if self._has_urgent_language(text) else ("medium" if deadline else "low")
        summary = self._direct_text_summary(text, document.title, profile="education_notice")
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=action_items[:5],
            warnings=[warning] if warning else [],
            key_dates=self._dedupe(([deadline] if deadline else []) + key_dates),
            urgency_level=urgency,
            follow_up_required=bool(deadline or action_items),
            workflow_metadata={
                "notice": {
                    "deadline": deadline,
                    "notice_type_hint": self._notice_type_hint(text),
                    "actionable_summary": summary,
                }
            },
        )

    def _meeting_notice(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        meeting_date = self._date_near_label(text, ["date", "meeting date", "scheduled for"])
        time_line = self._first_matching_line(text, ["am", "pm", "time", "starts at"])
        location = self._line_after_label(text, ["location", "room", "where"]) or self._first_matching_line(text, ["room", "building", "zoom", "teams"])
        purpose = self._first_matching_line(text, ["agenda", "purpose", "topic", "meeting"])
        summary = self._sentence([purpose or document.title, meeting_date, location])
        actions = self._action_lines(text) or ["Review the notice for attendance or preparation details."]
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, document.title, profile="meeting_notice"),
            action_items=actions[:5],
            warnings=[] if meeting_date else ["Meeting date or time was not clearly detected."],
            key_dates=self._dedupe(([meeting_date] if meeting_date else []) + self._key_dates(document, text)),
            urgency_level="medium" if meeting_date else "low",
            follow_up_required=True,
            workflow_metadata={
                "meeting_notice": {
                    "meeting_date": meeting_date,
                    "time_hint": time_line,
                    "location": location,
                    "purpose": purpose,
                }
            },
        )

    def _health(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        provider = document.merchant_name or self._first_meaningful_line(text)
        event_date = document.extracted_date.isoformat() if document.extracted_date else self._first_date(text)
        summary = self._sentence([provider, "health-related document", f"dated {event_date}" if event_date else None])
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=["Review before sharing or exporting.", "Keep this document in a privacy-sensitive folder."],
            warnings=["Sensitive health information may be present."],
            key_dates=self._dedupe(([event_date] if event_date else []) + self._key_dates(document, text)),
            urgency_level="medium",
            follow_up_required=True,
            workflow_metadata={
                "health": {
                    "provider_or_pharmacy": provider,
                    "visit_or_purchase_date": event_date,
                    "privacy_sensitive": True,
                    "claim_summary": summary,
                }
            },
        )

    def _office(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        amount = document.extracted_amount
        ready = bool(document.merchant_name and document.extracted_date and amount)
        warnings = [] if ready else ["Some reimbursement fields are missing."]
        return WorkflowEnrichment(
            workflow_summary=self._sentence([document.merchant_name, "business expense", self._money(amount)]),
            action_items=["Export or attach this receipt to a reimbursement report."] if ready else ["Review merchant, date, and amount for reimbursement."],
            warnings=warnings,
            key_dates=self._key_dates(document, text),
            urgency_level="low" if ready else "medium",
            follow_up_required=not ready,
            workflow_metadata={
                "office": {
                    "reimbursement_ready": ready,
                    "expense_type_hints": self._expense_type_hints(text),
                    "business_expense_summary": self._sentence([document.merchant_name, self._money(amount), document.category]),
                }
            },
        )

    def _spend_category(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        item_lines = self._item_lines(text)
        category_context = self._category_context(text, mode)
        summary = self._spend_summary(document, mode, category_context)
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=["File this receipt for spending review or export."],
            warnings=[] if document.extracted_amount else ["Amount is missing; review before expense tracking."],
            key_dates=self._key_dates(document, text),
            urgency_level="low" if document.extracted_amount else "medium",
            follow_up_required=document.extracted_amount is None,
            workflow_metadata={
                "spend": {
                    "merchant_summary": document.merchant_name,
                    "spending_interpretation": summary,
                    "item_highlights": item_lines[:5],
                    "category_spend_note": self._category_spend_note(mode, category_context),
                    "category_context": category_context,
                }
            },
        )

    def _generic(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        key_dates = self._key_dates(document, text)
        follow_up = self._has_follow_up_language(text)
        warnings = [] if document.title else ["Title quality is weak; review the heading."]
        summary = self._direct_text_summary(text, document.title, profile="generic")
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=["Review for follow-up actions."] if follow_up else [],
            warnings=warnings,
            key_dates=key_dates,
            urgency_level="medium" if follow_up else "low",
            follow_up_required=follow_up,
            workflow_metadata={
                "generic": {
                    "heading_quality": "usable" if document.title else "weak",
                    "key_entities": self._key_entities(text),
                    "follow_up_hint": follow_up,
                }
            },
        )

    def _syllabus(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        course_title = self._course_title(document, text)
        course_code = self._course_code(text)
        semester = self._semester(text)
        instructor = self._line_after_label(text, ["instructor", "professor", "faculty"])
        materials = self._matching_lines(text, ["required materials", "textbook", "materials", "required reading"])
        policies = self._matching_lines(text, ["attendance", "grading", "late work", "policy", "communication"])
        exam_dates = self._matching_lines(text, ["exam", "midterm", "final", "quiz"])
        communication_guidance = self._matching_lines(text, ["office hours", "email", "communication", "contact"])
        summary = self._sentence([course_title, course_code, semester, instructor])
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, course_title, profile="syllabus"),
            action_items=self._dedupe(materials[:2] + policies[:2] + exam_dates[:1]) or ["Review course materials and key policies."],
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=False,
            workflow_metadata={
                "syllabus": {
                    "document_subtype": "syllabus",
                    "course_title": course_title,
                    "course_code": course_code,
                    "semester": semester,
                    "instructor": instructor,
                    "required_materials": materials[:5],
                    "key_policies": policies[:5],
                    "exam_dates": exam_dates[:5],
                    "communication_guidance": communication_guidance[:5],
                }
            },
        )

    def _presentation_guide(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        purpose = self._line_after_label(text, ["purpose", "goal", "objective"]) or self._first_matching_line(text, ["presentation", "talk", "speaker"])
        audience = self._line_after_label(text, ["audience", "for", "target audience"])
        slide_guidance = self._matching_lines(text, ["slide", "opening", "closing", "transition"])
        speaking_notes = self._matching_lines(text, ["speaking note", "speaker note", "talk track", "say", "emphasize"])
        rehearsal = self._matching_lines(text, ["rehearse", "practice", "timing", "prepare"])
        actions = self._presentation_actions(audience, slide_guidance, speaking_notes, rehearsal)
        summary = self._sentence([purpose or document.title, audience, "presentation guide"])
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, document.title, profile="presentation_guide"),
            action_items=actions,
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=False,
            workflow_metadata={
                "guide": {
                    "document_subtype": "presentation_guide",
                    "purpose": purpose,
                    "audience": audience,
                    "slide_guidance": slide_guidance[:6],
                    "speaking_notes": speaking_notes[:6],
                    "preparation_actions": rehearsal[:5],
                }
            },
        )

    def _presentation_actions(
        self,
        audience: str | None,
        slide_guidance: list[str],
        speaking_notes: list[str],
        rehearsal: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if slide_guidance:
            actions.append("Review the slide sequence and tighten the transitions.")
        if speaking_notes:
            actions.append("Rehearse the speaking notes against the slide flow.")
        if rehearsal:
            actions.append("Practice timing and delivery before presenting.")
        if audience:
            actions.append("Tune examples and emphasis for the intended audience.")
        if not actions:
            actions.append("Review slide flow, speaker notes, and delivery timing before presenting.")
        return self._dedupe(actions)

    def _installation_guide(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        prerequisites = self._matching_lines(text, ["prerequisite", "requirement", "dependency", "install", "environment"])
        commands = self._matching_lines(text, ["docker", "npm", "pip", "python", "run", "command", "migrate", "build"])
        configuration = self._matching_lines(text, ["configure", "configuration", "env", "environment variable", "database", "api key", "port"])
        verification = self._matching_lines(text, ["verify", "test", "health", "check", "smoke"])
        actions = []
        if prerequisites:
            actions.append("Review prerequisites and dependencies before starting setup.")
        if configuration:
            actions.append("Confirm configuration and environment values for the target setup.")
        if commands or verification:
            actions.append("Run setup commands and verification checks in order.")
        if not actions:
            actions.append("Review setup steps, dependencies, and verification notes.")
        summary = self._sentence([document.title, "technical setup guide", self._list_preview(prerequisites, "prerequisites", 2)])
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, document.title, profile="installation_guide"),
            action_items=actions,
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=False,
            workflow_metadata={
                "technical_guide": {
                    "document_subtype": "installation_guide",
                    "prerequisites": prerequisites[:6],
                    "setup_commands": commands[:8],
                    "configuration_notes": configuration[:6],
                    "verification_steps": verification[:5],
                }
            },
        )

    def _implementation_schedule(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        task_lines = self._matching_lines(text, ["task", "feature", "implementation", "roadmap", "milestone"])
        status_lines = self._matching_lines(text, ["status", "claimed", "owner", "blocked", "done", "in progress"])
        testing_lines = self._matching_lines(text, ["testing", "coverage", "pipeline", "qa", "test"])
        sheet_names = self._sheet_names(text)
        actions = []
        if task_lines or status_lines:
            actions.append("Review open implementation tasks, status, and claimed ownership.")
        if testing_lines:
            actions.append("Check testing, coverage, and pipeline status before the next milestone.")
        if not actions:
            actions.append("Review tracker rows for task status, ownership, and schedule changes.")
        summary = self._sentence([document.title, "engineering implementation schedule", self._list_preview(sheet_names, "sheets", 3)])
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, document.title, profile="implementation_schedule"),
            action_items=actions,
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=True,
            workflow_metadata={
                "tracker": {
                    "document_subtype": "implementation_schedule",
                    "sheet_names": sheet_names,
                    "task_rows": task_lines[:8],
                    "status_rows": status_lines[:8],
                    "testing_rows": testing_lines[:8],
                }
            },
        )

    def _is_presentation_action(self, item: str) -> bool:
        lowered = item.lower()
        return any(
            term in lowered
            for term in [
                "presentation",
                "slide",
                "speaking",
                "speaker",
                "rehearse",
                "practice",
                "delivery",
                "timing",
                "audience",
                "transition",
                "examples",
                "emphasis",
            ]
        )

    def _resume_profile(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        person_name = self._resume_person_name(document, text)
        education = self._resume_section_lines(text, ["education"], ["experience", "projects", "skills", "technical skills"])
        experience = self._resume_section_lines(text, ["experience"], ["education", "projects", "skills", "technical skills"])
        projects = self._resume_section_lines(text, ["projects"], ["education", "experience", "skills", "technical skills"])
        skills = self._resume_section_lines(text, ["skills", "technical skills"], ["education", "experience", "projects"])
        graduation = self._first_matching_line(text, ["graduation", "expected", "class of", "202", "2026", "2027"])
        gpa = self._first_matching_line(text, ["gpa"])
        links = self._contact_links(text)
        summary = self._sentence([person_name, self._resume_degree(education), graduation, "resume profile"])
        return WorkflowEnrichment(
            workflow_summary=summary or self._direct_text_summary(text, document.title, profile="resume_profile"),
            action_items=["Review education, experience, projects, and skills for completeness."],
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=False,
            workflow_metadata={
                "resume": {
                    "person_name": person_name,
                    "education": education[:5],
                    "degree": self._resume_degree(education),
                    "graduation": graduation,
                    "gpa": gpa,
                    "work_experience": experience[:6],
                    "projects": projects[:6],
                    "technical_skills": skills[:8],
                    "contact_links": links,
                }
            },
        )

    def _profile_record(self, document: Document, text: str, mode: str) -> WorkflowEnrichment:
        facts = self._profile_facts(text)
        title = self._clean_text_fragment(document.title) or "Profile Note"
        fact_overview = self._profile_fact_overview(facts)
        summary = (
            f"Profile-like text containing {fact_overview}."
            if fact_overview
            else self._direct_text_summary(text, title, profile="profile_record")
        )
        return WorkflowEnrichment(
            workflow_summary=summary,
            action_items=[],
            warnings=[],
            key_dates=self._key_dates(document, text),
            urgency_level="low",
            follow_up_required=False,
            workflow_metadata={
                "profile": {
                    "identity_facts": facts[:8],
                    "profile_title": title,
                    "profile_type_hint": self._profile_type_hint(text),
                }
            },
        )

    def _receipt_spend_summary(self, document: Document, text: str) -> str:
        merchant = self._merchant_display(document) or "Unknown merchant"
        amount = self._money(document.extracted_amount) or "unknown amount"
        category = (document.category or "uncategorized").replace("_", " ")
        context = self._category_context(text, document.category or "")
        context_label = self._context_label(context)
        if context.get("subtype") == "repair_service":
            return f"Repair-service receipt from {merchant} totaling {amount} with parts and labor charges."
        category_phrase = f"{category}"
        if context_label:
            category_phrase = f"{category}, likely {context_label}"
        return f"{merchant} receipt for {amount}, categorized as {category_phrase}."

    def _merchant_confidence(self, document: Document) -> str:
        if document.merchant_name and document.extracted_amount and document.extracted_date:
            return "high"
        if document.merchant_name:
            return "medium"
        return "low"

    def _expected_total(self, document: Document) -> Decimal | None:
        if document.subtotal is None or document.tax is None:
            return None
        return (document.subtotal + document.tax).quantize(Decimal("0.01"))

    def _item_lines(self, text: str) -> list[str]:
        descriptive_lines = []
        fallback_lines = []
        for line in text.splitlines():
            cleaned = self._clean_item_line(line)
            if not cleaned:
                continue
            if re.search(r"\b(total|subtotal|tax|balance|amount due|visa|mastercard|cash|change)\b", cleaned, re.IGNORECASE):
                continue
            if re.search(r"\d+\.\d{2}\b", cleaned) and len(cleaned) <= 120:
                fallback_lines.append(cleaned)
                if self._is_descriptive_item_line(cleaned):
                    descriptive_lines.append(cleaned)
        preferred = descriptive_lines or fallback_lines
        return self._dedupe(preferred)

    def _key_dates(self, document: Document, text: str) -> list[str]:
        dates = []
        if document.extracted_date:
            dates.append(document.extracted_date.isoformat())
        dates.extend(self._date_candidates(text))
        return self._normalize_date_list(dates)

    def _first_date(self, text: str) -> str | None:
        dates = self._key_dates(_DateOnlyDocument(), text)
        return dates[0] if dates else None

    def _date_near_label(self, text: str, labels: list[str]) -> str | None:
        for line in text.splitlines():
            lowered = line.lower().replace("_", " ")
            if any(label in lowered for label in labels):
                dates = self._date_candidates(line)
                if dates:
                    return self._normalize_date_string(dates[0]) or dates[0]
        return None

    def _deadline_phrase_near_label(self, text: str, labels: list[str]) -> str | None:
        for line in text.splitlines():
            lowered = line.lower().replace("_", " ")
            if not any(label in lowered for label in labels):
                continue
            weekday_match = re.search(
                r"\b(?:by|before|until|on)\s+((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
                r"(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm))?)\b",
                line,
            )
            if weekday_match:
                phrase = weekday_match.group(1)
                activation_match = re.search(
                    r"\bfor\s+((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+activation)\b",
                    line,
                    flags=re.IGNORECASE,
                )
                if activation_match:
                    phrase = f"{phrase} for {activation_match.group(1)}"
                return self._clean_text_fragment(phrase)
            time_match = re.search(r"\b(?:by|before|until)\s+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm))\b", line)
            if time_match:
                return self._clean_text_fragment(time_match.group(1))
        return None

    def _amount_near_label(self, text: str, labels: list[str]) -> Decimal | None:
        for line in text.splitlines():
            lowered = line.lower().replace("_", " ")
            if any(label in lowered for label in labels):
                matches = re.findall(r"([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{2}))", line)
                if matches:
                    return Decimal(matches[-1].replace(",", ""))
        return None

    def _billing_period(self, text: str) -> str | None:
        match = re.search(
            r"\b(?:billing period|service period|period)\s*:?\s*([A-Za-z0-9, /.-]{6,60})",
            text,
            re.IGNORECASE,
        )
        return match.group(1).strip() if match else None

    def _action_lines(self, text: str) -> list[str]:
        actions = []
        for line in text.splitlines():
            if re.search(r"\b(please|must|required|bring|submit|pay|register|attend|contact|rsvp)\b", line, re.IGNORECASE):
                actions.append(re.sub(r"\s+", " ", line).strip())
        return self._dedupe(actions)

    def _notice_type_hint(self, text: str) -> str:
        lowered = text.lower()
        if self._looks_like_syllabus(text):
            return "syllabus"
        if self._looks_like_presentation_guide(text):
            return "presentation_guide"
        if "meeting" in lowered:
            return "meeting"
        if "payment" in lowered or "tuition" in lowered or "fee" in lowered:
            return "payment"
        if "deadline" in lowered or "submit" in lowered:
            return "submission"
        if "event" in lowered or "night" in lowered:
            return "event"
        return "general_notice"

    def _expense_type_hints(self, text: str) -> list[str]:
        hints = []
        lowered = text.lower()
        for hint in ["travel", "meal", "supplies", "software", "printing", "parking"]:
            if hint in lowered:
                hints.append(hint)
        return hints or ["general_business"]

    def _key_entities(self, text: str) -> list[str]:
        candidates = []
        for line in text.splitlines()[:12]:
            cleaned = re.sub(r"[^A-Za-z0-9 &.,'-]", "", line).strip()
            if 3 <= len(cleaned) <= 80 and not re.search(r"\d{2,}", cleaned):
                candidates.append(cleaned)
        return self._dedupe(candidates[:5])

    def _first_meaningful_line(self, text: str) -> str | None:
        for line in text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if re.fullmatch(r"sheet\s*:\s*.+", cleaned, flags=re.IGNORECASE):
                continue
            if re.fullmatch(r"invoice(?:\s+number)?\s*[:|,].+", cleaned, flags=re.IGNORECASE):
                continue
            if len(cleaned) >= 3:
                return cleaned[:120]
        return None

    def _summary_from_text(self, text: str) -> str | None:
        return self._direct_text_summary(text, None, profile="generic")

    def _apply_interpretation_hints(self, result: WorkflowEnrichment, interpretation: CategoryInterpretation) -> WorkflowEnrichment:
        if interpretation.summary_hint and (
            interpretation.ai_assisted or not result.workflow_summary or self._summary_is_generic(result.workflow_summary)
        ):
            result.workflow_summary = interpretation.summary_hint

        hint_actions = interpretation.workflow_hints.get("action_items", [])
        hint_warnings = interpretation.workflow_hints.get("warnings", [])
        if isinstance(hint_actions, list):
            result.action_items.extend(str(item) for item in hint_actions if item)
        if isinstance(hint_warnings, list):
            result.warnings.extend(str(item) for item in hint_warnings if item)

        urgency = interpretation.workflow_hints.get("urgency_level")
        if urgency in {"low", "medium", "high"}:
            result.urgency_level = self._max_urgency(result.urgency_level, urgency)
        if interpretation.workflow_hints.get("follow_up_required"):
            result.follow_up_required = True
        return result

    def _summary_is_generic(self, summary: str) -> bool:
        lowered = summary.lower()
        return (
            len(summary.split()) > 30
            or self._summary_hint_is_template(summary)
            or lowered.startswith("receipt with merchant")
            or lowered.startswith("profile-like text containing identity")
            or ";" in summary
        )

    def _max_urgency(self, current: str, new: str) -> str:
        scale = {"low": 1, "medium": 2, "high": 3}
        return new if scale.get(new, 1) > scale.get(current, 1) else current

    def _has_urgent_language(self, text: str) -> bool:
        return re.search(r"\b(overdue|urgent|immediately|final notice|past due|due now)\b", text, re.IGNORECASE) is not None

    def _has_follow_up_language(self, text: str) -> bool:
        return re.search(r"\b(follow up|respond|reply|sign|submit|required|deadline|due)\b", text, re.IGNORECASE) is not None

    def _money(self, amount: Decimal | None) -> str | None:
        return f"${amount}" if amount is not None else None

    def _sentence(self, parts: list[str | None]) -> str | None:
        values = [self._clean_text_fragment(part) for part in parts]
        values = [part for part in values if part]
        if not values:
            return None
        sentence = ", ".join(values)
        return sentence[:500]

    def _spend_summary(self, document: Document, mode: str, category_context: dict[str, Any]) -> str:
        merchant = self._merchant_display(document) or "Purchase"
        amount = self._money(document.extracted_amount)
        category = mode.replace("_", " ")
        context_label = self._context_label(category_context)
        if amount and context_label:
            return f"{merchant} purchase for {amount}, categorized as {category} with {context_label} context."
        if amount:
            return f"{merchant} purchase for {amount}, categorized as {category}."
        if context_label:
            return f"{merchant} purchase categorized as {category} with {context_label} context."
        return f"{merchant} purchase categorized as {category}."

    def _category_spend_note(self, mode: str, category_context: dict[str, Any]) -> str:
        category = mode.replace("_", " ")
        context_label = self._context_label(category_context)
        if context_label:
            confidence = category_context.get("confidence", "low")
            return f"Classified as {category}; extracted text also suggests {context_label} context ({confidence} confidence)."
        return f"Classified as {category} based on extracted text and category signals."

    def _category_context(self, text: str, mode: str) -> dict[str, Any]:
        lowered = text.lower()
        context_rules: list[tuple[str, list[str]]] = [
            ("repair_service", ["repair", "service", "labor", "parts", "maintenance", "technician", "brake", "cable", "pedal"]),
            ("pet_supplies", ["pet", "pets", "dog", "cat", "puppy", "kitten", "litter", "leash", "collar", "kibble", "purina", "friskies"]),
            ("grocery_style", ["grocery", "produce", "banana", "milk", "bread", "eggs", "deli", "meat", "vegetable", "fruit"]),
            ("pharmacy_health", ["pharmacy", "rx", "prescription", "medication", "clinic", "vitamin"]),
            ("home_improvement", ["hardware", "paint", "lumber", "tool", "garden", "plumbing", "electrical"]),
            ("electronics", ["electronics", "charger", "cable", "battery", "phone", "adapter", "usb"]),
            ("apparel", ["shirt", "pants", "shoe", "jacket", "apparel", "clothing"]),
            ("office_supplies", ["office", "paper", "staples", "ink", "toner", "folder", "notebook"]),
            ("fuel_transport", ["fuel", "gasoline", "diesel", "parking", "toll", "uber", "lyft", "taxi"]),
            ("meal_or_cafe", ["coffee", "latte", "cafe", "restaurant", "sandwich", "pizza", "burger", "meal"]),
        ]
        matches: list[tuple[str, list[str]]] = []
        for context, keywords in context_rules:
            signals = [keyword for keyword in keywords if re.search(rf"\b{re.escape(keyword)}\b", lowered)]
            if signals:
                matches.append((context, signals[:5]))

        if not matches:
            return {"subtype": None, "label": None, "confidence": "low", "signals": []}

        subtype, signals = max(matches, key=lambda match: len(match[1]))
        confidence = "medium" if len(signals) >= 2 or mode in {"retail", "groceries", "food_drink", "transport", "health", "office", "repair_service"} else "low"
        return {
            "subtype": subtype,
            "label": subtype.replace("_", " "),
            "confidence": confidence,
            "signals": signals,
        }

    def _content_profile(self, document: Document, text: str, mode: str) -> str:
        if self._looks_like_syllabus(text):
            return "syllabus"
        if self._looks_like_resume_profile(text):
            return "resume_profile"
        if self._looks_like_presentation_guide(text):
            return "presentation_guide"
        if self._looks_like_technical_guide(text):
            return "installation_guide"
        if self._looks_like_implementation_schedule(text):
            return "implementation_schedule"
        if self._looks_like_meeting_notice(text):
            return "meeting_notice"
        if self._looks_like_profile_record(text):
            return "profile_record"
        return "standard"

    def _looks_like_syllabus(self, text: str) -> bool:
        lowered = text.lower()
        signals = ["syllabus", "course code", "semester", "instructor", "office hours", "grading", "required materials"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_presentation_guide(self, text: str) -> bool:
        lowered = text.lower()
        signals = ["presentation", "slide", "audience", "speaker", "rehearse", "talk track", "speaking notes"]
        return sum(signal in lowered for signal in signals) >= 2

    def _looks_like_resume_profile(self, text: str) -> bool:
        lowered = text.lower()
        signals = ["education", "experience", "projects", "skills", "technical skills", "gpa", "linkedin", "github"]
        return sum(signal in lowered for signal in signals) >= 3

    def _looks_like_meeting_notice(self, text: str) -> bool:
        lowered = text.lower()
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

    def _looks_like_profile_record(self, text: str) -> bool:
        lowered = text.lower()
        if self._looks_like_technical_guide(text) or self._looks_like_implementation_schedule(text):
            return False
        signals = [
            r"(?m)^\s*name\s*:",
            r"(?m)^\s*(?:student\s+)?id\s*:",
            r"(?m)^\s*major\s*:",
            r"(?m)^\s*age\s*:",
            r"(?m)^\s*dob\s*:",
            r"(?m)^\s*department\s*:",
        ]
        return sum(bool(re.search(signal, lowered)) for signal in signals) >= 2

    def _looks_like_technical_guide(self, text: str) -> bool:
        lowered = text.lower()
        title_hits = sum(signal in lowered for signal in ["installation guide", "setup guide", "technical guide", "project setup", "engineering documentation"])
        instruction_hits = sum(signal in lowered for signal in ["install", "installation", "setup", "configure", "configuration", "environment", "dependencies", "prerequisites", "run", "command", "docker", "api", "database"])
        return title_hits >= 1 or instruction_hits >= 4

    def _looks_like_implementation_schedule(self, text: str) -> bool:
        lowered = text.lower()
        structure_hits = sum(signal in lowered for signal in ["sheet:", "|", "task", "feature", "status", "claimed", "owner"])
        planning_hits = sum(signal in lowered for signal in ["implementation", "schedule", "roadmap", "tracker", "testing", "coverage", "pipeline", "milestone"])
        return (structure_hits >= 3 and planning_hits >= 2) or planning_hits >= 4

    def _context_label(self, category_context: dict[str, Any]) -> str | None:
        label = category_context.get("label")
        return str(label) if label else None

    def _merchant_display(self, document: Document) -> str | None:
        for value in [document.merchant_name, document.title]:
            cleaned = self._clean_text_fragment(value)
            if cleaned:
                return cleaned
        return None

    def _clean_text_fragment(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        cleaned = re.sub(r"\s*[-–—]+\s*[.,:;]*\s*$", "", cleaned)
        cleaned = re.sub(r"(?:\s+[.,:;|%]+)+$", "", cleaned)
        cleaned = re.sub(r"\s+[-–—]\s+[.,:;]+$", "", cleaned)
        cleaned = cleaned.strip(" \t\r\n-–—|")
        if not cleaned or re.fullmatch(r"[.,:;/%\\-]+", cleaned):
            return None
        return self._truncate_text(cleaned, 160)

    def _clean_sentence_part(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        cleaned = re.sub(r"\s*[-–—]+\s*[.,:;]*\s*$", "", cleaned)
        cleaned = re.sub(r"(?:\s+[.,:;|%]+)+$", "", cleaned)
        cleaned = cleaned.strip(" \t\r\n-–—|")
        if not cleaned or re.fullmatch(r"[.,:;/%\\-]+", cleaned):
            return None
        return self._truncate_text(cleaned, 320)

    def _truncate_text(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        truncated = value[:limit].rstrip()
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        return truncated.rstrip(" ,;:-")

    def _direct_text_summary(self, text: str, title: str | None, profile: str = "generic") -> str | None:
        lines = self._unique_content_lines(text)
        if profile == "profile_record":
            facts = self._profile_facts(text)
            if facts:
                return ", ".join(facts[:5])
        if profile == "syllabus":
            summary = self._sentence([self._course_title_text(text) or title, self._course_code(text), self._semester(text), self._line_after_label(text, ["instructor", "professor"])])
            if summary:
                return summary
        if profile == "presentation_guide":
            summary = self._sentence([
                title or self._first_matching_line(text, ["presentation", "talk"]),
                self._line_after_label(text, ["purpose", "goal", "objective"]),
                self._line_after_label(text, ["audience", "target audience"]),
            ])
            if summary:
                return summary
        fact_lines = self._fact_like_lines(lines)
        if fact_lines:
            return "; ".join(fact_lines[:4])[:500]
        summary_lines = [line for line in lines if not self._is_placeholder_title(line)]
        if title and title not in summary_lines[:2]:
            summary_lines.insert(0, title)
        return " ".join(summary_lines[:4])[:500] if summary_lines else None

    def _clean_item_line(self, line: str) -> str:
        cleaned = re.sub(r"\s+", " ", line).strip()
        cleaned = cleaned.replace("�", "")
        cleaned = re.sub(r"\s+([.,])", r"\1", cleaned)
        cleaned = re.sub(r"([A-Za-z])\s+%", r"\1", cleaned)
        cleaned = self._strip_amount_suffix_noise(cleaned)
        cleaned = self._strip_embedded_item_codes(cleaned)
        cleaned = re.sub(r"\s+[%|]+$", "", cleaned)
        cleaned = re.sub(r"(?<=\d\.\d{2})\s+[A-Z]{1,3}$", "", cleaned)
        cleaned = re.sub(r"\s+\b(?:KX|XX|XXX)\b(?=\s|$)", "", cleaned, flags=re.IGNORECASE)
        cleaned = self._strip_amount_suffix_noise(cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|")
        return cleaned

    def _strip_amount_suffix_noise(self, line: str) -> str:
        """Remove OCR junk glued to receipt amounts without touching product text."""
        return re.sub(
            r"(?P<amount>\b\d{1,6}(?:,\d{3})*\.\d{2})(?:\s*(?:[%|;:*!#~^`]+|[.,]+))(?=\s|$)",
            r"\g<amount>",
            line,
        )

    def _strip_embedded_item_codes(self, line: str) -> str:
        if not re.search(r"\b\d{1,6}(?:,\d{3})*\.\d{2}\b", line):
            return line
        alpha_word_count = len(re.findall(r"\b[A-Za-z][A-Za-z/&'-]{1,}\b", line))
        if alpha_word_count < 2:
            return line
        cleaned = re.sub(r"\b\d{8,14}\b", "", line)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned or line

    def _is_descriptive_item_line(self, line: str) -> bool:
        alpha_words = re.findall(r"\b[A-Za-z][A-Za-z/&'-]{1,}\b", line)
        digit_blobs = re.findall(r"\b\d{4,}\b", line)
        service_terms = ["repair", "service", "labor", "parts", "maintenance", "brake", "cable", "pedal"]
        has_service_term = any(term in line.lower() for term in service_terms)
        return (len(alpha_words) >= 2 and len(digit_blobs) <= 1) or has_service_term

    def _date_candidates(self, text: str) -> list[str]:
        pattern = (
            r"\b(?:"
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
            r"\d{4}-\d{1,2}-\d{1,2}|"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4}"
            r")\b"
        )
        return re.findall(pattern, text, flags=re.IGNORECASE)

    def _normalize_date_list(self, values: list[str | None]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            normalized = self._normalize_date_string(value)
            if not normalized:
                continue
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    def _normalize_date_string(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"\s+", " ", value.strip().rstrip(".,"))
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%m-%d-%Y",
            "%m-%d-%y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%b. %d, %Y",
        ]
        for fmt in formats:
            try:
                parsed = datetime.strptime(cleaned, fmt).date()
                return parsed.isoformat()
            except ValueError:
                continue
        return None

    def _dedupe(self, values: list[str | None]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            if not value:
                continue
            cleaned = re.sub(r"\s+", " ", value).strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result

    def _unique_content_lines(self, text: str) -> list[str]:
        lines = []
        for line in text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if not cleaned or self._is_placeholder_title(cleaned):
                continue
            lines.append(cleaned)
        return self._dedupe(lines)

    def _fact_like_lines(self, lines: list[str]) -> list[str]:
        return [line for line in lines if ":" in line and len(line) <= 120][:8]

    def _profile_facts(self, text: str) -> list[str]:
        wanted = {"name", "id", "student id", "major", "age", "dob", "department", "role"}
        facts = []
        for line in self._unique_content_lines(text):
            if ":" not in line:
                continue
            key, value = [part.strip() for part in line.split(":", 1)]
            if key.lower() in wanted and value:
                facts.append(f"{key}: {value}")
        return self._dedupe(facts)

    def _profile_fact_overview(self, facts: list[str]) -> str | None:
        if not facts:
            return None
        labels = []
        for fact in facts[:5]:
            key = fact.split(":", 1)[0].strip().lower()
            labels.append(key)
        if len(labels) == 1:
            readable = labels[0]
        elif len(labels) == 2:
            readable = " and ".join(labels)
        else:
            readable = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        return readable

    def _profile_type_hint(self, text: str) -> str:
        lowered = text.lower()
        if any(term in lowered for term in ["major:", "student id", "department:"]):
            return "education_record"
        return "profile_record"

    def _resume_person_name(self, document: Document, text: str) -> str | None:
        title = self._clean_text_fragment(document.title)
        if title and "resume" not in title.lower():
            return title
        for line in self._unique_content_lines(text)[:5]:
            if 3 <= len(line) <= 60 and not re.search(r"[@:/]|linkedin|github|resume", line, re.IGNORECASE):
                words = line.split()
                if 1 < len(words) <= 4:
                    return line
        return title

    def _resume_degree(self, education_lines: list[str]) -> str | None:
        for line in education_lines:
            match = re.search(r"(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|Bachelor(?:'s)?|Master(?:'s)?)", line, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def _contact_links(self, text: str) -> list[str]:
        return re.findall(r"(https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|linkedin\.com/\S+|github\.com/\S+)", text, flags=re.IGNORECASE)[:6]

    def _resume_section_lines(self, text: str, headers: list[str], stop_headers: list[str]) -> list[str]:
        lines = self._unique_content_lines(text)
        results: list[str] = []
        capturing = False
        for line in lines:
            lowered = line.lower().rstrip(":")
            if any(lowered == header for header in headers):
                capturing = True
                continue
            if capturing and any(lowered == header for header in stop_headers):
                break
            if capturing:
                results.append(line)
        return results[:8]

    def _course_title(self, document: Document, text: str) -> str | None:
        return self._course_title_text(text) or self._clean_text_fragment(document.title)

    def _course_title_text(self, text: str) -> str | None:
        lines = self._unique_content_lines(text)[:10]
        for index, line in enumerate(lines):
            lowered = line.lower()
            if lowered == "syllabus" and index > 0:
                previous = lines[index - 1]
                if previous and not self._is_placeholder_title(previous):
                    return f"{previous} Syllabus"
            if any(keyword in lowered for keyword in ["syllabus", "course", "seminar", "introduction", "guide"]):
                return line
            if self._course_code(line):
                continue
        return None

    def _course_code(self, text: str) -> str | None:
        match = re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", text)
        return match.group(0) if match else None

    def _sheet_names(self, text: str) -> list[str]:
        names = []
        for line in text.splitlines():
            match = re.match(r"\s*sheet\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
            if match:
                cleaned = self._clean_text_fragment(match.group(1))
                if cleaned:
                    names.append(cleaned)
        return self._dedupe(names)[:6]

    def _semester(self, text: str) -> str | None:
        match = re.search(r"\b(?:spring|summer|fall|winter)\s+\d{4}\b", text, re.IGNORECASE)
        return match.group(0) if match else None

    def _line_after_label(self, text: str, labels: list[str]) -> str | None:
        for line in self._unique_content_lines(text):
            lowered = line.lower().replace("_", " ")
            for label in labels:
                if re.match(rf"^{re.escape(label)}\s*[:|,-]\s*", lowered, flags=re.IGNORECASE):
                    return re.sub(rf"^{re.escape(label)}\s*[:|,-]\s*", "", line, flags=re.IGNORECASE).strip()
        return None

    def _matching_lines(self, text: str, keywords: list[str]) -> list[str]:
        matches = []
        for line in self._unique_content_lines(text):
            lowered = line.lower()
            if any(keyword in lowered for keyword in keywords):
                matches.append(line)
        return self._dedupe(matches)

    def _first_matching_line(self, text: str, keywords: list[str]) -> str | None:
        matches = self._matching_lines(text, keywords)
        return matches[0] if matches else None

    def _is_placeholder_title(self, value: str) -> bool:
        lowered = value.lower()
        return bool(
            re.fullmatch(r"(page|slide)\s+\d+", lowered)
            or re.fullmatch(r"(?:연도|년도)\s*[.년]\s*월\s*[.월]\s*일\s*[.일]?", lowered)
        )


class _DateOnlyDocument:
    extracted_date: date | None = None
