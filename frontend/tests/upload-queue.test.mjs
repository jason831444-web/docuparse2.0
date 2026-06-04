import assert from "node:assert/strict";

import {
  DEFAULT_UPLOAD_CONCURRENCY,
  RECOMMENDED_MAX_UPLOAD_FILES,
  createUploadQueueItems,
  markUploadCompleted,
  markUploadFailed,
  markUploadStarted,
  nextQueuedUploadIds,
  retryUploadItem,
  runningUploadCount,
} from "../lib/upload-queue.ts";

const files = Array.from({ length: 5 }, (_, index) => ({
  name: index < 2 ? "same-name.txt" : `doc-${index}.txt`,
  size: 100 + index,
  type: "text/plain",
  lastModified: 1000,
}));

const queue = createUploadQueueItems(files, 12345, () => "fixed");
assert.equal(queue.length, 5);
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
assert.equal(running.find((item) => item.id === queue[1].id)?.status, "queued");
assert.deepEqual(nextQueuedUploadIds(running, DEFAULT_UPLOAD_CONCURRENCY), [queue[1].id]);

running = markUploadCompleted(running, queue[0].id, {
  id: "doc-1",
  title: "문서 1",
  original_filename: "same-name.txt",
  processing_status: "needs_review",
});
assert.equal(running.find((item) => item.id === queue[0].id)?.status, "needs_review");

const many = createUploadQueueItems(
  Array.from({ length: RECOMMENDED_MAX_UPLOAD_FILES + 5 }, (_, index) => ({ name: `many-${index}.pdf`, size: index + 1 })),
  999,
  () => "many"
);
assert.equal(many.length, RECOMMENDED_MAX_UPLOAD_FILES);

console.log("upload queue tests passed");
