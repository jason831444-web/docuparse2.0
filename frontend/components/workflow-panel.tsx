import { Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { bboxReviewFlagLabel, blockingReviewIssues, businessFieldDate, businessIssueDate, displayWarningsWithoutReviewDuplicates, documentFieldLabels, formatDate, formatMoney, informationalReviewIssues, layoutDebugMetadata, primaryCategoryLabel, reviewIssueSummaryItems, vlCandidateHandlingLabel, vlCandidateIssueLabel, vlCandidateMetadata } from "@/lib/utils";
import type { BBoxTableCandidate, DocumentRecord, LayoutDebugMetadata, VLCandidateMetadata } from "@/types/document";

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

function InfoDetails({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <details className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
      <summary className="cursor-pointer text-xs font-medium text-slate-600">참고 정보 {items.length}건</summary>
      <ul className="mt-2 space-y-1 text-xs">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </details>
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

function candidateValue(value: string | number | null | undefined) {
  return value === undefined || value === null || value === "" ? null : String(value);
}

function BBoxCandidateCard({ candidate }: { candidate: BBoxTableCandidate }) {
  const values = [
    ["수량", candidateValue(candidate.quantity)],
    ["단위", candidateValue(candidate.unit)],
    ["단가", candidateValue(candidate.unit_price)],
    ["공급가액", candidateValue(candidate.supply_amount)],
    ["세액", candidateValue(candidate.tax_amount)],
    ["합계", candidateValue(candidate.line_total)],
  ].filter(([, value]) => value);
  const flags = Array.from(new Set([...(candidate.review_flags || []), ...(candidate.missing_fields || []), ...(candidate.untrusted_fields || [])]));
  return (
    <div className="rounded-md border border-sky-200 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className="border-sky-300 bg-sky-50 text-sky-800">확정 품목 아님</Badge>
        {candidate.row_index !== undefined && candidate.row_index !== null ? <span className="text-xs text-muted-foreground">후보 행 {candidate.row_index}</span> : null}
        {candidate.confidence !== undefined && candidate.confidence !== null ? <span className="text-xs text-muted-foreground">신뢰도 {Math.round(Number(candidate.confidence) * 100)}%</span> : null}
      </div>
      <p className="mt-2 text-sm font-medium">{candidate.item_name || "품목명 OCR 없음"}</p>
      {candidate.source_text ? <p className="mt-1 text-xs text-muted-foreground">OCR 원문: {candidate.source_text}</p> : null}
      {values.length ? (
        <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
          {values.map(([label, value]) => (
            <div key={label} className="rounded border bg-slate-50 px-2 py-1">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {flags.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {flags.map((flag) => <Badge key={flag} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">{bboxReviewFlagLabel(flag)}</Badge>)}
        </div>
      ) : null}
    </div>
  );
}

function LayoutDebugBlock({ layoutDebug }: { layoutDebug: LayoutDebugMetadata | null }) {
  if (!layoutDebug) return null;
  const candidates = layoutDebug.bbox_table_candidates || [];
  const summary = layoutDebug.bbox_candidate_summary;
  const candidateCount = summary?.candidate_count ?? layoutDebug.candidate_count ?? candidates.length;
  const uncertainCount = summary?.uncertain_count ?? layoutDebug.uncertain_count ?? candidates.filter((candidate) => (candidate.review_flags || []).length > 0 || !candidate.item_name).length;
  const reviewFlags = Array.from(new Set([...(summary?.review_flags || []), ...(layoutDebug.bbox_review_flags || [])]));
  if (!candidateCount && !uncertainCount && !candidates.length && !reviewFlags.length) return null;
  return (
    <div className="rounded-md border border-sky-200 bg-sky-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium uppercase tracking-normal text-sky-900">OCR 위치 기반 검토 후보</p>
        <Badge className="border-sky-300 bg-white text-sky-800">확정 품목과 분리됨</Badge>
        {layoutDebug.parser_integrated === false ? <Badge variant="outline" className="border-slate-300 bg-white text-slate-700">parser 미통합</Badge> : null}
      </div>
      <p className="mt-2 text-sm text-sky-950">
        OCR 좌표로 추가 후보 {candidateCount ?? 0}건을 찾았고, 이 중 {uncertainCount ?? 0}건은 원본 확인이 필요합니다.
        품목 테이블에는 자동 반영하지 않았습니다.
      </p>
      {reviewFlags.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {reviewFlags.map((flag) => <Badge key={flag} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">{bboxReviewFlagLabel(flag)}</Badge>)}
        </div>
      ) : null}
      {candidates.length ? (
        <div className="mt-3 space-y-2">
          {candidates.slice(0, 3).map((candidate, index) => <BBoxCandidateCard key={`${candidate.row_index ?? index}-${candidate.source_text ?? index}`} candidate={candidate} />)}
        </div>
      ) : null}
    </div>
  );
}

function VLCandidateBlock({ metadata }: { metadata: VLCandidateMetadata | null }) {
  if (!metadata) return null;
  const candidates = metadata.vl_candidates || [];
  const summary = metadata.vl_candidate_summary;
  const candidateCount = summary?.candidate_count ?? candidates.length;
  const issueCodes = Array.from(new Set([...(summary?.issue_codes || []), ...candidates.flatMap((candidate) => candidate.issue_codes || [])]));
  const issueDetails = candidates.flatMap((candidate) => candidate.issue_details || []).slice(0, 4);
  const handlingLabel = vlCandidateHandlingLabel(summary?.recommended_handling || candidates[0]?.recommended_handling);
  if (!candidateCount && !issueCodes.length) return null;
  return (
    <div className="rounded-md border border-violet-200 bg-violet-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium uppercase tracking-normal text-violet-900">VL 문서 이해 후보</p>
        <Badge className="border-violet-300 bg-white text-violet-800">확정값 아님</Badge>
        <Badge variant="outline" className="border-slate-300 bg-white text-slate-700">ERP 내보내기 제외</Badge>
        {handlingLabel ? <Badge variant="outline" className="border-violet-300 bg-white text-violet-800">{handlingLabel}</Badge> : null}
      </div>
      <p className="mt-2 text-sm text-violet-950">
        PaddleOCR-VL GGUF가 참고 후보 {candidateCount ?? 0}건을 만들었습니다.
        이 값은 품목/금액 테이블에 자동 반영하지 않고 원본 대조용으로만 사용합니다.
      </p>
      {issueCodes.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {issueCodes.map((code) => <Badge key={code} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">{vlCandidateIssueLabel(code)}</Badge>)}
        </div>
      ) : null}
      {issueDetails.length ? (
        <ul className="mt-2 space-y-1 rounded border bg-white p-2 text-xs text-slate-700">
          {issueDetails.map((detail, index) => (
            <li key={`${detail.code ?? "issue"}-${detail.expected_value ?? index}`}>
              <span className="font-medium">{vlCandidateIssueLabel(detail.code) || "검토 필요"}:</span>{" "}
              {detail.label || detail.field || detail.row_contains || "원본 값"}{" "}
              {detail.expected_value !== undefined && detail.expected_value !== null ? <span>({detail.expected_value})</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {candidates[0]?.text_preview ? (
        <p className="mt-2 line-clamp-4 whitespace-pre-wrap rounded border bg-white p-2 text-xs text-slate-700">{candidates[0].text_preview}</p>
      ) : null}
    </div>
  );
}

export function WorkflowPanel({ document }: { document: DocumentRecord }) {
  const labels = documentFieldLabels(document.document_type);
  const blockingIssues = blockingReviewIssues(document);
  const reviewItems = reviewIssueSummaryItems(blockingIssues);
  const infoItems = informationalReviewIssues(document).map((issue) => issue.message_ko);
  const warningItems = displayWarningsWithoutReviewDuplicates(document.warnings, blockingIssues);
  const roleDate = businessFieldDate(document);
  const issueDate = businessIssueDate(document);
  const exportReady = !document.review_required && document.processing_status === "confirmed";
  const layoutDebug = layoutDebugMetadata(document);
  const vlMetadata = vlCandidateMetadata(document);

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
        <ListBlock title="처리 경고" items={warningItems} warning />
        <LayoutDebugBlock layoutDebug={layoutDebug} />
        <VLCandidateBlock metadata={vlMetadata} />
        <InfoDetails items={infoItems} />
        <ListBlock title="주요 날짜" items={document.key_dates} />
      </CardContent>
    </Card>
  );
}
