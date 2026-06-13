import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  formatCalendarEventTitle,
  formatCalendarItemTitle,
  getDocumentScheduleDate,
  preferredCalendarItems,
} from "../lib/utils.ts";

const purchaseOrder = {
  document_type: "purchase_order",
  category: "purchase_order",
  document_number: "PO-2026-0911-104",
  vendor_name: "대한정밀부품",
  customer_name: "한빛제조",
  issue_date: "2026-09-11",
  due_date: "2026-09-25",
  workflow_metadata: {
    business_fields: {
      requested_delivery_date: "2026-09-24",
    },
  },
};

assert.deepEqual(getDocumentScheduleDate(purchaseOrder), {
  date: "2026-09-24",
  role: "requested_delivery_date",
  label: "납기요청일",
  fallback: false,
});
assert.equal(
  formatCalendarEventTitle(purchaseOrder),
  "한빛제조, 발주서, 2026-09-24 (PO-2026-0911-104)"
);

const fallbackOnly = {
  document_type: "invoice",
  category: "invoice",
  document_number: "INV-2026-0001",
  vendor_name: "공급사",
  customer_name: "고객사",
  issue_date: "2026-10-01",
  workflow_metadata: {},
};
assert.equal(getDocumentScheduleDate(fallbackOnly)?.role, "issue_date");
assert.equal(getDocumentScheduleDate(fallbackOnly)?.fallback, true);
assert.equal(formatCalendarEventTitle({ ...fallbackOnly, category: null }), "고객사, 인보이스/세금계산서, 2026-10-01 (INV-2026-0001)");

const issueEvent = {
  id: "doc-1:issue_date:2026-09-11",
  document_id: "doc-1",
  document_title: null,
  document_number: "PO-2026-0911-104",
  original_filename: "po.pdf",
  document_type: "purchase_order",
  vendor_name: "대한정밀부품",
  customer_name: "한빛제조",
  date: "2026-09-11",
  date_role: "issue_date",
  date_label: "발행일",
  status: "예정",
  days_from_today: 1,
  processing_status: "ready",
  review_required: false,
  action_url: "/documents/doc-1",
};
const dueEvent = {
  ...issueEvent,
  id: "doc-1:due_date:2026-09-25",
  date: "2026-09-25",
  date_role: "due_date",
  date_label: "납기일",
};
assert.deepEqual(preferredCalendarItems([issueEvent, dueEvent]).map((item) => item.id), [dueEvent.id]);
assert.equal(formatCalendarItemTitle(dueEvent), "한빛제조, 발주서, 2026-09-25 (PO-2026-0911-104)");

const dashboardSource = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
assert.equal(dashboardSource.includes("UploadDropzone"), false, "dashboard must not render the upload queue as a primary section");
assert.match(dashboardSource, /납기 캘린더/);

console.log("dashboard calendar helper tests passed");
