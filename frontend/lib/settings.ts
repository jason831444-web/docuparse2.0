export type DocumentGroupingMode = "document_type" | "party" | "party_type" | "type_party";

export const DOCUMENT_GROUPING_STORAGE_KEY = "docuparse.documentGrouping.v1";

export const documentGroupingLabels: Record<DocumentGroupingMode, string> = {
  document_type: "문서 유형별",
  party: "거래처별",
  party_type: "거래처별 → 문서 유형별",
  type_party: "문서 유형별 → 거래처별",
};

export function loadDocumentGroupingMode(): DocumentGroupingMode {
  if (typeof window === "undefined") return "document_type";
  const value = window.localStorage.getItem(DOCUMENT_GROUPING_STORAGE_KEY);
  return isDocumentGroupingMode(value) ? value : "document_type";
}

export function saveDocumentGroupingMode(value: DocumentGroupingMode) {
  window.localStorage.setItem(DOCUMENT_GROUPING_STORAGE_KEY, value);
}

function isDocumentGroupingMode(value: unknown): value is DocumentGroupingMode {
  return value === "document_type" || value === "party" || value === "party_type" || value === "type_party";
}
