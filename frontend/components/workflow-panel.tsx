import { Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { businessFieldDate, businessIssueDate, documentFieldLabels, formatDate, formatMoney, normalizedReviewIssues, primaryCategoryLabel } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

function ListBlock({ title, items, warning = false }: { title: string; items: string[]; warning?: boolean }) {
  if (!items.length) return null;
  return (
    <div className={warning ? "rounded-md border border-amber-300 bg-amber-50 p-3" : "rounded-md border bg-white p-3"}>
      <p className="mb-2 text-xs font-medium uppercase tracking-normal text-muted-foreground">{title}</p>
      <ul className="space-y-1 text-sm">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function ValueGrid({ values }: { values: Array<[string, string | null | undefined]> }) {
  const present = values.filter(([, value]) => value);
  if (!present.length) return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {present.map(([label, value]) => (
        <div key={label} className="rounded-md border bg-white p-3">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="mt-1 font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}

export function WorkflowPanel({ document }: { document: DocumentRecord }) {
  const labels = documentFieldLabels(document.document_type);
  const reviewItems = normalizedReviewIssues(document).map((issue) => issue.message_ko);
  const roleDate = businessFieldDate(document);
  const issueDate = businessIssueDate(document);
  const exportReady = !document.review_required && document.processing_status === "confirmed";

  return (
    <Card className={document.review_required ? "border-amber-300 bg-amber-50/40" : ""}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="size-5 text-primary" />
          ERP/엑셀 입력 준비 상태
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Badge>{primaryCategoryLabel(document)}</Badge>
          <Badge className={document.review_required ? "border-amber-300 bg-amber-50 text-amber-800" : "border-emerald-300 bg-emerald-50 text-emerald-800"}>
            {document.review_required ? "일부 항목은 확인이 필요합니다" : "자동 추출 완료"}
          </Badge>
          {exportReady ? <Badge className="border-emerald-300 bg-emerald-50 text-emerald-800">내보내기 준비 완료</Badge> : null}
        </div>
        <ValueGrid
          values={[
            ["공급업체", document.vendor_name || document.merchant_name],
            ["고객사", document.customer_name],
            [labels.documentNumber, document.document_number],
            [labels.issueDate, issueDate ? formatDate(issueDate) : null],
            [labels.dueDate, roleDate ? formatDate(roleDate) : null],
            ["품목 수", `${document.line_items.length}건`],
            ["합계금액", document.extracted_amount ? formatMoney(document.extracted_amount, document.currency || "KRW") : null],
          ]}
        />
        {document.workflow_summary ? <p className="rounded-md border bg-white p-3 text-sm">{document.workflow_summary}</p> : null}
        <ListBlock title="검토 필요 항목" items={reviewItems} warning />
        <ListBlock title="처리 경고" items={document.warnings} warning />
        <ListBlock title="주요 날짜" items={document.key_dates} />
      </CardContent>
    </Card>
  );
}
