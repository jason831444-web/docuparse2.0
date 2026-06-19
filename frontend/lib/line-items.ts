import type { ManufacturingLineItem } from "@/types/document";

export const numericLineItemFields = new Set<string>([
  "quantity",
  "received_quantity",
  "accepted_quantity",
  "defective_quantity",
  "unit_price",
  "supply_amount",
  "tax_amount",
  "line_total",
]);

export const lineItemFieldLabels: Record<string, string> = {
  item_name: "품목명",
  item_code: "문서 품목코드",
  document_item_code: "문서 품목코드",
  internal_item_code: "내부 품목코드",
  specification: "규격",
  lot_code: "Lot/Code",
  quantity: "수량",
  received_quantity: "입고수량",
  accepted_quantity: "합격수량",
  defective_quantity: "불량수량",
  unit: "단위",
  unit_price: "단가",
  supply_amount: "공급가액",
  tax_amount: "세액",
  line_total: "합계금액",
  inspection_item: "검사항목",
  inspection_result: "판정",
  result: "결과",
  note: "비고",
  remarks: "비고",
};

export const suggestedLineItemFields = [
  "item_name",
  "document_item_code",
  "item_code",
  "specification",
  "lot_code",
  "quantity",
  "received_quantity",
  "accepted_quantity",
  "defective_quantity",
  "unit",
  "unit_price",
  "supply_amount",
  "tax_amount",
  "line_total",
  "inspection_item",
  "inspection_result",
  "note",
  "internal_item_code",
];

const hiddenLineItemMetadataFields = new Set<string>([
  "source_item_name",
  "source_item_code",
  "item_master_match_status",
  "item_master_match_confidence",
  "item_master_candidates",
  "item_master_match_reason",
]);

const warningTextPattern = /(비어 있습니다|미확인|신뢰도 낮음|확인 필요|장부 매칭|검토 필요|후보 확인|미매칭)/;

export function cleanLineItemValue(field: string, value: unknown) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text || warningTextPattern.test(text)) return "";
  if (numericLineItemFields.has(field)) {
    const numeric = text.replace(/[,₩원\s]/g, "");
    return /^-?\d+(\.\d+)?$/.test(numeric) ? numeric : "";
  }
  if ((field === "item_code" || field === "document_item_code" || field === "source_item_code" || field === "internal_item_code") && warningTextPattern.test(text)) return "";
  return text;
}

export function cleanLineItems(items: ManufacturingLineItem[]) {
  return (items || []).map((item) => {
    const cleaned: ManufacturingLineItem = {};
    for (const [field, value] of Object.entries(item || {})) {
      if (field === "item_master_candidates") {
        if (Array.isArray(value) && value.length) cleaned.item_master_candidates = value;
        continue;
      }
      if (hiddenLineItemMetadataFields.has(field)) {
        if (value !== null && value !== undefined && value !== "") {
          (cleaned as Record<string, unknown>)[field] = value;
        }
        continue;
      }
      const nextValue = cleanLineItemValue(field, value);
      if (nextValue !== "") {
        (cleaned as Record<string, unknown>)[field] = nextValue;
      }
    }
    if (!cleaned.source_item_name && item?.item_name) cleaned.source_item_name = String(item.item_name);
    if (!cleaned.source_item_code && (item?.source_item_code || item?.item_code || item?.document_item_code)) {
      const sourceCode = cleanLineItemValue("source_item_code", item.source_item_code ?? item.item_code ?? item.document_item_code);
      if (sourceCode) cleaned.source_item_code = sourceCode;
    }
    return cleaned;
  });
}

export function lineItemDisplayFields(item: ManufacturingLineItem, reviewFields: string[] = []) {
  const fields = new Set<string>();
  for (const [field, value] of Object.entries(item || {})) {
    if (hiddenLineItemMetadataFields.has(field)) continue;
    if (value !== null && value !== undefined) fields.add(field);
  }
  for (const field of reviewFields) {
    if (field && !hiddenLineItemMetadataFields.has(field)) fields.add(field);
  }
  return suggestedLineItemFields.filter((field) => fields.has(field)).concat(
    [...fields].filter((field) => !suggestedLineItemFields.includes(field)).sort()
  );
}

export function lineItemFieldLabel(field: string) {
  return lineItemFieldLabels[field] ?? field.replaceAll("_", " ");
}

export function lineItemAddableFields(item: ManufacturingLineItem) {
  const existing = new Set(Object.keys(item || {}).filter((field) => !hiddenLineItemMetadataFields.has(field)));
  return suggestedLineItemFields.filter((field) => !existing.has(field));
}

export function normalizeCustomLineItemField(value: string) {
  return value
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^A-Za-z0-9가-힣_]/g, "")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
}
