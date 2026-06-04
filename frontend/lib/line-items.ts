import type { ManufacturingLineItem } from "@/types/document";

export const numericLineItemFields = new Set<keyof ManufacturingLineItem>([
  "quantity",
  "unit_price",
  "supply_amount",
  "tax_amount",
  "line_total",
]);

const warningTextPattern = /(비어 있습니다|미확인|신뢰도 낮음|확인 필요|장부 매칭|검토 필요|후보 확인|미매칭)/;

export function cleanLineItemValue(field: keyof ManufacturingLineItem, value: unknown) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text || warningTextPattern.test(text)) return "";
  if (numericLineItemFields.has(field)) {
    const numeric = text.replace(/[,₩원\s]/g, "");
    return /^-?\d+(\.\d+)?$/.test(numeric) ? numeric : "";
  }
  if ((field === "item_code" || field === "source_item_code" || field === "internal_item_code") && warningTextPattern.test(text)) return "";
  return text;
}

export function cleanLineItems(items: ManufacturingLineItem[]) {
  return (items || []).map((item) => ({
    ...item,
    item_name: cleanLineItemValue("item_name", item.item_name),
    item_code: cleanLineItemValue("item_code", item.item_code),
    source_item_name: item.source_item_name ?? item.item_name ?? null,
    source_item_code: cleanLineItemValue("source_item_code", item.source_item_code ?? item.item_code),
    internal_item_code: cleanLineItemValue("internal_item_code", item.internal_item_code),
    specification: cleanLineItemValue("specification", item.specification),
    quantity: cleanLineItemValue("quantity", item.quantity),
    unit: cleanLineItemValue("unit", item.unit),
    unit_price: cleanLineItemValue("unit_price", item.unit_price),
    supply_amount: cleanLineItemValue("supply_amount", item.supply_amount),
    tax_amount: cleanLineItemValue("tax_amount", item.tax_amount),
    line_total: cleanLineItemValue("line_total", item.line_total),
    item_master_match_status: item.item_master_match_status ?? null,
    item_master_match_confidence: item.item_master_match_confidence ?? null,
    item_master_candidates: item.item_master_candidates ?? [],
    item_master_match_reason: item.item_master_match_reason ?? null,
  }));
}
