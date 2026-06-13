"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DocumentCard } from "@/components/document-card";
import { DocumentRow } from "@/components/document-row";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { requiresReviewExportConfirmation } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

type DuplicateHint = {
  count: number;
  isLatest: boolean;
  basis: "filename_and_number" | "filename";
};

export function DocumentList({
  documents,
  view = "list",
  onChanged,
  returnTo,
  duplicateHintsOverride,
  selected: controlledSelected,
  onSelectedChange,
  selectionScopeDocuments,
}: {
  documents: DocumentRecord[];
  view?: "list" | "grid";
  onChanged?: () => void;
  returnTo?: string;
  duplicateHintsOverride?: Map<string, DuplicateHint>;
  selected?: Set<string>;
  onSelectedChange?: (selected: Set<string>) => void;
  selectionScopeDocuments?: DocumentRecord[];
}) {
  const [uncontrolledSelected, setUncontrolledSelected] = useState<Set<string>>(new Set());
  const selected = controlledSelected ?? uncontrolledSelected;
  const setSelected = onSelectedChange ?? setUncontrolledSelected;
  const [excelMode, setExcelMode] = useState<"combined" | "party_tabs">("combined");
  const selectedIds = useMemo(() => Array.from(selected), [selected]);
  const allSelected = documents.length > 0 && documents.every((document) => selected.has(document.id));
  const scopedDocuments = selectionScopeDocuments ?? documents;
  const localDuplicateHints = useMemo(() => duplicateUploadHints(documents), [documents]);
  const duplicateHints = duplicateHintsOverride ?? localDuplicateHints;
  const duplicateCount = documents.filter((document) => duplicateHints.has(document.id)).length;

  useEffect(() => {
    if (controlledSelected) return;
    const visibleIds = new Set(documents.map((document) => document.id));
    setUncontrolledSelected((current) => new Set(Array.from(current).filter((id) => visibleIds.has(id))));
  }, [controlledSelected, documents]);

  function updateSelected(updater: (current: Set<string>) => Set<string>) {
    setSelected(updater(selected));
  }

  function toggle(id: string, checked: boolean) {
    updateSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAll(checked: boolean) {
    updateSelected((current) => {
      const next = new Set(current);
      documents.forEach((document) => {
        if (checked) next.add(document.id);
        else next.delete(document.id);
      });
      return next;
    });
  }

  async function downloadSelected() {
    try {
      await api.bulkDownload(selectedIds);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "다운로드에 실패했습니다");
    }
  }

  function selectedExportParams(extra?: Record<string, string>) {
    const params = new URLSearchParams();
    selectedIds.forEach((id) => params.append("document_ids", id));
    Object.entries(extra || {}).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    return params;
  }

  function exportSelected(kind: "csv" | "xlsx") {
    if (!selectedIds.length) {
      toast.error("선택된 문서가 없습니다. 내보낼 문서를 먼저 선택하세요.");
      return;
    }
    const selectedDocuments = scopedDocuments.filter((document) => selected.has(document.id));
    if (selectedDocuments.some(requiresReviewExportConfirmation)) {
      const confirmed = window.confirm("선택한 문서 중 검토 필요 문서가 있습니다. 내보내기 파일에는 review_required와 경고 정보가 포함됩니다. 계속할까요?");
      if (!confirmed) return;
    }
    const url = kind === "csv"
      ? api.exportCsvUrl(selectedExportParams())
      : api.exportExcelUrl(selectedExportParams({ sheet_mode: excelMode }));
    window.location.href = url;
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
          이 그룹 선택
        </label>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{selected.size}건 선택됨</span>
          {duplicateCount ? <span className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800">같은 파일 업로드 후보 {duplicateCount}건</span> : null}
          <select
            className="h-8 rounded-md border bg-white px-2 text-xs"
            value={excelMode}
            onChange={(event) => setExcelMode(event.target.value as "combined" | "party_tabs")}
            aria-label="Excel 내보내기 방식"
          >
            <option value="combined">통합 시트형</option>
            <option value="party_tabs">거래처별 탭</option>
          </select>
          <Button type="button" variant="outline" size="sm" disabled={!selected.size} onClick={() => exportSelected("xlsx")}>
            <Download className="size-4" />
            선택 Excel
          </Button>
          <Button type="button" variant="outline" size="sm" disabled={!selected.size} onClick={() => exportSelected("csv")}>
            <Download className="size-4" />
            선택 CSV
          </Button>
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
            <DocumentCard key={document.id} document={document} duplicateHint={duplicateHints.get(document.id)} selected={selected.has(document.id)} onSelect={(checked) => toggle(document.id, checked)} returnTo={returnTo} />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((document) => (
            <DocumentRow key={document.id} document={document} duplicateHint={duplicateHints.get(document.id)} selected={selected.has(document.id)} onSelect={(checked) => toggle(document.id, checked)} returnTo={returnTo} />
          ))}
        </div>
      )}
    </div>
  );
}

export function duplicateUploadHints(documents: DocumentRecord[]): Map<string, DuplicateHint> {
  const exactGroups = new Map<string, DocumentRecord[]>();
  const filenameGroups = new Map<string, DocumentRecord[]>();
  for (const document of documents) {
    const exactKey = duplicateExactKey(document);
    if (exactKey) {
      const group = exactGroups.get(exactKey) || [];
      group.push(document);
      exactGroups.set(exactKey, group);
    }
    const filenameKey = duplicateFilenameKey(document);
    if (filenameKey) {
      const group = filenameGroups.get(filenameKey) || [];
      group.push(document);
      filenameGroups.set(filenameKey, group);
    }
  }
  const hints = new Map<string, DuplicateHint>();
  for (const group of exactGroups.values()) {
    addDuplicateGroupHints(hints, group, "filename_and_number");
  }
  for (const group of filenameGroups.values()) {
    addDuplicateGroupHints(hints, group, "filename");
  }
  return hints;
}

function addDuplicateGroupHints(
  hints: Map<string, DuplicateHint>,
  group: DocumentRecord[],
  basis: DuplicateHint["basis"]
) {
    if (group.length < 2) return;
    const latestTime = Math.max(...group.map((document) => Date.parse(document.updated_at || document.created_at || "") || 0));
    group.forEach((document) => {
      if (hints.has(document.id) && hints.get(document.id)?.basis === "filename_and_number") return;
      const updatedTime = Date.parse(document.updated_at || document.created_at || "") || 0;
      hints.set(document.id, { count: group.length, isLatest: updatedTime >= latestTime, basis });
    });
}

function duplicateExactKey(document: DocumentRecord): string | null {
  const filename = normalizeDuplicateKey(document.original_filename);
  const number = (document.document_number || "").trim().toLowerCase();
  if (!filename || !number) return null;
  return `${filename}::${number}`;
}

function duplicateFilenameKey(document: DocumentRecord): string | null {
  const filename = normalizeDuplicateKey(document.original_filename);
  return filename || null;
}

function normalizeDuplicateKey(value?: string | null) {
  return (value || "").normalize("NFKC").replace(/\s+/g, "").trim().toLowerCase();
}
