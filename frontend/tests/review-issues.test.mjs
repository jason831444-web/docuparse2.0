import assert from "node:assert/strict";

import {
  displayWarningsWithoutReviewDuplicates,
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

console.log("review issue display tests passed");
