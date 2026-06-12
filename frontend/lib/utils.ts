import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { DocumentTaxonomy } from "@/types/document";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMoney(value?: string | number | null, currency = "USD") {
  if (value === undefined || value === null || value === "") return "금액 없음";
  return new Intl.NumberFormat("ko-KR", { style: "currency", currency }).format(Number(value));
}

export function formatDate(value?: string | null) {
  if (!value) return "날짜 없음";
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium" }).format(new Date(`${value}T00:00:00`));
}

export function formatDateTime(value?: string | null) {
  if (!value) return "알 수 없음";
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

const LABEL_ALIASES: Record<string, string> = {
  purchase_order: "발주서",
  quotation: "견적서",
  transaction_statement: "거래명세서",
  delivery_note: "납품서",
  invoice: "인보이스/세금계산서",
  packing_list: "포장명세서",
  inspection_report: "검사성적서",
  contract: "계약서",
  general_document: "일반 문서",
  receipt: "영수증",
  notice: "공지 문서",
  document: "문서",
  memo: "메모",
  presentation: "프레젠테이션",
  other: "기타",
  retail: "소매",
};

const DOCUMENT_SUBTYPE_LABELS: Record<string, string> = {
  tax_invoice: "세금계산서",
  commercial_invoice: "상업송장",
  return_note: "반품 문서",
  credit_note: "차감/크레딧 문서",
  internal_transfer: "내부 이동서",
};

const DOCUMENT_PROFILE_LABELS: Record<string, string> = {
  tax_document: "세금 문서",
  priced_document: "금액 문서",
  foreign_currency_document: "외화 문서",
  return_document: "반품/차감 문서",
  inventory_movement_document: "재고 이동 문서",
  no_price_document: "금액 없음 정상",
  quality_document: "품질/검사 문서",
  statement_document: "거래/정산 문서",
  option_quote_document: "옵션 견적 문서",
};

const LAYOUT_PROFILE_LABELS: Record<string, string> = {
  text_layer_pdf: "텍스트 PDF",
  scanned_pdf: "스캔 PDF",
  photographed_pdf: "사진 기반 PDF",
  fax_misaligned: "팩스/정렬 불량",
  multipage_table: "다중 페이지 표",
  sparse_table: "희소 표",
  dense_table: "밀집 표",
};

export function titleCaseLabel(value?: string | null): string {
  if (!value) return "미분류";
  if (value.includes(">")) return value.split(">").map((part) => titleCaseLabel(part)).join(" > ");
  const alias = LABEL_ALIASES[value];
  if (alias) return alias;
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function optionalString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function optionalBoolean(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return null;
}

function optionalStringList(...values: unknown[]) {
  const merged: string[] = [];
  values.forEach((value) => {
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (typeof item === "string" && item.trim() && !merged.includes(item)) merged.push(item);
      });
    }
  });
  return merged;
}

export function documentTaxonomy(document: {
  document_type?: string | null;
  workflow_metadata?: Record<string, unknown> | null;
  ingestion_metadata?: Record<string, unknown> | null;
}): DocumentTaxonomy {
  const workflow = asRecord(document.workflow_metadata);
  const ingestion = asRecord(document.ingestion_metadata);
  const workflowTaxonomy = asRecord(workflow.taxonomy);
  const ingestionTaxonomy = asRecord(ingestion.taxonomy);
  const documentProfile = optionalString(workflowTaxonomy.document_profile, workflow.document_profile, ingestionTaxonomy.document_profile);
  const documentProfiles = optionalStringList(workflowTaxonomy.document_profiles, workflow.document_profiles, ingestionTaxonomy.document_profiles);
  if (documentProfile && !documentProfiles.includes(documentProfile)) documentProfiles.unshift(documentProfile);
  return {
    document_subtype: optionalString(workflowTaxonomy.document_subtype, workflow.document_subtype, ingestionTaxonomy.document_subtype),
    document_profile: documentProfile,
    document_profiles: documentProfiles,
    layout_profile: optionalString(workflowTaxonomy.layout_profile, workflow.layout_profile, ingestionTaxonomy.layout_profile),
    amount_required: optionalBoolean(workflowTaxonomy.amount_required, workflow.amount_required, ingestionTaxonomy.amount_required),
    party_required: optionalBoolean(workflowTaxonomy.party_required, workflow.party_required, ingestionTaxonomy.party_required),
    evidence: optionalStringList(workflowTaxonomy.evidence, workflow.taxonomy_evidence, ingestionTaxonomy.evidence),
  };
}

export function documentSubtypeLabel(value?: string | null) {
  if (!value) return null;
  return DOCUMENT_SUBTYPE_LABELS[value] || titleCaseLabel(value);
}

export function documentProfileLabel(value?: string | null) {
  if (!value) return null;
  return DOCUMENT_PROFILE_LABELS[value] || titleCaseLabel(value);
}

export function layoutProfileLabel(value?: string | null) {
  if (!value) return null;
  return LAYOUT_PROFILE_LABELS[value] || titleCaseLabel(value);
}

export function taxonomyPolicyLines(taxonomy: DocumentTaxonomy) {
  const profiles = new Set(taxonomy.document_profiles || []);
  const lines: string[] = [];
  if (profiles.has("no_price_document")) lines.push("이 문서는 금액/통화가 없어도 정상일 수 있습니다. 품목명과 수량을 중심으로 검토하세요.");
  if (profiles.has("tax_document")) lines.push("공급가액, 세액, 합계금액의 일치 여부를 확인해야 합니다.");
  if (profiles.has("return_document")) lines.push("관련 원문서 번호와 차감 금액의 방향을 확인해야 합니다.");
  if (profiles.has("inventory_movement_document")) lines.push("거래처보다 출고/입고 위치와 요청 수량 확인이 중요합니다.");
  if (profiles.has("foreign_currency_document")) lines.push("외화 금액과 통화 단위, 환율 메모를 함께 확인하세요.");
  if (profiles.has("quality_document")) lines.push("입고수량, 합격수량, 불량수량과 판정 값을 확인하세요.");
  if (profiles.has("option_quote_document")) lines.push("옵션 라인은 단순 합산하지 말고 선택 대상인지 확인하세요.");
  return Array.from(new Set(lines));
}

export function taxonomyBadgeLabels(document: {
  document_type?: string | null;
  workflow_metadata?: Record<string, unknown> | null;
  ingestion_metadata?: Record<string, unknown> | null;
}, maxProfiles = 2) {
  const taxonomy = documentTaxonomy(document);
  const labels: Array<{ key: string; label: string; tone: "subtype" | "profile" | "layout" }> = [];
  const subtype = documentSubtypeLabel(taxonomy.document_subtype);
  if (subtype) labels.push({ key: `subtype-${taxonomy.document_subtype}`, label: subtype, tone: "subtype" });
  (taxonomy.document_profiles || []).slice(0, maxProfiles).forEach((profile) => {
    const label = documentProfileLabel(profile);
    if (label) labels.push({ key: `profile-${profile}`, label, tone: "profile" });
  });
  return labels;
}

export function documentDisplayTitle(document: { document_number?: string | null; title?: string | null; original_filename?: string | null }) {
  return document.document_number || document.title || document.original_filename || "문서";
}

export function primaryCategoryLabel(document: { category?: string | null; workflow_metadata?: Record<string, unknown> | null }) {
  const interpretation = (document.workflow_metadata?.category_interpretation ?? {}) as Record<string, unknown>;
  const profile = typeof interpretation.profile === "string" ? interpretation.profile : null;
  return titleCaseLabel(document.category || profile || null);
}

export function profileLabelForDocument(document: { document_type?: string | null; workflow_metadata?: Record<string, unknown> | null }) {
  const manufacturingProfiles = new Set(["purchase_order", "quotation", "transaction_statement", "delivery_note", "invoice", "packing_list", "inspection_report", "contract"]);
  const interpretation = (document.workflow_metadata?.category_interpretation ?? {}) as Record<string, unknown>;
  const profile = typeof interpretation.profile === "string" ? interpretation.profile : null;
  const documentType = document.document_type || null;
  if (documentType && manufacturingProfiles.has(documentType) && profile && profile !== documentType && manufacturingProfiles.has(profile)) {
    return titleCaseLabel(documentType);
  }
  return titleCaseLabel(profile || documentType);
}

export function extractionMethodLabel(document: { provider_chain?: string | null; extraction_method?: string | null; extraction_provider?: string | null; refinement_provider?: string | null }) {
  const chain = `${document.provider_chain || ""}+${document.extraction_method || ""}+${document.extraction_provider || ""}+${document.refinement_provider || ""}`.toLowerCase();
  const parts: string[] = [];
  if (chain.includes("ocr")) {
    parts.push("OCR 추출");
  } else if (chain.includes("pdf_text") || chain.includes("txt_direct") || chain.includes("structured_text") || chain.includes("_direct")) {
    parts.push("텍스트 직접 추출");
  } else if (chain.includes("office") || chain.includes("xlsx") || chain.includes("docx")) {
    parts.push("Office 문서 추출");
  } else {
    parts.push("문서 텍스트 추출");
  }

  const parserOnly = chain.includes("rule_based_structuring") || chain.includes("interpretation_skipped_rule_based_ready");
  const aiRan = !parserOnly && (
    chain.includes("ai_interpretation_openai") ||
    chain.includes("ai_interpretation_gemma") ||
    chain.includes("ai_interpretation_gemma_gguf") ||
    chain.includes("gemma_gguf") ||
    chain.includes("llama_cpp") ||
    chain.includes("openai")
  );
  if (parserOnly) {
    parts.push("규칙 기반 구조화");
  } else if (aiRan) {
    parts.push("AI 보조 분석");
  } else if (chain.includes("heuristic_fallback")) {
    parts.push("규칙 기반 구조화");
  }
  return Array.from(new Set(parts)).join(" + ");
}

export function documentFieldLabels(documentType?: string | null) {
  switch (documentType) {
    case "quotation":
      return { documentNumber: "견적번호", issueDate: "견적일", dueDate: "유효기간" };
    case "transaction_statement":
      return { documentNumber: "거래명세서번호", issueDate: "거래일자", dueDate: "발행일" };
    case "delivery_note":
      return { documentNumber: "납품번호", issueDate: "발행일", dueDate: "납품일" };
    case "invoice":
      return { documentNumber: "계산서번호", issueDate: "발행일", dueDate: "지급기한" };
    case "purchase_order":
    default:
      return { documentNumber: "문서번호", issueDate: "발행일", dueDate: "납기일" };
  }
}

export function businessFieldDate(document: { document_type?: string | null; due_date?: string | null; workflow_metadata?: Record<string, unknown> | null }) {
  const businessFields = (document.workflow_metadata?.business_fields ?? {}) as Record<string, unknown>;
  const valueForType =
    document.document_type === "quotation"
      ? businessFields.valid_until
      : document.document_type === "delivery_note"
        ? businessFields.delivery_date
        : document.document_type === "invoice"
          ? businessFields.payment_due_date
          : document.due_date;
  return typeof valueForType === "string" && valueForType ? valueForType : document.due_date || "";
}

export function businessIssueDate(document: { document_type?: string | null; issue_date?: string | null; extracted_date?: string | null; workflow_metadata?: Record<string, unknown> | null }) {
  const businessFields = (document.workflow_metadata?.business_fields ?? {}) as Record<string, unknown>;
  const valueForType =
    document.document_type === "transaction_statement"
      ? businessFields.transaction_date
      : document.document_type === "quotation"
        ? businessFields.quotation_date
        : document.issue_date;
  return typeof valueForType === "string" && valueForType ? valueForType : document.issue_date || document.extracted_date || "";
}

export interface NormalizedReviewIssue {
  code: string;
  message_ko: string;
  field?: string;
  item_index?: number;
  severity?: string;
  document_total?: string | number | null;
  line_total_sum?: string | number | null;
  difference?: string | number | null;
  currency?: string | null;
}

export function normalizedReviewIssues(document: {
  workflow_metadata?: Record<string, unknown> | null;
  ingestion_metadata?: Record<string, unknown> | null;
  warnings?: string[];
  low_confidence_fields?: string[];
  review_required?: boolean;
}): NormalizedReviewIssue[] {
  const metadata = document.workflow_metadata ?? {};
  const taxonomy = documentTaxonomy(document);
  const noPriceDocument = (taxonomy.document_profiles || []).includes("no_price_document") || taxonomy.amount_required === false;
  const fromMetadata = Array.isArray(metadata.normalized_review_issues)
    ? metadata.normalized_review_issues
    : Array.isArray(metadata.review_reasons)
      ? metadata.review_reasons
      : [];
  const issues: NormalizedReviewIssue[] = [];
  const seen = new Set<string>();
  const add = (issue: Partial<NormalizedReviewIssue> | string) => {
    const normalized: NormalizedReviewIssue = typeof issue === "string"
      ? { code: "validation_warning", message_ko: issue }
      : {
          code: String(issue.code || "review_required"),
          message_ko: String(issue.message_ko || ""),
          field: typeof issue.field === "string" ? issue.field : undefined,
          item_index: typeof issue.item_index === "number" ? issue.item_index : undefined,
          severity: typeof issue.severity === "string" ? issue.severity : undefined,
          document_total: typeof issue.document_total === "string" || typeof issue.document_total === "number" ? issue.document_total : undefined,
          line_total_sum: typeof issue.line_total_sum === "string" || typeof issue.line_total_sum === "number" ? issue.line_total_sum : undefined,
          difference: typeof issue.difference === "string" || typeof issue.difference === "number" ? issue.difference : undefined,
          currency: typeof issue.currency === "string" ? issue.currency : undefined,
        };
    if (noPriceDocument && normalized.code === "missing_price_or_total") {
      normalized.severity = "info";
      normalized.message_ko = "이 문서 유형에서는 금액 정보가 없을 수 있습니다.";
    }
    if (!normalized.message_ko) return;
    const messageKey = normalized.message_ko.replace(/\s+/g, " ").trim();
    const key = `${normalized.code}:${normalized.field || ""}:${normalized.item_index ?? ""}:${messageKey}`;
    if (seen.has(key)) return;
    if ([...seen].some((existing) => existing.endsWith(`:${messageKey}`))) return;
    seen.add(key);
    issues.push(normalized);
  };
  fromMetadata.forEach((issue) => {
    if (issue && typeof issue === "object") add(issue as Partial<NormalizedReviewIssue>);
  });
  if (Array.isArray(metadata.normalized_review_issues) && issues.length) {
    return issues;
  }
  const validationWarnings = Array.isArray(metadata.validation_warnings) ? metadata.validation_warnings : document.warnings || [];
  validationWarnings.forEach((message) => {
    if (typeof message === "string") add(message);
  });
  (document.low_confidence_fields || []).forEach((field) => add({ code: field.split(":", 1)[0], message_ko: reviewReasonLabel(field), field }));
  if (document.review_required && !issues.length) add({ code: "review_required", message_ko: "검토 필요 항목을 확인하세요.", field: "document" });
  return issues;
}

export function reviewIssueAmountLines(issue: NormalizedReviewIssue) {
  if (issue.code !== "amount_mismatch") return [];
  const currency = issue.currency || "KRW";
  const lines: string[] = [];
  if (issue.document_total !== undefined && issue.document_total !== null && issue.document_total !== "") {
    lines.push(`문서 총액: ${formatMoney(issue.document_total, currency)}`);
  }
  if (issue.line_total_sum !== undefined && issue.line_total_sum !== null && issue.line_total_sum !== "") {
    lines.push(`품목 합계: ${formatMoney(issue.line_total_sum, currency)}`);
  }
  if (issue.difference !== undefined && issue.difference !== null && issue.difference !== "") {
    lines.push(`차이: ${formatMoney(issue.difference, currency)}`);
  }
  return lines;
}

export function reviewIssueSummary(issue: NormalizedReviewIssue) {
  const labels: Record<string, string> = {
    missing_price_or_total: "금액/총액 누락",
    untrusted_ocr_amount: "금액 OCR 신뢰도 낮음",
    amount_mismatch: "품목 합계와 총액 불일치",
    line_items_total_mismatch: "품목 합계와 총액 불일치",
    ambiguous_item_match: "품목 매칭 모호",
    internal_item_ambiguous: "품목 매칭 모호",
    internal_item_unmatched: "내부 품목 미매칭",
    item_code_name_conflict: "품목명/코드 충돌",
    missing_line_items: "품목 정보 누락",
    missing_quantity: "수량 누락",
  };
  return labels[issue.code] || issue.message_ko;
}

export function reviewIssueDescription(issue: NormalizedReviewIssue) {
  const descriptions: Record<string, string> = {
    missing_price_or_total: "이 문서 유형에서는 총액 확인이 필요할 수 있습니다.",
    untrusted_ocr_amount: "OCR이 읽은 금액이 불확실하여 사람 검토가 필요합니다.",
    amount_mismatch: "품목별 금액 합계와 문서 총액이 맞지 않습니다.",
    line_items_total_mismatch: "품목별 금액 합계와 문서 총액이 맞지 않습니다.",
    ambiguous_item_match: "사내 품목 master와 정확히 일치하지 않습니다.",
    internal_item_ambiguous: "사내 품목 master와 정확히 일치하지 않습니다.",
    internal_item_unmatched: "사내 품목 master에서 확실한 항목을 찾지 못했습니다.",
    item_code_name_conflict: "문서 품목명과 선택된 내부 품목코드가 서로 맞지 않을 수 있습니다.",
    missing_line_items: "표 또는 품목 후보가 충분히 구조화되지 않았습니다.",
    missing_quantity: "ERP 입력에 필요한 수량을 확인해야 합니다.",
  };
  return descriptions[issue.code] || issue.message_ko;
}

export function displayWarningsWithoutReviewDuplicates(
  warnings: string[],
  issues: NormalizedReviewIssue[]
) {
  const issueMessages = new Set(issues.map((issue) => issue.message_ko.replace(/\s+/g, " ").trim()));
  const hasAmountMismatch = issues.some((issue) => issue.code === "amount_mismatch");
  return (warnings || []).filter((warning) => {
    const normalized = warning.replace(/\s+/g, " ").trim();
    if (issueMessages.has(normalized)) return false;
    if (hasAmountMismatch && normalized.includes("합계") && normalized.includes("일치하지 않습니다")) return false;
    return true;
  });
}

export function isBlockingReviewIssue(issue: NormalizedReviewIssue) {
  if (issue.severity === "info" || issue.severity === "low") return false;
  return [
    "missing_vendor_name",
    "missing_customer_name",
    "missing_document_number",
    "missing_issue_date",
    "missing_due_date",
    "missing_payment_due_date",
    "missing_line_items",
    "missing_item_name",
    "missing_quantity",
    "missing_price_or_total",
    "amount_mismatch",
    "invalid_line_amount",
    "item_code_name_conflict",
    "internal_item_unmatched",
    "internal_item_ambiguous",
    "item_matching_skipped",
    "review_required",
    "validation_warning",
  ].includes(issue.code);
}

export function blockingReviewIssues(document: {
  workflow_metadata?: Record<string, unknown> | null;
  warnings?: string[];
  low_confidence_fields?: string[];
  review_required?: boolean;
}) {
  return normalizedReviewIssues(document).filter(isBlockingReviewIssue);
}

export function informationalReviewIssues(document: {
  workflow_metadata?: Record<string, unknown> | null;
  warnings?: string[];
  low_confidence_fields?: string[];
  review_required?: boolean;
}) {
  return normalizedReviewIssues(document).filter((issue) => !isBlockingReviewIssue(issue));
}

export function reviewReasonLabel(value: string) {
  const [code, itemToken] = value.split(":");
  const itemNumber = itemToken?.replace("item_", "");
  const prefix = itemNumber ? `${itemNumber}번째 품목 ` : "";
  const labels: Record<string, string> = {
    missing_line_items: "품목 정보가 추출되지 않았습니다.",
    missing_item_name: `${prefix}품목명이 비어 있습니다.`,
    missing_quantity: `${prefix}수량이 비어 있습니다.`,
    missing_price_or_total: `${prefix}단가 또는 합계금액을 확인해야 합니다.`,
    missing_item_code: `${prefix}품목코드 미확인`,
    missing_document_item_code: `${prefix}문서 품목코드 미확인`,
    item_master_match_required: `${prefix}내부 품목코드 후보 확인 필요`,
    internal_item_ambiguous: `${prefix}내부 품목코드 후보 확인 필요`,
    internal_item_unmatched: `${prefix}내부 품목코드 미매칭`,
    item_code_name_conflict: `${prefix}품목명과 내부 품목코드가 충돌합니다.`,
    amount_mismatch: "문서 합계금액과 품목 합계금액이 일치하지 않습니다.",
    missing_document_number: "문서번호 미확인",
    missing_issue_date: "날짜 미확인",
    missing_due_date: "납기일 미확인",
    missing_payment_due_date: "지급기한 미확인",
    item_matching_skipped: "내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.",
  };
  return labels[code] || value;
}

function workflowSummaryFields(document: { workflow_metadata?: Record<string, unknown> | null }) {
  const summaries = (document.workflow_metadata?.summaries ?? {}) as Record<string, unknown>;
  return {
    short: typeof summaries.short === "string" ? summaries.short : null,
    detailed: typeof summaries.detailed === "string" ? summaries.detailed : null,
  };
}

export function documentSummaryShort(document: { workflow_metadata?: Record<string, unknown> | null; workflow_summary?: string | null; summary?: string | null }, limit = 120) {
  const summaries = workflowSummaryFields(document);
  const value = summaries.short || document.summary || document.workflow_summary;
  return shortSummary(value, limit);
}

export function documentSummaryDetailed(document: { workflow_metadata?: Record<string, unknown> | null; workflow_summary?: string | null; summary?: string | null }, limit = 500) {
  const summaries = workflowSummaryFields(document);
  const value = summaries.detailed || document.workflow_summary || document.summary;
  return shortSummary(value, limit);
}

export function shortSummary(summary?: string | null, limit = 120) {
  if (!summary) return "아직 추출된 업무 데이터가 없습니다.";
  return summary.length > limit ? `${summary.slice(0, limit).trim()}...` : summary;
}
