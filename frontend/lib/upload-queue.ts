import type { DocumentRecord } from "@/types/document";

export const DEFAULT_UPLOAD_CONCURRENCY = 3;
export const RECOMMENDED_MAX_UPLOAD_FILES = 20;
export const UPLOAD_QUEUE_STORAGE_KEY = "docuparse.uploadQueue.v1";
export const UPLOAD_QUEUE_SCHEMA_VERSION = 1;
export const UPLOAD_QUEUE_TTL_MS = 1000 * 60 * 60 * 24 * 3;

export type UploadQueueStatus = "queued" | "uploading" | "processing" | "done" | "needs_review" | "failed" | "needs_reselect" | "interrupted";

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
  fileAvailable?: boolean;
  updatedAt?: number;
}

export interface SerializedUploadQueueItem {
  id: string;
  file: UploadQueueFileLike;
  status: UploadQueueStatus;
  attempts: number;
  error?: string | null;
  documentId?: string | null;
  documentTitle?: string | null;
  fileAvailable?: boolean;
  updatedAt: number;
}

export interface SerializedUploadQueue {
  version: number;
  savedAt: number;
  items: SerializedUploadQueueItem[];
}

export function createUploadQueueItems<TFile extends UploadQueueFileLike>(
  files: ArrayLike<TFile>,
  now = Date.now(),
  randomToken = () => Math.random().toString(36).slice(2, 8)
): UploadQueueItem<TFile>[] {
  return Array.from(files).slice(0, RECOMMENDED_MAX_UPLOAD_FILES).map((file, index) => ({
    id: `${now}-${index}-${file.name}-${file.size}-${randomToken()}`,
    file,
    status: "queued",
    attempts: 0,
    error: null,
    documentId: null,
    documentTitle: null,
    fileAvailable: true,
    updatedAt: now,
  }));
}

export function runningUploadCount(items: UploadQueueItem<UploadQueueFileLike>[]) {
  return items.filter((item) => item.status === "uploading" || item.status === "processing").length;
}

export function nextQueuedUploadIds(items: UploadQueueItem<UploadQueueFileLike>[], concurrency = DEFAULT_UPLOAD_CONCURRENCY) {
  const capacity = Math.max(0, concurrency - runningUploadCount(items));
  return items.filter((item) => item.status === "queued" && item.fileAvailable !== false).slice(0, capacity).map((item) => item.id);
}

export function markUploadStarted(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.map((item) => item.id === id ? { ...item, status: "uploading" as const, attempts: item.attempts + 1, error: null, updatedAt: Date.now() } : item);
}

export function markUploadProcessing(items: UploadQueueItem<UploadQueueFileLike>[], id: string, document: DocumentRecord) {
  return items.map((item) => item.id === id ? {
    ...item,
    status: "processing" as const,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
    updatedAt: Date.now(),
  } : item);
}

export function markUploadCompleted(items: UploadQueueItem<UploadQueueFileLike>[], id: string, document: DocumentRecord) {
  const status: UploadQueueStatus = document.processing_status === "needs_review" ? "needs_review" : "done";
  return items.map((item) => item.id === id ? {
    ...item,
    status,
    error: null,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
    updatedAt: Date.now(),
  } : item);
}

export function markUploadFailed(items: UploadQueueItem<UploadQueueFileLike>[], id: string, error: string) {
  return items.map((item) => item.id === id ? { ...item, status: "failed" as const, error, updatedAt: Date.now() } : item);
}

export function retryUploadItem(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.map((item) => item.id === id && item.status === "failed" && item.fileAvailable !== false ? { ...item, status: "queued" as const, error: null, updatedAt: Date.now() } : item);
}

export function removeQueuedUploadItem(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.filter((item) => !(item.id === id && ["queued", "needs_reselect", "interrupted"].includes(item.status)));
}

export function serializeUploadQueue(items: UploadQueueItem<UploadQueueFileLike>[], now = Date.now()): SerializedUploadQueue {
  return {
    version: UPLOAD_QUEUE_SCHEMA_VERSION,
    savedAt: now,
    items: items.map((item) => ({
      id: item.id,
      file: {
        name: item.file.name,
        size: item.file.size,
        type: item.file.type,
        lastModified: item.file.lastModified,
      },
      status: item.status,
      attempts: item.attempts,
      error: item.error,
      documentId: item.documentId,
      documentTitle: item.documentTitle,
      fileAvailable: item.fileAvailable === false ? false : true,
      updatedAt: item.updatedAt || now,
    })),
  };
}

export function restoreUploadQueue(serialized: unknown, now = Date.now()): UploadQueueItem<UploadQueueFileLike>[] {
  if (!serialized || typeof serialized !== "object") return [];
  const payload = serialized as Partial<SerializedUploadQueue>;
  if (payload.version !== UPLOAD_QUEUE_SCHEMA_VERSION || !Array.isArray(payload.items)) return [];
  return payload.items
    .filter((item) => now - Number(item.updatedAt || payload.savedAt || 0) <= UPLOAD_QUEUE_TTL_MS)
    .map((item) => {
      let status = item.status;
      let error = item.error ?? null;
      if (status === "queued") {
        status = "needs_reselect";
        error = "새로고침 후에는 파일을 다시 선택해야 합니다.";
      } else if (status === "uploading" && !item.documentId) {
        status = "interrupted";
        error = "업로드가 중단되었습니다. 파일을 다시 선택하세요.";
      }
      return {
        id: item.id,
        file: item.file,
        status,
        attempts: item.attempts || 0,
        error,
        documentId: item.documentId ?? null,
        documentTitle: item.documentTitle ?? null,
        fileAvailable: false,
        updatedAt: item.updatedAt || payload.savedAt || now,
      };
    });
}
