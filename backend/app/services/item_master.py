import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.document import ItemMaster


REQUIRED_COLUMNS = {"internal_item_code", "item_name"}


def normalize_item_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("에스유에스", "sus")
    text = text.replace("스테인리스", "sus")
    text = text.replace("스텐레스", "sus")
    text = text.replace("스텐", "sus")
    text = re.sub(r"sus\s*[-_ ]?\s*304", "sus304", text)
    text = re.sub(r"(\d+)\.0t\b", r"\1t", text)
    text = re.sub(r"[^0-9a-z가-힣]+", "", text)
    return text


def normalize_spec(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"(\d+)\.0t\b", r"\1t", text)
    text = re.sub(r"[^0-9a-z가-힣.]+", "", text)
    return text


def decimal_or_none(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("원", "").strip())
    except (InvalidOperation, ValueError):
        return None


def bool_value(value: object) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() not in {"false", "0", "no", "n", "비활성"}


@dataclass
class ItemMasterUploadResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ItemMasterService:
    def list_items(
        self,
        db: Session,
        *,
        search: str | None = None,
        category: str | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[ItemMaster], int]:
        stmt = select(ItemMaster)
        count_stmt = select(func.count()).select_from(ItemMaster)
        filters = []
        if search:
            needle = f"%{search.strip()}%"
            normalized = f"%{normalize_item_text(search)}%"
            filters.append(or_(
                ItemMaster.internal_item_code.ilike(needle),
                ItemMaster.item_name.ilike(needle),
                ItemMaster.normalized_item_name.ilike(normalized),
                ItemMaster.spec.ilike(needle),
            ))
        if category:
            filters.append(ItemMaster.category == category)
        if active is not None:
            filters.append(ItemMaster.active.is_(active))
        for condition in filters:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)
        total = db.scalar(count_stmt) or 0
        items = list(db.scalars(stmt.order_by(ItemMaster.internal_item_code).offset((page - 1) * page_size).limit(page_size)).all())
        return items, total

    def stats(self, db: Session) -> dict[str, Any]:
        total = db.scalar(select(func.count()).select_from(ItemMaster)) or 0
        active = db.scalar(select(func.count()).select_from(ItemMaster).where(ItemMaster.active.is_(True))) or 0
        last_uploaded_at = db.scalar(select(func.max(ItemMaster.last_uploaded_at)).select_from(ItemMaster))
        return {
            "total_items": total,
            "active_items": active,
            "inactive_items": max(0, total - active),
            "last_uploaded_at": last_uploaded_at,
        }

    def upload_csv(self, db: Session, content: bytes, *, replace: bool = False) -> ItemMasterUploadResult:
        result = ItemMasterUploadResult()
        if replace:
            db.query(ItemMaster).delete()
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            result.errors.append(f"필수 컬럼이 없습니다: {', '.join(sorted(missing))}")
            return result
        uploaded_at = datetime.now(timezone.utc)
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("internal_item_code") or "").strip()
            name = (row.get("item_name") or "").strip()
            if not code or not name:
                result.skipped += 1
                result.errors.append(f"{row_number}행: internal_item_code와 item_name은 필수입니다.")
                continue
            existing = db.scalar(select(ItemMaster).where(ItemMaster.internal_item_code == code))
            values = {
                "item_name": name,
                "normalized_item_name": normalize_item_text(name),
                "spec": (row.get("spec") or "").strip() or None,
                "normalized_spec": normalize_spec(row.get("spec")),
                "unit": (row.get("unit") or "").strip() or None,
                "category": (row.get("category") or "").strip() or None,
                "standard_price": decimal_or_none(row.get("standard_price")),
                "active": bool_value(row.get("active")),
                "aliases": self._aliases(row),
                "last_uploaded_at": uploaded_at,
            }
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
                result.updated += 1
            else:
                db.add(ItemMaster(internal_item_code=code, **values))
                result.inserted += 1
        db.commit()
        return result

    def _aliases(self, row: dict[str, str]) -> list[str]:
        raw = row.get("aliases") or row.get("alias") or ""
        return [value.strip() for value in re.split(r"[,;|]", raw) if value.strip()]


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.86
    return SequenceMatcher(None, left, right).ratio()
