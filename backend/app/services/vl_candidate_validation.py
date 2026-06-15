from __future__ import annotations

from decimal import Decimal
from typing import Any


class VLCandidateValidationGate:
    """Decide whether a structured VL candidate is safe to promote.

    The gate itself never mutates confirmed document fields. Callers may promote
    clean candidates fully, or promote review-required candidates partially when
    the remaining issues are field/row-level review warnings. Only runtime
    failures, parser failures, dangerous issues, and hard document conflicts
    should force a PP-OCR fallback.
    """

    dangerous_issue_codes = {
        "vl_candidate_hallucinated_blank_quantity",
        "vl_candidate_exchange_rate_as_amount",
        "vl_candidate_dangerous_manual_error",
        "vl_candidate_manual_hallucination",
        "vl_candidate_document_number_mismatch",
        "vl_candidate_parser_failed",
        "no_price_candidate_amount_conflict",
    }

    review_issue_codes = {
        "vl_candidate_requires_review",
        "vl_candidate_missing_quantity",
        "vl_candidate_quantity_cell_blank",
        "vl_candidate_missing_line_amount",
        "vl_candidate_missing_document_total",
        "vl_candidate_missing_row_anchor",
        "vl_candidate_missing_row_fragment",
        "vl_candidate_missing_row_cell",
        "vl_candidate_known_input_limitation",
        "vl_candidate_missing_expected_pdf_value",
        "vl_candidate_missing_required_value",
        "vl_candidate_total_mismatch",
        "vl_candidate_row_count_mismatch",
        "vl_candidate_malformed_amount_columns_repaired",
        "vl_candidate_explicit_quantity_price_amount_mismatch",
        "vl_candidate_row_amount_hidden_do_not_infer",
        "vl_candidate_remaining_quantity_hidden",
        "vl_candidate_inspection_decision_hidden",
        "vl_candidate_fax_row_boundary_uncertain",
    }

    non_promotable_issue_codes = {
        "vl_candidate_header_row_as_item",
        "vl_candidate_total_mismatch",
        "vl_candidate_row_count_mismatch",
        "vl_candidate_return_credit_type_uncertain",
        "vl_candidate_internal_transfer_type_uncertain",
        "vl_candidate_total_row_amount_conflict",
        "vl_candidate_invalid_line_total",
        "vl_candidate_invalid_tax_greater_than_supply",
        "vl_candidate_invalid_tax_greater_than_total",
        "vl_candidate_invalid_supply_greater_than_total",
    }

    no_price_profiles = {
        "no_price_document",
        "inventory_movement_document",
        "quality_document",
    }

    def evaluate(self, document: Any, candidate: dict[str, Any]) -> dict[str, Any]:
        structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else None
        if not structured:
            return self._result("auxiliary_only", ["structured_candidate_missing"], [])

        issue_codes = self._candidate_issue_codes(candidate, structured)
        reasons: list[str] = []
        document_checks = self._document_checks(document, structured)
        issue_codes.extend(code for code in document_checks.get("issue_codes", []) if code not in issue_codes)

        if set(issue_codes) & self.dangerous_issue_codes:
            return self._result("reject", ["dangerous_vl_candidate_issue"], issue_codes, document_checks)
        if document_checks.get("hard_conflict"):
            return self._result("reject", ["document_conflict"], issue_codes, document_checks)

        if set(issue_codes) & self.review_issue_codes:
            reasons.append("vl_candidate_has_review_issues")
        elif any(
            code.startswith("vl_candidate_invalid_") or code.startswith("vl_candidate_untrusted_")
            for code in issue_codes
        ):
            reasons.append("vl_candidate_has_review_issues")
        if not candidate.get("provider_available_candidate"):
            reasons.append("provider_candidate_not_available")
        if structured.get("line_item_count") in (None, 0) and not (structured.get("line_items") or []):
            reasons.append("structured_line_items_missing")

        if reasons:
            has_non_promotable_issue = bool(set(issue_codes) & self.non_promotable_issue_codes) or any(
                code.startswith("vl_candidate_invalid_") for code in issue_codes
            )
            can_partial_promote = (
                "provider_candidate_not_available" not in reasons
                and "structured_line_items_missing" not in reasons
                and not has_non_promotable_issue
                and bool(structured.get("document") or structured.get("line_items"))
            )
            return self._result(
                "review_required",
                reasons,
                issue_codes,
                document_checks,
                auto_promote=can_partial_promote,
                promotion_mode="partial" if can_partial_promote else "none",
            )

        return self._result(
            "promotion_eligible",
            ["validated_candidate_without_known_issues"],
            issue_codes,
            document_checks,
            auto_promote=True,
            promotion_mode="full",
        )

    def _candidate_issue_codes(self, candidate: dict[str, Any], structured: dict[str, Any]) -> list[str]:
        codes: list[str] = []
        for source in (
            candidate.get("issue_codes"),
            candidate.get("review_flags"),
            structured.get("issue_codes"),
            structured.get("review_flags"),
        ):
            if isinstance(source, list):
                codes.extend(str(code) for code in source if code not in (None, ""))
        for issue in structured.get("issues") or []:
            if isinstance(issue, dict) and issue.get("code"):
                codes.append(str(issue["code"]))
        return list(dict.fromkeys(codes))

    def _document_checks(self, document: Any, structured: dict[str, Any]) -> dict[str, Any]:
        candidate_doc = structured.get("document") if isinstance(structured.get("document"), dict) else {}
        codes: list[str] = []
        hard_conflict = False

        confirmed_number = str(getattr(document, "document_number", "") or "").strip()
        candidate_number = str(candidate_doc.get("document_number") or "").strip()
        if confirmed_number and candidate_number and confirmed_number != candidate_number:
            codes.append("vl_candidate_document_number_mismatch")
            hard_conflict = True

        confirmed_total = self._decimal_text(getattr(document, "extracted_amount", None))
        candidate_total = self._decimal_text(candidate_doc.get("total"))
        if confirmed_total and candidate_total and confirmed_total != candidate_total:
            codes.append("vl_candidate_total_mismatch")

        profiles = self._profile_values(document)
        if profiles & self.no_price_profiles and self._candidate_has_amounts(candidate_doc, structured):
            codes.append("no_price_candidate_amount_conflict")
            hard_conflict = True

        return {
            "issue_codes": codes,
            "hard_conflict": hard_conflict,
            "confirmed_document_number": confirmed_number or None,
            "candidate_document_number": candidate_number or None,
            "confirmed_total": confirmed_total,
            "candidate_total": candidate_total,
            "profiles": sorted(profiles),
        }

    def _profile_values(self, document: Any) -> set[str]:
        metadata = getattr(document, "workflow_metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        taxonomy = metadata.get("taxonomy") if isinstance(metadata.get("taxonomy"), dict) else {}
        values: set[str] = set()
        for value in (
            metadata.get("document_profile"),
            metadata.get("content_profile"),
            taxonomy.get("document_profile"),
        ):
            if value:
                values.add(str(value))
        for source in (metadata, taxonomy):
            for key in ("document_profiles", "profiles"):
                items = source.get(key) if isinstance(source, dict) else None
                if isinstance(items, list):
                    values.update(str(item) for item in items if item)
        return values

    def _candidate_has_amounts(self, candidate_doc: dict[str, Any], structured: dict[str, Any]) -> bool:
        if any(candidate_doc.get(field) not in (None, "", []) for field in ("currency", "subtotal", "tax", "total")):
            return True
        for item in structured.get("line_items") or []:
            if not isinstance(item, dict):
                continue
            if any(item.get(field) not in (None, "", []) for field in ("unit_price", "supply_amount", "tax_amount", "line_total")):
                return True
        return False

    def _decimal_text(self, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        try:
            return str(Decimal(str(value).replace(",", "")))
        except Exception:
            return None

    def _result(
        self,
        decision: str,
        reasons: list[str],
        issue_codes: list[str],
        document_checks: dict[str, Any] | None = None,
        *,
        auto_promote: bool = False,
        promotion_mode: str = "none",
    ) -> dict[str, Any]:
        return {
            "decision": decision,
            "auto_promote": auto_promote,
            "promotion_mode": promotion_mode,
            "reasons": list(dict.fromkeys(reasons)),
            "issue_codes": list(dict.fromkeys(issue_codes)),
            "document_checks": document_checks or {},
        }
