import type { DocumentRecord } from "@/types/document";
import { ApiRequestError } from "@/lib/api";

export const DEFAULT_UPLOAD_CONCURRENCY = 3;
export const RECOMMENDED_MAX_UPLOAD_FILES = 20;
export const UPLOAD_QUEUE_STORAGE_KEY = "docuparse.uploadQueue.v1";
export const UPLOAD_QUEUE_SCHEMA_VERSION = 1;
export const UPLOAD_QUEUE_TTL_MS = 1000 * 60 * 60 * 24 * 3;

export type UploadQueueStatus =
  | "selected"
  | "waiting_upload"
  | "accepting"
  | "accepted"
  | "queued"
  | "processing"
  | "done"
  | "needs_review"
  | "failed"
  | "needs_reselect"
  | "interrupted";

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
    status: "selected",
    attempts: 0,
    error: null,
    documentId: null,
    documentTitle: null,
    fileAvailable: true,
    updatedAt: now,
  }));
}

export function runningUploadCount(items: UploadQueueItem<UploadQueueFileLike>[]) {
  return items.filter((item) => item.status === "accepting").length;
}

export function nextQueuedUploadIds(items: UploadQueueItem<UploadQueueFileLike>[], concurrency = DEFAULT_UPLOAD_CONCURRENCY) {
  const capacity = Math.max(0, concurrency - runningUploadCount(items));
  return items.filter((item) => ["selected", "waiting_upload"].includes(item.status) && item.fileAvailable !== false).slice(0, capacity).map((item) => item.id);
}

export function markUploadStarted(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.map((item) => item.id === id ? { ...item, status: "accepting" as const, attempts: item.attempts + 1, error: null, updatedAt: Date.now() } : item);
}

export function markUploadProcessing(items: UploadQueueItem<UploadQueueFileLike>[], id: string, document: DocumentRecord) {
  const status = uploadQueueStatusFromDocument(document);
  return items.map((item) => item.id === id ? {
    ...item,
    status,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
    updatedAt: Date.now(),
  } : item);
}

export function markUploadCompleted(items: UploadQueueItem<UploadQueueFileLike>[], id: string, document: DocumentRecord) {
  const status = uploadQueueStatusFromDocument(document);
  return items.map((item) => item.id === id ? {
    ...item,
    status,
    error: document.processing_status === "failed" ? document.processing_error || "서버 처리가 실패했습니다." : null,
    documentId: document.id,
    documentTitle: document.title || document.original_filename,
    updatedAt: Date.now(),
  } : item);
}

export function mergeDocumentStatusesIntoQueue(
  items: UploadQueueItem<UploadQueueFileLike>[],
  documents: DocumentRecord[],
) {
  if (!documents.length) return items;
  const byId = new Map(documents.map((document) => [document.id, document]));
  return items.map((item) => {
    if (!item.documentId) return item;
    const document = byId.get(item.documentId);
    if (!document) return item;
    return {
      ...item,
      status: uploadQueueStatusFromDocument(document),
      error: document.processing_status === "failed" ? document.processing_error || "서버 처리가 실패했습니다." : null,
      documentTitle: document.title || document.original_filename,
      updatedAt: Date.now(),
    };
  });
}

export function uploadQueueStatusFromDocument(document: Pick<DocumentRecord, "processing_status">): UploadQueueStatus {
  if (document.processing_status === "uploaded") return "accepted";
  if (document.processing_status === "queued") return "queued";
  if (document.processing_status === "processing") return "processing";
  if (document.processing_status === "needs_review") return "needs_review";
  if (document.processing_status === "failed") return "failed";
  return "done";
}

export function markUploadFailed(items: UploadQueueItem<UploadQueueFileLike>[], id: string, error: string) {
  return items.map((item) => item.id === id ? { ...item, status: "failed" as const, error, updatedAt: Date.now() } : item);
}

export function explainUploadError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    const detailText = apiErrorDetailText(error.detail);
    if (error.status === 413) {
      return "파일 용량이 업로드 제한을 넘었습니다. 파일을 나누거나 해상도를 낮춘 뒤 다시 올리세요.";
    }
    if (error.status === 415) {
      return "지원하지 않는 파일 형식입니다. PDF, 이미지, 엑셀/문서 파일인지 확인하세요.";
    }
    if (error.status >= 500) {
      return detailText
        ? `서버 처리 중 오류가 발생했습니다. 문서는 접수되지 않았을 수 있습니다. 잠시 후 다시 시도하세요. (${detailText})`
        : "서버 처리 중 오류가 발생했습니다. 문서는 접수되지 않았을 수 있습니다. 잠시 후 다시 시도하세요.";
    }
    return detailText || error.message || "업로드 요청을 처리하지 못했습니다.";
  }
  const message = error instanceof Error ? error.message : String(error || "");
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return "백엔드에 연결하지 못했습니다. 서버가 실행 중인지 확인한 뒤 다시 시도하세요.";
  }
  if (/abort|aborted|timeout|timed out/i.test(message)) {
    return "요청 시간이 초과되었거나 업로드가 중단되었습니다. 파일을 다시 선택해 시도하세요.";
  }
  if (/file.*missing|reselect|파일.*다시/i.test(message)) {
    return "새로고침 후에는 파일을 다시 선택해야 합니다.";
  }
  return message || "업로드에 실패했습니다";
}

function apiErrorDetailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return "";
  const record = detail as Record<string, unknown>;
  if (typeof record.message === "string") return record.message;
  if (typeof record.detail === "string") return record.detail;
  if (typeof record.error === "string") return record.error;
  return "";
}

export function retryUploadItem(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.map((item) => item.id === id && item.status === "failed" && item.fileAvailable !== false ? { ...item, status: "waiting_upload" as const, error: null, updatedAt: Date.now() } : item);
}

export function removeQueuedUploadItem(items: UploadQueueItem<UploadQueueFileLike>[], id: string) {
  return items.filter((item) => !(item.id === id && ["selected", "waiting_upload", "needs_reselect", "interrupted"].includes(item.status)));
}

export function clearUploadQueue(items: UploadQueueItem<UploadQueueFileLike>[]) {
  return items.filter((item) => ["accepting", "accepted", "queued", "processing"].includes(item.status));
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
      if (status === "selected" || status === "waiting_upload") {
        status = "needs_reselect";
        error = "새로고침 후에는 파일을 다시 선택해야 합니다.";
      } else if (status === "accepting" && !item.documentId) {
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
