import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

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

export function primaryCategoryLabel(document: { category?: string | null; workflow_metadata?: Record<string, unknown> | null }) {
  const interpretation = (document.workflow_metadata?.category_interpretation ?? {}) as Record<string, unknown>;
  const profile = typeof interpretation.profile === "string" ? interpretation.profile : null;
  return titleCaseLabel(document.category || profile || null);
}

export function extractionMethodLabel(document: { provider_chain?: string | null; extraction_method?: string | null }) {
  const chain = `${document.provider_chain || ""}+${document.extraction_method || ""}`.toLowerCase();
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
  if (chain.includes("ai_") || chain.includes("gemma") || chain.includes("llama") || chain.includes("heuristic_interpretation")) {
    parts.push("AI 보조 분석");
  }
  return Array.from(new Set(parts)).join(" + ");
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
