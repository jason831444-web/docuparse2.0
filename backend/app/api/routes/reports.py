from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import Document
from app.services.monthly_report import MonthlyReportService


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/monthly")
def monthly_report(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    period: str = Query(default="month", pattern="^(day|week|month|year|custom)$"),
    db: Session = Depends(get_db),
) -> dict:
    start, end = _resolve_report_range(year=year, month=month, start_date=start_date, end_date=end_date)
    service = MonthlyReportService()
    return service.build_for_range(_report_documents(db, start, end), start_date=start, end_date=end, period=period)


@router.get("/monthly/export")
def monthly_report_export(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    period: str = Query(default="month", pattern="^(day|week|month|year|custom)$"),
    format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
) -> Response:
    start, end = _resolve_report_range(year=year, month=month, start_date=start_date, end_date=end_date)
    service = MonthlyReportService()
    report = service.build_for_range(_report_documents(db, start, end), start_date=start, end_date=end, period=period)
    filename = f"docparse-report-{report['start_date']}-{report['end_date']}.{format}"
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


def _resolve_report_range(
    *,
    year: int | None,
    month: int | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    if start_date and end_date:
        exclusive_end = date.fromordinal(end_date.toordinal() + 1)
    elif year and month:
        start_date = date(year, month, 1)
        exclusive_end = date(year + (month // 12), (month % 12) + 1, 1)
    else:
        today = date.today()
        start_date = date(today.year, today.month, 1)
        exclusive_end = date(today.year + (today.month // 12), (today.month % 12) + 1, 1)
    if exclusive_end <= start_date:
        raise HTTPException(status_code=400, detail="종료일은 시작일 이후여야 합니다.")
    return start_date, exclusive_end


def _report_documents(db: Session, start: date, end: date) -> list[Document]:
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
    return [document for document in documents if service._belongs_to_range(document, start, end)]
