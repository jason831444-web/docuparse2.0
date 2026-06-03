"use client";

import { useMemo, useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DocumentCard } from "@/components/document-card";
import { DocumentRow } from "@/components/document-row";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { DocumentRecord } from "@/types/document";

export function DocumentList({
  documents,
  view = "list",
  onChanged,
  returnTo,
}: {
  documents: DocumentRecord[];
  view?: "list" | "grid";
  onChanged?: () => void;
  returnTo?: string;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const selectedIds = useMemo(() => Array.from(selected), [selected]);
  const allSelected = documents.length > 0 && selected.size === documents.length;

  function toggle(id: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAll(checked: boolean) {
    setSelected(checked ? new Set(documents.map((document) => document.id)) : new Set());
  }

  async function downloadSelected() {
    try {
      await api.bulkDownload(selectedIds);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "다운로드에 실패했습니다");
    }
  }

  async function deleteSelected() {
    if (!selectedIds.length) return;
    if (!window.confirm(`선택한 문서 ${selectedIds.length}건을 삭제할까요?`)) return;
    try {
      const result = await api.bulkDelete(selectedIds);
      toast.success(`문서 ${result.deleted}건을 삭제했습니다`);
      setSelected(new Set());
      onChanged?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "삭제에 실패했습니다");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white px-4 py-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4" checked={allSelected} onChange={(event) => toggleAll(event.target.checked)} />
          전체 선택
        </label>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{selected.size}건 선택됨</span>
          <Button type="button" variant="outline" size="sm" disabled={!selected.size} onClick={downloadSelected}>
            <Download className="size-4" />
            원본 다운로드
          </Button>
          <Button type="button" variant="destructive" size="sm" disabled={!selected.size} onClick={deleteSelected}>
            <Trash2 className="size-4" />
            삭제
          </Button>
        </div>
      </div>

      {view === "grid" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {documents.map((document) => (
            <DocumentCard key={document.id} document={document} selected={selected.has(document.id)} onSelect={(checked) => toggle(document.id, checked)} returnTo={returnTo} />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((document) => (
            <DocumentRow key={document.id} document={document} selected={selected.has(document.id)} onSelect={(checked) => toggle(document.id, checked)} returnTo={returnTo} />
          ))}
        </div>
      )}
    </div>
  );
}
