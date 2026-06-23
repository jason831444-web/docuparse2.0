"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Clock3, FileUp, Loader2, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ApiRequestError, api } from "@/lib/api";
import {
  DEFAULT_UPLOAD_CONCURRENCY,
  clearUploadQueue,
  createUploadQueueItems,
  explainUploadError,
  markUploadFailed,
  markUploadProcessing,
  markUploadStarted,
  mergeDocumentStatusesIntoQueue,
  nextQueuedUploadIds,
  removeUploadQueueItemsForDocumentIds,
  removeQueuedUploadItem,
  restoreUploadQueue,
  retryUploadItem,
  runningUploadCount,
  serializeUploadQueue,
  UPLOAD_QUEUE_STORAGE_KEY,
  type UploadQueueItem,
  type UploadQueueFileLike,
} from "@/lib/upload-queue";
import { cn } from "@/lib/utils";
import type { DocumentRecord } from "@/types/document";

const MAX_VISIBLE_QUEUE_ITEMS = 200;

const acceptedTypes = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/bmp",
  "image/tiff",
  "application/pdf",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/json",
  "application/xml",
  "text/xml",
  "text/html",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
].join(",");

const acceptedExtensions = [
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".bmp",
  ".tif",
  ".tiff",
  ".pdf",
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".xml",
  ".html",
  ".htm",
  ".docx",
  ".xlsx",
  ".pptx",
  ".doc",
  ".xls",
  ".ppt",
  ".rtf",
  ".odt",
  ".ods",
  ".odp",
  ".epub",
  ".eml",
  ".msg",
].join(",");

export function UploadDropzone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const activeIds = useRef(new Set<string>());
  const [dragging, setDragging] = useState(false);
  const [queue, setQueue] = useState<UploadQueueItem<UploadQueueFileLike>[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const activeCount = useMemo(() => runningUploadCount(queue), [queue]);
  const hasPendingWork = queue.some((item) => ["selected", "waiting_upload", "accepting", "accepted", "queued", "processing"].includes(item.status));
  const visibleQueue = useMemo(() => queue.slice(0, MAX_VISIBLE_QUEUE_ITEMS), [queue]);
  const hiddenQueueCount = Math.max(0, queue.length - visibleQueue.length);

  const uploadQueueItems = useCallback(async (items: UploadQueueItem<File>[]) => {
    if (!items.length) return;
    items.forEach((item) => activeIds.current.add(item.id));
    setQueue((current) => items.reduce((next, item) => markUploadStarted(next, item.id), current));
    try {
      const result = items.length === 1
        ? { items: [{ index: 0, document: await api.upload(items[0].file) }], errors: [] }
        : await api.uploadBatch(items.map((item) => item.file));
      const documentsByIndex = new Map(result.items.map((item) => [item.index, item.document]));
      const errorsByIndex = new Map(result.errors.map((error) => [error.index, error.error]));
      setQueue((current) => items.reduce((next, item, index) => {
        const document = documentsByIndex.get(index);
        if (document) return markUploadProcessing(next, item.id, document);
        return markUploadFailed(next, item.id, errorsByIndex.get(index) || "업로드 요청을 처리하지 못했습니다.");
      }, current));
    } catch (error) {
      const message = explainUploadError(error);
      setQueue((current) => items.reduce((next, item) => markUploadFailed(next, item.id, message), current));
    } finally {
      items.forEach((item) => activeIds.current.delete(item.id));
    }
  }, []);

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const nextItems = createUploadQueueItems(files);
    setQueue((current) => [...current, ...nextItems]);
    toast.success(`${nextItems.length}개 파일을 업로드 대기열에 추가했습니다`);
    if (inputRef.current) inputRef.current.value = "";
  }

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(UPLOAD_QUEUE_STORAGE_KEY);
      setQueue(stored ? restoreUploadQueue(JSON.parse(stored)) : []);
    } catch {
      setQueue([]);
    } finally {
      setHydrated(true);
    }
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(UPLOAD_QUEUE_STORAGE_KEY, JSON.stringify(serializeUploadQueue(queue)));
  }, [hydrated, queue]);

  useEffect(() => {
    if (!hydrated) return;
    const trackedIds = queue
      .filter((item) => item.documentId)
      .map((item) => item.documentId as string);
    if (!trackedIds.length) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const results = await Promise.allSettled(trackedIds.map((id) => api.get(id)));
        if (cancelled) return;
        const documents: DocumentRecord[] = [];
        const deletedIds: string[] = [];
        for (let index = 0; index < results.length; index += 1) {
          const result = results[index];
          if (result.status === "fulfilled") {
            documents.push(result.value);
          } else if (result.reason instanceof ApiRequestError && result.reason.status === 404) {
            deletedIds.push(trackedIds[index]);
          } else {
            return;
          }
        }
        setQueue((current) => removeUploadQueueItemsForDocumentIds(mergeDocumentStatusesIntoQueue(current, documents), deletedIds));
      } catch {
        // Keep the queue visible; the next interval will retry.
      }
    };
    void refresh();
    const interval = window.setInterval(refresh, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [hydrated, queue]);

  useEffect(() => {
    const available = DEFAULT_UPLOAD_CONCURRENCY - activeIds.current.size;
    if (available <= 0) return;
    const nextIds = nextQueuedUploadIds(queue, DEFAULT_UPLOAD_CONCURRENCY)
      .filter((id) => !activeIds.current.has(id))
      .slice(0, available);
    const items = nextIds
      .map((id) => queue.find((candidate) => candidate.id === id))
      .filter((item): item is UploadQueueItem<UploadQueueFileLike> => Boolean(item && item.fileAvailable !== false));
    if (items.length) void uploadQueueItems(items as UploadQueueItem<File>[]);
  }, [queue, uploadQueueItems]);

  useEffect(() => {
    if (!hasPendingWork) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasPendingWork]);

  const statusLabels: Record<UploadQueueItem["status"], string> = {
    selected: "선택됨",
    waiting_upload: "업로드 대기",
    accepting: "접수 중",
    accepted: "접수됨",
    queued: "처리 대기",
    processing: "처리 중",
    done: "준비 완료",
    needs_review: "검토 필요",
    failed: "실패",
    needs_reselect: "파일 재선택 필요",
    interrupted: "중단됨",
  };

  return (
    <div className="grid gap-4">
      <div
        className={cn(
          "flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed bg-white p-8 text-center transition",
          dragging && "border-primary bg-emerald-50"
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          handleFiles(event.dataTransfer.files);
        }}
      >
        <div className="mb-4 grid size-14 place-items-center rounded-md bg-secondary">
          {activeCount ? <Loader2 className="size-7 animate-spin" /> : <FileUp className="size-7 text-primary" />}
        </div>
        <h2 className="text-xl font-semibold">제조업 문서 업로드</h2>
        <p className="mt-2 max-w-lg text-sm text-muted-foreground">
          발주서, 견적서, 거래명세서, 납품서를 여러 개 선택하거나 끌어다 놓으세요. 동시 업로드는 {DEFAULT_UPLOAD_CONCURRENCY}개까지 자동으로 제한됩니다.
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={`${acceptedTypes},${acceptedExtensions}`}
          className="hidden"
          onChange={(event) => handleFiles(event.target.files)}
        />
        <Button className="mt-5" onClick={() => inputRef.current?.click()}>
          {activeCount ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
          파일을 끌어다 놓거나 클릭해서 업로드하세요
        </Button>
        <p className="mt-3 text-xs text-muted-foreground">
          PDF, 이미지, 엑셀, 워드 문서를 지원합니다. 대량 업로드도 대기열에 추가하고 {DEFAULT_UPLOAD_CONCURRENCY}개씩 순차 접수합니다.
        </p>
      </div>

      {queue.length ? (
        <div className="rounded-lg border bg-white">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div>
              <p className="text-sm font-semibold">업로드 대기열</p>
              <p className="text-xs text-muted-foreground">선택한 파일은 즉시 표시됩니다. 서버 접수 후에는 문서 ID 기준으로 상태를 갱신하고, 새로고침 후에도 최근 문서 목록에서 확인할 수 있습니다.</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground">처리 중 {activeCount} / {DEFAULT_UPLOAD_CONCURRENCY}</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  activeIds.current.clear();
                  setQueue(() => clearUploadQueue());
                  toast.success("업로드 대기열을 비웠습니다");
                }}
              >
                대기열 모두 지우기
              </Button>
            </div>
          </div>
          <div className="divide-y">
            {visibleQueue.map((item) => (
              <div key={item.id} className="grid gap-3 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    {item.status === "failed" ? <AlertCircle className="size-4 text-red-600" /> : item.status === "done" || item.status === "needs_review" ? <CheckCircle2 className="size-4 text-emerald-600" /> : ["selected", "waiting_upload", "accepted", "queued"].includes(item.status) ? <Clock3 className="size-4 text-slate-500" /> : <Loader2 className="size-4 animate-spin text-primary" />}
                    <p className="truncate text-sm font-medium">{item.file.name}</p>
                    <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{statusLabels[item.status]}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{Math.max(1, Math.round(item.file.size / 1024)).toLocaleString("ko-KR")} KB</p>
                  {item.error ? <p className="mt-1 text-xs text-red-700">{item.error}</p> : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  {item.documentId ? (
                    <Button asChild size="sm" variant="outline">
                      <Link href={`/documents/${item.documentId}`}>열기</Link>
                    </Button>
                  ) : null}
                  {item.status === "failed" && item.fileAvailable !== false ? (
                    <Button size="sm" variant="outline" onClick={() => setQueue((current) => retryUploadItem(current, item.id))}>
                      <RotateCcw className="size-4" />
                      다시 시도
                    </Button>
                  ) : null}
                  {item.status === "failed" && item.fileAvailable === false ? (
                    <Button size="sm" variant="outline" onClick={() => inputRef.current?.click()}>
                      <FileUp className="size-4" />
                      파일 다시 선택
                    </Button>
                  ) : null}
                  {["selected", "waiting_upload", "needs_reselect", "interrupted"].includes(item.status) ? (
                    <Button size="sm" variant="ghost" onClick={() => setQueue((current) => removeQueuedUploadItem(current, item.id))}>
                      <X className="size-4" />
                      제거
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
            {hiddenQueueCount ? (
              <div className="px-4 py-3 text-center text-xs text-muted-foreground">
                화면 안정성을 위해 먼저 {visibleQueue.length.toLocaleString("ko-KR")}개만 표시합니다. 나머지 {hiddenQueueCount.toLocaleString("ko-KR")}개도 대기열에서 순차 처리됩니다.
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
