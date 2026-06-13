import assert from "node:assert/strict";

import {
  bboxReviewFlagLabel,
  displayWarningsWithoutReviewDuplicates,
  isBlockingReviewIssue,
  layoutDebugMetadata,
  reviewIssueAmountLines,
  reviewIssueSummary,
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

const layoutDebug = layoutDebugMetadata({
  workflow_metadata: {
    layout_debug: {
      parser_integrated: false,
      candidate_count: 3,
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
assert.equal(layoutDebug?.bbox_candidate_summary?.candidate_count, 3);
assert.equal(layoutDebug?.bbox_candidate_summary?.uncertain_count, 1);
assert.equal(layoutDebug?.bbox_table_candidates?.[0]?.item_name, null);
assert.equal(layoutDebug?.bbox_table_candidates?.[0]?.source_text, "16000 1600C 176000");
assert.deepEqual(layoutDebug?.bbox_table_candidates?.[0]?.review_flags, ["missing_item_name_from_ocr", "row_boundary_uncertain", "untrusted_ocr_amount"]);
assert.equal(bboxReviewFlagLabel("missing_item_name_from_ocr"), "품목명 OCR 없음");
assert.equal(layoutDebugMetadata({ workflow_metadata: null }), null);

console.log("review issue display tests passed");
