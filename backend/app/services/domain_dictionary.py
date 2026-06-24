from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, ItemAlias, ItemMaster, ProcessingStatus
from app.services.item_master_matcher import normalize_item_text


@dataclass(frozen=True)
class DictionaryEntry:
    dictionary_type: str
    canonical_value: str
    normalized_value: str
    source: str
    field: str | None = None
    evidence: str | None = None


class DomainDictionarySuggestionService:
    """Build review suggestions from confirmed manufacturing-domain values.

    Suggestions intentionally do not mutate extracted values. They are review
    hints so a human can accept or ignore corrections before confirmation.
    """

    VERSION = "domain_dictionary_suggestions_v1"
    MAX_DOCUMENTS = 500
    MAX_SUGGESTIONS = 80

    LABEL_CANONICALS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("샘플번호", ("생플번호", "생플변호", "샘플변호", "팸플번호")),
        ("사업자번호", ("사엽자번호", "사엽자변호", "사업자변호")),
        ("담당", ("당당",)),
        ("매장판매", ("매장판애", "매장판애수")),
        ("배달판매", ("배달판마", "배당판매")),
        ("공급가액", ("공금가액", "공급가격")),
        ("결제합계", ("결재합계",)),
        ("합계금액", ("합계 금액", "총합계", "총 합계")),
        ("부가세", ("VAT", "V.A.T", "세액")),
    )

    PARTY_KEY_HINTS = ("공급자", "공급업체", "공급받는자", "고객사", "상호", "매장", "vendor", "customer", "buyer", "seller")
    ITEM_KEY_HINTS = ("품목", "품명", "item", "description")

    def suggestions_for_document(self, db: Session, document: Document, raw: dict[str, Any]) -> dict[str, Any]:
        entries = self._dictionary_entries(db, exclude_document_id=str(document.id) if document.id else None)
        suggestions = self._suggestions_from_raw(raw, entries)
        summary: dict[str, Any] = {
            "entry_count": len(entries),
            "suggestion_count": len(suggestions),
            "suggestion_types": self._counts(item.get("dictionary_type") for item in suggestions),
            "auto_applied": False,
            "confirmed_sources_only": True,
        }
        return {
            "version": self.VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "suggestions": suggestions[: self.MAX_SUGGESTIONS],
        }

    def _dictionary_entries(self, db: Session, *, exclude_document_id: str | None = None) -> list[DictionaryEntry]:
        entries: list[DictionaryEntry] = []
        entries.extend(self._label_entries())
        if not hasattr(db, "scalars"):
            return self._dedupe_entries(entries)
        entries.extend(self._item_master_entries(db))
        entries.extend(self._confirmed_document_entries(db, exclude_document_id=exclude_document_id))
        return self._dedupe_entries(entries)

    def _label_entries(self) -> list[DictionaryEntry]:
        entries: list[DictionaryEntry] = []
        for canonical, aliases in self.LABEL_CANONICALS:
            values = (canonical, *aliases)
            for value in values:
                entries.append(DictionaryEntry("field_label", canonical, self._normalize_label(value), "manufacturing_label_dictionary", field="key", evidence=value))
        return entries

    def _item_master_entries(self, db: Session) -> list[DictionaryEntry]:
        entries: list[DictionaryEntry] = []
        items = db.scalars(select(ItemMaster).where(ItemMaster.active.is_(True)).limit(2000)).all()
        for item in items:
            entries.append(DictionaryEntry("item", item.item_name, normalize_item_text(item.item_name), "item_master", field="item_name", evidence=item.internal_item_code))
            if item.spec:
                entries.append(DictionaryEntry("spec", item.spec, self._normalize_value(item.spec), "item_master", field="spec", evidence=item.internal_item_code))
            for alias in item.aliases or []:
                if str(alias).strip():
                    entries.append(DictionaryEntry("item", item.item_name, normalize_item_text(alias), "item_master_alias", field="item_name", evidence=str(alias)))
        aliases = db.scalars(select(ItemAlias).where(ItemAlias.active.is_(True)).limit(4000)).all()
        for alias in aliases:
            if alias.alias_name and alias.item_master:
                entries.append(DictionaryEntry("item", alias.item_master.item_name, normalize_item_text(alias.alias_name), "item_alias", field="item_name", evidence=alias.alias_name))
            if alias.alias_spec:
                entries.append(DictionaryEntry("spec", alias.alias_spec, self._normalize_value(alias.alias_spec), "item_alias", field="spec", evidence=alias.alias_name))
        return entries

    def _confirmed_document_entries(self, db: Session, *, exclude_document_id: str | None = None) -> list[DictionaryEntry]:
        stmt = (
            select(Document)
            .where(Document.processing_status == ProcessingStatus.confirmed)
            .order_by(Document.updated_at.desc())
            .limit(self.MAX_DOCUMENTS)
        )
        documents = db.scalars(stmt).all()
        entries: list[DictionaryEntry] = []
        for document in documents:
            if exclude_document_id and str(document.id) == exclude_document_id:
                continue
            source_id = str(document.id)
            for field in ("vendor_name", "customer_name", "merchant_name"):
                value = getattr(document, field, None)
                if self._usable_party_value(value):
                    entries.append(DictionaryEntry("party", str(value).strip(), self._normalize_party(value), "confirmed_document_field", field=field, evidence=source_id))
            metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
            confirmed_semantic = metadata.get("confirmed_semantic_mapping") if isinstance(metadata.get("confirmed_semantic_mapping"), dict) else {}
            fields = confirmed_semantic.get("fields") if isinstance(confirmed_semantic.get("fields"), dict) else {}
            for field in ("vendor_name", "customer_name", "merchant_name"):
                value = fields.get(field)
                if self._usable_party_value(value):
                    entries.append(DictionaryEntry("party", str(value).strip(), self._normalize_party(value), "confirmed_semantic_mapping", field=field, evidence=source_id))
            for item in confirmed_semantic.get("line_items") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("item_name"):
                    entries.append(DictionaryEntry("item", str(item["item_name"]).strip(), normalize_item_text(item["item_name"]), "confirmed_semantic_mapping", field="item_name", evidence=source_id))
                if item.get("spec"):
                    entries.append(DictionaryEntry("spec", str(item["spec"]).strip(), self._normalize_value(item["spec"]), "confirmed_semantic_mapping", field="spec", evidence=source_id))
            confirmed_raw = metadata.get("confirmed_raw_data") if isinstance(metadata.get("confirmed_raw_data"), dict) else {}
            for kv in confirmed_raw.get("key_values") or []:
                if not isinstance(kv, dict):
                    continue
                key = str(kv.get("key") or "")
                value = kv.get("value")
                if self._looks_like_party_key(key) and self._usable_party_value(value):
                    entries.append(DictionaryEntry("party", str(value).strip(), self._normalize_party(value), "confirmed_raw_data", field=key, evidence=source_id))
        return entries

    def _suggestions_from_raw(self, raw: dict[str, Any], entries: list[DictionaryEntry]) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        for index, item in enumerate(raw.get("key_values") or []):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            value = str(item.get("value") or "").strip()
            label = self._best_label_suggestion(key, entries)
            if label:
                suggestions.append(self._key_value_suggestion(index, key, value, "key", label))
            if self._looks_like_party_key(key):
                party = self._best_value_suggestion(value, entries, "party", normalizer=self._normalize_party, threshold=0.76)
                if party:
                    suggestions.append(self._key_value_suggestion(index, key, value, "value", party))
            if self._looks_like_item_key(key):
                item_match = self._best_value_suggestion(value, entries, "item", normalizer=normalize_item_text)
                if item_match:
                    suggestions.append(self._key_value_suggestion(index, key, value, "value", item_match))
        for table_index, table in enumerate(raw.get("tables") or []):
            if not isinstance(table, dict):
                continue
            for row_index, row in enumerate(table.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                for column, value in row.items():
                    if not self._looks_like_item_key(str(column)):
                        continue
                    match = self._best_value_suggestion(str(value or ""), entries, "item", normalizer=normalize_item_text)
                    if match:
                        suggestions.append({
                            "target": "raw_table_cell",
                            "table_index": table_index,
                            "row_index": row_index,
                            "column": column,
                            "original_value": value,
                            "suggested_value": match.canonical_value,
                            "dictionary_type": match.dictionary_type,
                            "confidence": match.score,
                            "source": match.source,
                            "evidence": match.evidence,
                            "auto_apply": False,
                        })
        return suggestions

    def _best_label_suggestion(self, key: str, entries: list[DictionaryEntry]) -> _Match | None:
        normalized = self._normalize_label(key)
        if not normalized:
            return None
        candidates = [entry for entry in entries if entry.dictionary_type == "field_label"]
        return self._best_match(normalized, candidates, threshold=0.78)

    def _best_value_suggestion(self, value: str, entries: list[DictionaryEntry], dictionary_type: str, *, normalizer, threshold: float = 0.82) -> _Match | None:
        normalized = normalizer(value)
        if len(normalized) < 2:
            return None
        candidates = [entry for entry in entries if entry.dictionary_type == dictionary_type]
        return self._best_match(normalized, candidates, threshold=threshold)

    def _best_match(self, normalized: str, entries: list[DictionaryEntry], *, threshold: float) -> _Match | None:
        best: _Match | None = None
        for entry in entries:
            if not entry.normalized_value:
                continue
            if self._canonical_normalized(entry) == normalized:
                continue
            score = 1.0 if entry.normalized_value == normalized else self._similarity(normalized, entry.normalized_value)
            if score < threshold:
                continue
            if best is None or score > best.score:
                best = _Match(entry=entry, score=round(score, 3))
        return best

    def _key_value_suggestion(self, index: int, key: str, value: str, field: str, match: _Match) -> dict[str, Any]:
        return {
            "target": "raw_key_value",
            "index": index,
            "field": field,
            "key": key,
            "original_value": key if field == "key" else value,
            "current_value": value,
            "suggested_value": match.canonical_value,
            "dictionary_type": match.dictionary_type,
            "confidence": match.score,
            "source": match.source,
            "evidence": match.evidence,
            "auto_apply": False,
        }

    def _looks_like_party_key(self, key: str) -> bool:
        normalized = self._normalize_label(key)
        return any(self._normalize_label(hint) in normalized for hint in self.PARTY_KEY_HINTS)

    def _looks_like_item_key(self, key: str) -> bool:
        normalized = self._normalize_label(key)
        return any(self._normalize_label(hint) in normalized for hint in self.ITEM_KEY_HINTS)

    def _usable_party_value(self, value: object) -> bool:
        text = str(value or "").strip()
        return len(text) >= 2 and not re.fullmatch(r"[-_/0-9., ]+", text)

    def _dedupe_entries(self, entries: Iterable[DictionaryEntry]) -> list[DictionaryEntry]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[DictionaryEntry] = []
        for entry in entries:
            key = (entry.dictionary_type, entry.canonical_value, entry.normalized_value)
            if not entry.normalized_value or key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        return deduped

    def _counts(self, values: Iterable[object]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            if value in (None, ""):
                continue
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _normalize_label(self, value: object) -> str:
        return re.sub(r"[\s:/._·\-]+", "", str(value or "").strip().lower())

    def _normalize_party(self, value: object) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"^\(?주\)?", "", text)
        text = text.replace("㈜", "")
        return re.sub(r"[^0-9a-z가-힣]+", "", text)

    def _normalize_value(self, value: object) -> str:
        return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").strip().lower())

    def _canonical_normalized(self, entry: DictionaryEntry) -> str:
        if entry.dictionary_type == "field_label":
            return self._normalize_label(entry.canonical_value)
        if entry.dictionary_type == "party":
            return self._normalize_party(entry.canonical_value)
        if entry.dictionary_type == "item":
            return normalize_item_text(entry.canonical_value)
        return self._normalize_value(entry.canonical_value)

    def _similarity(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        if len(left) >= 3 and (left in right or right in left):
            return 0.9
        return SequenceMatcher(None, left, right).ratio()


@dataclass(frozen=True)
class _Match:
    entry: DictionaryEntry
    score: float

    @property
    def canonical_value(self) -> str:
        return self.entry.canonical_value

    @property
    def dictionary_type(self) -> str:
        return self.entry.dictionary_type

    @property
    def source(self) -> str:
        return self.entry.source

    @property
    def evidence(self) -> str | None:
        return self.entry.evidence
