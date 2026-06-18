import assert from "node:assert/strict";

import {
  DEFAULT_UPLOAD_CONCURRENCY,
  RECOMMENDED_MAX_UPLOAD_FILES,
  clearUploadQueue,
  createUploadQueueItems,
  explainUploadError,
  markUploadProcessing,
  markUploadCompleted,
  markUploadFailed,
  markUploadStarted,
  mergeDocumentStatusesIntoQueue,
  nextQueuedUploadIds,
  removeUploadQueueItemsForDocumentIds,
  restoreUploadQueue,
  retryUploadItem,
  runningUploadCount,
  serializeUploadQueue,
} from "../lib/upload-queue.ts";

const files = Array.from({ length: 5 }, (_, index) => ({
  name: index < 2 ? "same-name.txt" : `doc-${index}.txt`,
  size: 100 + index,
  type: "text/plain",
  lastModified: 1000,
}));

const queue = createUploadQueueItems(files, 12345, () => "fixed");
assert.equal(queue.length, 5);
assert.deepEqual(queue.map((item) => item.status), ["selected", "selected", "selected", "selected", "selected"]);
assert.equal(new Set(queue.map((item) => item.id)).size, 5, "duplicate filenames must still get unique queue ids");
assert.deepEqual(nextQueuedUploadIds(queue, DEFAULT_UPLOAD_CONCURRENCY), queue.slice(0, 3).map((item) => item.id));

let running = queue;
for (const id of nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY)) {
  running = markUploadStarted(running, id);
}
assert.equal(runningUploadCount(running), 3);
assert.deepEqual(nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY), []);

running = markUploadFailed(running, queue[1].id, "boom");
assert.equal(running.find((item) => item.id === queue[1].id)?.status, "failed");
assert.deepEqual(nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY), [queue[3].id]);

running = retryUploadItem(running, queue[1].id);
assert.equal(running.find((item) => item.id === queue[1].id)?.status, "waiting_upload");
assert.deepEqual(nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY), [queue[1].id]);

running = markUploadProcessing(running, queue[1].id, {
  id: "doc-queued",
  title: "접수 문서",
  original_filename: "same-name.txt",
  processing_status: "uploaded",
});
assert.equal(running.find((item) => item.id === queue[1].id)?.status, "accepted");
assert.equal(running.find((item) => item.id === queue[1].id)?.documentId, "doc-queued");
assert.deepEqual(nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY), [queue[3].id], "accepted documents must be tracked by document id and must not occupy upload slots");

running = mergeDocumentStatusesIntoQueue(running, [{
  id: "doc-queued",
  title: "접수 문서",
  original_filename: "same-name.txt",
  processing_status: "processing",
}]);
assert.equal(running.find((item) => item.id === queue[1].id)?.status, "processing");

running = markUploadCompleted(running, queue[0].id, {
  id: "doc-1",
  title: "문서 1",
  original_filename: "same-name.txt",
  processing_status: "needs_review",
});
assert.equal(running.find((item) => item.id === queue[0].id)?.status, "needs_review");

running = markUploadCompleted(running, queue[2].id, {
  id: "doc-processing",
  title: "처리 중 문서",
  original_filename: "doc-2.txt",
  processing_status: "processing",
});
assert.equal(running.find((item) => item.id === queue[2].id)?.status, "processing", "server-side processing must not render as completed");

const many = createUploadQueueItems(
  Array.from({ length: RECOMMENDED_MAX_UPLOAD_FILES + 5 }, (_, index) => ({ name: `many-${index}.pdf`, size: index + 1 })),
  999,
  () => "many"
);
assert.equal(many.length, RECOMMENDED_MAX_UPLOAD_FILES);

const persisted = serializeUploadQueue([
  { ...queue[0], status: "selected", updatedAt: 2000 },
  { ...queue[1], status: "accepting", updatedAt: 2000 },
  { ...queue[2], status: "processing", documentId: "doc-processing", documentTitle: "처리 문서", updatedAt: 2000 },
  { ...queue[3], status: "done", documentId: "doc-done", documentTitle: "완료 문서", updatedAt: 2000 },
  { ...queue[4], status: "failed", error: "boom", updatedAt: 2000 },
], 2000);

const restored = restoreUploadQueue(persisted, 2500);
assert.equal(restored.find((item) => item.id === queue[0].id)?.status, "needs_reselect");
assert.equal(restored.find((item) => item.id === queue[1].id)?.status, "interrupted");
assert.equal(restored.find((item) => item.id === queue[2].id)?.status, "processing");
assert.equal(restored.find((item) => item.id === queue[2].id)?.documentId, "doc-processing");
assert.equal(restored.find((item) => item.id === queue[3].id)?.status, "done");
assert.equal(restored.find((item) => item.id === queue[3].id)?.documentId, "doc-done");
assert.equal(restored.find((item) => item.id === queue[4].id)?.status, "failed");
assert.deepEqual(nextQueuedUploadIds(restored, DEFAULT_UPLOAD_CONCURRENCY), [], "restored file metadata must not start upload without a File object");

const stale = restoreUploadQueue({ ...persisted, items: persisted.items.map((item) => ({ ...item, updatedAt: 1 })) }, 1000 * 60 * 60 * 24 * 4);
assert.equal(stale.length, 0);

assert.match(explainUploadError(new Error("Failed to fetch")), /백엔드에 연결하지 못했습니다/);
assert.match(explainUploadError(new Error("The operation was aborted")), /중단/);

const clearable = clearUploadQueue();
assert.deepEqual(clearable.map((item) => item.status), []);

const afterDeletedDocument = removeUploadQueueItemsForDocumentIds([
  { ...queue[0], status: "done", documentId: "doc-deleted" },
  { ...queue[1], status: "processing", documentId: "doc-kept" },
  { ...queue[2], status: "waiting_upload", documentId: null },
], ["doc-deleted"]);
assert.deepEqual(afterDeletedDocument.map((item) => item.documentId), ["doc-kept", null]);

console.log("upload queue tests passed");
