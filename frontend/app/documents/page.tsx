"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, Grid2X2, Rows3, Search } from "lucide-react";
import { toast } from "sonner";

import { DocumentList } from "@/components/document-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { DocumentListResponse, FolderSummary, ProcessingStatus } from "@/types/document";

const statuses: Array<"" | ProcessingStatus> = ["", "uploaded", "queued", "processing", "ready", "needs_review", "confirmed", "failed"];
const statusLabels: Record<"" | ProcessingStatus, string> = {
  "": "전체 상태",
  uploaded: "업로드됨",
  queued: "대기 중",
  processing: "처리 중",
  ready: "자동 추출 완료",
  needs_review: "검토 필요",
  confirmed: "확정 완료",
  completed: "자동 추출 완료",
  failed: "실패"
};

export default function DocumentsPage() {
  return (
    <Suspense fallback={<DocumentsSkeleton />}>
      <DocumentsContent />
    </Suspense>
  );
}

function DocumentsSkeleton() {
  return (
    <main className="shell py-8">
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-lg bg-muted" />)}
      </div>
    </main>
  );
}

function DocumentsContent() {
  const searchParams = useSearchParams();
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"grid" | "list">("list");
  const [categories, setCategories] = useState<FolderSummary[]>([]);
  const [filters, setFilters] = useState({
    search: searchParams.get("search") ?? "",
    category: "",
    source_file_type: "",
    processing_status: "",
    sort_by: "updated_at",
    order: "desc"
  });

  useEffect(() => {
    const search = searchParams.get("search") ?? "";
    setFilters((current) => current.search === search ? current : { ...current, search });
  }, [searchParams]);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => setCategories([]));
  }, []);

  const params = useMemo(() => {
    const next = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) next.set(key, value);
    });
    return next;
  }, [filters]);

  const loadDocuments = useCallback(() => {
    setLoading(true);
    const handle = window.setTimeout(() => {
      api.list(params).then(setData).catch((error) => toast.error(error instanceof Error ? error.message : "문서 목록을 불러오지 못했습니다")).finally(() => setLoading(false));
    }, 180);
    return () => window.clearTimeout(handle);
  }, [params]);

  useEffect(() => loadDocuments(), [loadDocuments]);

  function setFilter(key: keyof typeof filters, value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-normal">문서 목록</h1>
          <p className="mt-2 text-muted-foreground">발주서, 견적서, 거래명세서, 납품서의 추출 데이터와 검토 상태를 확인하세요.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline"><a href={api.exportCsvUrl()}><Download className="size-4" /> CSV로 내보내기</a></Button>
          <Button asChild variant="outline"><a href={api.exportExcelUrl()}><Download className="size-4" /> Excel로 내보내기</a></Button>
          <div className="flex rounded-md border bg-white p-1">
            <Button type="button" variant={view === "list" ? "default" : "ghost"} size="sm" onClick={() => setView("list")}><Rows3 className="size-4" /></Button>
            <Button type="button" variant={view === "grid" ? "default" : "ghost"} size="sm" onClick={() => setView("grid")}><Grid2X2 className="size-4" /></Button>
          </div>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="grid gap-3 p-5 lg:grid-cols-[1.5fr_repeat(4,1fr)]">
          <label className="relative">
            <Search className="absolute left-3 top-3 size-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="파일명, 거래처명, 품목명, 문서번호로 검색" value={filters.search} onChange={(event) => setFilter("search", event.target.value)} />
          </label>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={filters.category} onChange={(event) => setFilter("category", event.target.value)}>
            <option value="">문서 유형</option>
            {categories.map((folder) => (
              <option key={folder.value} value={folder.category || folder.value}>
                {folder.label}
              </option>
            ))}
          </select>
          <Input placeholder="파일 형식" value={filters.source_file_type} onChange={(event) => setFilter("source_file_type", event.target.value)} />
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={filters.processing_status} onChange={(event) => setFilter("processing_status", event.target.value)}>
            {statuses.map((status) => <option key={status || "all"} value={status}>{statusLabels[status]}</option>)}
          </select>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={filters.order} onChange={(event) => setFilter("order", event.target.value)}>
            <option value="desc">최근 업로드 날짜순</option>
            <option value="asc">오래된 업로드 날짜순</option>
          </select>
        </CardContent>
      </Card>

      {loading ? (
        <div className={view === "grid" ? "grid gap-4 lg:grid-cols-2" : "space-y-3"}>
          {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-lg bg-muted" />)}
        </div>
      ) : data?.items.length ? (
        <DocumentList documents={data.items} view={view} onChanged={() => api.list(params).then(setData)} returnTo="/documents" />
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-muted-foreground">조건에 맞는 문서가 없습니다.</CardContent>
        </Card>
      )}
    </main>
  );
}
