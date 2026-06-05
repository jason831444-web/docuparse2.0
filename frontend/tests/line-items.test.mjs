import assert from "node:assert/strict";

import { cleanLineItemValue, cleanLineItems } from "../lib/line-items.ts";

assert.equal(cleanLineItemValue("quantity", "확인 필요"), "");
assert.equal(cleanLineItemValue("quantity", "의 수량이 비어 있습니다."), "");
assert.equal(cleanLineItemValue("quantity", "1,200"), "1200");
assert.equal(cleanLineItemValue("unit_price", "25,000원"), "25000");
assert.equal(cleanLineItemValue("item_code", "품목코드 미확인"), "");
assert.equal(cleanLineItemValue("internal_item_code", "내부 품목코드 후보 확인 필요"), "");

const [item] = cleanLineItems([
  {
    item_name: "PCB Connector",
    item_code: "품목코드 미확인",
    internal_item_code: "확인 필요",
    quantity: "의 수량이 비어 있습니다.",
    unit_price: "300",
    supply_amount: "450,000",
    tax_amount: "45,000",
    line_total: "495,000",
  },
]);

assert.equal(item.item_code, "");
assert.equal(item.internal_item_code, "");
assert.equal(item.quantity, "");
assert.equal(item.unit_price, "300");
assert.equal(item.supply_amount, "450000");
assert.equal(item.tax_amount, "45000");
assert.equal(item.line_total, "495000");

const [malformed] = cleanLineItems([
  {
    item_name: "SUS316 PLATE 2T",
    item_code: "확인 필요",
    internal_item_code: "후보 확인 필요",
    quantity: "1",
    unit_price: "42,000",
    supply_amount: "4,200",
    tax_amount: "46,200",
    line_total: "4,200",
  },
]);

assert.equal(malformed.item_code, "");
assert.equal(malformed.internal_item_code, "");
assert.equal(malformed.quantity, "1");
assert.equal(malformed.unit_price, "42000");
assert.equal(malformed.supply_amount, "4200");
assert.equal(malformed.tax_amount, "46200");
assert.equal(malformed.line_total, "4200");

console.log("line item sanitation tests passed");
