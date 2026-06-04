from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ItemMasterRead(BaseModel):
    id: UUID
    internal_item_code: str
    item_name: str
    normalized_item_name: str | None = None
    spec: str | None = None
    normalized_spec: str | None = None
    unit: str | None = None
    category: str | None = None
    standard_price: Decimal | None = None
    active: bool
    aliases: list[str] = Field(default_factory=list)
    last_uploaded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ItemMasterListResponse(BaseModel):
    items: list[ItemMasterRead]
    total: int
    page: int
    page_size: int


class ItemMasterStats(BaseModel):
    total_items: int
    active_items: int
    inactive_items: int
    last_uploaded_at: datetime | None = None


class ItemMasterUploadResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
