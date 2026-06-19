from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import ExportTemplate
from app.schemas.export_template import ExportTemplateCreate, ExportTemplateRead, ExportTemplateSourceField, ExportTemplateUpdate
from app.services.export_templates import (
    SOURCE_FIELD_OPTIONS,
    apply_default_flag,
    ensure_default_export_templates,
    export_template_to_read,
    normalize_template_columns,
)


router = APIRouter(prefix="/export-templates", tags=["export-templates"])


@router.get("", response_model=list[ExportTemplateRead])
def list_export_templates(
    scope: Annotated[str, Query(max_length=40)] = "global",
    db: Session = Depends(get_db),
) -> list[ExportTemplateRead]:
    ensure_default_export_templates(db)
    templates = db.scalars(select(ExportTemplate).where(ExportTemplate.scope == scope).order_by(ExportTemplate.is_default.desc(), ExportTemplate.name.asc())).all()
    return [ExportTemplateRead.model_validate(export_template_to_read(template)) for template in templates]


@router.get("/source-fields", response_model=list[ExportTemplateSourceField])
def export_template_source_fields() -> list[ExportTemplateSourceField]:
    return [ExportTemplateSourceField(**item) for item in SOURCE_FIELD_OPTIONS]


@router.post("", response_model=ExportTemplateRead, status_code=201)
def create_export_template(payload: ExportTemplateCreate, db: Session = Depends(get_db)) -> ExportTemplateRead:
    name = payload.name.strip()
    scope = (payload.scope or "global").strip() or "global"
    existing = db.scalar(select(ExportTemplate).where(ExportTemplate.name == name, ExportTemplate.scope == scope))
    if existing:
        raise HTTPException(status_code=409, detail="같은 이름의 출력 템플릿이 이미 있습니다.")
    template = ExportTemplate(
        name=name,
        description=payload.description,
        scope=scope,
        template_columns=normalize_template_columns([column.model_dump() for column in payload.columns]),
    )
    db.add(template)
    db.flush()
    apply_default_flag(db, template, payload.is_default)
    db.commit()
    db.refresh(template)
    return ExportTemplateRead.model_validate(export_template_to_read(template))


@router.get("/{template_id}", response_model=ExportTemplateRead)
def get_export_template(template_id: UUID, db: Session = Depends(get_db)) -> ExportTemplateRead:
    template = db.get(ExportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="출력 템플릿을 찾을 수 없습니다.")
    return ExportTemplateRead.model_validate(export_template_to_read(template))


@router.put("/{template_id}", response_model=ExportTemplateRead)
def update_export_template(template_id: UUID, payload: ExportTemplateUpdate, db: Session = Depends(get_db)) -> ExportTemplateRead:
    template = db.get(ExportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="출력 템플릿을 찾을 수 없습니다.")
    updates = payload.model_dump(exclude_unset=True)
    if "scope" in updates and updates["scope"] is not None:
        template.scope = updates["scope"].strip() or "global"
    if "name" in updates and updates["name"] is not None:
        name = updates["name"].strip()
        conflict = db.scalar(select(ExportTemplate).where(ExportTemplate.id != template.id, ExportTemplate.name == name, ExportTemplate.scope == template.scope))
        if conflict:
            raise HTTPException(status_code=409, detail="같은 이름의 출력 템플릿이 이미 있습니다.")
        template.name = name
    if "description" in updates:
        template.description = updates["description"]
    if "columns" in updates and updates["columns"] is not None:
        template.template_columns = normalize_template_columns([column.model_dump() if hasattr(column, "model_dump") else column for column in updates["columns"]])
    if "is_default" in updates:
        apply_default_flag(db, template, updates["is_default"])
    db.add(template)
    db.commit()
    db.refresh(template)
    return ExportTemplateRead.model_validate(export_template_to_read(template))


@router.delete("/{template_id}", status_code=204)
def delete_export_template(template_id: UUID, db: Session = Depends(get_db)) -> Response:
    template = db.get(ExportTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="출력 템플릿을 찾을 수 없습니다.")
    was_default = template.is_default
    scope = template.scope
    db.delete(template)
    db.commit()
    if was_default:
        replacement = db.scalar(select(ExportTemplate).where(ExportTemplate.scope == scope).order_by(ExportTemplate.name.asc()).limit(1))
        if replacement:
            replacement.is_default = True
            db.add(replacement)
            db.commit()
    return Response(status_code=204)
