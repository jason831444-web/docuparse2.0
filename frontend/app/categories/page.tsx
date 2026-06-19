"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, BellRing, Building2, CheckCircle2, ChevronRight, FileText, Folder, FolderOpen, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DocumentList } from "@/components/document-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { primaryCategoryLabel, titleCaseLabel } from "@/lib/utils";
import type { DocumentListResponse, DocumentRecord, FolderSummary } from "@/types/document";

type DocumentListItem = DocumentListResponse["items"][number];
type ExplorerMode = "party_type" | "type_party";
type ExplorerNodeKind = "mode" | "party" | "document_type";
type ExplorerNode = {
  id: string;
  label: string;
  kind: ExplorerNodeKind;
  mode: ExplorerMode;
  documents: DocumentListItem[];
  children: ExplorerNode[];
};

const MAX_EXPLORER_DOCUMENTS = 500;

export default function CategoriesPage() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState("");
  const [parent, setParent] = useState("");
  const [pathIds, setPathIds] = useState<string[]>([]);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const [categoryFolders, allDocuments] = await Promise.all([
        api.categories().catch(() => [] as FolderSummary[]),
        loadExplorerDocuments(),
      ]);
      setFolders(categoryFolders);
      setDocuments(allDocuments);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "폴더 정보를 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }

  async function createFolder() {
    if (!label.trim()) return;
    try {
      await api.createCategory({ label, parent: parent || null });
      setLabel("");
      setParent("");
      toast.success("문서 유형 폴더를 추가했습니다");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "문서 유형 폴더를 추가하지 못했습니다");
    }
  }

  async function deleteFolder(folder: FolderSummary) {
    if (folder.count > 0) {
      toast.error("비어 있는 문서 유형만 삭제할 수 있습니다");
      return;
    }
    if (!window.confirm(`빈 문서 유형 "${folder.label}"을 삭제할까요?`)) return;
    try {
      await api.deleteCategory(folder.value);
      toast.success("문서 유형 폴더를 삭제했습니다");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "문서 유형 폴더를 삭제하지 못했습니다");
    }
  }

  const roots = useMemo(() => buildExplorerRoots(documents), [documents]);
  const currentNode = useMemo(() => findNodeByPath(roots, pathIds), [pathIds, roots]);
  const currentChildren = currentNode ? currentNode.children : roots;
  const currentDocuments = currentNode && !currentNode.children.length ? currentNode.documents : [];
  const totalDocuments = documents.length;
  const reviewCount = documents.filter((document) => document.processing_status === "needs_review").length;
  const confirmedCount = documents.filter((document) => document.processing_status === "confirmed").length;
  const processingCount = documents.filter((document) => ["uploaded", "queued", "processing"].includes(document.processing_status)).length;
  const breadcrumb = buildBreadcrumb(roots, pathIds);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">폴더 분류</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">제조업 문서 폴더</h1>
          <p className="mt-2 text-muted-foreground">
            먼저 거래처별 또는 문서유형별 기준 폴더를 열고, 그 안에서 다시 하위 폴더를 선택해 문서를 확인합니다.
          </p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{totalDocuments}</span>건 문서
          {totalDocuments >= MAX_EXPLORER_DOCUMENTS ? <span className="ml-2 text-amber-700">최근 {MAX_EXPLORER_DOCUMENTS}건 기준</span> : null}
        </div>
      </div>

      <section className="mb-6 grid gap-3 md:grid-cols-4">
        <SummaryCard label="문서" value={totalDocuments} icon={<FolderOpen className="size-6 text-primary" />} />
        <SummaryCard label="검토 필요" value={reviewCount} icon={<BellRing className="size-6 text-amber-600" />} />
        <SummaryCard label="확정 완료" value={confirmedCount} icon={<CheckCircle2 className="size-6 text-emerald-600" />} />
        <SummaryCard label="처리 중" value={processingCount} icon={<LoaderCircle className="size-6 text-primary" />} />
      </section>

      <Card className="mb-6 border-dashed">
        <CardContent className="grid gap-4 p-5 lg:grid-cols-[1.2fr_1fr_auto]">
          <div>
            <p className="text-sm font-medium">문서 유형 폴더 만들기</p>
            <p className="mt-1 text-sm text-muted-foreground">자동 분류 외에 직접 관리할 문서유형 폴더를 추가할 수 있습니다.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:col-span-1">
            <Input placeholder="새 문서 유형" value={label} onChange={(event) => setLabel(event.target.value)} />
            <select className="h-10 rounded-md border bg-white px-3 text-sm" value={parent} onChange={(event) => setParent(event.target.value)}>
              <option value="">상위 유형 없음</option>
              {folders.filter((folder) => folder.depth === 0).map((folder) => <option key={folder.value} value={folder.value}>{folder.label}</option>)}
            </select>
          </div>
          <Button type="button" onClick={createFolder} className="lg:self-end">
            <Plus className="size-4" />
            유형 추가
          </Button>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardContent className="p-0">
          <div className="border-b bg-white px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                  <button type="button" className="font-medium text-primary hover:underline" onClick={() => setPathIds([])}>폴더</button>
                  {breadcrumb.map((item, index) => (
                    <span key={item.id} className="flex items-center gap-2">
                      <ChevronRight className="size-4" />
                      <button
                        type="button"
                        className="font-medium text-slate-900 hover:text-primary"
                        onClick={() => setPathIds(pathIds.slice(0, index + 1))}
                      >
                        {item.label}
                      </button>
                    </span>
                  ))}
                </div>
                <h2 className="mt-2 text-xl font-semibold">{currentNode?.label ?? "분류 기준 선택"}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {currentNode ? `${currentNode.documents.length}건 문서 · ${currentNode.children.length}개 하위 폴더` : "회사 기준 또는 문서유형 기준으로 탐색을 시작하세요."}
                </p>
              </div>
              {pathIds.length ? (
                <Button type="button" variant="outline" onClick={() => setPathIds(pathIds.slice(0, -1))}>
                  <ArrowLeft className="size-4" />
                  상위 폴더
                </Button>
              ) : null}
            </div>
          </div>

          {loading ? (
            <div className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-36 animate-pulse rounded-lg bg-muted" />)}
            </div>
          ) : currentChildren.length ? (
            <div className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
              {currentChildren.map((node) => (
                <ExplorerFolderCard key={node.id} node={node} onOpen={() => setPathIds([...pathIds, node.id])} />
              ))}
            </div>
          ) : currentDocuments.length ? (
            <div className="p-5">
              <DocumentList documents={currentDocuments} onChanged={load} returnTo="/categories" />
            </div>
          ) : (
            <div className="p-10 text-center text-muted-foreground">이 폴더에는 아직 문서가 없습니다.</div>
          )}
        </CardContent>
      </Card>

      {folders.some((folder) => folder.custom && folder.count === 0) ? (
        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">빈 사용자 문서 유형</h2>
            <p className="text-sm text-muted-foreground">문서가 없는 직접 생성 폴더만 삭제할 수 있습니다.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {folders.filter((folder) => folder.custom && folder.count === 0).map((folder) => (
              <div key={folder.value} className="flex items-center justify-between gap-3 rounded-lg border bg-white p-4">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-amber-50 text-amber-700">
                    <Folder className="size-5 fill-amber-100" />
                  </span>
                  <span className="truncate font-medium">{folder.label}</span>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => deleteFolder(folder)}>
                  <Trash2 className="size-4" />
                  삭제
                </Button>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="mt-6 rounded-lg border bg-slate-50 px-4 py-3 text-sm text-muted-foreground">
        설정에서 기본 문서 그룹 방식을 바꿔도 이 화면에서는 항상 폴더 탐색 방식으로 볼 수 있습니다. 빠른 목록형 작업은 <Link className="font-medium text-primary underline" href="/documents">문서 목록</Link>에서 이어가세요.
      </div>
    </main>
  );
}

function SummaryCard({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-4">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
        </div>
        {icon}
      </CardContent>
    </Card>
  );
}

function ExplorerFolderCard({ node, onOpen }: { node: ExplorerNode; onOpen: () => void }) {
  const isMode = node.kind === "mode";
  const isParty = node.kind === "party";
  const total = Math.max(node.documents.length, 1);
  const review = node.documents.filter((document) => document.processing_status === "needs_review").length;
  const confirmed = node.documents.filter((document) => document.processing_status === "confirmed").length;
  const processing = node.documents.filter((document) => ["uploaded", "queued", "processing"].includes(document.processing_status)).length;
  const confirmedWidth = Math.round((confirmed / total) * 100);
  const reviewWidth = Math.round((review / total) * 100);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex h-full min-h-36 min-w-0 flex-col justify-between rounded-lg border bg-white p-4 text-left transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="flex min-w-0 gap-3">
          <span className="grid size-12 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-700 group-hover:bg-amber-100">
            {isMode ? (isParty ? <Building2 className="size-6" /> : <FileText className="size-6" />) : <Folder className="size-6 fill-amber-100" />}
          </span>
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold text-slate-900">{node.label}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {node.children.length ? `${node.children.length}개 폴더` : "문서 보기"} · {node.documents.length}건
            </p>
          </div>
        </div>
        <ChevronRight className="mt-2 size-5 shrink-0 text-muted-foreground group-hover:text-primary" />
      </div>
      <div className="mt-4">
        <div className="mb-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>검토 {review}</span>
          <span>확정 {confirmed}</span>
          <span>처리중 {processing}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div className="flex h-full">
            <span className="bg-emerald-500" style={{ width: `${confirmedWidth}%` }} />
            <span className="bg-amber-500" style={{ width: `${reviewWidth}%` }} />
          </div>
        </div>
      </div>
    </button>
  );
}

async function loadExplorerDocuments() {
  const collected: DocumentListItem[] = [];
  let page = 1;
  let total = Infinity;
  while (collected.length < Math.min(total, MAX_EXPLORER_DOCUMENTS)) {
    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("page_size", "100");
    params.set("sort_by", "created_at");
    params.set("order", "desc");
    const data = await api.list(params);
    collected.push(...data.items);
    total = data.total;
    if (!data.items.length || collected.length >= data.total) break;
    page += 1;
  }
  return collected.slice(0, MAX_EXPLORER_DOCUMENTS);
}

function buildExplorerRoots(documents: DocumentListItem[]): ExplorerNode[] {
  return [
    buildRootNode("party_type", "거래처별", "거래처 폴더를 열고 문서유형별로 다시 확인합니다.", documents),
    buildRootNode("type_party", "문서유형별", "문서유형 폴더를 열고 거래처별로 다시 확인합니다.", documents),
  ];
}

function buildRootNode(mode: ExplorerMode, label: string, _description: string, documents: DocumentListItem[]): ExplorerNode {
  const firstLevel = groupDocuments(documents, mode === "party_type" ? partyLabel : documentTypeLabel);
  const children = Array.from(firstLevel.entries())
    .sort(sortGroupEntries)
    .map(([firstLabel, firstDocuments]) => {
      const secondLevel = groupDocuments(firstDocuments, mode === "party_type" ? documentTypeLabel : partyLabel);
      return {
        id: `${mode}:${stableKey(firstLabel)}`,
        label: firstLabel,
        kind: mode === "party_type" ? "party" as const : "document_type" as const,
        mode,
        documents: firstDocuments,
        children: Array.from(secondLevel.entries())
          .sort(sortGroupEntries)
          .map(([secondLabel, secondDocuments]) => ({
            id: `${mode}:${stableKey(firstLabel)}:${stableKey(secondLabel)}`,
            label: secondLabel,
            kind: mode === "party_type" ? "document_type" as const : "party" as const,
            mode,
            documents: secondDocuments,
            children: [],
          })),
      };
    });
  return {
    id: mode,
    label,
    kind: "mode",
    mode,
    documents,
    children,
  };
}

function groupDocuments(documents: DocumentListItem[], labeler: (document: DocumentListItem) => string) {
  const groups = new Map<string, DocumentListItem[]>();
  for (const document of documents) {
    const label = labeler(document);
    const items = groups.get(label) ?? [];
    items.push(document);
    groups.set(label, items);
  }
  return groups;
}

function sortGroupEntries(a: [string, DocumentListItem[]], b: [string, DocumentListItem[]]) {
  return b[1].length - a[1].length || a[0].localeCompare(b[0]);
}

function findNodeByPath(roots: ExplorerNode[], pathIds: string[]) {
  let current: ExplorerNode | undefined;
  let nodes = roots;
  for (const id of pathIds) {
    current = nodes.find((node) => node.id === id);
    if (!current) return undefined;
    nodes = current.children;
  }
  return current;
}

function buildBreadcrumb(roots: ExplorerNode[], pathIds: string[]) {
  const breadcrumb: ExplorerNode[] = [];
  let nodes = roots;
  for (const id of pathIds) {
    const current = nodes.find((node) => node.id === id);
    if (!current) break;
    breadcrumb.push(current);
    nodes = current.children;
  }
  return breadcrumb;
}

function partyLabel(document: Pick<DocumentRecord, "customer_name" | "vendor_name" | "merchant_name">) {
  return document.customer_name || document.vendor_name || document.merchant_name || "거래처 미확인";
}

function documentTypeLabel(document: DocumentListItem) {
  return primaryCategoryLabel(document) || titleCaseLabel(document.document_type);
}

function stableKey(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9가-힣_ -]/gi, "").slice(0, 80) || "unknown";
}
