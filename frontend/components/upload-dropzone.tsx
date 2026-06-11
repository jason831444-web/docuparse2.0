"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Clock3, FileUp, Loader2, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  DEFAULT_UPLOAD_CONCURRENCY,
  RECOMMENDED_MAX_UPLOAD_FILES,
  clearUploadQueue,
  createUploadQueueItems,
  explainUploadError,
  markUploadCompleted,
  markUploadFailed,
  markUploadProcessing,
  markUploadStarted,
  nextQueuedUploadIds,
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
  const hasPendingWork = queue.some((item) => item.status === "queued" || item.status === "uploading" || item.status === "processing");

  const waitForCompletion = useCallback(async (document: DocumentRecord) => {
    if (!["uploaded", "queued", "processing"].includes(document.processing_status)) return document;
    let latest = document;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      latest = await api.get(document.id);
      if (!["uploaded", "queued", "processing"].includes(latest.processing_status)) return latest;
    }
    return latest;
  }, []);

  const uploadQueueItem = useCallback(async (item: UploadQueueItem<File>) => {
    activeIds.current.add(item.id);
    setQueue((current) => markUploadStarted(current, item.id));
    try {
      const uploaded = await api.upload(item.file);
      setQueue((current) => markUploadProcessing(current, item.id, uploaded));
      const document = await waitForCompletion(uploaded);
      setQueue((current) => markUploadCompleted(current, item.id, document));
    } catch (error) {
      setQueue((current) => markUploadFailed(current, item.id, explainUploadError(error)));
    } finally {
      activeIds.current.delete(item.id);
    }
  }, [waitForCompletion]);

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
    queue
      .filter((item) => item.status === "processing" && item.documentId && !activeIds.current.has(item.id))
      .forEach((item) => {
        activeIds.current.add(item.id);
        void (async () => {
          try {
            const latest = await api.get(item.documentId as string);
            const document = await waitForCompletion(latest);
            setQueue((current) => markUploadCompleted(current, item.id, document));
          } catch (error) {
            setQueue((current) => markUploadFailed(current, item.id, error instanceof Error ? error.message : "처리 상태 확인에 실패했습니다"));
          } finally {
            activeIds.current.delete(item.id);
          }
        })();
      });
  }, [hydrated, queue, waitForCompletion]);

  useEffect(() => {
    const available = DEFAULT_UPLOAD_CONCURRENCY - activeIds.current.size;
    if (available <= 0) return;
    const nextIds = nextQueuedUploadIds(queue, DEFAULT_UPLOAD_CONCURRENCY)
      .filter((id) => !activeIds.current.has(id))
      .slice(0, available);
    nextIds.forEach((id) => {
      const item = queue.find((candidate) => candidate.id === id);
      if (item && item.fileAvailable !== false) void uploadQueueItem(item as UploadQueueItem<File>);
    });
  }, [queue, uploadQueueItem]);

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
    queued: "대기 중",
    uploading: "업로드 중",
    processing: "처리 중",
    done: "완료",
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
          PDF, 이미지, 엑셀, 워드 문서를 지원합니다. 한 번에 최대 {RECOMMENDED_MAX_UPLOAD_FILES}개까지 추가할 수 있습니다.
        </p>
      </div>

      {queue.length ? (
        <div className="rounded-lg border bg-white">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div>
              <p className="text-sm font-semibold">업로드 대기열</p>
              <p className="text-xs text-muted-foreground">일부 파일이 실패해도 나머지 파일은 계속 처리됩니다. 새로고침 전 서버에 올라간 문서는 자동으로 상태 확인을 재개합니다.</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground">처리 중 {activeCount} / {DEFAULT_UPLOAD_CONCURRENCY}</span>
              <Button size="sm" variant="ghost" onClick={() => setQueue((current) => clearUploadQueue(current))}>
                대기열 모두 지우기
              </Button>
            </div>
          </div>
          <div className="divide-y">
            {queue.map((item) => (
              <div key={item.id} className="grid gap-3 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    {item.status === "failed" ? <AlertCircle className="size-4 text-red-600" /> : item.status === "done" || item.status === "needs_review" ? <CheckCircle2 className="size-4 text-emerald-600" /> : item.status === "queued" ? <Clock3 className="size-4 text-slate-500" /> : <Loader2 className="size-4 animate-spin text-primary" />}
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
                  {["queued", "needs_reselect", "interrupted"].includes(item.status) ? (
                    <Button size="sm" variant="ghost" onClick={() => setQueue((current) => removeQueuedUploadItem(current, item.id))}>
                      <X className="size-4" />
                      제거
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
