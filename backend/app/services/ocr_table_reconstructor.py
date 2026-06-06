from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


UNIT_PATTERN = r"(?:EA|PCS|SET|KG|BOX|M|개|식|대|매|박스|세트)"
CODE_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]{2,}$")
SPEC_PATTERN = re.compile(
    r"^(?:[A-Z]?\d+(?:\.\d+)?\s*[xX×]\s*\d+|[A-Z]\d{1,3}$|\d+(?:\.\d+)?\s*(?:T|MM|CM|M|핀|P)|[A-Z]{2,}\d{2,})",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class OCRLineItemCandidate:
    item: dict
    confidence: float
    source_line: str


def reconstruct_ocr_line_items(lines: list[str]) -> list[OCRLineItemCandidate]:
    """Recover manufacturing line item rows from OCR text without reliable delimiters."""
    candidates: list[OCRLineItemCandidate] = []
    candidates.extend(_reconstruct_vertical_table(lines))
    for line in lines:
        if _has_reliable_table_delimiter(str(line or "")):
            continue
        cleaned = cleanup_ocr_line(line)
        if not cleaned or _looks_like_header_or_note(cleaned):
            continue
        item = _parse_priced_row(cleaned) or _parse_no_price_row(cleaned)
        if not item:
            continue
        confidence = _candidate_confidence(item)
        if confidence >= 0.55:
            candidates.append(OCRLineItemCandidate(item=item, confidence=confidence, source_line=line))
    return candidates


def _reconstruct_vertical_table(lines: list[str]) -> list[OCRLineItemCandidate]:
    cleaned_lines = [cleanup_ocr_line(line) for line in lines]
    header_start = None
    header_end = None
    header_fields: list[str] = []
    for index in range(len(cleaned_lines)):
        fields: list[str] = []
        cursor = index
        while cursor < len(cleaned_lines):
            field = _field_for_vertical_header(cleaned_lines[cursor])
            if not field:
                break
            fields.append(field)
            cursor += 1
        if len(set(fields)) >= 5 and "item_name" in fields and "item_code" in fields:
            header_start = index
            header_end = cursor
            header_fields = fields
            break
    if header_start is None or header_end is None:
        return []

    cells: list[str] = []
    for line in cleaned_lines[header_end:]:
        if not line:
            continue
        if _looks_like_vertical_table_boundary(line):
            break
        if _field_for_vertical_header(line):
            continue
        cells.append(line)

    if not cells:
        return []

    row_starts = _vertical_row_starts(cells)
    candidates: list[OCRLineItemCandidate] = []
    for position, start in enumerate(row_starts):
        end = row_starts[position + 1] if position + 1 < len(row_starts) else len(cells)
        row_cells = cells[start:end]
        item = _parse_vertical_row(row_cells, header_fields)
        if not item:
            continue
        confidence = max(_candidate_confidence(item), 0.72)
        candidates.append(OCRLineItemCandidate(item=item, confidence=confidence, source_line=" / ".join(row_cells)))
    return candidates


def _field_for_vertical_header(value: str) -> str | None:
    key = re.sub(r"[\s_/:：-]+", "", value.lower())
    if key in {"품목명", "품명", "itemname", "itemdescription", "description"}:
        return "item_name"
    if key in {"품목코드", "품번", "거래처코드", "거래처품목코드", "itemcode", "vendorsku", "sku", "partno", "partnumber"}:
        return "item_code"
    if key in {"규격", "사양", "spec", "specification", "size", "dimension"}:
        return "specification"
    if key in {"수량", "주문수량", "납품수량", "qty", "quantity"}:
        return "quantity"
    if key in {"단위", "unit"}:
        return "unit"
    if key in {"단가", "unitprice"}:
        return "unit_price"
    if key in {"공급가액", "공급액", "공급금액", "subtotal", "supplyamount"}:
        return "supply_amount"
    if key in {"세액", "부가세", "vat", "tax"}:
        return "tax_amount"
    if key in {"합계", "합겨", "합계금액", "총액", "linetotal", "total"}:
        return "line_total"
    return None


def _looks_like_vertical_table_boundary(line: str) -> bool:
    key = re.sub(r"\s+", "", line.lower())
    return bool(re.search(r"(공급가액합계|공급액합계|세액합계|부가세|총액|총합계|grandtotal|invoicetotal)", key, flags=re.IGNORECASE))


def _vertical_row_starts(cells: list[str]) -> list[int]:
    code_positions = [index for index, cell in enumerate(cells) if _looks_like_code(_normalize_ocr_code(cell))]
    starts: list[int] = []
    for code_index in code_positions:
        start = code_index
        while start > 0 and not _is_numericish_cell(cells[start - 1]):
            previous = cells[start - 1]
            if _looks_like_code(_normalize_ocr_code(previous)):
                break
            start -= 1
        starts.append(start)
    deduped: list[int] = []
    for start in starts:
        if start not in deduped:
            deduped.append(start)
    return deduped


def _parse_vertical_row(cells: list[str], header_fields: list[str]) -> dict | None:
    if not cells:
        return None
    code_index = next((index for index, cell in enumerate(cells) if _looks_like_code(_normalize_ocr_code(cell))), None)
    if code_index is None:
        return None
    item_code = _normalize_ocr_code(cells[code_index])
    name_cells = cells[:code_index]
    remainder = cells[code_index + 1:]
    specification = None
    if remainder and _looks_like_spec_token(_normalize_spec(remainder[0])):
        specification = _normalize_spec(remainder.pop(0))
    elif name_cells and _looks_like_spec_token(_normalize_spec(name_cells[-1])) and len(name_cells) > 1:
        specification = _normalize_spec(name_cells.pop())

    unit = None
    unit_index = next((index for index, cell in enumerate(remainder) if _unit(cell)), None)
    if unit_index is not None:
        unit = _unit(remainder.pop(unit_index))

    quantity_token = None
    if remainder:
        quantity_token = remainder.pop(0)

    numeric_values = [_to_decimal(cell) for cell in remainder if _is_numeric_token(cell)]
    numeric_values = [value for value in numeric_values if value is not None]
    if len(numeric_values) >= 4:
        unit_price, supply_amount, tax_amount, line_total = numeric_values[-4:]
    elif len(numeric_values) >= 3:
        unit_price = None
        supply_amount, tax_amount, line_total = numeric_values[-3:]
    else:
        return None

    tax_amount = _repair_tax_amount(supply_amount, tax_amount, line_total)
    quantity, inferred_unit_price = _choose_quantity_and_unit_price(
        quantity_token,
        unit_price,
        supply_amount,
        item_code=item_code,
        item_name=" ".join(name_cells),
        specification=specification,
    )
    if unit_price is None:
        unit_price = inferred_unit_price
    if unit is None and quantity is not None:
        unit = "EA"

    item_name = _normalize_item_name(" ".join(name_cells))
    item = {
        "item_name": item_name,
        "item_code": item_code,
        "specification": specification,
        "quantity": _number_value(quantity),
        "unit": unit,
        "unit_price": _number_value(unit_price),
        "supply_amount": _number_value(supply_amount),
        "tax_amount": _number_value(tax_amount),
        "line_total": _number_value(line_total),
    }
    return {key: value for key, value in item.items() if value not in (None, "")}


def _is_numericish_cell(value: str) -> bool:
    normalized = value.strip()
    if _looks_like_spec_token(_normalize_spec(normalized)):
        return False
    return bool(
        re.fullmatch(r"(?:KRW|USD|₩|\$)?[-+]?\d[\d,]*(?:\.\d+)?(?:원|KRW|USD)?", normalized, flags=re.IGNORECASE)
        or re.fullmatch(r"\d+[A-Za-z&]", normalized)
    )


def _normalize_ocr_code(value: str) -> str:
    text = value.strip()
    text = re.sub(r"(?<=-)[Oo](?=\d)", "0", text)
    text = re.sub(r"(?<=\d)[Oo](?=$|-)", "0", text)
    return text


def _normalize_spec(value: str) -> str:
    text = value.strip().replace("×", "x")
    text = re.sub(r"[&]", "8", text)
    text = re.sub(r"(?<=\d)[\]\)]$", "T", text)
    text = re.sub(r"(?<=x\d)7$", "T", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "x", text)
    return text


def _normalize_item_name(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"(?<=[A-Za-z가-힣])(?=\d)|(?<=\d)(?=[가-힣A-Za-z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _quantity_candidates(value: object) -> list[Decimal]:
    text = str(value or "").strip()
    candidates: list[Decimal] = []
    compact = text.replace(",", "")
    if re.fullmatch(r"\d+[Cc]", compact):
        base = Decimal(compact[:-1])
        candidates.extend([base * 10, base, base / 2])
    elif re.fullmatch(r"\d+[Aa]", compact):
        base = Decimal(compact[:-1])
        candidates.append(base)
    else:
        direct = _to_decimal(text)
        if direct is not None:
            candidates.append(direct)
    deduped: list[Decimal] = []
    for candidate in candidates:
        if candidate > 0 and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _choose_quantity_and_unit_price(
    quantity_token: object,
    unit_price: Decimal | None,
    supply_amount: Decimal | None,
    *,
    item_code: str | None = None,
    item_name: str | None = None,
    specification: str | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    if supply_amount is None:
        candidates = _quantity_candidates(quantity_token)
        return (candidates[0], None) if candidates else (None, None)
    if unit_price is not None and unit_price > 0:
        inferred = supply_amount / unit_price
        if inferred == inferred.to_integral_value():
            return inferred, unit_price
    candidates = _quantity_candidates(quantity_token)
    if not candidates:
        return None, unit_price
    scored: list[tuple[int, Decimal, Decimal]] = []
    for quantity in candidates:
        if quantity <= 0:
            continue
        inferred_price = supply_amount / quantity
        score = 0
        if inferred_price == inferred_price.to_integral_value():
            score += 3
        if Decimal("10") <= inferred_price <= Decimal("1000000"):
            score += 2
        if str(quantity_token or "").strip().upper().endswith("C") and quantity == candidates[0]:
            score += 1
        score += _quantity_context_score(quantity, inferred_price, item_code=item_code, item_name=item_name, specification=specification)
        scored.append((score, quantity, inferred_price))
    if not scored:
        return candidates[0], unit_price
    scored.sort(key=lambda entry: (-entry[0], -entry[1]))
    _, quantity, inferred_price = scored[0]
    return quantity, inferred_price


def _quantity_context_score(
    quantity: Decimal,
    unit_price: Decimal,
    *,
    item_code: str | None,
    item_name: str | None,
    specification: str | None,
) -> int:
    identity = " ".join(value for value in [item_code, item_name, specification] if value).lower()
    if not identity:
        return 0
    fastener_like = bool(re.search(r"(bolt|washer|screw|nut|볼트|와셔|나사|너트|wash)", identity, flags=re.IGNORECASE))
    plate_like = bool(re.search(r"(plate|plt|bracket|plate|플레이트|판재|철판|고정판|브라켓|판)", identity, flags=re.IGNORECASE))
    has_large_flat_spec = bool(re.search(r"\d{2,4}\s*x\s*\d{2,4}(?:\s*x\s*\d+(?:\.\d+)?t?)?", identity, flags=re.IGNORECASE))
    if fastener_like:
        if quantity >= 500 and unit_price <= 500:
            return 5
        if quantity <= 100 and unit_price >= 1000:
            return -2
    if plate_like or has_large_flat_spec:
        if quantity <= 50 and unit_price >= 2000:
            return 8
        if quantity <= 100 and unit_price >= 1000:
            return 5
        if quantity >= 500 and unit_price <= 500:
            return -4
    return 0


def _repair_tax_amount(supply: Decimal | None, tax: Decimal | None, total: Decimal | None) -> Decimal | None:
    if supply is None or total is None:
        return tax
    expected = total - supply
    if expected <= 0:
        return tax
    if tax is None:
        return expected
    if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.02")):
        return tax
    if tax * 10 == expected:
        return expected
    return tax


def cleanup_ocr_line(line: str) -> str:
    text = str(line or "")
    text = text.replace("×", "x")
    text = re.sub(r"\bS\$\s*US", "SUS", text, flags=re.IGNORECASE)
    text = re.sub(r"\$US(?=\d)", "SUS", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSSUS(?=\d)", "SUS", text, flags=re.IGNORECASE)
    text = re.sub(r"\bS\$\s*U", "SU", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "x", text)
    text = re.sub(r"[,，](?=\s*[A-Za-z가-힣])", " ", text)
    text = re.sub(r"[|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:;")


def _parse_priced_row(line: str) -> dict | None:
    tokens = _tokenize(line)
    if len(tokens) < 7:
        return None
    amount_positions = [(index, _to_decimal(token)) for index, token in enumerate(tokens) if _is_numeric_token(token)]
    amount_positions = [(index, value) for index, value in amount_positions if value is not None]
    if len(amount_positions) < 4:
        return None
    last_amounts = amount_positions[-4:]
    unit_price_pos, supply_pos, tax_pos, total_pos = [position for position, _ in last_amounts]
    if total_pos != len(tokens) - 1:
        return None
    unit_pos = None
    for candidate_pos in range(unit_price_pos - 1, max(-1, unit_price_pos - 5), -1):
        if candidate_pos >= 0 and _unit(tokens[candidate_pos]):
            unit_pos = candidate_pos
            break
    if unit_pos is None:
        return None
    unit = _unit(tokens[unit_pos])
    quantity_pos = unit_pos - 1
    quantity = _to_decimal(tokens[quantity_pos]) if quantity_pos >= 0 and _is_numeric_token(tokens[quantity_pos]) else None
    if unit is None:
        return None
    prefix = _strip_leading_line_number(tokens[:quantity_pos] if quantity is not None else tokens[:unit_pos])
    item_name, item_code, specification = _split_identity(prefix)
    if not item_name and not item_code:
        return None
    item = {
        "item_name": item_name,
        "item_code": item_code,
        "specification": specification,
        "unit": unit,
        "unit_price": _number_value(last_amounts[0][1]),
        "supply_amount": _number_value(last_amounts[1][1]),
        "tax_amount": _number_value(last_amounts[2][1]),
        "line_total": _number_value(last_amounts[3][1]),
    }
    if quantity is not None:
        item["quantity"] = _number_value(quantity)
    return item


def _parse_no_price_row(line: str) -> dict | None:
    tokens = _tokenize(line)
    if len(tokens) < 4:
        return None
    unit_index = None
    for index in range(len(tokens) - 1, 0, -1):
        if _unit(tokens[index]):
            unit_index = index
            break
    if unit_index is None or unit_index < 2:
        return None
    quantity = _to_decimal(tokens[unit_index - 1])
    if quantity is None:
        return None
    prefix = _strip_leading_line_number(tokens[: unit_index - 1])
    item_name, item_code, specification = _split_identity(prefix)
    if not item_name and not item_code:
        return None
    return {
        "item_name": item_name,
        "item_code": item_code,
        "specification": specification,
        "quantity": _number_value(quantity),
        "unit": _unit(tokens[unit_index]),
    }


def _split_identity(tokens: list[str]) -> tuple[str | None, str | None, str | None]:
    if not tokens:
        return None, None, None
    code_index = next((index for index, token in enumerate(tokens) if _looks_like_code(token)), None)
    spec_indexes = [index for index, token in enumerate(tokens) if _looks_like_spec_token(token)]
    spec_index = spec_indexes[-1] if spec_indexes else None
    item_code = tokens[code_index] if code_index is not None else None

    excluded = {index for index in [code_index, spec_index] if index is not None}
    item_tokens = [token for index, token in enumerate(tokens) if index not in excluded]
    if spec_index is not None:
        specification = tokens[spec_index]
        if spec_index + 2 < len(tokens) and tokens[spec_index + 1].lower() == "x" and _to_decimal(tokens[spec_index + 2]) is not None:
            specification = f"{tokens[spec_index]}x{tokens[spec_index + 2]}"
            item_tokens = [token for index, token in enumerate(tokens) if index not in {code_index, spec_index, spec_index + 1, spec_index + 2}]
    else:
        specification = None
    item_name = " ".join(item_tokens).strip() or None
    return item_name, item_code, specification


def _strip_leading_line_number(tokens: list[str]) -> list[str]:
    if len(tokens) >= 2 and re.fullmatch(r"\d{1,3}", tokens[0]):
        return tokens[1:]
    if len(tokens) >= 2 and tokens[0] in {"I", "l", "|"}:
        return tokens[1:]
    return tokens


def _candidate_confidence(item: dict) -> float:
    score = 0.35
    if item.get("item_name"):
        score += 0.16
    if item.get("item_code"):
        score += 0.13
    if item.get("specification"):
        score += 0.12
    if item.get("quantity") is not None and item.get("unit"):
        score += 0.16
    if item.get("line_total") is not None:
        score += 0.12
    return min(score, 0.98)


def _looks_like_header_or_note(line: str) -> bool:
    lowered = line.lower()
    return bool(
        re.search(r"(품목명|item\s+name|vendor\s+sku|unit\s+price|supply\s+amount|line\s+total|주의|note|must\s+not|should\s+not)", lowered)
    )


def _has_reliable_table_delimiter(line: str) -> bool:
    if line.count("|") >= 4 or "\t" in line:
        return True
    if " / " in line:
        return True
    if "," in line and len(line.split(",")) >= 4:
        return True
    return False


def _looks_like_code(token: str) -> bool:
    normalized = token.strip("()[]{}")
    if not CODE_PATTERN.match(normalized):
        return False
    if re.match(r"^(?:SUS|SS)[-_]?\d{3,4}$", normalized, flags=re.IGNORECASE):
        return False
    if "-" in normalized or "_" in normalized:
        return True
    return bool(re.search(r"[A-Za-z]{2,}\d{2,}[A-Za-z0-9]*$", normalized))


def _looks_like_spec_token(token: str) -> bool:
    if re.match(r"^(?:SUS|SS)[-_]?\d{3,4}$", token.strip(), flags=re.IGNORECASE):
        return False
    return bool(SPEC_PATTERN.search(token.strip()))


def _tokenize(line: str) -> list[str]:
    return [token for token in re.split(r"\s+", line.strip()) if token]


def _is_numeric_token(token: str) -> bool:
    return bool(re.fullmatch(r"(?:KRW|USD|₩|\$)?[-+]?\d[\d,]*(?:\.\d+)?(?:원|KRW|USD)?", token.strip(), flags=re.IGNORECASE))


def _unit(token: str) -> str | None:
    if re.search(r"[€E]A$", token.strip(), flags=re.IGNORECASE):
        return "EA"
    normalized = re.sub(r"[^A-Za-z가-힣]", "", token.strip())
    if normalized.lower() == "ea":
        normalized = "EA"
    match = re.fullmatch(UNIT_PATTERN, normalized, flags=re.IGNORECASE)
    return match.group(0).upper() if match else None


def _to_decimal(value: object) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", "."}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _number_value(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)
