"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BellRing, CheckCircle2, ChevronRight, Folder, FolderKanban, LayoutGrid, ListTree, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { FolderCard } from "@/components/folder-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { FolderSummary } from "@/types/document";

type FolderTreeNode = FolderSummary & { children: FolderSummary[] };
type FolderViewMode = "folders" | "list";
type FolderTreeRow = FolderSummary & { level: number; parentLabel: string | null };

export default function CategoriesPage() {
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [label, setLabel] = useState("");
  const [parent, setParent] = useState("");
  const [viewMode, setViewMode] = useState<FolderViewMode>("folders");

  useEffect(() => {
    load();
    const savedViewMode = window.localStorage.getItem("docparse.folderViewMode");
    if (savedViewMode === "folders" || savedViewMode === "list") setViewMode(savedViewMode);
  }, []);

  function changeViewMode(mode: FolderViewMode) {
    setViewMode(mode);
    window.localStorage.setItem("docparse.folderViewMode", mode);
  }

  function load() {
    api.categories().then(setFolders).catch(() => setFolders([]));
  }

  async function createFolder() {
    if (!label.trim()) return;
    try {
      await api.createCategory({ label, parent: parent || null });
      setLabel("");
      setParent("");
      toast.success("문서 유형을 추가했습니다");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "문서 유형을 추가하지 못했습니다");
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
      toast.success("문서 유형을 삭제했습니다");
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "문서 유형을 삭제하지 못했습니다");
    }
  }

  const totalDocuments = folders.reduce((sum, folder) => sum + folder.count, 0);
  const reviewCount = folders.reduce((sum, folder) => sum + folder.needs_review, 0);
  const confirmedCount = folders.reduce((sum, folder) => sum + folder.confirmed, 0);
  const processingCount = folders.reduce((sum, folder) => sum + folder.processing, 0);
  const activeFolders = folders
    .filter((folder) => folder.count > 0)
    .sort((a, b) => b.count - a.count || b.needs_review - a.needs_review || a.label.localeCompare(b.label));
  const emptyCustomFolders = folders.filter((folder) => folder.custom && folder.count === 0);
  const folderTree = buildFolderTree(folders);
  const folderRows = flattenFolderTree(folderTree);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">문서 유형 분류</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">제조업 문서 유형별 분류</h1>
          <p className="mt-2 text-muted-foreground">
            발주서, 견적서, 거래명세서, 납품서 등 AI가 분류한 업무 문서 유형과 회사별 하위 폴더를 트리 구조로 확인합니다.
          </p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{activeFolders.length}</span>개 유형
          <span className="mx-2 text-slate-300">/</span>
          <span className="font-semibold text-foreground">{totalDocuments}</span>건 문서
        </div>
      </div>

      <section className="mb-6 grid gap-3 md:grid-cols-4">
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">문서</p>
              <p className="mt-1 text-2xl font-semibold">{totalDocuments}</p>
            </div>
            <FolderKanban className="size-6 text-primary" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">검토 필요</p>
              <p className="mt-1 text-2xl font-semibold">{reviewCount}</p>
            </div>
            <BellRing className="size-6 text-amber-600" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">확정 완료</p>
              <p className="mt-1 text-2xl font-semibold">{confirmedCount}</p>
            </div>
            <CheckCircle2 className="size-6 text-emerald-600" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-sm text-muted-foreground">처리 중</p>
              <p className="mt-1 text-2xl font-semibold">{processingCount}</p>
            </div>
            <LoaderCircle className="size-6 text-primary" />
          </CardContent>
        </Card>
      </section>

      <Card className="mb-6 border-dashed">
        <CardContent className="grid gap-4 p-5 lg:grid-cols-[1.2fr_1fr_auto]">
          <div>
            <p className="text-sm font-medium">문서 유형 만들기</p>
            <p className="mt-1 text-sm text-muted-foreground">거래처명 같은 상위 폴더를 만들고 그 아래에 발주서, 납품서, 세금계산서 같은 하위 폴더를 둘 수 있습니다.</p>
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

      {folderTree.length ? (
        <section>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">폴더 구조</h2>
              <p className="text-sm text-muted-foreground">맥북 폴더처럼 열어보거나, 목록으로 한 번에 비교할 수 있습니다</p>
            </div>
            <FolderViewToggle value={viewMode} onChange={changeViewMode} />
          </div>
          {viewMode === "folders" ? (
            <div className="grid gap-4">
              {folderTree.map((folder) => (
                <div key={folder.value} className="rounded-xl border bg-slate-50/70 p-3">
                  <FolderCard
                    folder={folder}
                    href={`/categories/${encodeURIComponent(folder.value)}`}
                    onDelete={folder.custom && folder.count === 0 ? () => deleteFolder(folder) : undefined}
                  />
                  {folder.children.length ? (
                    <div className="mt-3 grid gap-3 border-l-2 border-slate-200 pl-4 md:grid-cols-2 xl:grid-cols-3">
                      {folder.children.map((child) => (
                        <FolderCard
                          key={child.value}
                          folder={child}
                          href={`/categories/${encodeURIComponent(child.value)}`}
                          onDelete={child.custom && child.count === 0 ? () => deleteFolder(child) : undefined}
                        />
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <FolderListView rows={folderRows} onDelete={deleteFolder} />
          )}
        </section>
      ) : null}

      {activeFolders.length && !folderTree.length ? (
        <section>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">사용 중인 문서 유형</h2>
              <p className="text-sm text-muted-foreground">문서 수와 검토 필요 상태 기준으로 정렬됩니다</p>
            </div>
            <FolderViewToggle value={viewMode} onChange={changeViewMode} />
          </div>
          {viewMode === "folders" ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {activeFolders.map((folder) => (
                <FolderCard
                  key={folder.value}
                  folder={folder}
                  href={`/categories/${encodeURIComponent(folder.value)}`}
                  onDelete={folder.custom && folder.count === 0 ? () => deleteFolder(folder) : undefined}
                />
              ))}
            </div>
          ) : (
            <FolderListView rows={activeFolders.map((folder) => ({ ...folder, level: 0, parentLabel: null }))} onDelete={deleteFolder} />
          )}
        </section>
      ) : null}

      {emptyCustomFolders.length ? (
        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">빈 사용자 문서 유형</h2>
            <p className="text-sm text-muted-foreground">사용 전에는 안전하게 삭제할 수 있습니다</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {emptyCustomFolders.map((folder) => (
            <FolderCard
              key={folder.value}
              folder={folder}
              href={`/categories/${encodeURIComponent(folder.value)}`}
              onDelete={folder.custom && folder.count === 0 ? () => deleteFolder(folder) : undefined}
            />
            ))}
          </div>
        </section>
      ) : null}

      {!folders.length ? (
        <Card><CardContent className="p-10 text-center text-muted-foreground">문서가 분석되면 문서 유형이 자동으로 표시됩니다.</CardContent></Card>
      ) : null}
    </main>
  );
}

function FolderViewToggle({ value, onChange }: { value: FolderViewMode; onChange: (mode: FolderViewMode) => void }) {
  return (
    <div className="inline-flex rounded-lg border bg-white p-1">
      <Button
        type="button"
        variant={value === "folders" ? "default" : "ghost"}
        size="sm"
        onClick={() => onChange("folders")}
        className="gap-1.5"
      >
        <LayoutGrid className="size-4" />
        폴더형
      </Button>
      <Button
        type="button"
        variant={value === "list" ? "default" : "ghost"}
        size="sm"
        onClick={() => onChange("list")}
        className="gap-1.5"
      >
        <ListTree className="size-4" />
        목록형
      </Button>
    </div>
  );
}

function FolderListView({ rows, onDelete }: { rows: FolderTreeRow[]; onDelete: (folder: FolderSummary) => void }) {
  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b bg-slate-50 text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3 text-left font-medium">폴더</th>
                <th className="px-4 py-3 text-left font-medium">상위 폴더</th>
                <th className="px-4 py-3 text-right font-medium">문서</th>
                <th className="px-4 py-3 text-right font-medium">검토 필요</th>
                <th className="px-4 py-3 text-right font-medium">확정 완료</th>
                <th className="px-4 py-3 text-right font-medium">처리 중</th>
                <th className="px-4 py-3 text-right font-medium">작업</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((folder) => (
                <tr key={folder.value} className="bg-white hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/categories/${encodeURIComponent(folder.value)}`}
                      className="flex min-w-0 items-center gap-2 font-medium text-slate-900"
                      style={{ paddingLeft: `${folder.level * 1.25}rem` }}
                    >
                      {folder.level ? <ChevronRight className="size-4 shrink-0 text-slate-400" /> : null}
                      <span className="grid size-8 shrink-0 place-items-center rounded-md bg-amber-50 text-amber-700">
                        <Folder className="size-4 fill-amber-100" />
                      </span>
                      <span className="truncate">{folder.label}</span>
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{folder.parentLabel || "-"}</td>
                  <td className="px-4 py-3 text-right font-semibold">{folder.count}</td>
                  <td className="px-4 py-3 text-right text-amber-700">{folder.needs_review}</td>
                  <td className="px-4 py-3 text-right text-emerald-700">{folder.confirmed}</td>
                  <td className="px-4 py-3 text-right text-primary">{folder.processing}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button asChild variant="outline" size="sm">
                        <Link href={`/categories/${encodeURIComponent(folder.value)}`}>열기</Link>
                      </Button>
                      {folder.custom && folder.count === 0 ? (
                        <Button type="button" variant="outline" size="sm" onClick={() => onDelete(folder)}>
                          <Trash2 className="size-4" />
                          삭제
                        </Button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function buildFolderTree(folders: FolderSummary[]): FolderTreeNode[] {
  const byValue = new Map(folders.map((folder) => [folder.value, folder]));
  const roots: FolderTreeNode[] = [];
  for (const folder of folders) {
    if (folder.parent && byValue.has(folder.parent)) continue;
    const children = folders
      .filter((candidate) => candidate.parent === folder.value || candidate.value.startsWith(`${folder.value}>`))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    roots.push({ ...folder, children });
  }
  return roots.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function flattenFolderTree(nodes: FolderTreeNode[]): FolderTreeRow[] {
  const rows: FolderTreeRow[] = [];
  for (const node of nodes) {
    rows.push({ ...node, level: 0, parentLabel: null });
    for (const child of node.children) {
      rows.push({ ...child, level: 1, parentLabel: node.label });
    }
  }
  return rows;
}
