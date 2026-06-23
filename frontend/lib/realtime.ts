"use client";

import { useEffect } from "react";

import type { DocumentStats, ProcessingStatus } from "@/types/document";

export const DOCUMENTS_CHANGED_EVENT = "docuparse:documents-changed";

export type DocumentsChangedDetail = {
  source?: string;
  stats?: DocumentStats | null;
};

export function emitDocumentsChanged(detail: DocumentsChangedDetail = {}) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<DocumentsChangedDetail>(DOCUMENTS_CHANGED_EVENT, { detail }));
}

export function useDocumentsChanged(callback: (detail: DocumentsChangedDetail) => void, enabled = true) {
  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    const handleEvent = (event: Event) => {
      callback((event as CustomEvent<DocumentsChangedDetail>).detail ?? {});
    };
    window.addEventListener(DOCUMENTS_CHANGED_EVENT, handleEvent);
    return () => window.removeEventListener(DOCUMENTS_CHANGED_EVENT, handleEvent);
  }, [callback, enabled]);
}

export function isLiveProcessingStatus(status: ProcessingStatus | string | null | undefined) {
  return status === "uploaded" || status === "queued" || status === "processing";
}
