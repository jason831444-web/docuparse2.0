import assert from "node:assert/strict";

import {
  getDocumentTypeLabel,
  getErpReadinessStatus,
  getErpReadinessSummary,
  getPrimaryCompanyName,
} from "../lib/utils.ts";
import { documentFileUrl } from "../lib/api.ts";

const baseDocument = {
  id: "doc-1",
  original_filename: "document.pdf",
  stored_file_path: "",
  mime_type: "application/pdf",
  source_file_type: "pdf",
  extraction_method: null,
  ingestion_metadata: null,
  document_type: "general_document",
  title: null,
  raw_text: null,
  extracted_date: null,
  extracted_amount: null,
  subtotal: null,
  tax: null,
  currency: null,
  merchant_name: null,
  vendor_name: null,
  customer_name: null,
  document_number: null,
  issue_date: null,
  due_date: null,
  line_items: [],
  low_confidence_fields: [],
  category: "general_document",
  tags: [],
  summary: null,
  confidence_score: null,
  ai_document_type: null,
  ai_confidence_score: null,
  ai_extraction_notes: null,
  review_required: false,
  extraction_provider: null,
  refinement_provider: null,
  provider_chain: null,
  merge_strategy: null,
  field_sources: null,
  workflow_summary: null,
  action_items: [],
  warnings: [],
  key_dates: [],
  urgency_level: null,
  follow_up_required: false,
  workflow_metadata: null,
  is_favorite: false,
  processing_status: "ready",
  preview_image_path: null,
  processing_error: null,
  created_at: "2026-01-01T00:00:00",
  updated_at: "2026-01-01T00:00:00",
  file_url: "/api/file",
};

const internalTransfer = {
  ...baseDocument,
  document_number: "TRF-2026-0922-002",
  line_items: [{ item_name: "M8 육각 볼트", quantity: 500 }],
  workflow_metadata: {
    taxonomy: {
      document_subtype: "internal_transfer",
      document_profiles: ["inventory_movement_document", "no_price_document"],
      amount_required: false,
    },
  },
};

assert.equal(getDocumentTypeLabel(internalTransfer), "내부 이동서");
assert.equal(getPrimaryCompanyName({ customer_name: "고객사", vendor_name: "공급사" }), "고객사");
assert.equal(getErpReadinessStatus(internalTransfer).title, "입력 준비 완료");
assert.match(getErpReadinessSummary(internalTransfer), /금액 없는 문서/);

const needsReview = {
  ...baseDocument,
  review_required: true,
  processing_status: "needs_review",
  line_items: [{ item_name: "품목", quantity: null }],
  workflow_metadata: {
    normalized_review_issues: [
      {
        code: "missing_quantity",
        message_ko: "1번째 품목 수량이 비어 있습니다.",
        field: "line_items.quantity",
        item_index: 0,
        severity: "warning",
      },
    ],
  },
};

assert.equal(getErpReadinessStatus(needsReview).title, "검토 필요");
assert.match(getErpReadinessSummary(needsReview), /1개 검토 항목/);

const bboxCandidate = {
  ...baseDocument,
  line_items: [{ item_name: "S45C PIN", quantity: null }],
  workflow_metadata: {
    layout_debug: {
      parser_integrated: false,
      bbox_candidate_summary: {
        candidate_count: 1,
        uncertain_count: 1,
        review_flags: ["missing_item_name_from_ocr"],
      },
      bbox_table_candidates: [
        {
          item_name: null,
          source_text: "16000 1600C 176000",
          review_flags: ["missing_item_name_from_ocr"],
        },
      ],
    },
  },
};

assert.equal(getErpReadinessStatus(bboxCandidate).title, "내보내기 주의");
assert.match(getErpReadinessSummary(bboxCandidate), /OCR 위치 기반 후보 1건/);
assert.equal(documentFileUrl("http://localhost:8001/uploads/source.pdf"), "http://localhost:8001/uploads/source.pdf");
assert.equal(documentFileUrl("/uploads/source.pdf"), "http://localhost:8001/uploads/source.pdf");

console.log("document detail readiness helper tests passed");
