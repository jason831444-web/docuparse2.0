from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


UNIT_PATTERN = r"(?:EA|PCS|SET|KG|BOX|M|개|식|대|매|박스|세트)"
CODE_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]{2,}$")
SPEC_PATTERN = re.compile(
    r"^(?:\d+(?:\.\d+)?\s*[xX×]\s*\d+|\d+(?:\.\d+)?\s*(?:T|MM|CM|M|핀|P)|[A-Z]{2,}\d{2,})",
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
