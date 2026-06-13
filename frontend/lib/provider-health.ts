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
  if (providers.primary_provider_available) {
    const model = providers.ocr_model || providers.paddleocr_vl_model || "PaddleOCR-VL";
    return {
      label: `OCR 정상 · ${model}${device}`,
      detail: `Primary provider: ${providers.primary_provider || "paddleocr_vl"}. Fallback: ${providers.fallback_provider || "PP-OCRv4"}.`,
      tone: "primary",
    };
  }

  const workerModel = providers.ocr_worker_health?.model || providers.ocr_worker_health?.ocr_version || providers.ocr_model || "PP-OCRv4";
  const reason = providers.fallback_reason || providers.paddleocr_vl_init_error || "paddleocr_vl_unavailable";
  return {
    label: `OCR 정상 · ${workerModel}`,
    detail: `AI 문서 파싱 비활성: ${reason}. Fallback provider: ${providers.fallback_provider || "PP-OCRv4"}.`,
    tone: "fallback",
  };
}
