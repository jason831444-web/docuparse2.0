import assert from "node:assert/strict";

import {
  bboxReviewFlagLabel,
  documentDisplayTitle,
  displayWarningsWithoutReviewDuplicates,
  isBlockingReviewIssue,
  layoutDebugMetadata,
  normalizedReviewIssues,
  requiresReviewExportConfirmation,
  reviewIssueAmountLines,
  reviewIssueProgressCounts,
  reviewIssuesForLineItem,
  reviewIssueSummary,
  reviewIssueSummaryItems,
  titleCaseLabel,
  vlCandidateIssueLabel,
  vlCandidateMetadata,
} from "../lib/utils.ts";

const amountIssue = {
  code: "amount_mismatch",
  message_ko: "문서 총액 500,000원과 품목 합계 495,000원이 일치하지 않습니다. 차이 5,000원.",
  field: "total_amount",
  severity: "warning",
  document_total: "500000",
  line_total_sum: "495000",
  difference: "5000",
  currency: "KRW",
};

assert.equal(reviewIssueSummary(amountIssue), "문서 총액과 품목 합계 불일치");
assert.deepEqual(reviewIssueAmountLines(amountIssue), [
  "문서 총액: ₩500,000",
  "품목 합계: ₩495,000",
  "차이: ₩5,000",
]);

assert.deepEqual(
  displayWarningsWithoutReviewDuplicates(
    [
      "문서 총액 500,000원과 품목 합계 495,000원이 일치하지 않습니다. 차이 5,000원.",
      "문서 합계금액과 품목 합계금액이 일치하지 않습니다.",
      "다른 처리 경고",
    ],
    [amountIssue]
  ),
  ["다른 처리 경고"]
);

assert.equal(isBlockingReviewIssue({
  code: "item_code_name_conflict",
  message_ko: "품목명과 품목코드 매칭이 충돌합니다.",
  field: "line_items.internal_item_code",
  severity: "warning",
}), true);

assert.equal(reviewIssueSummary({
  code: "amount_direction_requires_review",
  message_ko: "반품/차감 문서는 금액의 차감 방향과 원문서 반영 방식을 확인해야 합니다.",
  field: "total_amount",
  severity: "warning",
}), "반품/차감 금액 방향 확인");

assert.equal(reviewIssueSummary({
  code: "statement_balance_summary_requires_review",
  message_ko: "거래명세서에 전월이월/입금액/미수잔액 등 정산 요약이 포함되어 있어 품목 합계와 잔액 구분을 확인해야 합니다.",
  field: "statement_summary",
  severity: "warning",
}), "정산 요약 확인");

assert.equal(isBlockingReviewIssue({
  code: "amount_direction_requires_review",
  message_ko: "반품/차감 문서는 금액의 차감 방향과 원문서 반영 방식을 확인해야 합니다.",
  field: "total_amount",
  severity: "warning",
}), true);

const layoutDebug = layoutDebugMetadata({
  workflow_metadata: {
    layout_debug: {
      parser_integrated: false,
      reconstructed_candidate_count: 3,
      candidate_count: 1,
      uncertain_count: 1,
      bbox_review_flags: ["missing_item_name_from_ocr"],
      bbox_table_candidates: [
        {
          row_index: 3,
          item_name: null,
          source_text: "16000 1600C 176000",
          quantity: 1000,
          unit: "SET",
          line_total: 176000,
          confidence: 0.62,
          review_flags: ["missing_item_name_from_ocr", "row_boundary_uncertain", "untrusted_ocr_amount"],
          source: "bbox_table_reconstructor",
        },
      ],
    },
  },
});

assert.equal(layoutDebug?.parser_integrated, false);
assert.equal(layoutDebug?.bbox_candidate_summary?.candidate_count, 1);
assert.equal(layoutDebug?.bbox_candidate_summary?.uncertain_count, 1);
assert.equal(layoutDebug?.bbox_table_candidates?.[0]?.item_name, null);
assert.equal(layoutDebug?.bbox_table_candidates?.[0]?.source_text, "16000 1600C 176000");
assert.deepEqual(layoutDebug?.bbox_table_candidates?.[0]?.review_flags, ["missing_item_name_from_ocr", "row_boundary_uncertain", "untrusted_ocr_amount"]);
assert.equal(bboxReviewFlagLabel("missing_item_name_from_ocr"), "품목명 OCR 없음");
assert.equal(layoutDebugMetadata({ workflow_metadata: null }), null);
const vlMetadata = vlCandidateMetadata({
  workflow_metadata: {
    vl_candidates: [
      {
        provider: "paddleocr_vl_1_6_gguf",
        candidate_only: true,
        parser_integrated: false,
        provider_available_candidate: false,
        validation_severity: "warn",
        issue_codes: ["vl_candidate_missing_document_total"],
        issue_details: [
          {
            code: "vl_candidate_missing_document_total",
            expected_value: "418,000",
            message: "The source PDF total is missing in the VL output.",
          },
        ],
        text_preview: "FAX-PO-2026-0921 ...",
      },
    ],
    vl_candidate_summary: {
      candidate_count: 1,
      warning_count: 1,
      failure_count: 0,
      issue_codes: ["vl_candidate_missing_document_total"],
      provider: "paddleocr_vl_1_6_gguf",
      provider_available_candidate: false,
    },
  },
});
assert.equal(vlMetadata?.vl_candidate_summary?.candidate_count, 1);
assert.equal(vlMetadata?.vl_candidate_summary?.provider_available_candidate, false);
assert.equal(vlMetadata?.vl_candidates?.[0]?.candidate_only, true);
assert.equal(vlMetadata?.vl_candidates?.[0]?.parser_integrated, false);
assert.equal(vlMetadata?.vl_candidates?.[0]?.issue_details?.[0]?.expected_value, "418,000");
assert.equal(vlCandidateIssueLabel("vl_candidate_missing_document_total"), "문서 합계 누락");
assert.equal(vlCandidateIssueLabel("vl_candidate_missing_row_anchor"), "품목 행 누락");
assert.equal(vlCandidateIssueLabel("vl_candidate_missing_row_fragment"), "품목 행 텍스트 불완전");
assert.equal(vlCandidateIssueLabel("vl_candidate_missing_row_cell"), "품목 행 값 누락");
assert.equal(vlCandidateIssueLabel("vl_candidate_missing_expected_pdf_value"), "원본 핵심 값 누락");
assert.equal(vlCandidateMetadata({ workflow_metadata: null }), null);
assert.equal(titleCaseLabel("credit_note"), "차감/크레딧 문서");
assert.equal(titleCaseLabel("internal_transfer"), "내부 이동서");
assert.equal(titleCaseLabel("return_note"), "반품 문서");
assert.deepEqual(reviewIssueProgressCounts({ issues: [] }, 3), { open: 3, resolved: 0 });
assert.deepEqual(reviewIssueProgressCounts({ issues: [
  { key: "a", status: "open" },
  { key: "b", status: "resolved" },
  { key: "c", status: "ignored" },
] }, 9), { open: 1, resolved: 2 });
assert.deepEqual(reviewIssueSummaryItems([
  { code: "missing_quantity", message_ko: "1번째 품목 수량이 비어 있습니다.", field: "line_items.quantity", item_index: 0 },
  { code: "missing_quantity", message_ko: "2번째 품목 수량이 비어 있습니다.", field: "line_items.quantity", item_index: 1 },
  amountIssue,
]), ["수량 누락 ×2", "문서 총액과 품목 합계 불일치"]);
const reviewTableIssues = [
  { code: "amount_mismatch", message_ko: "문서 총액과 품목 합계가 맞지 않습니다.", field: "total_amount" },
  { code: "missing_quantity", message_ko: "2번째 품목 수량이 비어 있습니다.", field: "line_items.quantity", item_index: 1 },
];
assert.deepEqual(reviewIssuesForLineItem(reviewTableIssues, 0).map((issue) => issue.code), ["amount_mismatch"]);
assert.deepEqual(reviewIssuesForLineItem(reviewTableIssues, 1).map((issue) => issue.code), ["missing_quantity"]);
assert.equal(requiresReviewExportConfirmation({ review_required: true, processing_status: "ready" }), true);
assert.equal(requiresReviewExportConfirmation({ review_required: false, processing_status: "needs_review" }), true);
assert.equal(requiresReviewExportConfirmation({ review_required: false, processing_status: "confirmed" }), false);
assert.equal(requiresReviewExportConfirmation({ review_required: true, processing_status: "processing" }), false);
assert.equal(requiresReviewExportConfirmation({ review_required: true, processing_status: "queued" }), false);
assert.equal(requiresReviewExportConfirmation({ review_required: true, processing_status: "uploaded" }), false);
assert.equal(documentDisplayTitle({
  document_type: "delivery_note",
  customer_name: "오성테크",
  due_date: "2026-09-14",
  document_number: "DN-2026-0914-2F",
  line_items: [{ document_item_code: "BRG-H-100" }],
}), "오성테크, 납품서, 2026-09-14, BRG-H-100");
assert.equal(documentDisplayTitle({
  document_number: "PO-2026-0801-101",
  original_filename: "po.pdf",
}), "PO-2026-0801-101");

const noPriceIssues = normalizedReviewIssues({
  workflow_metadata: {
    taxonomy: {
      document_profiles: ["inventory_movement_document", "no_price_document"],
      amount_required: false,
    },
    normalized_review_issues: [
      {
        code: "missing_price_or_total",
        message_ko: "1번째 품목의 단가 또는 합계금액을 확인해야 합니다.",
        field: "line_items.line_total",
        item_index: 0,
        severity: "warning",
      },
    ],
  },
  review_required: true,
});

assert.equal(noPriceIssues[0].severity, "info");
assert.equal(noPriceIssues[0].message_ko, "이 문서 유형에서는 금액 정보가 없을 수 있습니다.");
assert.equal(isBlockingReviewIssue(noPriceIssues[0]), false);

console.log("review issue display tests passed");
