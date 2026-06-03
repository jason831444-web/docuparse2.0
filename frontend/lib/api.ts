import type { ActivitySummary, AppNotification, DocumentListResponse, DocumentRecord, DocumentStats, DocumentUpdate, FolderSummary } from "@/types/document";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail ?? "요청에 실패했습니다");
  }
  return response.json() as Promise<T>;
}

export const api = {
  stats: () => request<DocumentStats>("/documents/stats", { cache: "no-store" }),
  activity: () => request<ActivitySummary>("/documents/activity", { cache: "no-store" }),
  list: (params: URLSearchParams) => request<DocumentListResponse>(`/documents?${params.toString()}`, { cache: "no-store" }),
  categories: () => request<FolderSummary[]>("/documents/categories", { cache: "no-store" }),
  notifications: () => request<AppNotification[]>("/documents/notifications", { cache: "no-store" }),
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
  bulkDelete: async (ids: string[]) =>
    request<{ deleted: number }>("/documents/bulk/delete", { method: "POST", body: JSON.stringify({ ids }) }),
  bulkDownload: async (ids: string[]) => {
    const response = await fetch(`${API_BASE}/documents/bulk/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!response.ok) throw new Error("선택한 파일을 다운로드하지 못했습니다");
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "docuparse-originals.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },
  reprocess: (id: string) => request<DocumentRecord>(`/documents/${id}/reprocess`, { method: "POST" }),
  confirm: (id: string) => request<DocumentRecord>(`/documents/${id}/confirm`, { method: "POST" }),
  markNeedsReview: (id: string) => request<DocumentRecord>(`/documents/${id}/needs-review`, { method: "POST" }),
  toggleFavorite: (id: string) => request<DocumentRecord>(`/documents/${id}/favorite`, { method: "POST" }),
  exportCsvUrl: () => `${API_BASE}/documents/export/csv`,
  exportExcelUrl: () => `${API_BASE}/documents/export/xlsx`,
  exportJsonUrl: (id: string) => `${API_BASE}/documents/${id}/export/json`
};
