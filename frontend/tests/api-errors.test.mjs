import assert from "node:assert/strict";

import { humanReadableApiError } from "../lib/api.ts";

const koreanPayload = {
  ok: false,
  error_code: "approval_blocked_by_review_issues",
  message_ko: "아직 해결되지 않은 검토 항목이 있어 확정할 수 없습니다.",
  action_ko: "문서 검토 영역에서 값을 수정한 뒤 ‘해결’을 누르거나, 원본 확인 결과 업무상 문제 없으면 ‘무시’를 선택하세요.",
  blocking_details: [
    {
      message_ko: "1번째 품목의 내부 품목코드 후보를 선택해야 합니다.",
      field_label_ko: "내부 품목코드",
      item_label_ko: "1번째 품목",
      action_ko: "원본과 품목 후보를 비교해 맞는 내부 품목코드를 선택한 뒤 해결로 표시하세요.",
    },
  ],
};

const koreanMessage = humanReadableApiError(koreanPayload);
assert.match(koreanMessage, /아직 해결되지 않은 검토 항목/);
assert.match(koreanMessage, /1번째 품목의 내부 품목코드 후보/);
assert.match(koreanMessage, /조치: 원본과 품목 후보/);
assert.doesNotMatch(koreanMessage, /approval_blocked_by_review_issues/);
assert.doesNotMatch(koreanMessage, /internal_item_ambiguous/);

const legacyPayload = {
  message: "Approval blocked by unresolved review issues.",
  ok: false,
  blocking: ["unresolved:internal_item_unmatched:line_items.internal_item_code:1"],
};

const legacyMessage = humanReadableApiError(legacyPayload);
assert.match(legacyMessage, /아직 해결되지 않은 검토 항목/);
assert.match(legacyMessage, /2번째 품목/);
assert.match(legacyMessage, /사내 품목마스터/);
assert.doesNotMatch(legacyMessage, /Approval blocked/);
