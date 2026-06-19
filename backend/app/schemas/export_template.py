from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ExportTemplateColumn(BaseModel):
    header: str = Field(min_length=1, max_length=120)
    source_field: str = Field(default="__blank__", max_length=160)
    column_type: str = Field(default="field", pattern="^(field|static|blank)$")
    static_value: str | None = Field(default=None, max_length=255)


class ExportTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    scope: str = Field(default="global", max_length=40)
    is_default: bool = False
    columns: list[ExportTemplateColumn] = Field(default_factory=list)


class ExportTemplateCreate(ExportTemplateBase):
    pass


class ExportTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    scope: str | None = Field(default=None, max_length=40)
    is_default: bool | None = None
    columns: list[ExportTemplateColumn] | None = None


class ExportTemplateRead(ExportTemplateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ExportTemplateSourceField(BaseModel):
    value: str
    label: str
    group: str
