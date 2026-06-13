"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, Grid2X2, Rows3, Search } from "lucide-react";
import { toast } from "sonner";

import { DocumentList, duplicateUploadHints } from "@/components/document-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { documentGroupingLabels, loadDocumentGroupingMode, saveDocumentGroupingMode, type DocumentGroupingMode } from "@/lib/settings";
import { primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
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
  const [grouping, setGrouping] = useState<DocumentGroupingMode>("document_type");
  const [categories, setCategories] = useState<FolderSummary[]>([]);
  const [selectedDocuments, setSelectedDocuments] = useState<Set<string>>(new Set());
  const [selectionScopeDocuments, setSelectionScopeDocuments] = useState<DocumentListResponse["items"]>([]);
  const [selectingAllDocuments, setSelectingAllDocuments] = useState(false);
  const [allDocumentCount, setAllDocumentCount] = useState<number | null>(null);
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
    api.stats().then((stats) => setAllDocumentCount(stats.total)).catch(() => setAllDocumentCount(null));
    setGrouping(loadDocumentGroupingMode());
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

  useEffect(() => {
    if (!data?.items.some((document) => ["uploaded", "queued", "processing"].includes(document.processing_status))) return;
    const interval = window.setInterval(() => {
      api.list(params).then(setData).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(interval);
  }, [data?.items, params]);

  function setFilter(key: keyof typeof filters, value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  const groupLabel = useCallback((document: DocumentListResponse["items"][number]) => {
    const type = primaryCategoryLabel(document);
    const party = document.customer_name || document.vendor_name || "거래처 미확인";
    if (grouping === "party") return party;
    if (grouping === "party_type") return `${party} / ${type}`;
    if (grouping === "type_party") return `${type} / ${party}`;
    return type;
  }, [grouping]);

  const groupedItems = useMemo(() => {
    return (data?.items || []).reduce<Array<{ label: string; items: DocumentListResponse["items"] }>>((groups, document) => {
      const label = groupLabel(document);
      const group = groups.find((candidate) => candidate.label === label);
      if (group) group.items.push(document);
      else groups.push({ label, items: [document] });
      return groups;
    }, []);
  }, [data?.items, groupLabel]);
  const duplicateHints = useMemo(() => duplicateUploadHints(data?.items || []), [data?.items]);
  const duplicateHintCount = useMemo(() => duplicateFilenameCount(data?.items || []), [data?.items]);
  const visibleDocuments = useMemo(() => data?.items || [], [data?.items]);
  const totalDocumentCount = allDocumentCount ?? data?.total ?? visibleDocuments.length;
  const selectionScopeById = useMemo(() => {
    const combined = new Map<string, DocumentListResponse["items"][number]>();
    visibleDocuments.forEach((document) => combined.set(document.id, document));
    selectionScopeDocuments.forEach((document) => combined.set(document.id, document));
    return Array.from(combined.values());
  }, [selectionScopeDocuments, visibleDocuments]);
  const allDocumentsSelected = totalDocumentCount > 0 && selectedDocuments.size >= totalDocumentCount;

  async function selectAllDocuments() {
    setSelectingAllDocuments(true);
    try {
      const documents = await loadAllDocumentPages();
      setSelectionScopeDocuments(documents);
      setSelectedDocuments(new Set(documents.map((document) => document.id)));
      setAllDocumentCount(documents.length);
      toast.success(`전체 문서 ${documents.length}건을 선택했습니다`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "전체 문서 선택에 실패했습니다");
    } finally {
      setSelectingAllDocuments(false);
    }
  }

  function clearSelectedDocuments() {
    setSelectedDocuments(new Set());
    setSelectionScopeDocuments([]);
  }

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-normal">문서 목록</h1>
          <p className="mt-2 text-muted-foreground">발주서, 견적서, 거래명세서, 납품서의 추출 데이터와 검토 상태를 확인하세요.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline"><a href={api.exportCsvUrl()}><Download className="size-4" /> 전체 CSV</a></Button>
          <Button asChild variant="outline"><a href={api.exportExcelUrl()}><Download className="size-4" /> 전체 Excel</a></Button>
          <div className="flex rounded-md border bg-white p-1">
            <Button type="button" variant={view === "list" ? "default" : "ghost"} size="sm" onClick={() => setView("list")}><Rows3 className="size-4" /></Button>
            <Button type="button" variant={view === "grid" ? "default" : "ghost"} size="sm" onClick={() => setView("grid")}><Grid2X2 className="size-4" /></Button>
          </div>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="grid gap-3 p-5 lg:grid-cols-[1.5fr_repeat(5,1fr)]">
          <label className="relative">
            <Search className="absolute left-3 top-3 size-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="파일명, 거래처명, 품목명, 문서번호로 검색" value={filters.search} onChange={(event) => setFilter("search", event.target.value)} />
          </label>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={filters.category} onChange={(event) => setFilter("category", event.target.value)}>
            <option value="">문서 유형</option>
            {categories.map((folder) => (
              <option key={folder.value} value={folder.category || folder.value}>
                {titleCaseLabel(folder.category || folder.value || folder.label)}
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
          <select
            className="h-10 rounded-md border bg-white px-3 text-sm"
            value={grouping}
            onChange={(event) => {
              const value = event.target.value as DocumentGroupingMode;
              setGrouping(value);
              saveDocumentGroupingMode(value);
            }}
          >
            {Object.entries(documentGroupingLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </CardContent>
      </Card>

      {duplicateHintCount ? (
        <div className="mb-5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          같은 파일명으로 업로드된 후보 {duplicateHintCount}건이 있습니다. 재처리/검토 전 최신 업로드인지 확인하세요.
        </div>
      ) : null}

      {data?.items.length ? (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white px-4 py-3">
          <div>
            <p className="text-sm font-semibold">문서 선택</p>
            <p className="text-xs text-muted-foreground">필터/그룹과 관계없이 저장된 모든 문서를 한 번에 선택합니다.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">선택됨: {selectedDocuments.size} / 전체 문서 {totalDocumentCount}개</span>
            <Button type="button" variant="outline" size="sm" disabled={!totalDocumentCount || allDocumentsSelected || selectingAllDocuments} onClick={selectAllDocuments}>
              {selectingAllDocuments ? "전체 문서 선택 중..." : "전체 문서 선택"}
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={!selectedDocuments.size} onClick={clearSelectedDocuments}>
              전체 선택 해제
            </Button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className={view === "grid" ? "grid gap-4 lg:grid-cols-2" : "space-y-3"}>
          {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-lg bg-muted" />)}
        </div>
      ) : data?.items.length ? (
        <div className="space-y-5">
          {groupedItems.map((group) => (
            <section key={group.label} className="space-y-3">
              <div className="flex items-center justify-between rounded-lg border bg-white px-4 py-3">
                <h2 className="font-semibold">{group.label}</h2>
                <span className="text-sm text-muted-foreground">{group.items.length}건</span>
              </div>
              <DocumentList
                documents={group.items}
                view={view}
                duplicateHintsOverride={duplicateHints}
                selected={selectedDocuments}
                onSelectedChange={setSelectedDocuments}
                selectionScopeDocuments={selectionScopeById}
                onChanged={() => api.list(params).then(setData)}
                returnTo="/documents"
              />
            </section>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-muted-foreground">조건에 맞는 문서가 없습니다.</CardContent>
        </Card>
      )}
    </main>
  );
}

async function loadAllDocumentPages() {
  const pageSize = 100;
  const documents: DocumentListResponse["items"] = [];
  let page = 1;
  let total = 0;

  do {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      sort_by: "updated_at",
      order: "desc",
    });
    const response = await api.list(params);
    documents.push(...response.items);
    total = response.total;
    page += 1;
  } while (documents.length < total);

  return documents;
}

function duplicateFilenameCount(documents: DocumentListResponse["items"]) {
  const counts = new Map<string, number>();
  for (const document of documents) {
    const key = duplicateFilenameKey(document.original_filename);
    if (!key) continue;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  let total = 0;
  for (const count of counts.values()) {
    if (count > 1) total += count;
  }
  return total;
}

function duplicateFilenameKey(value?: string | null) {
  return (value || "").normalize("NFKC").replace(/\s+/g, "").trim().toLowerCase() || null;
}
