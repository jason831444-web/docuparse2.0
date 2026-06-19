"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Building2, ChevronRight, Download, FileText, Folder, FolderOpen, Grid2X2, Rows3, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DocumentList, duplicateUploadHints } from "@/components/document-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { documentGroupingLabels, loadDocumentGroupingMode, saveDocumentGroupingMode, type DocumentGroupingMode } from "@/lib/settings";
import { primaryCategoryLabel, requiresReviewExportConfirmation, titleCaseLabel } from "@/lib/utils";
import type { DocumentListResponse, FolderSummary, ProcessingStatus } from "@/types/document";

const statuses: Array<"" | ProcessingStatus> = ["", "uploaded", "queued", "processing", "ready", "needs_review", "confirmed", "failed"];
const MAX_GLOBAL_SELECTION = 500;
type DocumentListItem = DocumentListResponse["items"][number];
type FlatDocumentGroup = { label: string; items: DocumentListItem[] };
type NestedDocumentGroup = { label: string; count: number; children: FlatDocumentGroup[] };
type DocumentViewMode = "folders" | "list" | "grid";
type FolderNodeKind = "mode" | "party" | "document_type";
type FolderNode = {
  id: string;
  label: string;
  kind: FolderNodeKind;
  documents: DocumentListItem[];
  children: FolderNode[];
};
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
  const [view, setView] = useState<DocumentViewMode>("folders");
  const [grouping, setGrouping] = useState<DocumentGroupingMode>("none");
  const [folderPathIds, setFolderPathIds] = useState<string[]>([]);
  const [categories, setCategories] = useState<FolderSummary[]>([]);
  const [selectedDocuments, setSelectedDocuments] = useState<Set<string>>(new Set());
  const [selectionScopeDocuments, setSelectionScopeDocuments] = useState<DocumentListResponse["items"]>([]);
  const [selectingAllDocuments, setSelectingAllDocuments] = useState(false);
  const [allDocumentCount, setAllDocumentCount] = useState<number | null>(null);
  const [bulkExcelMode, setBulkExcelMode] = useState<"combined" | "party_tabs">("combined");
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    search: searchParams.get("search") ?? "",
    category: "",
    source_file_type: "",
    processing_status: "",
    sort_by: "created_at",
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
    next.set("page", String(page));
    next.set("page_size", "100");
    return next;
  }, [filters, page]);

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
    setPage(1);
  }

  const documentTypeLabel = useCallback((document: DocumentListItem) => primaryCategoryLabel(document), []);
  const partyLabel = useCallback((document: DocumentListItem) => document.customer_name || document.vendor_name || "거래처 미확인", []);

  const groupLabel = useCallback((document: DocumentListItem) => {
    if (grouping === "none") return "전체 문서";
    const type = primaryCategoryLabel(document);
    const party = partyLabel(document);
    if (grouping === "party") return party;
    if (grouping === "party_type") return `${party} / ${type}`;
    if (grouping === "type_party") return `${type} / ${party}`;
    return type;
  }, [grouping, partyLabel]);

  const groupedItems = useMemo(() => {
    return (data?.items || []).reduce<FlatDocumentGroup[]>((groups, document) => {
      const label = groupLabel(document);
      const group = groups.find((candidate) => candidate.label === label);
      if (group) group.items.push(document);
      else groups.push({ label, items: [document] });
      return groups;
    }, []);
  }, [data?.items, groupLabel]);
  const nestedGroupedItems = useMemo<NestedDocumentGroup[]>(() => {
    if (grouping !== "party_type" && grouping !== "type_party") return [];
    const parentGroups: NestedDocumentGroup[] = [];
    for (const document of data?.items || []) {
      const parentLabel = grouping === "party_type" ? partyLabel(document) : documentTypeLabel(document);
      const childLabel = grouping === "party_type" ? documentTypeLabel(document) : partyLabel(document);
      let parent = parentGroups.find((candidate) => candidate.label === parentLabel);
      if (!parent) {
        parent = { label: parentLabel, count: 0, children: [] };
        parentGroups.push(parent);
      }
      parent.count += 1;
      let child = parent.children.find((candidate) => candidate.label === childLabel);
      if (!child) {
        child = { label: childLabel, items: [] };
        parent.children.push(child);
      }
      child.items.push(document);
    }
    return parentGroups;
  }, [data?.items, documentTypeLabel, grouping, partyLabel]);
  const duplicateHints = useMemo(() => duplicateUploadHints(data?.items || []), [data?.items]);
  const duplicateHintCount = useMemo(() => duplicateFilenameCount(data?.items || []), [data?.items]);
  const visibleDocuments = useMemo(() => data?.items || [], [data?.items]);
  const folderRoots = useMemo(() => buildDocumentFolderRoots(visibleDocuments, grouping, partyLabel, documentTypeLabel), [documentTypeLabel, grouping, partyLabel, visibleDocuments]);
  const activeFolderNode = useMemo(() => findFolderNodeByPath(folderRoots, folderPathIds), [folderPathIds, folderRoots]);
  const activeFolderChildren = activeFolderNode ? activeFolderNode.children : folderRoots;
  const activeFolderDocuments = activeFolderNode && !activeFolderNode.children.length ? activeFolderNode.documents : [];
  const folderBreadcrumb = useMemo(() => buildFolderBreadcrumb(folderRoots, folderPathIds), [folderPathIds, folderRoots]);
  const totalDocumentCount = allDocumentCount ?? data?.total ?? visibleDocuments.length;
  const selectionScopeById = useMemo(() => {
    const combined = new Map<string, DocumentListResponse["items"][number]>();
    visibleDocuments.forEach((document) => combined.set(document.id, document));
    selectionScopeDocuments.forEach((document) => combined.set(document.id, document));
    return Array.from(combined.values());
  }, [selectionScopeDocuments, visibleDocuments]);
  const selectedDocumentIds = useMemo(() => Array.from(selectedDocuments), [selectedDocuments]);
  const selectedScopeDocuments = useMemo(
    () => selectionScopeById.filter((document) => selectedDocuments.has(document.id)),
    [selectedDocuments, selectionScopeById]
  );
  const globalSelectionLimit = Math.min(totalDocumentCount, MAX_GLOBAL_SELECTION);
  const allDocumentsSelected = globalSelectionLimit > 0 && selectedDocuments.size >= globalSelectionLimit;

  async function selectAllDocuments() {
    setSelectingAllDocuments(true);
    try {
      const documents = await loadAllDocumentPages(MAX_GLOBAL_SELECTION);
      setSelectionScopeDocuments(documents);
      setSelectedDocuments(new Set(documents.map((document) => document.id)));
      setAllDocumentCount((current) => Math.max(current ?? 0, documents.length));
      if (documents.length < totalDocumentCount) {
        toast.info(`문서가 많아 최근 ${documents.length}건만 선택했습니다. 전체 선택은 최대 ${MAX_GLOBAL_SELECTION}건까지 지원합니다.`);
      } else {
        toast.success(`전체 문서 ${documents.length}건을 선택했습니다`);
      }
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

  function updateGrouping(value: DocumentGroupingMode) {
    setGrouping(value);
    saveDocumentGroupingMode(value);
    setFolderPathIds([]);
  }

  function selectedExportParams(extra?: Record<string, string>) {
    const exportParams = new URLSearchParams();
    selectedDocumentIds.forEach((id) => exportParams.append("document_ids", id));
    Object.entries(extra || {}).forEach(([key, value]) => {
      if (value) exportParams.set(key, value);
    });
    return exportParams;
  }

  function confirmReviewExport() {
    if (!selectedDocumentIds.length) {
      toast.error("선택된 문서가 없습니다.");
      return false;
    }
    if (selectedScopeDocuments.some(requiresReviewExportConfirmation)) {
      return window.confirm("선택한 문서 중 검토 필요 문서가 있습니다. 내보내기 파일에는 review_required와 경고 정보가 포함됩니다. 계속할까요?");
    }
    return true;
  }

  function exportSelectedDocuments(kind: "csv" | "xlsx") {
    if (!confirmReviewExport()) return;
    window.location.href = kind === "csv"
      ? api.exportCsvUrl(selectedExportParams())
      : api.exportExcelUrl(selectedExportParams({ sheet_mode: bulkExcelMode }));
  }

  async function downloadSelectedDocuments() {
    if (!selectedDocumentIds.length) {
      toast.error("선택된 문서가 없습니다.");
      return;
    }
    try {
      await api.bulkDownload(selectedDocumentIds);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "원본 다운로드에 실패했습니다");
    }
  }

  async function deleteSelectedDocuments() {
    if (!selectedDocumentIds.length) return;
    if (!window.confirm(`선택한 문서 ${selectedDocumentIds.length}건을 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return;
    try {
      const result = await api.bulkDelete(selectedDocumentIds);
      toast.success(`문서 ${result.deleted}건을 삭제했습니다`);
      clearSelectedDocuments();
      const [nextData, stats] = await Promise.all([
        api.list(params),
        api.stats().catch(() => null),
      ]);
      setData(nextData);
      if (stats) setAllDocumentCount(stats.total);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "삭제에 실패했습니다");
    }
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
            <Button type="button" variant={view === "folders" ? "default" : "ghost"} size="sm" onClick={() => setView("folders")} title="폴더 보기"><FolderOpen className="size-4" /></Button>
            <Button type="button" variant={view === "list" ? "default" : "ghost"} size="sm" onClick={() => setView("list")}><Rows3 className="size-4" /></Button>
            <Button type="button" variant={view === "grid" ? "default" : "ghost"} size="sm" onClick={() => setView("grid")}><Grid2X2 className="size-4" /></Button>
          </div>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="grid gap-3 p-5 lg:grid-cols-[1.5fr_repeat(6,1fr)]">
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
            <option value="desc">내림차순</option>
            <option value="asc">오름차순</option>
          </select>
          <select className="h-10 rounded-md border bg-white px-3 text-sm" value={filters.sort_by} onChange={(event) => setFilter("sort_by", event.target.value)}>
            <option value="created_at">전체 업로드순</option>
            <option value="updated_at">최근 수정순</option>
            <option value="extracted_date">문서 날짜순</option>
            <option value="extracted_amount">금액순</option>
            <option value="title">제목순</option>
          </select>
          <select
            className="h-10 rounded-md border bg-white px-3 text-sm"
            value={grouping}
            onChange={(event) => {
              updateGrouping(event.target.value as DocumentGroupingMode);
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
            <span className="text-sm text-muted-foreground">
              선택됨: {selectedDocuments.size} / 전체 문서 {totalDocumentCount}개
              {totalDocumentCount > MAX_GLOBAL_SELECTION ? ` · 전체 선택 최대 ${MAX_GLOBAL_SELECTION}건` : ""}
            </span>
            <Button type="button" variant="outline" size="sm" disabled={!totalDocumentCount || allDocumentsSelected || selectingAllDocuments} onClick={selectAllDocuments}>
              {selectingAllDocuments ? "전체 문서 선택 중..." : totalDocumentCount > MAX_GLOBAL_SELECTION ? `최근 ${MAX_GLOBAL_SELECTION}건 선택` : "전체 문서 선택"}
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={!selectedDocuments.size} onClick={clearSelectedDocuments}>
              전체 선택 해제
            </Button>
            <select
              className="h-8 rounded-md border bg-white px-2 text-xs"
              value={bulkExcelMode}
              onChange={(event) => setBulkExcelMode(event.target.value as "combined" | "party_tabs")}
              aria-label="전체 선택 Excel 내보내기 방식"
            >
              <option value="combined">통합 시트형</option>
              <option value="party_tabs">거래처별 탭</option>
            </select>
            <Button type="button" variant="outline" size="sm" disabled={!selectedDocuments.size} onClick={() => exportSelectedDocuments("xlsx")}>
              <Download className="size-4" />
              선택 Excel
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={!selectedDocuments.size} onClick={() => exportSelectedDocuments("csv")}>
              <Download className="size-4" />
              선택 CSV
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={!selectedDocuments.size} onClick={downloadSelectedDocuments}>
              <Download className="size-4" />
              원본 다운로드
            </Button>
            <Button type="button" variant="destructive" size="sm" disabled={!selectedDocuments.size} onClick={deleteSelectedDocuments}>
              <Trash2 className="size-4" />
              선택 삭제
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
          <PaginationBar page={page} total={data.total} pageSize={data.page_size} onPageChange={setPage} />
          {view === "folders" ? (
            <DocumentFolderExplorer
              roots={folderRoots}
              pathIds={folderPathIds}
              breadcrumb={folderBreadcrumb}
              currentNode={activeFolderNode}
              folderChildren={activeFolderChildren}
              documents={activeFolderDocuments}
              duplicateHints={duplicateHints}
              selected={selectedDocuments}
              onSelectedChange={setSelectedDocuments}
              selectionScopeDocuments={selectionScopeById}
              onOpen={(id) => setFolderPathIds([...folderPathIds, id])}
              onBack={() => setFolderPathIds(folderPathIds.slice(0, -1))}
              onRoot={() => setFolderPathIds([])}
              onCrumb={(index) => setFolderPathIds(folderPathIds.slice(0, index + 1))}
              onChanged={() => api.list(params).then(setData)}
            />
          ) : grouping === "party_type" || grouping === "type_party" ? (
            nestedGroupedItems.map((group) => (
              <section key={group.label} className="space-y-3 rounded-xl border bg-slate-50/50 p-3">
                <div className="flex items-center justify-between rounded-lg border bg-white px-4 py-3">
                  <h2 className="font-semibold">{group.label}</h2>
                  <span className="text-sm text-muted-foreground">{group.count}건</span>
                </div>
                {group.children.map((child) => (
                  <div key={`${group.label}:${child.label}`} className="space-y-3">
                    <div className="flex items-center justify-between px-2">
                      <h3 className="text-sm font-semibold text-slate-700">{child.label}</h3>
                      <span className="text-xs text-muted-foreground">{child.items.length}건</span>
                    </div>
                    <DocumentList
                      documents={child.items}
                      view={view}
                      duplicateHintsOverride={duplicateHints}
                      selected={selectedDocuments}
                      onSelectedChange={setSelectedDocuments}
                      selectionScopeDocuments={selectionScopeById}
                      onChanged={() => api.list(params).then(setData)}
                      returnTo="/documents"
                    />
                  </div>
                ))}
              </section>
            ))
          ) : (
            groupedItems.map((group) => (
              <section key={group.label} className="space-y-3">
                {grouping !== "none" ? <div className="flex items-center justify-between rounded-lg border bg-white px-4 py-3">
                  <h2 className="font-semibold">{group.label}</h2>
                  <span className="text-sm text-muted-foreground">{group.items.length}건</span>
                </div> : null}
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
            ))
          )}
          <PaginationBar page={page} total={data.total} pageSize={data.page_size} onPageChange={setPage} />
        </div>
      ) : (
        <Card>
          <CardContent className="p-10 text-center text-muted-foreground">조건에 맞는 문서가 없습니다.</CardContent>
        </Card>
      )}
    </main>
  );
}

function DocumentFolderExplorer({
  roots,
  pathIds,
  breadcrumb,
  currentNode,
  folderChildren,
  documents,
  duplicateHints,
  selected,
  onSelectedChange,
  selectionScopeDocuments,
  onOpen,
  onBack,
  onRoot,
  onCrumb,
  onChanged,
}: {
  roots: FolderNode[];
  pathIds: string[];
  breadcrumb: FolderNode[];
  currentNode?: FolderNode;
  folderChildren: FolderNode[];
  documents: DocumentListItem[];
  duplicateHints: ReturnType<typeof duplicateUploadHints>;
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
  selectionScopeDocuments: DocumentListItem[];
  onOpen: (id: string) => void;
  onBack: () => void;
  onRoot: () => void;
  onCrumb: (index: number) => void;
  onChanged: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-xl border bg-white">
      <div className="border-b bg-slate-50/70 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <button type="button" className="font-medium text-primary hover:underline" onClick={onRoot}>문서</button>
              {breadcrumb.map((item, index) => (
                <span key={item.id} className="flex items-center gap-2">
                  <ChevronRight className="size-4" />
                  <button type="button" className="font-medium text-slate-900 hover:text-primary" onClick={() => onCrumb(index)}>
                    {item.label}
                  </button>
                </span>
              ))}
            </div>
            <h2 className="mt-1 text-lg font-semibold">{currentNode?.label ?? "폴더 보기"}</h2>
            <p className="text-sm text-muted-foreground">
              {currentNode ? `${currentNode.documents.length}건 문서 · ${currentNode.children.length}개 하위 폴더` : "회사별 또는 문서유형별 폴더를 열어 탐색합니다."}
            </p>
          </div>
          {pathIds.length ? (
            <Button type="button" variant="outline" size="sm" onClick={onBack}>
              <ArrowLeft className="size-4" />
              상위 폴더
            </Button>
          ) : null}
        </div>
      </div>

      {folderChildren.length ? (
        <div className="grid gap-x-7 gap-y-8 p-6 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {folderChildren.map((node) => (
            <FinderFolderIcon key={node.id} node={node} onOpen={() => onOpen(node.id)} />
          ))}
        </div>
      ) : documents.length ? (
        <div className="space-y-5 p-5">
          <div className="grid gap-x-7 gap-y-8 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
            {documents.map((document) => (
              <FinderDocumentIcon key={document.id} document={document} />
            ))}
          </div>
          <DocumentList
            documents={documents}
            view="list"
            duplicateHintsOverride={duplicateHints}
            selected={selected}
            onSelectedChange={onSelectedChange}
            selectionScopeDocuments={selectionScopeDocuments}
            onChanged={onChanged}
            returnTo="/documents"
          />
        </div>
      ) : (
        <div className="p-10 text-center text-muted-foreground">
          {roots.length ? "이 폴더에는 아직 문서가 없습니다." : "표시할 문서 폴더가 없습니다."}
        </div>
      )}
    </section>
  );
}

function FinderFolderIcon({ node, onOpen }: { node: FolderNode; onOpen: () => void }) {
  const caption = node.children.length ? `${node.children.length}개 폴더` : `${node.documents.length}건`;
  const Icon = node.kind === "party" ? Building2 : node.kind === "document_type" ? FileText : FolderOpen;
  return (
    <button type="button" className="group flex min-w-0 flex-col items-center text-center" onClick={onOpen}>
      <span className="relative grid h-20 w-24 place-items-center rounded-xl bg-sky-100 text-sky-700 shadow-sm ring-1 ring-sky-200 transition group-hover:-translate-y-0.5 group-hover:bg-sky-200 group-hover:shadow-md">
        <span className="absolute left-3 top-[-7px] h-4 w-10 rounded-t-lg bg-sky-200 ring-1 ring-sky-200" />
        <Folder className="size-12 fill-sky-300 stroke-sky-600" />
        <Icon className="absolute bottom-3 right-4 size-4 rounded bg-white/75 p-0.5 text-sky-700" />
      </span>
      <span className="mt-2 line-clamp-2 min-h-10 max-w-28 break-words text-sm font-medium leading-tight text-slate-900">{node.label}</span>
      <span className="text-xs text-muted-foreground">{caption}</span>
    </button>
  );
}

function FinderDocumentIcon({ document }: { document: DocumentListItem }) {
  const title = document.document_number || document.title || document.original_filename;
  return (
    <Link href={`/documents/${document.id}?from=${encodeURIComponent("/documents")}`} className="group flex min-w-0 flex-col items-center text-center">
      <span className="grid h-20 w-16 place-items-center rounded-lg border bg-white shadow-sm transition group-hover:-translate-y-0.5 group-hover:shadow-md">
        <FileText className="size-9 text-slate-500" />
      </span>
      <span className="mt-2 line-clamp-2 min-h-10 max-w-28 break-words text-sm font-medium leading-tight text-slate-900">{title}</span>
      <span className="max-w-28 truncate text-xs text-muted-foreground">{statusLabels[document.processing_status] ?? document.processing_status}</span>
    </Link>
  );
}

function PaginationBar({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(total, page * pageSize);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white px-4 py-3 text-sm">
      <span className="text-muted-foreground">
        전체 {total}건 중 {start}-{end}건 표시 · {page}/{totalPages}페이지
      </span>
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={() => onPageChange(Math.max(1, page - 1))}>
          이전
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPageChange(Math.min(totalPages, page + 1))}>
          다음
        </Button>
      </div>
    </div>
  );
}

async function loadAllDocumentPages(limit = MAX_GLOBAL_SELECTION) {
  const pageSize = 100;
  const documents: DocumentListResponse["items"] = [];
  let page = 1;
  let total = 0;

  do {
    const remaining = Math.max(limit - documents.length, 0);
    if (!remaining) break;
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(Math.min(pageSize, remaining)),
      sort_by: "created_at",
      order: "desc",
    });
    const response = await api.list(params);
    documents.push(...response.items);
    total = response.total;
    page += 1;
  } while (documents.length < total && documents.length < limit);

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

function buildDocumentFolderRoots(
  documents: DocumentListItem[],
  grouping: DocumentGroupingMode,
  partyLabel: (document: DocumentListItem) => string,
  documentTypeLabel: (document: DocumentListItem) => string,
) {
  if (grouping === "party") return buildSingleLevelFolders(documents, partyLabel, "party");
  if (grouping === "document_type") return buildSingleLevelFolders(documents, documentTypeLabel, "document_type");
  if (grouping === "party_type") return buildNestedFolders(documents, partyLabel, documentTypeLabel, "party", "document_type");
  if (grouping === "type_party") return buildNestedFolders(documents, documentTypeLabel, partyLabel, "document_type", "party");
  return [
    {
      id: "party_type",
      label: "거래처별",
      kind: "mode" as const,
      documents,
      children: buildNestedFolders(documents, partyLabel, documentTypeLabel, "party", "document_type", "party_type"),
    },
    {
      id: "type_party",
      label: "문서유형별",
      kind: "mode" as const,
      documents,
      children: buildNestedFolders(documents, documentTypeLabel, partyLabel, "document_type", "party", "type_party"),
    },
  ];
}

function buildSingleLevelFolders(
  documents: DocumentListItem[],
  labeler: (document: DocumentListItem) => string,
  kind: Exclude<FolderNodeKind, "mode">,
  prefix: string = kind,
): FolderNode[] {
  return Array.from(groupDocumentsForFolders(documents, labeler).entries())
    .sort(sortFolderEntries)
    .map(([label, items]) => ({
      id: `${prefix}:${stableFolderKey(label)}`,
      label,
      kind,
      documents: items,
      children: [],
    }));
}

function buildNestedFolders(
  documents: DocumentListItem[],
  firstLabeler: (document: DocumentListItem) => string,
  secondLabeler: (document: DocumentListItem) => string,
  firstKind: Exclude<FolderNodeKind, "mode">,
  secondKind: Exclude<FolderNodeKind, "mode">,
  prefix: string = `${firstKind}_${secondKind}`,
): FolderNode[] {
  return Array.from(groupDocumentsForFolders(documents, firstLabeler).entries())
    .sort(sortFolderEntries)
    .map(([label, items]) => ({
      id: `${prefix}:${stableFolderKey(label)}`,
      label,
      kind: firstKind,
      documents: items,
      children: buildSingleLevelFolders(items, secondLabeler, secondKind, `${prefix}:${stableFolderKey(label)}`),
    }));
}

function groupDocumentsForFolders(documents: DocumentListItem[], labeler: (document: DocumentListItem) => string) {
  const groups = new Map<string, DocumentListItem[]>();
  for (const document of documents) {
    const label = labeler(document);
    const items = groups.get(label) ?? [];
    items.push(document);
    groups.set(label, items);
  }
  return groups;
}

function sortFolderEntries(a: [string, DocumentListItem[]], b: [string, DocumentListItem[]]) {
  return b[1].length - a[1].length || a[0].localeCompare(b[0]);
}

function findFolderNodeByPath(roots: FolderNode[], pathIds: string[]) {
  let current: FolderNode | undefined;
  let nodes = roots;
  for (const id of pathIds) {
    current = nodes.find((node) => node.id === id);
    if (!current) return undefined;
    nodes = current.children;
  }
  return current;
}

function buildFolderBreadcrumb(roots: FolderNode[], pathIds: string[]) {
  const breadcrumb: FolderNode[] = [];
  let nodes = roots;
  for (const id of pathIds) {
    const current = nodes.find((node) => node.id === id);
    if (!current) break;
    breadcrumb.push(current);
    nodes = current.children;
  }
  return breadcrumb;
}

function stableFolderKey(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9가-힣_ -]/gi, "").slice(0, 80) || "unknown";
}
