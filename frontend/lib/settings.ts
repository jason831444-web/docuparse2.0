export type DocumentGroupingMode = "none" | "document_type" | "party" | "party_type" | "type_party";
export type DocumentViewMode = "folders" | "list" | "grid";

export const DOCUMENT_GROUPING_STORAGE_KEY = "docuparse.documentGrouping.v1";
export const DOCUMENT_VIEW_STORAGE_KEY = "docuparse.documentView.v1";

export const documentGroupingLabels: Record<DocumentGroupingMode, string> = {
  none: "전체 업로드순 보기",
  document_type: "문서 유형별",
  party: "거래처별",
  party_type: "거래처별 → 문서 유형별",
  type_party: "문서 유형별 → 거래처별",
};

export function loadDocumentGroupingMode(): DocumentGroupingMode {
  if (typeof window === "undefined") return "none";
  const value = window.localStorage.getItem(DOCUMENT_GROUPING_STORAGE_KEY);
  return isDocumentGroupingMode(value) ? value : "none";
}

export function saveDocumentGroupingMode(value: DocumentGroupingMode) {
  window.localStorage.setItem(DOCUMENT_GROUPING_STORAGE_KEY, value);
}

export function loadDocumentViewMode(): DocumentViewMode {
  if (typeof window === "undefined") return "folders";
  const value = window.localStorage.getItem(DOCUMENT_VIEW_STORAGE_KEY);
  return isDocumentViewMode(value) ? value : "folders";
}

export function saveDocumentViewMode(value: DocumentViewMode) {
  window.localStorage.setItem(DOCUMENT_VIEW_STORAGE_KEY, value);
}

function isDocumentGroupingMode(value: unknown): value is DocumentGroupingMode {
  return value === "none" || value === "document_type" || value === "party" || value === "party_type" || value === "type_party";
}

function isDocumentViewMode(value: unknown): value is DocumentViewMode {
  return value === "folders" || value === "list" || value === "grid";
}
