"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CalendarDays, CheckCircle2, Clock3, FileText, RefreshCcw, ShieldCheck, TriangleAlert } from "lucide-react";

import { DocumentCard } from "@/components/document-card";
import { FolderCard } from "@/components/folder-card";
import { UploadDropzone } from "@/components/upload-dropzone";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { documentDisplayTitle, documentSummaryShort, formatDate } from "@/lib/utils";
import type { ActivitySummary, DocumentCalendarItem, DocumentStats } from "@/types/document";

export default function DashboardPage() {
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [activity, setActivity] = useState<ActivitySummary | null>(null);
  const [calendar, setCalendar] = useState<DocumentCalendarItem[]>([]);

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null));
    api.activity().then(setActivity).catch(() => setActivity(null));
    api.calendar(new URLSearchParams({ limit: "6" })).then(setCalendar).catch(() => setCalendar([]));
  }, []);

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
        <UploadDropzone />
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
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
            <CardTitle>다가오는 일정</CardTitle>
            <Button asChild variant="ghost" size="sm"><Link href="/calendar">캘린더 열기</Link></Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {calendar.slice(0, 5).map((item) => (
              <Link key={item.id} href={item.action_url} className="flex min-w-0 items-center justify-between gap-3 overflow-hidden rounded-lg border bg-white p-4 shadow-sm shadow-slate-200/50 transition hover:border-primary/30 hover:shadow-md">
                <div className="min-w-0">
                  <p className="line-clamp-1 break-words font-medium leading-snug">{documentDisplayTitle({ document_number: item.document_number, title: item.document_title, original_filename: item.original_filename })}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{item.date_label} · {formatDate(item.date)} · {item.status}</p>
                </div>
                <CalendarDays className="size-5 shrink-0 text-primary" />
              </Link>
            ))}
            {!calendar.length ? <p className="text-sm text-muted-foreground">등록된 문서 일정이 없습니다.</p> : null}
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
