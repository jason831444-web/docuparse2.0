"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CalendarDays, CheckCircle2, Clock3, FileText, RefreshCcw, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentCard } from "@/components/document-card";
import { FolderCard } from "@/components/folder-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatCalendarItemTitle, formatDate, getCalendarItemScheduleDate, preferredCalendarItems, documentSummaryShort } from "@/lib/utils";
import type { ActivitySummary, DocumentCalendarItem, DocumentStats } from "@/types/document";

export default function DashboardPage() {
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [activity, setActivity] = useState<ActivitySummary | null>(null);
  const [calendar, setCalendar] = useState<DocumentCalendarItem[]>([]);

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null));
    api.activity().then(setActivity).catch(() => setActivity(null));
    api.calendar(new URLSearchParams({ limit: "80" })).then(setCalendar).catch(() => setCalendar([]));
  }, []);

  const preferredItems = preferredCalendarItems(calendar);
  const scheduleItems = preferredItems.filter((item) => item.date_role !== "issue_date");
  const visibleScheduleItems = preferredItems.slice(0, 10);
  const upcomingWeek = (scheduleItems.length ? scheduleItems : preferredItems).filter((item) => item.days_from_today >= 0 && item.days_from_today <= 7).slice(0, 5);
  const monthCells = buildDashboardCalendarCells(visibleScheduleItems);

  const metrics = [
    { label: "총 문서 수", value: stats?.total ?? 0, icon: FileText },
    { label: "처리 중", value: stats?.processing ?? 0, icon: Clock3 },
    { label: "검토 필요", value: stats?.needs_review ?? 0, icon: TriangleAlert },
    { label: "확정 완료", value: stats?.confirmed ?? 0, icon: ShieldCheck },
    { label: "처리 실패", value: stats?.failed ?? 0, icon: RefreshCcw }
  ];

  return (
    <main className="shell py-8">
      <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="space-y-5">
          <div className="rounded-2xl border bg-white/95 p-8 shadow-sm shadow-slate-200/70">
            <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">제조업 문서 자동화 현황</p>
            <h1 className="mt-3 max-w-3xl text-4xl font-semibold tracking-normal">
              발주서, 견적서, 거래명세서, 납품서를 ERP/엑셀 입력용 데이터로 변환하세요.
            </h1>
            <p className="mt-4 max-w-2xl text-muted-foreground">
              DocuParse는 문서 유형, 거래처, 문서번호, 날짜, 납기일, 품목 테이블, 금액을 자동 추출하고
              신뢰도 낮은 항목을 검토 필요로 표시합니다.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button asChild><Link href="/upload">제조업 문서 업로드</Link></Button>
              <Button asChild variant="outline"><Link href="/documents">문서 목록</Link></Button>
              <Button asChild variant="outline"><Link href="/review">검토 필요</Link></Button>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            {metrics.map((metric) => (
              <Card key={metric.label}>
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <p className="text-sm text-muted-foreground">{metric.label}</p>
                    <p className="mt-2 text-3xl font-semibold">{metric.value}</p>
                  </div>
                  <metric.icon className="size-7 text-primary" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
        <Card className="border-primary/20 bg-white">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>이번 주 납기 문서</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">발행일보다 납기요청일/납기일을 우선해 표시합니다.</p>
            </div>
            <Button asChild variant="ghost" size="sm"><Link href="/calendar">전체 일정</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {upcomingWeek.length ? upcomingWeek.map((item) => <DashboardScheduleRow key={item.id} item={item} />) : (
              <p className="rounded-lg border bg-slate-50 p-4 text-sm text-muted-foreground">이번 주 납기 문서가 없습니다.</p>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>납기 캘린더</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">납기요청일/납품일/지급기한이 있는 문서를 월 단위로 확인합니다.</p>
            </div>
            <Button asChild variant="ghost" size="sm"><Link href="/calendar">캘린더 열기</Link></Button>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-7 gap-2 text-center text-xs font-medium text-muted-foreground">
              {["월", "화", "수", "목", "금", "토", "일"].map((day) => <span key={day}>{day}</span>)}
            </div>
            <div className="mt-2 grid grid-cols-7 gap-2">
              {monthCells.map((cell) => (
                <div key={cell.key} className="min-h-24 rounded-lg border bg-white p-2">
                  <p className="text-xs font-medium text-muted-foreground">{cell.day}</p>
                  <div className="mt-2 space-y-1">
                    {cell.items.slice(0, 2).map((item) => (
                      <Link key={item.id} href={item.action_url} className="block rounded-md bg-secondary px-2 py-1 text-left text-[11px] leading-snug hover:bg-primary/10">
                        <span className="line-clamp-2">{formatCalendarItemTitle(item)}</span>
                      </Link>
                    ))}
                    {cell.items.length > 2 ? <span className="text-[11px] text-muted-foreground">+{cell.items.length - 2}건</span> : null}
                  </div>
                </div>
              ))}
            </div>
            {!visibleScheduleItems.length ? <p className="mt-4 text-sm text-muted-foreground">등록된 납기 일정이 없습니다.</p> : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>최근 업로드 문서</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">전체 보기</Link></Button>
          </CardHeader>
          <CardContent className="grid min-w-0 gap-4 lg:grid-cols-2">
            {(stats?.recent ?? []).slice(0, 4).map((document) => <DocumentCard key={document.id} document={document} />)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>검토 필요</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/review">검토 목록 열기</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(stats?.recent_review ?? []).slice(0, 4).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="block min-w-0 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                    <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
                  </div>
                  <TriangleAlert className="size-5 shrink-0 text-amber-600" />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>문서 유형별 분류</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/categories">유형 보기</Link></Button>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            {(stats?.category_overview ?? []).slice(0, 6).map((folder) => (
              <FolderCard key={folder.value} folder={folder} href={`/categories/${encodeURIComponent(folder.value)}`} />
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>OCR worker 안정성</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/settings">상태 설정 보기</Link></Button>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-4">
            {[
              ["PaddleOCR 성공", stats?.ocr_metrics?.paddleocr_success ?? 0],
              ["PaddleOCR retry", stats?.ocr_metrics?.paddleocr_retry ?? 0],
              ["Tesseract fallback", stats?.ocr_metrics?.tesseract_fallback ?? 0],
              ["평균 처리 ms", stats?.ocr_metrics?.average_processing_ms ?? 0],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border bg-white p-4">
                <p className="text-sm text-muted-foreground">{label}</p>
                <p className="mt-1 text-2xl font-semibold">{value}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>최근 수정 문서</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">문서 목록</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.recent_edits ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="flex min-w-0 items-center justify-between gap-3 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <div className="min-w-0">
                  <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                  <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
                </div>
                <CheckCircle2 className="size-5 shrink-0 text-primary" />
              </Link>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>즐겨찾기</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/favorites">즐겨찾기 문서</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.favorites ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="block min-w-0 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <p className="line-clamp-2 break-words font-medium leading-snug">{document.title || document.original_filename}</p>
                <p className="mt-1 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{documentSummaryShort(document, 140)}</p>
              </Link>
            ))}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}

function DashboardScheduleRow({ item }: { item: DocumentCalendarItem }) {
  const schedule = getCalendarItemScheduleDate(item);
  return (
    <Link href={item.action_url} className="block rounded-lg border bg-white p-4 transition hover:border-primary/30 hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="line-clamp-2 text-sm font-semibold leading-snug">{formatCalendarItemTitle(item)}</p>
          <p className="mt-1 text-xs text-muted-foreground">{schedule.label} · {formatDate(schedule.date)}</p>
        </div>
        <CalendarDays className="size-5 shrink-0 text-primary" />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Badge variant="outline">{item.status}</Badge>
        {item.review_required ? <Badge className="border-amber-300 bg-amber-50 text-amber-800">검토 필요</Badge> : <Badge className="border-emerald-300 bg-emerald-50 text-emerald-800">입력 준비</Badge>}
        {schedule.fallback ? <Badge variant="outline">발행일 기준</Badge> : null}
      </div>
    </Link>
  );
}

function buildDashboardCalendarCells(items: DocumentCalendarItem[]) {
  const firstDate = items[0]?.date ? new Date(`${items[0].date}T00:00:00`) : new Date();
  const year = firstDate.getFullYear();
  const month = firstDate.getMonth();
  const firstDay = new Date(year, month, 1);
  const startOffset = (firstDay.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - startOffset);
  return Array.from({ length: 35 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    const iso = date.toISOString().slice(0, 10);
    return {
      key: iso,
      day: date.getDate(),
      items: items.filter((item) => item.date === iso),
    };
  });
}
