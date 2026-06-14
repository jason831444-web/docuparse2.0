import assert from "node:assert/strict";

import { providerHealthLabel } from "../lib/provider-health.ts";

const active = providerHealthLabel({
  providers: {
    ocr_engine: "PaddleOCR-VL GGUF",
    ocr_model: "PaddleOCR-VL-1.6-GGUF.gguf",
    primary_provider: "paddleocr_vl_1_6_gguf",
    primary_provider_available: true,
    fallback_provider: "paddleocr_ppocrv4",
    device: "cpu",
  },
});

assert.equal(active.label, "OCR 정상 · PaddleOCR-VL-1.6-GGUF.gguf · CPU");
assert.equal(active.tone, "primary");
assert.match(active.detail, /Primary provider: paddleocr_vl_1_6_gguf/);

const degraded = providerHealthLabel({
  providers: {
    ocr_engine: "PP-OCRv4",
    ocr_model: "PP-OCRv4",
    primary_provider: "paddleocr_vl_1_6_gguf",
    primary_provider_available: false,
    fallback_provider: "paddleocr_ppocrv4",
    fallback_reason: "paddleocr_vl_gguf_disabled",
    paddleocr_vl_gguf: {
      status: "disabled",
      error: "paddleocr_vl_gguf_disabled",
    },
    ocr_worker_health: {
      model: "PP-OCRv4",
      ocr_version: "PP-OCRv4",
      device: "cpu",
    },
  },
});

assert.equal(degraded.label, "OCR 정상 · PP-OCRv4");
assert.equal(degraded.tone, "fallback");
assert.match(degraded.detail, /AI 문서 파싱 비활성/);
assert.match(degraded.detail, /VL 후보: GGUF/);
assert.match(degraded.detail, /paddleocr_vl_gguf_disabled/);

const loading = providerHealthLabel(null);
assert.equal(loading.label, "OCR 상태 확인 중");
assert.equal(loading.tone, "fallback");

console.log("provider health label tests passed");
