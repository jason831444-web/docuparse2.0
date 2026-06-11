"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ShieldCheck, TriangleAlert } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { blockingReviewIssues, businessFieldDate, businessIssueDate, documentDisplayTitle, formatMoney, profileLabelForDocument, reviewIssueSummary } from "@/lib/utils";
import type { DocumentListResponse, DocumentRecord, ManufacturingLineItem } from "@/types/document";

export default function ReviewPage() {
  const [data, setData] = useState<DocumentListResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    api.review().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rows = useMemo(() => {
    return (data?.items || []).flatMap((document) => {
      const issues = blockingReviewIssues(document);
      const items = document.line_items?.length ? document.line_items : [{} as ManufacturingLineItem];
      return items.map((item, index) => ({ document, item, index, issues: issues.filter((issue) => issue.item_index === undefined || issue.item_index === index) }));
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
    const params = new URLSearchParams({ sheet_mode: "combined" });
    selectedIds.forEach((id) => params.append("document_ids", id));
    window.location.href = api.exportExcelUrl(params);
  }

  return (
    <main className="shell py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-3xl">
          <p className="text-sm font-medium uppercase tracking-normal text-muted-foreground">사람 검토 단계</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">검토 필요</h1>
          <p className="mt-2 text-muted-foreground">여러 문서의 품목 매칭, 수량, 단가, 금액 불일치를 한 화면에서 훑고 ERP/엑셀 내보내기 전에 정리합니다.</p>
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
                  <p className="mt-1 text-lg font-semibold">ERP 입력 준비</p>
                </div>
                <CheckCircle2 className="size-6 text-emerald-600" />
              </CardContent>
            </Card>
          </div>
          <ReviewTable rows={rows} selected={selected} onToggle={toggle} />
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white p-4">
            <p className="text-sm text-muted-foreground">{selected.size}개 문서 선택됨</p>
            <div className="flex gap-2">
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
  rows: Array<{ document: DocumentRecord; item: ManufacturingLineItem; index: number; issues: ReturnType<typeof blockingReviewIssues> }>;
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
            {rows.map(({ document, item, index, issues }) => {
              const confidence = Number(item.item_master_match_confidence ?? 0);
              const hasAmountIssue = issues.some((issue) => issue.code.includes("amount") || issue.code.includes("invalid_line"));
              const hasConflict = issues.some((issue) => issue.code === "item_code_name_conflict");
              return (
                <tr key={`${document.id}-${index}`} className={hasConflict ? "bg-red-50/60" : hasAmountIssue ? "bg-amber-50/60" : ""}>
                  <td className="px-3 py-3 align-top">
                    <input aria-label={`${documentDisplayTitle(document)} 선택`} type="checkbox" className="size-4" checked={selected.has(document.id)} onChange={(event) => onToggle(document.id, event.target.checked)} />
                  </td>
                  <td className="max-w-56 px-3 py-3 align-top">
                    <Link href={`/documents/${document.id}?from=/review`} className="font-medium text-primary underline-offset-4 hover:underline">{documentDisplayTitle(document)}</Link>
                    <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{document.original_filename}</p>
                    <div className="mt-2"><StatusBadge status={document.processing_status} /></div>
                  </td>
                  <td className="px-3 py-3 align-top">{profileLabelForDocument(document)}</td>
                  <td className="px-3 py-3 align-top">{document.vendor_name || "공급업체 미확인"}<br /><span className="text-muted-foreground">{document.customer_name || "고객사 미확인"}</span></td>
                  <td className="px-3 py-3 align-top">{businessIssueDate(document) || "-"}<br /><span className="text-muted-foreground">{businessFieldDate(document) || ""}</span></td>
                  <td className="max-w-56 px-3 py-3 align-top">{item.item_name || "-"}</td>
                  <td className="px-3 py-3 align-top">{item.document_item_code || item.item_code || item.source_item_code || "-"}</td>
                  <td className="px-3 py-3 align-top">{item.internal_item_code || "-"}</td>
                  <td className={`px-3 py-3 align-top ${confidence && confidence < 0.7 ? "text-amber-700" : ""}`}>{item.item_master_match_status || "-"}{confidence ? ` · ${Math.round(confidence * 100)}%` : ""}</td>
                  <td className="px-3 py-3 align-top">{item.quantity ?? "-"}</td>
                  <td className="px-3 py-3 align-top">{item.unit_price ?? "-"}</td>
                  <td className="px-3 py-3 align-top">{item.supply_amount ?? "-"}</td>
                  <td className="px-3 py-3 align-top">{item.tax_amount ?? "-"}</td>
                  <td className="px-3 py-3 align-top">{item.line_total ?? "-"}</td>
                  <td className="px-3 py-3 align-top">{formatMoney(document.extracted_amount, document.currency || "KRW")}</td>
                  <td className="max-w-72 px-3 py-3 align-top text-xs">
                    {issues.length ? issues.map((issue) => <p key={`${issue.code}-${issue.field}-${issue.item_index}`} className={hasConflict || hasAmountIssue ? "font-medium text-red-700" : "text-amber-700"}>{reviewIssueSummary(issue)}</p>) : <span className="text-muted-foreground">-</span>}
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
