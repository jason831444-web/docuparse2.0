"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CalendarDays, Clock3, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatCalendarItemTitle, formatDate, getCalendarItemScheduleDate, isReviewActionable, preferredCalendarItems, primaryCategoryLabel } from "@/lib/utils";
import type { DocumentCalendarItem } from "@/types/document";

function toneFor(item: DocumentCalendarItem) {
  if (item.days_from_today < 0) return "border-slate-200 bg-slate-50 text-slate-700";
  if (item.days_from_today === 0) return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (item.days_from_today <= 7) return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-blue-100 bg-blue-50 text-blue-800";
}

export default function CalendarPage() {
  const [items, setItems] = useState<DocumentCalendarItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.calendar().then(setItems).catch(() => setItems([])).finally(() => setLoading(false));
  }, []);

  const grouped = useMemo(() => {
    return preferredCalendarItems(items).reduce<Record<string, DocumentCalendarItem[]>>((groups, item) => {
      const key = item.date.slice(0, 7);
      groups[key] = [...(groups[key] || []), item];
      return groups;
    }, {});
  }, [items]);

  const preferredItems = preferredCalendarItems(items);
  const upcoming = preferredItems.filter((item) => item.days_from_today >= 0).slice(0, 5);
  const overdue = preferredItems.filter((item) => item.days_from_today < 0).slice(0, 5);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">문서 일정</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">Calendar</h1>
          <p className="mt-2 text-muted-foreground">납기요청일, 납품일, 지급기한, 유효기간을 우선 표시하고 날짜가 없을 때만 발행일을 참고합니다.</p>
        </div>
        <Badge variant="outline">{items.length}개 일정</Badge>
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>다가오는 일정</CardTitle>
            <Clock3 className="size-5 text-primary" />
          </CardHeader>
          <CardContent className="space-y-3">
            {upcoming.length ? upcoming.map((item) => <CalendarRow key={item.id} item={item} />) : <p className="text-sm text-muted-foreground">다가오는 일정이 없습니다.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>지난 일정</CardTitle>
            <TriangleAlert className="size-5 text-amber-600" />
          </CardHeader>
          <CardContent className="space-y-3">
            {overdue.length ? overdue.map((item) => <CalendarRow key={item.id} item={item} />) : <p className="text-sm text-muted-foreground">지난 일정이 없습니다.</p>}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-5">
        {loading ? (
          <div className="h-40 animate-pulse rounded-lg bg-muted" />
        ) : Object.entries(grouped).length ? (
          Object.entries(grouped).map(([month, monthItems]) => (
            <Card key={month}>
              <CardHeader>
                <CardTitle>{month}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                {monthItems.map((item) => <CalendarRow key={item.id} item={item} />)}
              </CardContent>
            </Card>
          ))
        ) : (
          <Card>
            <CardContent className="p-10 text-center text-muted-foreground">표시할 문서 일정이 없습니다.</CardContent>
          </Card>
        )}
      </div>
    </main>
  );
}

function CalendarRow({ item }: { item: DocumentCalendarItem }) {
  const schedule = getCalendarItemScheduleDate(item);
  return (
    <Link href={item.action_url} className="grid gap-3 rounded-lg border bg-white p-4 transition hover:border-primary/30 hover:shadow-sm md:grid-cols-[140px_1fr_auto] md:items-center">
      <div className="flex items-center gap-2 text-sm font-medium">
        <CalendarDays className="size-4 text-primary" />
        {formatDate(schedule.date)}
      </div>
      <div className="min-w-0">
        <p className="truncate font-medium">{formatCalendarItemTitle(item)}</p>
        <p className="mt-1 truncate text-sm text-muted-foreground">{item.vendor_name || "거래처 미확인"} · {item.customer_name || "고객사 미확인"} · {primaryCategoryLabel({ category: item.document_type })}</p>
      </div>
      <div className="flex flex-wrap gap-2 md:justify-end">
        <Badge className={toneFor(item)}>{item.status}</Badge>
        <Badge variant="outline">{schedule.label}</Badge>
        {schedule.fallback ? <Badge variant="outline">fallback</Badge> : null}
        {isReviewActionable(item) ? <Badge className="bg-amber-100 text-amber-800">검토 필요</Badge> : null}
      </div>
    </Link>
  );
}
