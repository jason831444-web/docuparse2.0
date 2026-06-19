from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from app.models.document import Document, ProcessingStatus


VERIFIED_STATUSES = {ProcessingStatus.confirmed, ProcessingStatus.completed}
AMOUNT_TOLERANCE = Decimal("1")


class MonthlyReportService:
    """Build business-data transaction reports from reviewed manufacturing documents."""

    def build(self, documents: list[Document], *, year: int, month: int) -> dict[str, Any]:
        start = date(year, month, 1)
        end = date(year + (month // 12), (month % 12) + 1, 1)
        return self.build_for_range(documents, start_date=start, end_date=end, period="month")

    def build_for_range(
        self,
        documents: list[Document],
        *,
        start_date: date,
        end_date: date,
        period: str = "custom",
        party_name: str | None = None,
    ) -> dict[str, Any]:
        range_documents = [document for document in documents if self._belongs_to_range(document, start_date, end_date)]
        if party_name:
            normalized_party = self._normalize_party_filter(party_name)
            range_documents = [
                document
                for document in range_documents
                if self._normalize_party_filter(self._party_name(document)) == normalized_party
            ]
        verified_documents = [document for document in range_documents if self._is_verified(document)]
        pending_documents = [document for document in range_documents if not self._is_verified(document)]

        by_party: dict[str, dict[str, Any]] = {}
        by_item: dict[tuple[str, str], dict[str, Any]] = {}
        by_document_type: dict[str, dict[str, Any]] = {}
        missing_required_fields: list[dict[str, Any]] = []
        calculation_mismatches: list[dict[str, Any]] = []
        pending_issue_rows: list[dict[str, Any]] = []

        for document in range_documents:
            missing_required_fields.extend(self._missing_required_field_issues(document))
            calculation_mismatches.extend(self._calculation_mismatch_issues(document))
            if not self._is_verified(document):
                pending_issue_rows.append(self._issue_row(document, "미검수 문서", "검수 완료 전 문서입니다."))

        total_amount = Decimal("0")
        no_price_documents = 0
        review_required_documents = 0
        for document in range_documents:
            document_type = self._document_type_value(document)
            type_row = by_document_type.setdefault(
                document_type,
                {
                    "document_type": document_type,
                    "document_count": 0,
                    "verified_documents": 0,
                    "pending_documents": 0,
                    "total_amount": Decimal("0"),
                    "no_price_documents": 0,
                },
            )
            type_row["document_count"] += 1
            if self._is_verified(document):
                type_row["verified_documents"] += 1
            else:
                type_row["pending_documents"] += 1
            if not self._amount_required(document):
                no_price_documents += 1
                type_row["no_price_documents"] += 1
            if document.review_required:
                review_required_documents += 1
            amount = self._document_amount(document)
            if amount is not None and self._is_verified(document):
                type_row["total_amount"] += amount

        for document in verified_documents:
            amount = self._document_amount(document)
            if amount is not None:
                total_amount += amount
            party = self._party_name(document)
            party_row = by_party.setdefault(party, {"name": party, "document_count": 0, "total_amount": Decimal("0")})
            party_row["document_count"] += 1
            if amount is not None:
                party_row["total_amount"] += amount

            for item in document.line_items or []:
                if not isinstance(item, dict):
                    continue
                item_name = self._clean_text(item.get("item_name")) or "품목 미확인"
                spec = self._clean_text(item.get("specification") or item.get("spec")) or ""
                item_key = (item_name, spec)
                item_row = by_item.setdefault(
                    item_key,
                    {"item_name": item_name, "spec": spec, "quantity": Decimal("0"), "total_amount": Decimal("0")},
                )
                quantity = self._decimal(item.get("quantity"))
                if quantity is not None:
                    item_row["quantity"] += quantity
                line_amount = self._line_amount(item)
                if line_amount is not None:
                    item_row["total_amount"] += line_amount

        by_party_rows = sorted(by_party.values(), key=lambda row: (row["total_amount"], row["document_count"]), reverse=True)
        by_item_rows = sorted(by_item.values(), key=lambda row: (row["total_amount"], row["quantity"]), reverse=True)
        by_document_type_rows = sorted(
            by_document_type.values(),
            key=lambda row: (row["document_count"], row["total_amount"]),
            reverse=True,
        )
        issues = {
            "missing_required_fields": missing_required_fields,
            "calculation_mismatches": calculation_mismatches,
            "pending_documents": pending_issue_rows,
        }
        return {
            "year": start_date.year,
            "month": start_date.month,
            "period": period,
            "start_date": start_date.isoformat(),
            "end_date": self._inclusive_end_date(end_date).isoformat(),
            "range_label": self._range_label(start_date, end_date, period),
            "party_name": party_name or None,
            "summary": {
                "total_documents": len(range_documents),
                "verified_documents": len(verified_documents),
                "pending_documents": len(pending_documents),
                "total_amount": self._number(total_amount),
                "documents_with_errors": self._documents_with_errors(issues),
                "no_price_documents": no_price_documents,
                "review_required_documents": review_required_documents,
            },
            "by_party": [self._json_row(row) for row in by_party_rows],
            "by_item": [self._json_row(row) for row in by_item_rows],
            "by_document_type": [self._json_row(row) for row in by_document_type_rows],
            "top_parties": [self._json_row(row) for row in by_party_rows[:5]],
            "top_items": [self._json_row(row) for row in by_item_rows[:10]],
            "issues": issues,
        }

    def to_excel(self, report: dict[str, Any]) -> bytes:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame([self._summary_row(report)]).to_excel(writer, index=False, sheet_name="Summary")
            pd.DataFrame(report.get("by_party") or []).to_excel(writer, index=False, sheet_name="By Party")
            pd.DataFrame(report.get("by_item") or []).to_excel(writer, index=False, sheet_name="By Item")
            pd.DataFrame(report.get("by_document_type") or []).to_excel(writer, index=False, sheet_name="By Document Type")
            pd.DataFrame(self._issue_rows(report)).to_excel(writer, index=False, sheet_name="Issues")
        return buffer.getvalue()

    def to_csv(self, report: dict[str, Any]) -> str:
        rows: list[dict[str, Any]] = []
        rows.append({"section": "Summary", **self._summary_row(report)})
        for row in report.get("by_party") or []:
            rows.append({"section": "By Party", **row})
        for row in report.get("by_item") or []:
            rows.append({"section": "By Item", **row})
        for row in report.get("by_document_type") or []:
            rows.append({"section": "By Document Type", **row})
        for row in self._issue_rows(report):
            rows.append({"section": "Issues", **row})
        buffer = io.StringIO()
        pd.DataFrame(rows).to_csv(buffer, index=False)
        return buffer.getvalue()

    def _belongs_to_month(self, document: Document, year: int, month: int) -> bool:
        value = document.issue_date or document.extracted_date or self._date_from_datetime(document.created_at)
        return bool(value and value.year == year and value.month == month)

    def _belongs_to_range(self, document: Document, start_date: date, end_date: date) -> bool:
        value = document.issue_date or document.extracted_date or self._date_from_datetime(document.created_at)
        return bool(value and start_date <= value < end_date)

    def _date_from_datetime(self, value: datetime | None) -> date | None:
        return value.date() if isinstance(value, datetime) else None

    def _is_verified(self, document: Document) -> bool:
        if document.processing_status in VERIFIED_STATUSES:
            return True
        review = (document.workflow_metadata or {}).get("review") if isinstance(document.workflow_metadata, dict) else None
        return bool(isinstance(review, dict) and review.get("approved"))

    def _party_name(self, document: Document) -> str:
        return self._clean_text(document.customer_name or document.vendor_name or document.merchant_name) or "거래처 미확인"

    def _normalize_party_filter(self, value: str | None) -> str:
        return re.sub(r"\s+", "", self._clean_text(value) or "").casefold()

    def _document_date(self, document: Document) -> str | None:
        value = document.issue_date or document.extracted_date or self._date_from_datetime(document.created_at)
        return value.isoformat() if value else None

    def _document_amount(self, document: Document) -> Decimal | None:
        explicit_amount = self._decimal(document.extracted_amount)
        if explicit_amount is not None:
            return explicit_amount
        line_total = Decimal("0")
        has_line_amount = False
        for item in document.line_items or []:
            if not isinstance(item, dict):
                continue
            amount = self._line_amount(item)
            if amount is not None:
                has_line_amount = True
                line_total += amount
        return line_total if has_line_amount else None

    def _line_amount(self, item: dict[str, Any]) -> Decimal | None:
        return self._decimal(item.get("supply_amount") or item.get("line_total") or item.get("line_amount"))

    def _missing_required_field_issues(self, document: Document) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        missing: list[str] = []
        if not self._party_name(document) or self._party_name(document) == "거래처 미확인":
            missing.append("거래처명")
        if not self._document_date(document):
            missing.append("문서 날짜")
        if not document.line_items:
            missing.append("품목")
        for index, item in enumerate(document.line_items or [], start=1):
            if not isinstance(item, dict):
                missing.append(f"{index}번째 품목")
                continue
            if not self._clean_text(item.get("item_name")):
                missing.append(f"{index}번째 품명")
            if self._decimal(item.get("quantity")) is None:
                missing.append(f"{index}번째 수량")
            if self._amount_required(document) and self._decimal(item.get("unit_price")) is None and self._line_amount(item) is None:
                missing.append(f"{index}번째 단가 또는 금액")
        if missing:
            issues.append(self._issue_row(document, "필수값 누락", ", ".join(missing)))
        return issues

    def _calculation_mismatch_issues(self, document: Document) -> list[dict[str, Any]]:
        if not self._amount_required(document):
            return []
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(document.line_items or [], start=1):
            if not isinstance(item, dict):
                continue
            quantity = self._decimal(item.get("quantity"))
            unit_price = self._decimal(item.get("unit_price"))
            line_amount = self._line_amount(item)
            if quantity is None or unit_price is None or line_amount is None:
                continue
            expected = quantity * unit_price
            if abs(expected - line_amount) > AMOUNT_TOLERANCE:
                issues.append(
                    self._issue_row(
                        document,
                        "계산 불일치",
                        f"{index}번째 품목: 수량 x 단가 {self._number(expected)}와 금액 {self._number(line_amount)}가 일치하지 않습니다.",
                    )
                )
        return issues

    def _issue_row(self, document: Document, issue_type: str, description: str) -> dict[str, Any]:
        return {
            "document_id": str(document.id) if document.id else None,
            "document_type": getattr(document.document_type, "value", str(document.document_type or "")),
            "document_number": document.document_number,
            "party_name": self._party_name(document),
            "date": self._document_date(document),
            "issue_type": issue_type,
            "description": description,
        }

    def _amount_required(self, document: Document) -> bool:
        metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
        taxonomy = metadata.get("taxonomy") if isinstance(metadata.get("taxonomy"), dict) else {}
        if taxonomy.get("amount_required") is False:
            return False
        profiles = taxonomy.get("document_profiles")
        if isinstance(profiles, list) and "no_price_document" in profiles:
            return False
        if taxonomy.get("document_profile") == "no_price_document":
            return False
        return True

    def _documents_with_errors(self, issues: dict[str, list[dict[str, Any]]]) -> int:
        ids = {
            issue.get("document_id")
            for rows in issues.values()
            for issue in rows
            if issue.get("document_id")
        }
        return len(ids)

    def _summary_row(self, report: dict[str, Any]) -> dict[str, Any]:
        summary = report.get("summary") or {}
        return {
            "기간": report.get("range_label") or f"{report.get('year')}-{int(report.get('month')):02d}",
            "전체 문서 수": summary.get("total_documents", 0),
            "검수 완료 문서 수": summary.get("verified_documents", 0),
            "미검수 문서 수": summary.get("pending_documents", 0),
            "총 거래 금액": summary.get("total_amount", 0),
            "오류/확인 필요 문서 수": summary.get("documents_with_errors", 0),
            "금액 없는 수량 확인 문서 수": summary.get("no_price_documents", 0),
            "검토 필요 표시 문서 수": summary.get("review_required_documents", 0),
        }

    def _issue_rows(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        issues = report.get("issues") or {}
        rows: list[dict[str, Any]] = []
        for issue_group in ("missing_required_fields", "calculation_mismatches", "pending_documents"):
            for issue in issues.get(issue_group) or []:
                rows.append(issue)
        return rows

    def _json_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {key: self._number(value) if isinstance(value, Decimal) else value for key, value in row.items()}

    def _document_type_value(self, document: Document) -> str:
        return getattr(document.document_type, "value", str(document.document_type or "unknown")) or "unknown"

    def _number(self, value: Decimal | None) -> int | float | None:
        if value is None:
            return None
        normalized = value.quantize(Decimal("0.01"))
        if normalized == normalized.to_integral_value():
            return int(normalized)
        return float(normalized)

    def _decimal(self, value: Any) -> Decimal | None:
        if value in (None, "", []):
            return None
        if isinstance(value, Decimal):
            return value
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
        if not match:
            return None
        try:
            return Decimal(match.group(0).replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    def _clean_text(self, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _inclusive_end_date(self, end_date: date) -> date:
        return date.fromordinal(end_date.toordinal() - 1)

    def _range_label(self, start_date: date, end_date: date, period: str) -> str:
        end_label = self._inclusive_end_date(end_date)
        if start_date == end_label:
            return start_date.isoformat()
        period_label = {"day": "일", "week": "주", "month": "월", "year": "년"}.get(period, "기간")
        return f"{start_date.isoformat()} ~ {end_label.isoformat()} ({period_label})"
