"use client";

import { useEffect, useState } from "react";
import { BellRing, CheckCircle2, FolderKanban, LoaderCircle, Plus } from "lucide-react";
import { toast } from "sonner";

import { FolderCard } from "@/components/folder-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { FolderSummary } from "@/types/document";

export default function CategoriesPage() {
  const [folders, setFolders] = useState<FolderSummary[]>([]);
  const [label, setLabel] = useState("");
  const [parent, setParent] = useState("");

  useEffect(() => {
    load();
  }, []);

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

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">문서 유형 분류</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">제조업 문서 유형별 분류</h1>
          <p className="mt-2 text-muted-foreground">
            발주서, 견적서, 거래명세서, 납품서 등 AI가 분류한 업무 문서 유형과 검토 상태를 확인합니다.
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
            <p className="mt-1 text-sm text-muted-foreground">업무에 필요한 분류를 미리 만들고, 사용하지 않으면 나중에 삭제할 수 있습니다.</p>
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

      {activeFolders.length ? (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">사용 중인 문서 유형</h2>
            <p className="text-sm text-muted-foreground">문서 수와 검토 필요 상태 기준으로 정렬됩니다</p>
          </div>
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
