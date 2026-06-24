from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DomainDictionaryAliasRead(BaseModel):
    id: UUID
    entry_id: UUID
    alias_value: str
    normalized_alias_value: str | None = None
    source: str
    confidence: Decimal | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DomainDictionaryEntryRead(BaseModel):
    id: UUID
    dictionary_type: str
    canonical_value: str
    normalized_value: str | None = None
    field: str | None = None
    source: str
    memo: str | None = None
    active: bool
    aliases: list[DomainDictionaryAliasRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DomainDictionaryListResponse(BaseModel):
    items: list[DomainDictionaryEntryRead]
    total: int
    page: int
    page_size: int


class DomainDictionaryStats(BaseModel):
    total_entries: int = 0
    active_entries: int = 0
    inactive_entries: int = 0
    alias_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    feedback_count: int = 0
    rejected_count: int = 0


class DomainDictionaryEntryCreate(BaseModel):
    dictionary_type: str = Field(min_length=1, max_length=40)
    canonical_value: str = Field(min_length=1, max_length=255)
    field: str | None = Field(default=None, max_length=120)
    source: str = Field(default="manual", max_length=80)
    memo: str | None = None
    active: bool = True
    aliases: list[str] = Field(default_factory=list)


class DomainDictionaryEntryUpdate(BaseModel):
    canonical_value: str | None = Field(default=None, min_length=1, max_length=255)
    field: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=80)
    memo: str | None = None
    active: bool | None = None


class DomainDictionaryAliasCreate(BaseModel):
    alias_value: str = Field(min_length=1, max_length=255)
    source: str = Field(default="manual", max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    active: bool = True


class DomainDictionaryAliasUpdate(BaseModel):
    alias_value: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = Field(default=None, max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    active: bool | None = None


class DomainDictionaryFeedbackCreate(BaseModel):
    document_id: UUID | None = None
    target: str = Field(min_length=1, max_length=80)
    field: str | None = Field(default=None, max_length=80)
    original_value: str = Field(min_length=1, max_length=255)
    suggested_value: str = Field(min_length=1, max_length=255)
    action: str = Field(pattern="^(accepted|rejected|ignored)$")
    metadata: dict | None = None
