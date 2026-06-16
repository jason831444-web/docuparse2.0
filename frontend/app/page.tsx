"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CalendarDays, CheckCircle2, Clock3, FileDown, RefreshCcw, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentCard } from "@/components/document-card";
import { FolderCard } from "@/components/folder-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn, documentSummaryShort, formatCalendarItemTitle, formatDate, getCalendarItemScheduleDate, isReviewActionable, preferredCalendarItems, titleCaseLabel } from "@/lib/utils";
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
    { label: "검토 필요", value: stats?.needs_review ?? 0, caption: "오늘 처리 우선", icon: TriangleAlert, tone: "amber" },
    { label: "입력 준비 완료", value: stats?.completed ?? 0, caption: "내보내기 가능", icon: ShieldCheck, tone: "emerald" },
    { label: "처리 중", value: stats?.processing ?? 0, caption: "OCR/파싱 진행", icon: Clock3, tone: "sky" },
    { label: "실패", value: stats?.failed ?? 0, caption: "재처리 필요", icon: RefreshCcw, tone: "red" }
  ];
  const exportReadyCount = stats?.completed ?? 0;
  const exportReviewCount = stats?.needs_review ?? 0;

  return (
    <main className="shell py-7">
      <section>
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal text-slate-950">운영 현황</h1>
            <p className="mt-1 text-sm text-muted-foreground">오늘 무엇을 검토하고 무엇을 내보낼 수 있는지 확인하세요.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm"><Link href="/upload">문서 업로드</Link></Button>
            <Button asChild size="sm"><Link href="/review">검토 대시보드</Link></Button>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {metrics.map((metric) => (
            <Card key={metric.label}>
              <CardContent className="flex items-start justify-between p-5">
                <div>
                  <p className="text-sm text-muted-foreground">{metric.label}</p>
                  <p className="mt-5 text-3xl font-semibold leading-none text-slate-950">{metric.value}</p>
                  <p className="mt-3 text-xs text-muted-foreground">{metric.caption}</p>
                </div>
                <span
                  className={cn(
                    "grid size-8 place-items-center rounded-lg",
                    metric.tone === "amber" && "bg-amber-100 text-amber-700",
                    metric.tone === "emerald" && "bg-emerald-100 text-emerald-700",
                    metric.tone === "sky" && "bg-sky-100 text-sky-700",
                    metric.tone === "red" && "bg-red-100 text-red-700"
                  )}
                >
                  <metric.icon className="size-4" />
                </span>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">오늘 검토할 문서</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">확정 전 검토가 필요한 문서를 빠르게 처리하세요.</p>
            </div>
            <Button asChild variant="ghost" size="sm"><Link href="/review">검토 대시보드 <ArrowRight className="size-4" /></Link></Button>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y">
              {(stats?.recent_review ?? []).slice(0, 4).map((document) => (
                <Link key={document.id} href={`/documents/${document.id}`} className="flex items-center justify-between gap-4 px-5 py-3.5 transition hover:bg-slate-50">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{titleCaseLabel(document.document_type)}</Badge>
                      <p className="truncate text-sm font-semibold text-slate-950">{document.vendor_name || document.customer_name || document.title || document.original_filename}</p>
                    </div>
                    <p className="mt-1 truncate text-sm text-muted-foreground">{documentSummaryShort(document, 120)}</p>
                  </div>
                  <Badge className="border-red-200 bg-red-50 text-red-700 shadow-none">검토 항목</Badge>
                </Link>
              ))}
              {!(stats?.recent_review ?? []).length ? <p className="px-5 py-8 text-sm text-muted-foreground">검토할 문서가 없습니다.</p> : null}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base"><FileDown className="size-4 text-emerald-700" /> 업무데이터 / 엑셀 내보내기 준비</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex items-center justify-between rounded-lg bg-emerald-50 px-4 py-3 text-emerald-800">
                <span className="text-sm">내보내기 가능</span>
                <strong className="text-xl">{exportReadyCount}건</strong>
              </div>
              <div className="flex items-center justify-between rounded-lg bg-amber-50 px-4 py-3 text-amber-800">
                <span className="text-sm">검토 후 가능</span>
                <strong className="text-xl">{exportReviewCount}건</strong>
              </div>
              <Button asChild className="mt-2 w-full"><Link href="/documents">문서 목록에서 내보내기</Link></Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">OCR / Provider</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {[
                ["OCR 엔진", stats?.ocr_metrics?.paddleocr_success ?? 0],
                ["AI 파서", stats?.ocr_metrics?.paddleocr_retry ?? 0],
                ["Tesseract fallback", stats?.ocr_metrics?.tesseract_fallback ?? 0],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="flex items-center gap-1 text-emerald-700"><span className="size-1.5 rounded-full bg-emerald-600" /> 정상 {value ? `· ${value}` : ""}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">이번 주 납기 문서</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">납기요청일/납기일을 우선해 표시합니다.</p>
            </div>
            <Button asChild variant="ghost" size="sm"><Link href="/calendar">전체 일정 <ArrowRight className="size-4" /></Link></Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {upcomingWeek.length ? upcomingWeek.map((item) => <DashboardScheduleRow key={item.id} item={item} />) : (
              <p className="rounded-lg border bg-slate-50 p-4 text-sm text-muted-foreground">이번 주 납기 문서가 없습니다.</p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">이번 주 납기 문서</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">월 단위 납기 흐름을 빠르게 훑어봅니다.</p>
            </div>
            <Button asChild variant="ghost" size="sm"><Link href="/calendar">캘린더 열기</Link></Button>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-muted-foreground">
              {["월", "화", "수", "목", "금", "토", "일"].map((day) => <span key={day}>{day}</span>)}
            </div>
            <div className="mt-2 grid grid-cols-7 gap-1.5">
              {monthCells.map((cell) => (
                <div key={cell.key} className="min-h-20 rounded-lg border bg-white p-1.5">
                  <p className="text-[11px] font-medium text-muted-foreground">{cell.day}</p>
                  <div className="mt-1 space-y-1">
                    {cell.items.slice(0, 2).map((item) => (
                      <Link key={item.id} href={item.action_url} className="block rounded-md bg-blue-50 px-1.5 py-1 text-left text-[10px] leading-tight text-blue-900 hover:bg-blue-100">
                        <span className="line-clamp-2">{formatCalendarItemTitle(item)}</span>
                      </Link>
                    ))}
                    {cell.items.length > 2 ? <span className="text-[10px] text-muted-foreground">+{cell.items.length - 2}</span> : null}
                  </div>
                </div>
              ))}
            </div>
            {!visibleScheduleItems.length ? <p className="mt-4 text-sm text-muted-foreground">등록된 납기 일정이 없습니다.</p> : null}
          </CardContent>
        </Card>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">최근 업로드 문서</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">전체 보기</Link></Button>
          </CardHeader>
          <CardContent className="grid min-w-0 gap-3 lg:grid-cols-2">
            {(stats?.recent ?? []).slice(0, 4).map((document) => <DocumentCard key={document.id} document={document} />)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">문서 유형별 분류</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/categories">유형 보기</Link></Button>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            {(stats?.category_overview ?? []).slice(0, 6).map((folder) => (
              <FolderCard key={folder.value} folder={folder} href={`/categories/${encodeURIComponent(folder.value)}`} />
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">최근 수정 문서</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/documents">문서 목록</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.recent_edits ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="flex min-w-0 items-center justify-between gap-3 overflow-hidden rounded-lg border bg-white p-4 transition hover:border-primary/30 hover:bg-slate-50">
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
            <CardTitle className="text-base">즐겨찾기</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/favorites">즐겨찾기 문서</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {(activity?.favorites ?? []).slice(0, 5).map((document) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="block min-w-0 overflow-hidden rounded-lg border bg-white p-4 transition hover:border-primary/30 hover:bg-slate-50">
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
        {isReviewActionable(item) ? <Badge className="border-amber-300 bg-amber-50 text-amber-800">검토 필요</Badge> : <Badge className="border-emerald-300 bg-emerald-50 text-emerald-800">입력 준비</Badge>}
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
