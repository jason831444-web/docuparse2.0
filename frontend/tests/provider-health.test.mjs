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

assert.equal(active.label, "VL Reader 정상 · GGUF");
assert.equal(active.tone, "primary");
assert.match(active.detail, /Primary reader: paddleocr_vl_1_6_gguf/);
assert.match(active.detail, /Fallback OCR: paddleocr_ppocrv4/);

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

assert.equal(degraded.label, "VL 대기 · PP-OCRv4 fallback");
assert.equal(degraded.tone, "fallback");
assert.match(degraded.detail, /AI 문서 파싱 비활성/);
assert.match(degraded.detail, /VL 후보: GGUF/);
assert.match(degraded.detail, /VL 후보 비활성/);
assert.match(degraded.detail, /paddleocr_vl_gguf_disabled/);

const smokePending = providerHealthLabel({
  providers: {
    ocr_engine: "PP-OCRv4",
    ocr_model: "PP-OCRv4",
    primary_provider: "paddleocr_vl_1_6_gguf",
    primary_provider_available: false,
    fallback_provider: "paddleocr_ppocrv4",
    fallback_reason: "paddleocr_vl_gguf_smoke_not_run",
    paddleocr_vl_gguf: {
      status: "llama_server_ready",
      error: "paddleocr_vl_gguf_smoke_not_run",
    },
    ocr_worker_health: {
      model: "PP-OCRv4",
    },
  },
});

assert.equal(smokePending.label, "VL 대기 · PP-OCRv4 fallback");
assert.equal(smokePending.tone, "fallback");
assert.match(smokePending.detail, /서버 smoke 검증 전/);
assert.match(smokePending.detail, /paddleocr_vl_gguf_smoke_not_run/);

const candidateNotIntegrated = providerHealthLabel({
  providers: {
    ocr_engine: "PP-OCRv4",
    ocr_model: "PP-OCRv4",
    primary_provider: "paddleocr_vl_1_6_gguf",
    primary_provider_available: false,
    primary_provider_candidate_available: true,
    primary_reader_available: true,
    primary_reader_mode: "candidate_only_validated_by_parser",
    fallback_provider: "paddleocr_ppocrv4",
    fallback_reason: "paddleocr_vl_gguf_confirmed_extraction_not_enabled",
    paddleocr_vl_gguf: {
      status: "primary_reader_candidate",
      error: "paddleocr_vl_gguf_confirmed_extraction_not_enabled",
      candidate_available: true,
      primary_reader_available: true,
      in_process_enabled: false,
    },
    ocr_worker_health: {
      model: "PP-OCRv4",
    },
  },
});

assert.equal(candidateNotIntegrated.label, "VL Reader 정상 · GGUF");
assert.equal(candidateNotIntegrated.tone, "primary");
assert.match(candidateNotIntegrated.detail, /Confirmed business-data values still pass parser\/validation/);
assert.match(candidateNotIntegrated.detail, /Fallback OCR: paddleocr_ppocrv4/);

const remoteWorker = providerHealthLabel({
  providers: {
    ocr_engine: "PaddleOCR-VL GGUF",
    ocr_model: "PaddleOCR-VL-1.6-GGUF.gguf",
    primary_provider: "paddleocr_vl_1_6_gguf",
    primary_provider_available: true,
    primary_reader_available: true,
    fallback_provider: "paddleocr_ppocrv4",
    paddleocr_vl_gguf: {
      status: "remote_primary_reader_candidate",
      candidate_available: true,
      primary_reader_available: true,
      worker_location: "remote",
      worker_transport: "multipart_upload",
      worker_url_host: "remote-gateway",
      model_file: "PaddleOCR-VL-1.6-GGUF.gguf",
    },
  },
});

assert.equal(remoteWorker.label, "VL Reader 정상 · Remote GPU · Multipart Upload");
assert.match(remoteWorker.detail, /remote-gateway/);

const loading = providerHealthLabel(null);
assert.equal(loading.label, "OCR 상태 확인 중");
assert.equal(loading.tone, "fallback");

console.log("provider health label tests passed");
