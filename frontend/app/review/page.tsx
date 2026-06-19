"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ShieldCheck, TriangleAlert } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { TaxonomyBadges } from "@/components/taxonomy-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { blockingReviewIssues, businessFieldDate, businessIssueDate, documentDisplayTitle, formatMoney, profileLabelForDocument, requiresReviewExportConfirmation, reviewIssueDescription, reviewIssueSummary, reviewIssueSummaryItems } from "@/lib/utils";
import type { DocumentListResponse, DocumentRecord, ExportTemplateRecord, ManufacturingLineItem } from "@/types/document";

export default function ReviewPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exportTemplates, setExportTemplates] = useState<ExportTemplateRecord[]>([]);
  const [exportTemplateId, setExportTemplateId] = useState("");

  const load = useCallback(() => {
    api.review().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
    api.exportTemplates.list()
      .then((items) => {
        setExportTemplates(items);
        setExportTemplateId((current) => current || items.find((item) => item.is_default)?.id || "");
      })
      .catch(() => setExportTemplates([]));
  }, [load]);

  const rows = useMemo(() => {
    return (data?.items || []).map((document) => {
      const issues = blockingReviewIssues(document);
      return { document, issues };
    });
  }, [data]);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  function toggle(id: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function exportSelected() {
    if (!selectedIds.length) return;
    const selectedDocuments = (data?.items || []).filter((document) => selected.has(document.id));
    if (selectedDocuments.some(requiresReviewExportConfirmation) && !window.confirm("검토 필요 문서를 내보냅니다. 업무데이터 확정 전 review_required와 경고 정보를 확인해야 합니다. 계속할까요?")) return;
    const params = new URLSearchParams({ sheet_mode: "combined" });
    selectedIds.forEach((id) => params.append("document_ids", id));
    if (exportTemplateId) params.set("template_id", exportTemplateId);
    window.location.href = api.exportExcelUrl(params);
  }

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">사람 검토 단계</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">검토 필요</h1>
          <p className="mt-2 text-muted-foreground">여러 문서의 품목 매칭, 수량, 단가, 금액 불일치를 한 화면에서 훑고 업무데이터/엑셀 내보내기 전에 정리합니다.</p>
        </div>
        <div className="rounded-lg border bg-white px-4 py-3 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground">{data?.total ?? 0}</span>건 대기 중
        </div>
      </div>
      {data?.items.length ? (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">검토 대기</p>
                  <p className="mt-1 text-2xl font-semibold">{data.total}</p>
                </div>
                <TriangleAlert className="size-6 text-amber-600" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">작업</p>
                  <p className="mt-1 text-lg font-semibold">수정 또는 확정</p>
                </div>
                <ShieldCheck className="size-6 text-primary" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-sm text-muted-foreground">결과</p>
                  <p className="mt-1 text-lg font-semibold">업무데이터 입력 준비</p>
                </div>
                <CheckCircle2 className="size-6 text-emerald-600" />
              </CardContent>
            </Card>
          </div>
          <ReviewTable rows={rows} selected={selected} onToggle={toggle} />
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white p-4">
            <div className="text-sm text-muted-foreground">
              <p>{selected.size}개 문서 선택됨</p>
              <p className="mt-1">검토 필요 문서를 내보내면 업무데이터 확정 전 확인해야 할 review_required와 경고 정보가 함께 포함됩니다.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <select
                className="h-10 rounded-md border bg-white px-3 text-sm"
                value={exportTemplateId}
                onChange={(event) => setExportTemplateId(event.target.value)}
                aria-label="출력 템플릿"
              >
                <option value="">기본 출력</option>
                {exportTemplates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
              </select>
              <Button type="button" variant="outline" disabled={!selected.size} onClick={exportSelected}>선택 문서 Excel</Button>
              <Button asChild type="button"><Link href="/documents">문서 목록에서 더 보기</Link></Button>
            </div>
          </div>
        </div>
      ) : (
        <Card>
          <CardContent className="grid gap-4 p-10 text-center text-muted-foreground">
            <TriangleAlert className="mx-auto size-10 text-amber-600" />
            <div>
              <p className="font-medium text-foreground">현재 검토가 필요한 문서가 없습니다.</p>
              <p className="mt-1">발주서, 견적서, 거래명세서, 납품서를 업로드하면 검토 필요 항목이 여기에 표시됩니다.</p>
            </div>
          </CardContent>
        </Card>
      )}
    </main>
  );
}

function ReviewTable({
  rows,
  selected,
  onToggle,
}: {
  rows: Array<{ document: DocumentRecord; issues: ReturnType<typeof blockingReviewIssues> }>;
  selected: Set<string>;
  onToggle: (id: string, checked: boolean) => void;
}) {
  return (
    <Card>
      <CardContent className="overflow-x-auto p-0">
        <table className="min-w-[1320px] text-left text-sm">
          <thead className="border-b bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="w-10 px-3 py-3" />
              {["파일/문서", "유형", "거래처", "일자", "품목명", "문서 품목코드", "내부 품목코드", "매칭", "수량", "단가", "공급가액", "세액", "합계", "문서 총액", "검토 이유"].map((head) => (
                <th key={head} className="px-3 py-3">{head}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map(({ document, issues }) => {
              const items = document.line_items || [];
              const confidence = maxItemMasterConfidence(items);
              const hasAmountIssue = issues.some((issue) => issue.code.includes("amount") || issue.code.includes("invalid_line"));
              const hasConflict = issues.some((issue) => issue.code === "item_code_name_conflict");
              return (
                <tr key={document.id} className={hasConflict ? "bg-red-50/60" : hasAmountIssue ? "bg-amber-50/60" : ""}>
                  <td className="px-3 py-3 align-top">
                    <input aria-label={`${documentDisplayTitle(document)} 선택`} type="checkbox" className="size-4" checked={selected.has(document.id)} onChange={(event) => onToggle(document.id, event.target.checked)} />
                  </td>
                  <td className="max-w-56 px-3 py-3 align-top">
                    <Link href={`/documents/${document.id}?from=/review`} className="font-medium text-primary underline-offset-4 hover:underline">{documentDisplayTitle(document)}</Link>
                    <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{document.original_filename}</p>
                    <div className="mt-2"><StatusBadge status={document.processing_status} /></div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <div className="grid gap-1">
                      <span>{profileLabelForDocument(document)}</span>
                      <span className="flex flex-wrap gap-1"><TaxonomyBadges document={document} maxProfiles={1} /></span>
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">{document.vendor_name || "공급업체 미확인"}<br /><span className="text-muted-foreground">{document.customer_name || "고객사 미확인"}</span></td>
                  <td className="px-3 py-3 align-top">{businessIssueDate(document) || "-"}<br /><span className="text-muted-foreground">{businessFieldDate(document) || ""}</span></td>
                  <td className="max-w-64 px-3 py-3 align-top">
                    <p className="font-medium">{items.length ? `품목 ${items.length}개` : "품목 없음"}</p>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{summarizeLineItems(items, "item_name")}</p>
                  </td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "document_item_code", "item_code", "source_item_code")}</td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "internal_item_code")}</td>
                  <td className={`px-3 py-3 align-top ${confidence && confidence < 0.7 ? "text-amber-700" : ""}`}>
                    {summarizeLineItems(items, "item_master_match_status")}
                    {confidence ? ` · 최고 ${Math.round(confidence * 100)}%` : ""}
                  </td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "quantity")}</td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "unit_price")}</td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "supply_amount")}</td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "tax_amount")}</td>
                  <td className="px-3 py-3 align-top">{summarizeLineItems(items, "line_total")}</td>
                  <td className="px-3 py-3 align-top">{formatMoney(document.extracted_amount, document.currency || "KRW")}</td>
                  <td className="max-w-72 px-3 py-3 align-top text-xs">
                    {reviewIssueSummaryItems(issues).length ? (
                      <div className="mb-2 flex flex-wrap gap-1">
                        {reviewIssueSummaryItems(issues).map((item) => (
                          <span key={item} className="rounded bg-white px-1.5 py-0.5 text-[11px] text-slate-700">{item}</span>
                        ))}
                      </div>
                    ) : null}
                    {issues.length ? issues.map((issue) => (
                      <div key={`${issue.code}-${issue.field}-${issue.item_index ?? "document"}`} className={hasConflict || hasAmountIssue ? "text-red-700" : "text-amber-700"}>
                        <p className="font-medium">{reviewIssueSummary(issue)}</p>
                        <p className="mt-0.5 text-[11px]">{reviewIssueDescription(issue)}</p>
                      </div>
                    )) : <span className="text-muted-foreground">-</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function summarizeLineItems(items: ManufacturingLineItem[], ...fields: Array<keyof ManufacturingLineItem>): string {
  const values: string[] = [];
  for (const item of items) {
    const value = fields.map((field) => item[field]).find((candidate) => candidate !== undefined && candidate !== null && String(candidate).trim() !== "");
    if (value !== undefined && value !== null) {
      const text = String(value).trim();
      if (text && !values.includes(text)) values.push(text);
    }
  }
  if (!values.length) return "-";
  const shown = values.slice(0, 3).join(", ");
  return values.length > 3 ? `${shown} 외 ${values.length - 3}개` : shown;
}

function maxItemMasterConfidence(items: ManufacturingLineItem[]): number {
  return items.reduce((max, item) => {
    const value = Number(item.item_master_match_confidence ?? 0);
    return Number.isFinite(value) && value > max ? value : max;
  }, 0);
}
