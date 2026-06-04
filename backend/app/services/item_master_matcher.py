import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.document import ItemAlias, ItemMaster


logger = logging.getLogger(__name__)


REQUIRED_ITEM_MASTER_COLUMNS = {"internal_item_code", "item_name"}
OPTIONAL_ITEM_MASTER_COLUMNS = {"spec", "unit", "category", "standard_price", "active", "aliases"}
ITEM_MASTER_COLUMNS = REQUIRED_ITEM_MASTER_COLUMNS | OPTIONAL_ITEM_MASTER_COLUMNS


@dataclass
class ItemMasterUploadResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors or [],
        }


def normalize_item_text(value: object) -> str:
    text = str(value or "").lower().strip()
    if not text:
        return ""
    replacements = {
        "에스유에스": "sus",
        "스테인레스": "sus",
        "스테인리스": "sus",
        "써스": "sus",
        "철판": "plate",
        "판재": "plate",
        "플레이트": "plate",
        "와샤": "와셔",
        "육각볼트": "육각 볼트",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\bsus[\s\-_]*304\b", "sus304", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\.0+t\b", r"\1t", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9a-z가-힣]+", "", text)
    return text


def normalize_spec_text(value: object) -> str:
    text = str(value or "").lower().strip()
    if not text:
        return ""
    text = text.replace("×", "x").replace("*", "x")
    text = re.sub(r"(\d+)\.0+t\b", r"\1t", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9a-z가-힣x.]+", "", text)
    return text


def parse_decimal(value: object) -> Decimal | None:
    if value in (None, "", []):
        return None
    text = str(value).replace(",", "").replace("₩", "").replace("원", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def parse_bool(value: object, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "활성", "사용", "사용중"}


def parse_aliases(value: object) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[|;,]", str(value)) if part.strip()]


def parse_item_master_csv(content: bytes | str) -> tuple[list[dict[str, Any]], list[str]]:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["CSV 헤더를 찾을 수 없습니다."]
    normalized_headers = {header.strip(): header for header in reader.fieldnames if header}
    missing = sorted(REQUIRED_ITEM_MASTER_COLUMNS - set(normalized_headers))
    if missing:
        return [], [f"필수 컬럼이 없습니다: {', '.join(missing)}"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, row in enumerate(reader, start=2):
        normalized = {key.strip(): (row.get(source) or "").strip() for key, source in normalized_headers.items()}
        code = normalized.get("internal_item_code", "")
        name = normalized.get("item_name", "")
        if not code or not name:
            errors.append(f"{row_number}행: internal_item_code와 item_name은 필수입니다.")
            continue
        rows.append({
            "internal_item_code": code,
            "item_name": name,
            "normalized_item_name": normalize_item_text(name),
            "spec": normalized.get("spec") or None,
            "normalized_spec": normalize_spec_text(normalized.get("spec")),
            "unit": normalized.get("unit") or None,
            "category": normalized.get("category") or None,
            "standard_price": parse_decimal(normalized.get("standard_price")),
            "active": parse_bool(normalized.get("active"), default=True),
            "aliases": parse_aliases(normalized.get("aliases")),
        })
    return rows, errors


def parse_item_master_upload(filename: str, content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    lowered = filename.lower()
    if lowered.endswith(".csv") or lowered.endswith(".txt"):
        return parse_item_master_csv(content)
    if lowered.endswith((".xlsx", ".xls")):
        try:
            import pandas as pd
        except Exception:
            return [], ["Excel 업로드를 처리하려면 pandas/openpyxl 의존성이 필요합니다. CSV로 업로드하세요."]
        frame = pd.read_excel(io.BytesIO(content))
        return parse_item_master_csv(frame.to_csv(index=False))
    return [], ["CSV 또는 Excel 파일만 업로드할 수 있습니다."]


class ItemMasterImportService:
    def upload(self, db: Session, filename: str, content: bytes, mode: str = "upsert") -> dict[str, Any]:
        rows, errors = parse_item_master_upload(filename, content)
        result = ItemMasterUploadResult(errors=errors)
        result.skipped = len(errors)
        if mode == "replace":
            db.execute(delete(ItemAlias))
            db.execute(delete(ItemMaster))
        now = datetime.now(timezone.utc)
        for row in rows:
            existing = db.scalar(select(ItemMaster).where(ItemMaster.internal_item_code == row["internal_item_code"]))
            if existing:
                self._apply_row(existing, row, now)
                result.updated += 1
            else:
                db.add(ItemMaster(**row, last_uploaded_at=now))
                result.inserted += 1
        db.commit()
        return result.as_dict()

    def _apply_row(self, item: ItemMaster, row: dict[str, Any], now: datetime) -> None:
        for key in [
            "item_name",
            "normalized_item_name",
            "spec",
            "normalized_spec",
            "unit",
            "category",
            "standard_price",
            "active",
            "aliases",
        ]:
            setattr(item, key, row.get(key))
        item.last_uploaded_at = now


class ItemMasterMatcher:
    auto_threshold = Decimal("0.90")
    review_threshold = Decimal("0.65")

    def match_line_items(self, db: Session, line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hasattr(db, "scalars"):
            masters = []
        else:
            masters = list(db.scalars(select(ItemMaster).options(selectinload(ItemMaster.alias_records)).where(ItemMaster.active.is_(True))).all())
        return self.match_line_items_against_masters(line_items, masters)

    def match_line_items_against_masters(self, line_items: list[dict[str, Any]], masters: Iterable[Any]) -> list[dict[str, Any]]:
        master_list = list(masters)
        if not line_items:
            return []
        if not master_list:
            logger.info("[DocuParse] item master matching skipped: no item master records")
            return [self._mark_no_master(dict(item)) for item in line_items]
        logger.info("[DocuParse] item master matching started: line_items=%s, item_master_count=%s", len(line_items), len(master_list))
        matched: list[dict[str, Any]] = []
        for index, source_item in enumerate(line_items):
            item = dict(source_item)
            item["source_item_name"] = item.get("source_item_name") or item.get("item_name")
            item["source_item_code"] = item.get("source_item_code") or item.get("item_code")
            candidates = self._rank_candidates(item, master_list)
            best = candidates[0] if candidates else None
            item["item_master_candidates"] = candidates[:5]
            item["item_master_match_confidence"] = best["score"] if best else None
            alias_tie = bool(best and best.get("alias_code_match") and sum(1 for candidate in candidates if candidate.get("alias_code_match")) > 1)
            if best and best.get("direct_code_match"):
                item["internal_item_code"] = best["internal_item_code"]
                item["item_master_match_status"] = "direct_code_match"
                item["item_master_match_reason"] = "DOCUMENT_CODE_MATCHED_INTERNAL_CODE"
            elif best and best.get("alias_code_match") and not alias_tie:
                item["internal_item_code"] = best["internal_item_code"]
                item["item_master_match_status"] = "alias_matched"
                item["item_master_match_reason"] = "DOCUMENT_CODE_MATCHED_ITEM_ALIAS"
            elif best and Decimal(str(best["score"])) >= self.auto_threshold and not self._is_ambiguous(best, candidates):
                item["internal_item_code"] = best["internal_item_code"]
                item["item_master_match_status"] = "auto_matched"
                item["item_master_match_reason"] = "HIGH_CONFIDENCE_MATCH"
            elif best and Decimal(str(best["score"])) >= self.review_threshold:
                item["internal_item_code"] = item.get("internal_item_code") if self._looks_like_real_code(item.get("internal_item_code")) else None
                item["item_master_match_status"] = "ambiguous"
                item["item_master_match_reason"] = "CANDIDATE_REVIEW_REQUIRED"
            else:
                item["internal_item_code"] = item.get("internal_item_code") if self._looks_like_real_code(item.get("internal_item_code")) else None
                item["item_master_match_status"] = "unmatched"
                item["item_master_match_reason"] = "NO_CONFIDENT_MATCH"
            if best:
                logger.info(
                    "[DocuParse] item master candidate selected: item_index=%s, best_code=%s, score=%s, status=%s",
                    index + 1,
                    best["internal_item_code"],
                    best["score"],
                    item["item_master_match_status"],
                )
            matched.append(item)
        return matched

    def count_active_items(self, db: Session) -> int:
        return int(db.scalar(select(func.count()).select_from(ItemMaster).where(ItemMaster.active.is_(True))) or 0)

    def _mark_no_master(self, item: dict[str, Any]) -> dict[str, Any]:
        item["source_item_name"] = item.get("source_item_name") or item.get("item_name")
        item["source_item_code"] = item.get("source_item_code") or item.get("item_code")
        item["internal_item_code"] = item.get("internal_item_code") if self._looks_like_real_code(item.get("internal_item_code")) else None
        item["item_master_match_status"] = "skipped_no_item_master"
        item["item_master_match_confidence"] = None
        item["item_master_candidates"] = []
        item["item_master_match_reason"] = "NO_ITEM_MASTER"
        return item

    def _rank_candidates(self, item: dict[str, Any], masters: list[Any]) -> list[dict[str, Any]]:
        candidates = [self._score_candidate(item, master) for master in masters]
        exact_candidates = [candidate for candidate in candidates if candidate.get("direct_code_match") or candidate.get("alias_code_match")]
        if exact_candidates:
            return sorted(exact_candidates, key=lambda candidate: (candidate.get("direct_code_match") is True, Decimal(str(candidate["score"]))), reverse=True)
        candidates = [candidate for candidate in candidates if Decimal(str(candidate["score"])) >= Decimal("0.35")]
        return sorted(candidates, key=lambda candidate: Decimal(str(candidate["score"])), reverse=True)

    def _score_candidate(self, item: dict[str, Any], master: Any) -> dict[str, Any]:
        source_code = str(item.get("item_code") or "").strip()
        source_code_normalized = normalize_item_text(source_code)
        internal_code = str(getattr(master, "internal_item_code", "") or "").strip()
        alias_entries = self._active_alias_entries(master)
        alias_values = [entry["name"] for entry in alias_entries]
        alias_code_match = bool(source_code_normalized and any(source_code_normalized == normalize_item_text(alias) for alias in alias_values))
        if source_code and source_code.lower() == internal_code.lower():
            name_score = Decimal("1")
            alias_score = Decimal("1")
        elif alias_code_match:
            name_score = Decimal("1")
            alias_score = Decimal("1")
        else:
            source_name = normalize_item_text(item.get("item_name"))
            master_name = getattr(master, "normalized_item_name", None) or normalize_item_text(getattr(master, "item_name", ""))
            alias_scores = [self._alias_score(source_name, item.get("specification"), entry) for entry in alias_entries]
            alias_score = max(alias_scores or [Decimal("0")])
            name_score = max(self._similarity(source_name, master_name), alias_score)
        spec_score = self._spec_score(item.get("specification"), getattr(master, "spec", None))
        unit_score = self._unit_score(item.get("unit"), getattr(master, "unit", None))
        price_score = self._price_score(item.get("unit_price"), getattr(master, "standard_price", None))
        direct_code_match = bool(source_code and source_code.lower() == internal_code.lower())
        if direct_code_match or alias_code_match:
            score = Decimal("1.00")
        else:
            score = (name_score * Decimal("0.55")) + (spec_score * Decimal("0.20")) + (unit_score * Decimal("0.15")) + (price_score * Decimal("0.10"))
        score = score.quantize(Decimal("0.001"))
        return {
            "item_master_id": str(getattr(master, "id", "")) if getattr(master, "id", None) is not None else None,
            "internal_item_code": internal_code,
            "item_name": getattr(master, "item_name", None),
            "spec": getattr(master, "spec", None),
            "unit": getattr(master, "unit", None),
            "standard_price": str(getattr(master, "standard_price", "")) if getattr(master, "standard_price", None) is not None else None,
            "score": str(score),
            "direct_code_match": direct_code_match,
            "alias_code_match": alias_code_match,
            "score_breakdown": {
                "name_score": str(name_score.quantize(Decimal("0.001"))),
                "spec_score": str(spec_score.quantize(Decimal("0.001"))),
                "unit_score": str(unit_score.quantize(Decimal("0.001"))),
                "price_score": str(price_score.quantize(Decimal("0.001"))),
                "alias_score": str(alias_score.quantize(Decimal("0.001"))),
            },
        }

    def _similarity(self, source: str, target: str) -> Decimal:
        if not source or not target:
            return Decimal("0")
        if source == target:
            return Decimal("1")
        if source in target or target in source:
            return Decimal("0.88")
        return Decimal(str(SequenceMatcher(None, source, target).ratio()))

    def _active_alias_entries(self, master: Any) -> list[dict[str, Any]]:
        entries = [{"name": str(alias), "spec": None} for alias in (getattr(master, "aliases", None) or []) if str(alias).strip()]
        for alias in getattr(master, "alias_records", None) or []:
            if getattr(alias, "active", True) is False:
                continue
            entries.append({
                "name": getattr(alias, "alias_name", None),
                "spec": getattr(alias, "alias_spec", None),
            })
        return [entry for entry in entries if entry.get("name")]

    def _alias_score(self, source_name: str, source_spec: object, alias: dict[str, Any]) -> Decimal:
        name_score = self._similarity(source_name, normalize_item_text(alias.get("name")))
        spec = alias.get("spec")
        if spec:
            return (name_score * Decimal("0.82")) + (self._spec_score(source_spec, spec) * Decimal("0.18"))
        return name_score

    def _spec_score(self, source: object, target: object) -> Decimal:
        source_norm = normalize_spec_text(source)
        target_norm = normalize_spec_text(target)
        if not source_norm or not target_norm:
            return Decimal("0.50")
        if source_norm == target_norm:
            return Decimal("1")
        if source_norm in target_norm or target_norm in source_norm:
            return Decimal("0.75")
        return self._similarity(source_norm, target_norm) * Decimal("0.80")

    def _unit_score(self, source: object, target: object) -> Decimal:
        source_norm = str(source or "").strip().lower()
        target_norm = str(target or "").strip().lower()
        if not source_norm or not target_norm:
            return Decimal("0.50")
        return Decimal("1") if source_norm == target_norm else Decimal("0")

    def _price_score(self, source: object, target: object) -> Decimal:
        source_price = parse_decimal(source)
        target_price = parse_decimal(target)
        if source_price is None or target_price is None or target_price == 0:
            return Decimal("0.50")
        diff_ratio = abs(source_price - target_price) / target_price
        if diff_ratio <= Decimal("0.02"):
            return Decimal("1")
        if diff_ratio <= Decimal("0.10"):
            return Decimal("0.70")
        return Decimal("0")

    def _is_ambiguous(self, best: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
        if len(candidates) < 2:
            return False
        best_score = Decimal(str(best["score"]))
        second_score = Decimal(str(candidates[1]["score"]))
        return best_score - second_score <= Decimal("0.03")

    def _looks_like_real_code(self, value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        return not re.search(r"(미확인|신뢰도|검토|장부|비어)", text)
