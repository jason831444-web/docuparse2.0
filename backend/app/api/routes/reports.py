from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import Document
from app.services.monthly_report import MonthlyReportService


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/monthly")
def monthly_report(
    year: int = Query(ge=2000, le=2100),
    month: int = Query(ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    return MonthlyReportService().build(_monthly_documents(db, year, month), year=year, month=month)


@router.get("/monthly/export")
def monthly_report_export(
    year: int = Query(ge=2000, le=2100),
    month: int = Query(ge=1, le=12),
    format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
) -> Response:
    service = MonthlyReportService()
    report = service.build(_monthly_documents(db, year, month), year=year, month=month)
    filename = f"docuparse-monthly-report-{year}-{month:02d}.{format}"
    if format == "csv":
        return Response(
            service.to_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return Response(
        service.to_excel(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _monthly_documents(db: Session, year: int, month: int) -> list[Document]:
    start = date(year, month, 1)
    end = date(year + (month // 12), (month % 12) + 1, 1)
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.min)
    stmt = (
        select(Document)
        .where(
            or_(
                and_(Document.issue_date >= start, Document.issue_date < end),
                and_(Document.extracted_date >= start, Document.extracted_date < end),
                and_(Document.created_at >= start_dt, Document.created_at < end_dt),
            )
        )
        .order_by(Document.issue_date, Document.extracted_date, Document.created_at)
    )
    documents = list(db.scalars(stmt).all())
    service = MonthlyReportService()
    return [document for document in documents if service._belongs_to_month(document, year, month)]
