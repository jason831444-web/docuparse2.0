import type { ProviderHealth } from "@/types/document";

export function providerHealthLabel(health: ProviderHealth | null): { label: string; detail: string; tone: "primary" | "fallback" } {
  const providers = health?.providers;
  if (!providers) {
    return {
      label: "OCR 상태 확인 중",
      detail: "OCR provider health를 불러오는 중입니다.",
      tone: "fallback",
    };
  }

  const device = providers.device ? ` · ${providers.device.toUpperCase()}` : "";
  const isGgufPrimary = providers.primary_provider === "paddleocr_vl_1_6_gguf";
  const candidateReady = providers.primary_provider_candidate_available || providers.paddleocr_vl_gguf?.candidate_available;
  const primaryReaderReady = providers.primary_reader_available || providers.paddleocr_vl_gguf?.primary_reader_available;
  const vlModel = providers.paddleocr_vl_gguf?.model_file || providers.ocr_model || "PaddleOCR-VL-1.6-GGUF";

  if (isGgufPrimary && (providers.primary_provider_available || primaryReaderReady)) {
    return {
      label: `VL Reader 정상 · GGUF${device}`,
      detail: `Primary reader: ${providers.primary_provider}. Model: ${vlModel}. Confirmed business-data values still pass parser/validation. Fallback OCR: ${providers.fallback_provider || "PP-OCRv4"}.`,
      tone: "primary",
    };
  }

  if (providers.primary_provider_available) {
    const model = providers.ocr_model || providers.paddleocr_vl_model || "PaddleOCR-VL";
    return {
      label: `OCR 정상 · ${model}${device}`,
      detail: `Primary provider: ${providers.primary_provider || "paddleocr_vl"}. Fallback: ${providers.fallback_provider || "PP-OCRv4"}.`,
      tone: "primary",
    };
  }

  const workerModel = providers.ocr_worker_health?.model || providers.ocr_worker_health?.ocr_version || providers.ocr_model || "PP-OCRv4";
  const reason =
    providers.fallback_reason ||
    providers.paddleocr_vl_gguf?.error ||
    providers.paddleocr_vl_gguf?.status ||
    providers.paddleocr_vl_init_error ||
    "paddleocr_vl_unavailable";
  const candidate = isGgufPrimary ? " · VL 후보: GGUF" : "";
  const reasonLabel = providerFallbackReasonLabel(String(reason));
  const statusPrefix = candidateReady
      ? "AI 문서 파싱 후보 검증됨 · 운영 연동 대기"
      : "AI 문서 파싱 비활성";
  return {
    label: isGgufPrimary ? `VL 대기 · ${workerModel} fallback` : `OCR 정상 · ${workerModel}`,
    detail: `${statusPrefix}${candidate}: ${reasonLabel} (${reason}). Fallback provider: ${providers.fallback_provider || "PP-OCRv4"}.`,
    tone: "fallback",
  };
}

export function providerFallbackReasonLabel(reason: string): string {
  if (reason.includes("smoke_not_run")) {
    return "서버 smoke 검증 전";
  }
  if (reason.includes("smoke_failed")) {
    return "서버 smoke 검증 실패";
  }
  if (reason.includes("llama_server_unreachable")) {
    return "VL worker 연결 불가";
  }
  if (reason.includes("model_missing")) {
    return "VL 모델 파일 없음";
  }
  if (reason.includes("in_process")) {
    return "backend 직접 실행 차단";
  }
  if (reason.includes("confirmed_extraction_not_enabled")) {
    return "확정 추출은 parser 검증 대기";
  }
  if (reason.includes("disabled")) {
    return "VL 후보 비활성";
  }
  if (reason.includes("memory_blocked")) {
    return "현재 서버 메모리 한계";
  }
  return "VL 후보 사용 불가";
}
