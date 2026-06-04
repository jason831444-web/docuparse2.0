import type { DocumentRecord } from "@/types/document";

export const DEFAULT_UPLOAD_CONCURRENCY = 3;

export type UploadQueueStatus = "queued" | "uploading" | "processing" | "done" | "needs_review" | "failed";

export interface UploadQueueFileLike {
  name: string;
  size: number;
  type?: string;
  lastModified?: number;
}

export interface UploadQueueItem<TFile extends UploadQueueFileLike = File> {
  id: string;
  file: TFile;
  status: UploadQueueStatus;
  attempts: number;
  error?: string | null;
  documentId?: string | null;
  documentTitle?: string | null;
}

export function createUploadQueueItems<TFile extends UploadQueueFileLike>(
  files: ArrayLike<TFile>,
  now = Date.now(),
  randomToken = () => Math.random().toString(36).slice(2, 8)
): UploadQueueItem<TFile>[] {
  return Array.from(files).map((file, index) => ({
    id: `${now}-${index}-${file.name}-${file.size}-${randomToken()}`,
    file,
    status: "queued",
    attempts: 0,
    error: null,
    documentId: null,
    documentTitle: null,
  }));
}

export function runningUploadCount(items: UploadQueueItem[]) {
  return items.filter((item) => item.status === "uploading" || item.status === "processing").length;
}

export function nextQueuedUploadIds(items: UploadQueueItem[], concurrency = DEFAULT_UPLOAD_CONCURRENCY) {
  const capacity = Math.max(0, concurrency - runningUploadCount(items));
  return items.filter((item) => item.status === "queued").slice(0, capacity).map((item) => item.id);
}

export function markUploadStarted(items: UploadQueueItem[], id: string) {
  return items.map((item) => item.id === id ? { ...item, status: "uploading" as const, attempts: item.attempts + 1, error: null } : item);
}

export function markUploadProcessing(items: UploadQueueItem[], id: string, document: DocumentRecord) {
  return items.map((item) => item.id === id ? {
    ...item,
    status: "processing" as const,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
  } : item);
}

export function markUploadCompleted(items: UploadQueueItem[], id: string, document: DocumentRecord) {
  const status: UploadQueueStatus = document.processing_status === "needs_review" ? "needs_review" : "done";
  return items.map((item) => item.id === id ? {
    ...item,
    status,
    error: null,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
  } : item);
}

export function markUploadFailed(items: UploadQueueItem[], id: string, error: string) {
  return items.map((item) => item.id === id ? { ...item, status: "failed" as const, error } : item);
}

export function retryUploadItem(items: UploadQueueItem[], id: string) {
  return items.map((item) => item.id === id && item.status === "failed" ? { ...item, status: "queued" as const, error: null } : item);
}

export function removeQueuedUploadItem(items: UploadQueueItem[], id: string) {
  return items.filter((item) => !(item.id === id && item.status === "queued"));
}
