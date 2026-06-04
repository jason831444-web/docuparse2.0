from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ItemAliasRead(BaseModel):
    id: UUID
    item_master_id: UUID
    alias_name: str
    normalized_alias_name: str | None = None
    alias_spec: str | None = None
    vendor_name: str | None = None
    customer_name: str | None = None
    source: str | None = None
    confidence: Decimal | None = None
    memo: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
    alias_records: list[ItemAliasRead] = Field(default_factory=list)
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
    alias_count: int = 0
    last_uploaded_at: datetime | None = None
    last_updated_at: datetime | None = None


class ItemMasterUploadResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class ItemMasterCreate(BaseModel):
    internal_item_code: str = Field(min_length=1, max_length=120)
    item_name: str = Field(min_length=1, max_length=255)
    spec: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=120)
    standard_price: Decimal | None = Field(default=None, ge=0)
    active: bool = True
    aliases: list[str] = Field(default_factory=list)


class ItemMasterUpdate(BaseModel):
    item_name: str | None = Field(default=None, min_length=1, max_length=255)
    spec: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=120)
    standard_price: Decimal | None = Field(default=None, ge=0)
    active: bool | None = None
    aliases: list[str] | None = None


class ItemAliasCreate(BaseModel):
    alias_name: str = Field(min_length=1, max_length=255)
    alias_spec: str | None = Field(default=None, max_length=255)
    vendor_name: str | None = Field(default=None, max_length=255)
    customer_name: str | None = Field(default=None, max_length=255)
    source: str = Field(default="manual", max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    memo: str | None = None
    active: bool = True


class ItemAliasUpdate(BaseModel):
    alias_name: str | None = Field(default=None, min_length=1, max_length=255)
    alias_spec: str | None = Field(default=None, max_length=255)
    vendor_name: str | None = Field(default=None, max_length=255)
    customer_name: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    memo: str | None = None
    active: bool | None = None
