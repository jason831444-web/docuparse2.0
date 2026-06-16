import type {
  ActivitySummary,
  AppNotification,
  DocumentCalendarItem,
  DocumentListResponse,
  DocumentRecord,
  DocumentStats,
  DocumentUpdate,
  FolderSummary,
  CreateItemAliasPayload,
  CreateItemMasterPayload,
  ItemAliasRecord,
  ItemMasterListResponse,
  ItemMasterRecord,
  ItemMasterStats,
  ItemMasterUploadResult,
  MonthlyReport,
  ProviderHealth,
  UpdateItemAliasPayload,
  UpdateItemMasterPayload,
} from "@/types/document";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001/api";
const BULK_DOCUMENT_CHUNK_SIZE = 500;
const HEALTH_URL = API_BASE.startsWith("/")
  ? `${API_BASE}/health`
  : API_BASE.endsWith("/api")
    ? `${API_BASE.slice(0, -4)}/health`
    : `${API_BASE}/health`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail ?? "요청에 실패했습니다"));
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: async () => {
    const response = await fetch(HEALTH_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("OCR 상태를 불러오지 못했습니다");
    return response.json() as Promise<ProviderHealth>;
  },
  stats: () => request<DocumentStats>("/documents/stats", { cache: "no-store" }),
  activity: () => request<ActivitySummary>("/documents/activity", { cache: "no-store" }),
  list: (params: URLSearchParams) => request<DocumentListResponse>(`/documents?${params.toString()}`, { cache: "no-store" }),
  categories: () => request<FolderSummary[]>("/documents/categories", { cache: "no-store" }),
  notifications: () => request<AppNotification[]>("/documents/notifications", { cache: "no-store" }),
  calendar: (params = new URLSearchParams()) => request<DocumentCalendarItem[]>(`/documents/calendar?${params.toString()}`, { cache: "no-store" }),
  monthlyReport: (params: URLSearchParams) => request<MonthlyReport>(`/reports/monthly?${params.toString()}`, { cache: "no-store" }),
  createCategory: (payload: { label: string; parent?: string | null; category?: string | null }) =>
    request<FolderSummary>("/documents/categories", { method: "POST", body: JSON.stringify(payload) }),
  deleteCategory: async (value: string) => {
    const response = await fetch(`${API_BASE}/documents/categories/${encodeURIComponent(value)}`, { method: "DELETE" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: "문서 유형을 삭제하지 못했습니다" }));
      throw new Error(error.detail ?? "문서 유형을 삭제하지 못했습니다");
    }
  },
  fileTypes: () => request<FolderSummary[]>("/documents/file-types", { cache: "no-store" }),
  review: () => request<DocumentListResponse>("/documents/review", { cache: "no-store" }),
  favorites: () => request<DocumentListResponse>("/documents/favorites", { cache: "no-store" }),
  get: (id: string) => request<DocumentRecord>(`/documents/${id}`, { cache: "no-store" }),
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<DocumentRecord>("/documents/upload", { method: "POST", body: data });
  },
  update: (id: string, payload: DocumentUpdate) =>
    request<DocumentRecord>(`/documents/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: async (id: string) => {
    const response = await fetch(`${API_BASE}/documents/${id}`, { method: "DELETE" });
    if (!response.ok) throw new Error("문서를 삭제하지 못했습니다");
  },
  bulkDelete: async (ids: string[]) => {
    let deleted = 0;
    for (const chunk of chunkArray(ids, BULK_DOCUMENT_CHUNK_SIZE)) {
      const result = await request<{ deleted: number }>("/documents/bulk/delete", { method: "POST", body: JSON.stringify({ ids: chunk }) });
      deleted += result.deleted;
    }
    return { deleted };
  },
  bulkDownload: async (ids: string[]) => {
    const chunks = chunkArray(ids, BULK_DOCUMENT_CHUNK_SIZE);
    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      const response = await fetch(`${API_BASE}/documents/bulk/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: chunk }),
      });
      if (!response.ok) throw new Error("선택한 파일을 다운로드하지 못했습니다");
      const blob = await response.blob();
      const suffix = chunks.length > 1 ? `-${index + 1}-of-${chunks.length}` : "";
      downloadBlob(blob, `docuparse-originals${suffix}.zip`);
    }
  },
  reprocess: (id: string) => request<DocumentRecord>(`/documents/${id}/reprocess`, { method: "POST" }),
  confirm: (id: string, payload?: { approval_note?: string | null }) =>
    request<DocumentRecord>(`/documents/${id}/confirm`, { method: "POST", body: JSON.stringify(payload ?? {}) }),
  markNeedsReview: (id: string) => request<DocumentRecord>(`/documents/${id}/needs-review`, { method: "POST" }),
  updateReviewIssue: (id: string, payload: { key: string; status: "open" | "resolved" | "ignored" | "blocked"; note?: string | null }) =>
    request<DocumentRecord>(`/documents/${id}/review/issues`, { method: "POST", body: JSON.stringify(payload) }),
  reopenReview: (id: string, payload?: { note?: string | null }) =>
    request<DocumentRecord>(`/documents/${id}/review/reopen`, { method: "POST", body: JSON.stringify(payload ?? {}) }),
  toggleFavorite: (id: string) => request<DocumentRecord>(`/documents/${id}/favorite`, { method: "POST" }),
  exportCsvUrl: (params = new URLSearchParams()) => `${API_BASE}/documents/export/csv${params.toString() ? `?${params.toString()}` : ""}`,
  exportExcelUrl: (params = new URLSearchParams()) => `${API_BASE}/documents/export/xlsx${params.toString() ? `?${params.toString()}` : ""}`,
  exportJsonUrl: (id: string) => `${API_BASE}/documents/${id}/export/json`,
  exportTaxInvoiceXmlUrl: (id: string) => `${API_BASE}/documents/${id}/export/tax-invoice-xml`,
  monthlyReportExportUrl: (params: URLSearchParams, format: "xlsx" | "csv") => {
    const query = new URLSearchParams(params);
    query.set("format", format);
    return `${API_BASE}/reports/monthly/export?${query.toString()}`;
  },
  itemMaster: {
    list: (params: URLSearchParams) => request<ItemMasterListResponse>(`/item-master/items?${params.toString()}`, { cache: "no-store" }),
    stats: () => request<ItemMasterStats>("/item-master/stats", { cache: "no-store" }),
    get: (id: string) => request<ItemMasterRecord>(`/item-master/items/${id}`, { cache: "no-store" }),
    create: (payload: CreateItemMasterPayload) =>
      request<ItemMasterRecord>("/item-master/items", { method: "POST", body: JSON.stringify(payload) }),
    update: (id: string, payload: UpdateItemMasterPayload) =>
      request<ItemMasterRecord>(`/item-master/items/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    upload: (file: File) => {
      const data = new FormData();
      data.append("file", file);
      return request<ItemMasterUploadResult>("/item-master/upload", { method: "POST", body: data });
    },
    remove: async (id: string) => {
      const response = await fetch(`${API_BASE}/item-master/items/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("품목을 비활성화하지 못했습니다");
    },
    createAlias: (itemId: string, payload: CreateItemAliasPayload) =>
      request<ItemAliasRecord>(`/item-master/items/${itemId}/aliases`, { method: "POST", body: JSON.stringify(payload) }),
    updateAlias: (aliasId: string, payload: UpdateItemAliasPayload) =>
      request<ItemAliasRecord>(`/item-master/aliases/${aliasId}`, { method: "PATCH", body: JSON.stringify(payload) }),
    removeAlias: async (aliasId: string) => {
      const response = await fetch(`${API_BASE}/item-master/aliases/${aliasId}`, { method: "DELETE" });
      if (!response.ok) throw new Error("별칭을 비활성화하지 못했습니다");
    },
    clear: () => request<{ deleted_items: number; deleted_aliases: number }>("/item-master", { method: "DELETE" }),
  }
};

export function documentFileUrl(fileUrl: string | null | undefined): string {
  if (!fileUrl) return "";
  if (fileUrl.startsWith("/api/uploads/")) return fileUrl;
  if (fileUrl.startsWith("/uploads/")) return API_BASE.startsWith("/") ? `${API_BASE}${fileUrl}` : `${backendOrigin()}${fileUrl}`;
  try {
    const parsed = new URL(fileUrl);
    if (parsed.pathname.startsWith("/uploads/") && API_BASE.startsWith("/")) {
      return `${API_BASE}${parsed.pathname}`;
    }
  } catch {
    return fileUrl;
  }
  return fileUrl;
}

function chunkArray<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }
  return chunks;
}

function backendOrigin(): string {
  try {
    const parsed = new URL(API_BASE);
    return parsed.origin;
  } catch {
    return "";
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
