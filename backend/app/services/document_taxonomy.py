from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.models.document import Document, DocumentType


@dataclass(frozen=True)
class DocumentTaxonomy:
    document_type: str
    document_subtype: str | None = None
    document_profile: str | None = None
    document_profiles: list[str] = field(default_factory=list)
    layout_profile: str | None = None
    amount_required: bool = True
    party_required: bool = True
    confidence: float = 0.6
    evidence: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [])}


class DocumentTaxonomyService:
    """Business taxonomy overlay that avoids expanding the DB document_type enum.

    The persisted document_type remains broad and stable. More specific business
    semantics live in workflow/ingestion metadata so parser, review, and reports
    can reason about tax invoices, returns, and internal movements without a
    risky schema migration.
    """

    def classify(
        self,
        document: Document,
        text: str | None = None,
        *,
        extraction_method: str | None = None,
        file_metadata: dict | None = None,
    ) -> DocumentTaxonomy:
        doc_type = getattr(document.document_type, "value", str(document.document_type or "general_document"))
        content = "\n".join([
            str(text or ""),
            str(document.document_number or ""),
            str(document.category or ""),
            " ".join(str(tag or "") for tag in (document.tags or [])),
        ])
        lowered = content.lower()
        layout_profile = self._layout_profile(extraction_method, file_metadata)

        if self._is_internal_transfer(content):
            return DocumentTaxonomy(
                document_type=doc_type,
                document_subtype="internal_transfer",
                document_profile="inventory_movement_document",
                document_profiles=["inventory_movement_document", "no_price_document"],
                layout_profile=layout_profile,
                amount_required=False,
                party_required=False,
                confidence=0.9,
                evidence=self._evidence(content, [
                    r"\bTRF[-_ ]?\d{4}",
                    r"사업장\s*간",
                    r"자재\s*이동",
                    r"내부\s*이동",
                    r"내부품목코드",
                    r"요청수량|요청수림",
                    r"internal\s+transfer|branch\s+transfer|transfer\s+slip",
                ]),
            )

        if self._is_return_or_credit(content):
            subtype = "credit_note" if self._is_credit_note(content) else "return_note"
            return DocumentTaxonomy(
                document_type=doc_type,
                document_subtype=subtype,
                document_profile="return_document",
                document_profiles=["return_document", "priced_document"],
                layout_profile=layout_profile,
                amount_required=True,
                party_required=True,
                confidence=0.88,
                evidence=self._evidence(content, [
                    r"\bRTN[-_ ]?\d{4}",
                    r"반품",
                    r"차감",
                    r"credit\s+note|credit\s+memo",
                    r"return\s+note|return\s+authorization",
                    r"관련\s*납품서|원\s*납품서|related\s+(?:delivery|document)",
                ]),
            )

        if self._is_tax_invoice(content, doc_type):
            return DocumentTaxonomy(
                document_type=doc_type,
                document_subtype="tax_invoice",
                document_profile="tax_document",
                document_profiles=["tax_document", "priced_document"],
                layout_profile=layout_profile,
                amount_required=True,
                party_required=True,
                confidence=0.86,
                evidence=self._evidence(content, [
                    r"전자\s*세금계산서|세금계산서",
                    r"사업자등록번호",
                    r"공급받는자",
                    r"승인번호",
                    r"작성일자",
                    r"공급가액",
                    r"부가세|세액",
                ]),
            )

        if self._is_commercial_invoice(content, doc_type):
            return DocumentTaxonomy(
                document_type=doc_type,
                document_subtype="commercial_invoice",
                document_profile="foreign_currency_document",
                document_profiles=["foreign_currency_document", "priced_document"],
                layout_profile=layout_profile,
                amount_required=True,
                party_required=True,
                confidence=0.82,
                evidence=self._evidence(content, [
                    r"commercial\s+invoice",
                    r"\bINV[-_ ]?US[-_ ]?\d{4}",
                    r"\bUSD\b|US\$|\$",
                ]),
            )

        if doc_type == "delivery_note" or doc_type == "inspection_report":
            return DocumentTaxonomy(
                document_type=doc_type,
                document_subtype="incoming_inspection" if doc_type == "inspection_report" else None,
                document_profile="quality_document" if doc_type == "inspection_report" else "no_price_document",
                document_profiles=["quality_document", "no_price_document"] if doc_type == "inspection_report" else ["no_price_document"],
                layout_profile=layout_profile,
                amount_required=False,
                party_required=True,
                confidence=0.78,
                evidence=[doc_type],
            )

        profile = "priced_document" if doc_type in {"purchase_order", "quotation", "invoice", "transaction_statement"} else "generic_document"
        return DocumentTaxonomy(
            document_type=doc_type,
            document_profile=profile,
            document_profiles=[profile],
            layout_profile=layout_profile,
            amount_required=profile == "priced_document",
            party_required=doc_type not in {"general_document", "other"},
            confidence=0.65,
            evidence=[doc_type],
        )

    def _is_tax_invoice(self, text: str, doc_type: str) -> bool:
        if doc_type != "invoice":
            return False
        score = 0
        score += 3 if re.search(r"전자\s*세금계산서|세금계산서", text, flags=re.IGNORECASE) else 0
        score += 2 if re.search(r"사업자등록번호", text) else 0
        score += 1 if re.search(r"공급받는자|공급자", text) else 0
        score += 1 if re.search(r"공급가액", text) else 0
        score += 1 if re.search(r"부가세|세액", text) else 0
        score += 1 if re.search(r"승인번호|작성일자", text) else 0
        if re.search(r"commercial\s+invoice|proforma\s+invoice|\bUSD\b|US\$|\$", text, flags=re.IGNORECASE):
            score -= 3
        return score >= 4

    def _is_commercial_invoice(self, text: str, doc_type: str) -> bool:
        if doc_type != "invoice":
            return False
        return bool(re.search(r"commercial\s+invoice|\bINV[-_ ]?US[-_ ]?\d{4}|\bUSD\b|US\$|\$", text, flags=re.IGNORECASE))

    def _is_return_or_credit(self, text: str) -> bool:
        return bool(re.search(
            r"\bRTN[-_ ]?\d{4}|반품\s*/?\s*차감|반품\s*요청|차감\s*요청|반품전표|차감전표|"
            r"return\s+note|credit\s+note|credit\s+memo|deduction",
            text,
            flags=re.IGNORECASE,
        ))

    def _is_credit_note(self, text: str) -> bool:
        return bool(re.search(r"credit\s+note|credit\s+memo|deduction|차감", text, flags=re.IGNORECASE))

    def _is_internal_transfer(self, text: str) -> bool:
        return bool(re.search(
            r"\bTRF[-_ ]?\d{4}|사업장\s*간|자재\s*이동|내부\s*이동|창고\s*이동|지점\s*이동|"
            r"내부품목코드|요청수량|요청수림|internal\s+transfer|branch\s+transfer|transfer\s+slip|"
            r"from\s+warehouse|to\s+warehouse",
            text,
            flags=re.IGNORECASE,
        ))

    def _layout_profile(self, extraction_method: str | None, file_metadata: dict | None) -> str | None:
        method = (extraction_method or "").lower()
        metadata = file_metadata or {}
        if metadata.get("image_only"):
            return "scanned_pdf"
        if "ocr" in method:
            return "scanned_pdf"
        if "pdf_text" in method or metadata.get("text_layer_exists"):
            return "text_layer_pdf"
        return None

    def _evidence(self, text: str, patterns: list[str]) -> list[str]:
        found: list[str] = []
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                found.append(pattern)
        return found[:8]
