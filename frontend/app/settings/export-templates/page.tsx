"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowLeft, ArrowUp, Copy, Plus, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { ExportTemplateColumn, ExportTemplateRecord, ExportTemplateSourceField } from "@/types/document";

const blankColumn: ExportTemplateColumn = { header: "새 컬럼", source_field: "__blank__", column_type: "blank", static_value: "" };

export default function ExportTemplatesPage() {
  const [templates, setTemplates] = useState<ExportTemplateRecord[]>([]);
  const [sourceFields, setSourceFields] = useState<ExportTemplateSourceField[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ExportTemplateRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (selectId?: string) => {
    setLoading(true);
    try {
      const items = await api.exportTemplates.list();
      setTemplates(items);
      const nextId = selectId || items.find((item) => item.is_default)?.id || items[0]?.id || null;
      setSelectedId(nextId);
      setDraft(nextId ? cloneTemplate(items.find((item) => item.id === nextId) || null) : null);
    } catch {
      setTemplates([]);
      setDraft(null);
      toast.error("출력 템플릿을 불러오지 못했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    api.exportTemplates.sourceFields().then(setSourceFields).catch(() => setSourceFields([]));
  }, [load]);

  function selectTemplate(template: ExportTemplateRecord) {
    setSelectedId(template.id);
    setDraft(cloneTemplate(template));
  }

  function newTemplate() {
    setSelectedId(null);
    setDraft({
      id: "new",
      name: "새 출력 템플릿",
      description: "",
      scope: "global",
      is_default: false,
      columns: [
        { header: "거래일자", source_field: "document_date", column_type: "field" },
        { header: "거래처", source_field: "customer_name", column_type: "field" },
        { header: "품목명", source_field: "line_items.item_name", column_type: "field" },
        { header: "수량", source_field: "line_items.quantity", column_type: "field" },
        { header: "비고", source_field: "line_items.note", column_type: "field" },
      ],
      created_at: "",
      updated_at: "",
    });
  }

  function copyTemplate() {
    if (!draft) return;
    const cloned = cloneTemplate(draft);
    if (!cloned) return;
    setSelectedId(null);
    setDraft({ ...cloned, id: "new", name: `${draft.name} 복사본`, is_default: false });
  }

  async function saveTemplate() {
    if (!draft) return;
    if (!draft.name.trim()) {
      toast.error("템플릿 이름을 입력하세요");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: draft.name.trim(),
        description: draft.description || null,
        scope: "global",
        is_default: draft.is_default,
        columns: draft.columns.map(normalizeColumn),
      };
      const saved = selectedId ? await api.exportTemplates.update(selectedId, payload) : await api.exportTemplates.create(payload);
      toast.success("출력 템플릿을 저장했습니다");
      await load(saved.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "템플릿 저장에 실패했습니다");
    } finally {
      setSaving(false);
    }
  }

  async function deleteTemplate() {
    if (!selectedId || !draft) return;
    if (!window.confirm(`'${draft.name}' 템플릿을 삭제할까요?`)) return;
    try {
      await api.exportTemplates.remove(selectedId);
      toast.success("출력 템플릿을 삭제했습니다");
      setSelectedId(null);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "템플릿 삭제에 실패했습니다");
    }
  }

  const groupedSourceFields = useMemo(() => {
    const groups = new Map<string, ExportTemplateSourceField[]>();
    sourceFields.forEach((field) => groups.set(field.group, [...(groups.get(field.group) || []), field]));
    return Array.from(groups.entries());
  }, [sourceFields]);

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2">
            <Link href="/settings"><ArrowLeft className="size-4" /> 설정으로 돌아가기</Link>
          </Button>
          <h1 className="text-3xl font-semibold tracking-normal">Export Templates</h1>
          <p className="mt-2 text-muted-foreground">엑셀/CSV 출력 컬럼, 순서, 표시 이름을 업무 양식에 맞게 저장합니다.</p>
        </div>
        <Button type="button" onClick={newTemplate}><Plus className="size-4" /> 새 템플릿</Button>
      </div>

      <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>템플릿 목록</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {loading ? <p className="text-sm text-muted-foreground">불러오는 중...</p> : null}
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => selectTemplate(template)}
                className={`w-full rounded-lg border px-3 py-3 text-left text-sm transition ${selectedId === template.id ? "border-primary bg-primary/5" : "bg-white hover:border-primary/40"}`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{template.name}</span>
                  {template.is_default ? <Badge variant="outline">기본</Badge> : null}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">{template.columns.length}개 컬럼</span>
              </button>
            ))}
          </CardContent>
        </Card>

        {draft ? (
          <div className="space-y-6">
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle>템플릿 편집</CardTitle>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="outline" onClick={copyTemplate}><Copy className="size-4" /> 복사</Button>
                  <Button type="button" variant="outline" disabled={!selectedId} onClick={deleteTemplate}><Trash2 className="size-4" /> 삭제</Button>
                  <Button type="button" disabled={saving} onClick={saveTemplate}><Save className="size-4" /> 저장</Button>
                </div>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-4 lg:grid-cols-[1fr_1.5fr_auto]">
                  <label className="grid gap-2 text-sm font-medium">
                    템플릿 이름
                    <Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
                  </label>
                  <label className="grid gap-2 text-sm font-medium">
                    설명
                    <Textarea className="min-h-10" value={draft.description || ""} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
                  </label>
                  <label className="flex items-center gap-2 self-end rounded-lg border bg-white px-4 py-3 text-sm font-medium">
                    <input type="checkbox" className="size-4" checked={draft.is_default} onChange={(event) => setDraft({ ...draft, is_default: event.target.checked })} />
                    기본 템플릿
                  </label>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle>컬럼 구성</CardTitle>
                <Button type="button" variant="outline" size="sm" onClick={() => setDraft({ ...draft, columns: [...draft.columns, blankColumn] })}>
                  <Plus className="size-4" /> 컬럼 추가
                </Button>
              </CardHeader>
              <CardContent className="space-y-3">
                {draft.columns.map((column, index) => (
                  <div key={`${index}-${column.header}`} className="grid gap-3 rounded-lg border bg-white p-3 lg:grid-cols-[44px_1fr_1.4fr_1fr_auto] lg:items-end">
                    <div className="text-sm font-semibold text-muted-foreground">#{index + 1}</div>
                    <label className="grid gap-2 text-xs font-medium">
                      표시 이름
                      <Input value={column.header} onChange={(event) => updateColumn(draft, setDraft, index, { header: event.target.value })} />
                    </label>
                    <label className="grid gap-2 text-xs font-medium">
                      source field
                      <select
                        className="h-10 rounded-md border bg-white px-3 text-sm"
                        value={column.source_field}
                        onChange={(event) => updateColumn(draft, setDraft, index, sourceFieldUpdate(event.target.value))}
                      >
                        {groupedSourceFields.map(([group, fields]) => (
                          <optgroup key={group} label={group}>
                            {fields.map((field) => <option key={field.value} value={field.value}>{field.label}</option>)}
                          </optgroup>
                        ))}
                      </select>
                    </label>
                    <label className="grid gap-2 text-xs font-medium">
                      고정값
                      <Input
                        value={column.static_value || ""}
                        disabled={column.column_type !== "static"}
                        onChange={(event) => updateColumn(draft, setDraft, index, { static_value: event.target.value })}
                        placeholder={column.column_type === "static" ? "예: 창고 A" : "고정값 컬럼일 때 사용"}
                      />
                    </label>
                    <div className="flex gap-1">
                      <Button type="button" variant="outline" size="sm" disabled={index === 0} onClick={() => moveColumn(draft, setDraft, index, -1)} aria-label="위로 이동"><ArrowUp className="size-4" /></Button>
                      <Button type="button" variant="outline" size="sm" disabled={index === draft.columns.length - 1} onClick={() => moveColumn(draft, setDraft, index, 1)} aria-label="아래로 이동"><ArrowDown className="size-4" /></Button>
                      <Button type="button" variant="outline" size="sm" onClick={() => removeColumn(draft, setDraft, index)} aria-label="삭제"><Trash2 className="size-4" /></Button>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>미리보기</CardTitle>
              </CardHeader>
              <CardContent className="overflow-auto">
                <table className="min-w-full border text-sm">
                  <thead>
                    <tr>
                      {draft.columns.map((column, index) => <th key={`${column.header}-${index}`} className="border bg-slate-50 px-3 py-2 text-left">{column.header || "빈 컬럼"}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      {draft.columns.map((column, index) => <td key={`${column.source_field}-${index}`} className="border px-3 py-2 text-muted-foreground">{previewValue(column)}</td>)}
                    </tr>
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </div>
        ) : (
          <Card><CardContent className="p-10 text-center text-muted-foreground">템플릿을 선택하거나 새 템플릿을 만드세요.</CardContent></Card>
        )}
      </div>
    </main>
  );
}

function cloneTemplate(template: ExportTemplateRecord | null): ExportTemplateRecord | null {
  if (!template) return null;
  return { ...template, columns: template.columns.map((column) => ({ ...column })) };
}

function normalizeColumn(column: ExportTemplateColumn): ExportTemplateColumn {
  const update = sourceFieldUpdate(column.source_field);
  return { ...column, ...update, header: column.header.trim() || "빈 컬럼" };
}

function sourceFieldUpdate(value: string): Partial<ExportTemplateColumn> {
  if (value === "__blank__") return { source_field: value, column_type: "blank", static_value: "" };
  if (value === "__static__") return { source_field: value, column_type: "static" };
  return { source_field: value, column_type: "field", static_value: "" };
}

function updateColumn(
  draft: ExportTemplateRecord,
  setDraft: (value: ExportTemplateRecord) => void,
  index: number,
  patch: Partial<ExportTemplateColumn>
) {
  const columns = draft.columns.map((column, columnIndex) => columnIndex === index ? { ...column, ...patch } : column);
  setDraft({ ...draft, columns });
}

function moveColumn(draft: ExportTemplateRecord, setDraft: (value: ExportTemplateRecord) => void, index: number, delta: number) {
  const target = index + delta;
  if (target < 0 || target >= draft.columns.length) return;
  const columns = [...draft.columns];
  const [item] = columns.splice(index, 1);
  columns.splice(target, 0, item);
  setDraft({ ...draft, columns });
}

function removeColumn(draft: ExportTemplateRecord, setDraft: (value: ExportTemplateRecord) => void, index: number) {
  setDraft({ ...draft, columns: draft.columns.filter((_, columnIndex) => columnIndex !== index) });
}

function previewValue(column: ExportTemplateColumn): string {
  if (column.column_type === "blank") return "";
  if (column.column_type === "static") return column.static_value || "고정값";
  if (column.source_field.startsWith("line_items.")) return `품목.${column.source_field.split(".")[1]}`;
  return column.source_field;
}
