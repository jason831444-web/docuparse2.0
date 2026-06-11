from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


UNIT_PATTERN = r"(?:EA|PCS|SET|KG|BOX|ROLL|M|개|식|대|매|박스|세트|롤)"
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
    if len(candidates) < 3:
        candidates.extend(_reconstruct_sparse_table_candidates(lines))
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[OCRLineItemCandidate]) -> list[OCRLineItemCandidate]:
    by_key: dict[tuple[str, str, str], OCRLineItemCandidate] = {}
    for candidate in candidates:
        item = candidate.item
        code = str(item.get("item_code") or "")
        key = (
            code,
            "" if code else str(item.get("item_name") or "").lower(),
            "" if code else str(item.get("line_total") or item.get("supply_amount") or ""),
        )
        existing = by_key.get(key)
        if existing is None or _candidate_rank(candidate) > _candidate_rank(existing):
            by_key[key] = candidate
    return list(by_key.values())


def _candidate_rank(candidate: OCRLineItemCandidate) -> tuple[int, int, int]:
    item = candidate.item
    amount_score = sum(1 for field in ["line_total", "supply_amount", "tax_amount", "quantity", "unit_price"] if item.get(field) not in (None, ""))
    identity_score = sum(1 for field in ["item_code", "specification", "item_name"] if item.get(field))
    name_penalty = -len(str(item.get("item_name") or ""))
    return amount_score, identity_score, name_penalty


def _reconstruct_sparse_table_candidates(lines: list[str]) -> list[OCRLineItemCandidate]:
    cleaned = [cleanup_ocr_line(line) for line in lines]
    start = _sparse_table_start(cleaned)
    if start is None:
        return []
    body: list[str] = []
    for line in cleaned[start + 1:]:
        if body and _looks_like_vertical_table_boundary(line):
            break
        if _field_for_vertical_header(line):
            continue
        if _looks_like_vertical_table_boundary(line):
            break
        if _looks_like_header_or_note(line):
            continue
        body.extend(_split_leaked_amount_prefix(line))
    segments = _sparse_segments(body)
    candidates: list[OCRLineItemCandidate] = []
    for segment in segments:
        item = _parse_sparse_segment(segment)
        if not item:
            continue
        confidence = max(_candidate_confidence(item), 0.50)
        candidates.append(OCRLineItemCandidate(item=item, confidence=confidence, source_line=" / ".join(segment)))
    return candidates


def _sparse_table_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        field = _field_for_vertical_header(line)
        if field == "item_name":
            return index
        normalized = re.sub(r"[^0-9a-z가-힣]+", "", line.lower())
        if normalized in {"item", "description", "itemdescription", "품복명", "품목명"}:
            return index
    return None


def _sparse_segments(cells: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for cell in cells:
        if not cell:
            continue
        starts_item = _looks_like_sparse_item_start(cell)
        if current and starts_item and _segment_has_identity(current):
            segments.append(current)
            current = []
        current.append(cell)
    if current:
        segments.append(current)
    return segments


def _looks_like_sparse_item_start(cell: str) -> bool:
    if _is_numericish_cell(cell) or _unit(cell) or _field_for_vertical_header(cell):
        return False
    normalized_code = _normalize_ocr_code(cell)
    if _looks_like_code(normalized_code):
        return False
    if _looks_like_spec_token(_normalize_spec(cell)):
        return False
    return bool(re.search(r"[A-Za-z가-힣]", cell))


def _segment_has_identity(segment: list[str]) -> bool:
    return any(_looks_like_code(_normalize_ocr_code(cell)) for cell in segment) or any(_looks_like_spec_token(_normalize_spec(cell)) for cell in segment)


def _parse_sparse_segment(segment: list[str]) -> dict | None:
    cells = _remove_leading_amount_noise_cells([cell for cell in segment if cell])
    if not cells:
        return None
    identity = _identity_from_vertical_cells(cells, allow_item_code=True)
    if not identity.get("item_name"):
        return None
    numeric_cells = [cell for cell in cells if _numeric_candidates(cell)]
    amount = _best_boundary_amounts(numeric_cells, item_name=identity.get("item_name"), specification=identity.get("specification"), item_code=identity.get("item_code"))
    item = {key: value for key, value in identity.items() if value}
    if amount:
        unit_index = next((index for index, cell in enumerate(cells) if _unit(cell)), None)
        if unit_index is not None:
            has_quantity_before_unit = (
                unit_index > 0
                and _is_numericish_cell(cells[unit_index - 1])
                and not _looks_like_spec_token(_normalize_spec(cells[unit_index - 1]))
                and not re.search(r"[xX×]|(?:mm|cm|t)$", cells[unit_index - 1], flags=re.IGNORECASE)
            )
            if not has_quantity_before_unit:
                amount["quantity"] = None
                item["_quantity_inferred_without_cell"] = True
        item.update({
            "quantity": _number_value(amount.get("quantity")),
            "unit": "EA",
            "unit_price": _number_value(amount.get("unit_price")),
            "supply_amount": _number_value(amount.get("supply_amount")),
            "tax_amount": _number_value(amount.get("tax_amount")),
            "line_total": _number_value(amount.get("line_total")),
        })
    return {key: value for key, value in item.items() if value not in (None, "")}


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
        if len(set(fields)) >= 5 and "item_name" in fields:
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
        cells.extend(_split_leaked_amount_prefix(line))

    if not cells:
        return []

    if any(field in header_fields for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]):
        if "item_code" not in header_fields and not any(_unit(cell) for cell in cells):
            return _reconstruct_vertical_table_without_item_codes(cells)
        priced_candidates = _reconstruct_priced_vertical_table(cells, allow_item_code="item_code" in header_fields)
        if priced_candidates:
            return priced_candidates

    if "item_code" not in header_fields:
        return _reconstruct_vertical_table_without_item_codes(cells)

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


def _reconstruct_priced_vertical_table(cells: list[str], *, allow_item_code: bool) -> list[OCRLineItemCandidate]:
    boundary_candidates = _reconstruct_priced_rows_by_boundaries(cells, allow_item_code=allow_item_code)
    if (
        len(boundary_candidates) >= 3
        and _boundary_candidates_are_safe(boundary_candidates)
        and not any(re.search(r"[가-힣]", cell) for cell in cells)
    ):
        return boundary_candidates
    candidates: list[OCRLineItemCandidate] = []
    start = 0
    while start < len(cells):
        parsed_options: list[tuple[int, dict, int]] = []
        for end in range(start + 5, min(len(cells), start + 16) + 1):
            segment = cells[start:end]
            item = _parse_priced_vertical_segment(segment, allow_item_code=allow_item_code)
            if item:
                score = _reconstructed_item_score(item, segment)
                if end < len(cells) and _is_numericish_cell(cells[end]):
                    score -= 30
                if end + 1 < len(cells) and _is_numericish_cell(cells[end + 1]):
                    score -= 20
                parsed_options.append((score, item, end))
        if not parsed_options:
            start += 1
            continue
        parsed_options.sort(key=lambda entry: (-entry[0], entry[2]))
        _, item, next_start = parsed_options[0]
        confidence = max(_candidate_confidence(item), 0.76)
        candidates.append(OCRLineItemCandidate(item=item, confidence=confidence, source_line=" / ".join(cells[start:next_start])))
        start = next_start
    return candidates


def _reconstruct_priced_rows_by_boundaries(cells: list[str], *, allow_item_code: bool) -> list[OCRLineItemCandidate]:
    starts = _priced_boundary_starts(cells, allow_item_code=allow_item_code)
    if len(starts) < 2:
        return []
    candidates: list[OCRLineItemCandidate] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(cells)
        segment = cells[start:end]
        item = _parse_priced_boundary_segment(segment, allow_item_code=allow_item_code)
        if not item:
            continue
        candidates.append(OCRLineItemCandidate(item=item, confidence=max(_candidate_confidence(item), 0.74), source_line=" / ".join(segment)))
    return candidates


def _boundary_candidates_are_safe(candidates: list[OCRLineItemCandidate]) -> bool:
    for candidate in candidates:
        name = str(candidate.item.get("item_name") or "")
        if len(name) > 80:
            return False
        if re.search(r"\b\d{4,}\b.*\b\d{4,}\b", name):
            return False
    return True


def _priced_boundary_starts(cells: list[str], *, allow_item_code: bool) -> list[int]:
    starts: list[int] = []
    code_positions = [index for index, cell in enumerate(cells) if allow_item_code and _looks_like_code(_normalize_ocr_code(cell))]
    for code_index in code_positions:
        start = code_index
        while start > 0 and not _is_amount_noise_cell(cells[start - 1]):
            previous = cells[start - 1]
            if _looks_like_code(_normalize_ocr_code(previous)):
                break
            if _field_for_vertical_header(previous):
                break
            start -= 1
        starts.append(start)
    for spec_index, cell in enumerate(cells):
        if not _looks_like_spec_token(_normalize_spec(cell)):
            continue
        if any(start <= spec_index < (starts[pos + 1] if pos + 1 < len(starts) else len(cells)) for pos, start in enumerate(starts)):
            continue
        start = spec_index
        while start > 0 and not _is_amount_noise_cell(cells[start - 1]):
            if _field_for_vertical_header(cells[start - 1]):
                break
            start -= 1
        if start < spec_index:
            starts.append(start)
    return sorted(set(starts))


def _is_amount_noise_cell(value: str) -> bool:
    text = value.strip()
    return bool(re.fullmatch(r"\d{4,}(?:\.\d+)?", text) or text.upper() in {"KRW", "USD"})


def _parse_priced_boundary_segment(segment: list[str], *, allow_item_code: bool) -> dict | None:
    cells = _remove_leading_amount_noise_cells([cell for cell in segment if cell and not _field_for_vertical_header(cell)])
    if len(cells) < 4:
        return None
    identity = _identity_from_vertical_cells(cells, allow_item_code=allow_item_code)
    if not identity.get("item_name"):
        return None
    identity_values = {identity.get("item_name"), identity.get("item_code"), identity.get("specification")}
    numeric_cells = [cell for cell in cells if _numeric_candidates(cell) and cell not in identity_values]
    amount = _best_boundary_amounts(numeric_cells, item_name=identity.get("item_name"), specification=identity.get("specification"), item_code=identity.get("item_code"))
    if not amount:
        if _looks_like_code(identity.get("item_code") or "") or identity.get("specification"):
            return {key: value for key, value in identity.items() if value}
        return None
    unit_index = next((index for index, cell in enumerate(cells) if _unit(cell)), None)
    if unit_index is not None:
        has_quantity_before_unit = (
            unit_index > 0
            and _is_numericish_cell(cells[unit_index - 1])
            and not _looks_like_spec_token(_normalize_spec(cells[unit_index - 1]))
        )
        if not has_quantity_before_unit:
            amount["quantity"] = None
    item = {
        **identity,
        "quantity": _number_value(amount.get("quantity")),
        "unit": "EA",
        "unit_price": _number_value(amount.get("unit_price")),
        "supply_amount": _number_value(amount.get("supply_amount")),
        "tax_amount": _number_value(amount.get("tax_amount")),
        "line_total": _number_value(amount.get("line_total")),
    }
    return {key: value for key, value in item.items() if value not in (None, "")}


def _best_boundary_amounts(numeric_cells: list[str], *, item_name: str | None, specification: str | None, item_code: str | None) -> dict[str, Decimal | None] | None:
    expanded: list[tuple[str, Decimal]] = []
    for cell in numeric_cells:
        for value in _numeric_candidates(cell):
            expanded.append((cell, value))
    if not expanded:
        return None
    triples: list[tuple[int, Decimal, Decimal, Decimal, int]] = []
    for i, (_, supply) in enumerate(expanded):
        for j, (_, tax) in enumerate(expanded):
            if j <= i:
                continue
            for k, (_, total) in enumerate(expanded):
                if k <= j:
                    continue
                score = 0
                if abs(tax - supply * Decimal("0.1")) <= max(Decimal("1"), supply * Decimal("0.02")):
                    score += 20
                if abs(total - (supply + tax)) <= max(Decimal("1"), total * Decimal("0.02")):
                    score += 20
                if score >= 30:
                    triples.append((score, supply, tax, total, i))
    if triples:
        triples.sort(key=lambda entry: (-entry[0], -entry[3]))
        _, supply, tax, total, first_amount_index = triples[0]
    else:
        supply = tax = total = None
        for _, value in reversed(expanded):
            if value >= Decimal("1000"):
                total = value
                break
        if total is None:
            return None
        if total % Decimal("11") == 0:
            supply = total * Decimal("10") / Decimal("11")
            tax = total - supply
        else:
            supply = total
            tax = None
        first_amount_index = max(0, len(expanded) - 1)
    pre_cells = [cell for cell in numeric_cells[: max(0, first_amount_index)]]
    if len(pre_cells) == 1:
        quantity = None
        unit_price = _best_single_price_cell(pre_cells[0], supply)
    else:
        quantity, unit_price = _best_quantity_price_from_cells(pre_cells, supply, item_name=item_name, specification=specification, item_code=item_code)
    return {
        "quantity": quantity,
        "unit_price": unit_price,
        "supply_amount": supply,
        "tax_amount": tax,
        "line_total": total,
    }


def _best_single_price_cell(cell: str, supply: Decimal | None) -> Decimal | None:
    if supply is None or supply <= 0:
        return None
    candidates = _price_candidates(cell, _to_decimal(cell)) + _amount_value_candidates(cell)
    candidates = [candidate for candidate in candidates if candidate and Decimal("1") <= candidate <= supply]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]


def _best_quantity_price_from_cells(cells: list[str], supply: Decimal | None, *, item_name: str | None, specification: str | None, item_code: str | None) -> tuple[Decimal | None, Decimal | None]:
    if not cells or supply is None or supply <= 0:
        return None, None
    quantity_values: list[Decimal] = []
    price_values: list[Decimal] = []
    for cell in cells:
        quantity_values.extend(_quantity_candidates(cell))
        raw = _to_decimal(cell)
        price_values.extend(_price_candidates(cell, raw))
        price_values.extend(_amount_value_candidates(cell))
    scored: list[tuple[int, Decimal | None, Decimal | None]] = []
    for quantity in quantity_values:
        if quantity <= 0:
            continue
        inferred_price = supply / quantity
        if inferred_price > 0 and inferred_price == inferred_price.to_integral_value():
            score = 10 + _quantity_context_score(quantity, inferred_price, item_code=item_code, item_name=item_name, specification=specification)
            if quantity > 5000:
                score -= 50
            scored.append((score, quantity, inferred_price))
    for price in price_values:
        if price <= 0:
            continue
        inferred_quantity = supply / price
        if inferred_quantity > 0 and inferred_quantity == inferred_quantity.to_integral_value():
            score = 10 + _quantity_context_score(inferred_quantity, price, item_code=item_code, item_name=item_name, specification=specification)
            if inferred_quantity > 5000:
                score -= 50
            scored.append((score, inferred_quantity, price))
    if not scored:
        return None, None
    scored.sort(key=lambda entry: (-entry[0], entry[1] or Decimal("999999")))
    _, quantity, unit_price = scored[0]
    return quantity, unit_price


def _numeric_candidates(cell: str) -> list[Decimal]:
    values = _amount_value_candidates(cell)
    values.extend(_quantity_candidates(cell))
    raw = _to_decimal(cell)
    values.extend(_price_candidates(cell, raw))
    deduped: list[Decimal] = []
    for value in values:
        if value is not None and value > 0 and value not in deduped:
            deduped.append(value)
    return deduped


def _reconstructed_item_score(item: dict, segment: list[str]) -> int:
    quantity = _to_decimal(item.get("quantity"))
    unit_price = _to_decimal(item.get("unit_price"))
    supply = _to_decimal(item.get("supply_amount"))
    tax = _to_decimal(item.get("tax_amount"))
    total = _to_decimal(item.get("line_total"))
    score = 0
    if item.get("item_name"):
        score += 8
    if item.get("item_code"):
        score += 5
    if item.get("specification"):
        score += 4
    if item.get("unit"):
        score += 3
    if quantity is not None and unit_price is not None and supply is not None:
        if abs((quantity * unit_price) - supply) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
            score += 35
        else:
            score -= 20
    if supply is not None and tax is not None and total is not None:
        if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.01")):
            score += 30
        else:
            score -= 25
        if abs(tax - (supply * Decimal("0.1"))) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
            score += 24
        else:
            score -= 10
    if quantity is not None and quantity != quantity.to_integral_value():
        score -= 35
    if quantity is not None and quantity > 5000:
        score -= 45
    if quantity is not None and unit_price is not None:
        score += _quantity_context_score(
            quantity,
            unit_price,
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            specification=item.get("specification"),
        )
    raw_numbers = [_to_decimal(cell) for cell in segment]
    raw_numbers = [number for number in raw_numbers if number is not None]
    for value in [quantity, unit_price, supply, tax, total]:
        if value is not None and value in raw_numbers:
            score += 2
    code_count = sum(1 for cell in segment if _looks_like_code(_normalize_ocr_code(cell)))
    if code_count > 1:
        score -= 50 * (code_count - 1)
    numericish_count = sum(1 for cell in segment if _is_numericish_cell(cell))
    if numericish_count > 7:
        score -= 12 * (numericish_count - 7)
    if len(segment) > 11:
        score -= 4 * (len(segment) - 11)
    return score


def _parse_priced_vertical_segment(cells: list[str], *, allow_item_code: bool) -> dict | None:
    if len(cells) < 5:
        return None
    line_total_raw = _to_decimal(cells[-1])
    tax_raw = _to_decimal(cells[-2]) if len(cells) >= 2 else None
    supply_raw = _to_decimal(cells[-3]) if len(cells) >= 3 else None
    if line_total_raw is None or tax_raw is None or supply_raw is None:
        return None
    if not _raw_amount_tail_is_plausible(supply_raw, tax_raw, line_total_raw):
        return None

    prefix = cells[:-3]
    if not prefix:
        return None

    unit = None
    had_explicit_unit = False
    quantity_token: str | None = None
    unit_price_token: str | None = None
    identity_cells: list[str] = []
    unit_index = next((index for index in range(len(prefix) - 1, -1, -1) if _unit(prefix[index])), None)
    if unit_index is not None:
        had_explicit_unit = True
        unit = _unit(prefix[unit_index])
        if unit_index == 0:
            return None
        possible_quantity = prefix[unit_index - 1]
        if (
            _is_numericish_cell(possible_quantity)
            and not _looks_like_spec_token(_normalize_spec(possible_quantity))
            and not re.search(r"[xX×]|(?:mm|cm|t)$", possible_quantity, flags=re.IGNORECASE)
        ):
            quantity_token = possible_quantity
            identity_cells = prefix[: unit_index - 1]
        else:
            quantity_token = None
            identity_cells = prefix[:unit_index]
        trailing_price_cells = prefix[unit_index + 1:]
        if trailing_price_cells:
            unit_price_token = trailing_price_cells[-1]
    else:
        unit = "EA"
        if len(prefix) >= 2 and _is_numericish_cell(prefix[-2]) and _is_numericish_cell(prefix[-1]):
            quantity_token = prefix[-2]
            unit_price_token = prefix[-1]
            identity_cells = prefix[:-2]
        elif len(prefix) >= 2 and _is_numericish_cell(prefix[-1]):
            quantity_token = prefix[-1]
            unit_price_token = prefix[-1]
            identity_cells = prefix[:-1]
        else:
            quantity_token = prefix[-1]
            identity_cells = prefix[:-1]

    quantity_candidates = _quantity_candidates(quantity_token)
    price_candidates = _price_candidates(unit_price_token, _to_decimal(unit_price_token)) if unit_price_token else []
    explicit_price_candidates = list(price_candidates)
    supply_candidates = _amount_value_candidates(cells[-3])
    tax_candidates = _amount_value_candidates(cells[-2])
    total_candidates = _amount_value_candidates(cells[-1])
    if supply_raw not in supply_candidates:
        supply_candidates.append(supply_raw)
    if tax_raw not in tax_candidates:
        tax_candidates.append(tax_raw)
    if line_total_raw not in total_candidates:
        total_candidates.append(line_total_raw)
    for total in list(total_candidates):
        if total and total > 0:
            inferred_supply = (total / Decimal("1.1")).quantize(Decimal("1")) if total % Decimal("11") == 0 else None
            if inferred_supply and inferred_supply > 0:
                inferred_tax = total - inferred_supply
                if inferred_supply not in supply_candidates:
                    supply_candidates.append(inferred_supply)
                if inferred_tax not in tax_candidates:
                    tax_candidates.append(inferred_tax)
    for supply in list(supply_candidates):
        for unit_price in list(price_candidates):
            if unit_price and unit_price > 0 and _has_strong_numeric_evidence(unit_price, [unit_price_token, cells[-3], cells[-2], cells[-1]]):
                inferred_quantity = supply / unit_price
                if (
                    inferred_quantity > 0
                    and inferred_quantity == inferred_quantity.to_integral_value()
                    and inferred_quantity not in quantity_candidates
                    and (
                        _has_strong_numeric_evidence(inferred_quantity, [quantity_token])
                        or _looks_like_corrupted_quantity_token(quantity_token)
                        or (
                            quantity_token is None
                            and not had_explicit_unit
                            and unit_price_token is not None
                            and inferred_quantity <= Decimal("5000")
                        )
                    )
                ):
                    quantity_candidates.append(inferred_quantity)
    for quantity in list(quantity_candidates):
        for supply in list(supply_candidates):
            if quantity and quantity > 0 and _has_strong_numeric_evidence(quantity, [quantity_token]):
                inferred_price = supply / quantity
                if (
                    inferred_price > 0
                    and inferred_price == inferred_price.to_integral_value()
                    and inferred_price not in price_candidates
                    and (
                        _has_strong_numeric_evidence(inferred_price, [unit_price_token, cells[-3], cells[-2], cells[-1]])
                        or (unit_price_token is None and Decimal("10") <= inferred_price <= Decimal("1000000"))
                    )
                ):
                    price_candidates.append(inferred_price)

    identity = _identity_from_vertical_cells(identity_cells, allow_item_code=allow_item_code)
    if not identity.get("item_name"):
        return None

    scored: list[tuple[int, dict[str, Decimal | None]]] = []
    for quantity in quantity_candidates or [None]:
        dynamic_prices = list(price_candidates)
        for supply in supply_candidates:
            if quantity and quantity > 0 and not dynamic_prices:
                inferred_price = supply / quantity
                if inferred_price > 0:
                    dynamic_prices.append(inferred_price)
            for unit_price in dynamic_prices or [None]:
                dynamic_supplies = list(supply_candidates)
                for supply in dynamic_supplies:
                    for tax in _candidate_taxes(supply, tax_candidates, total_candidates):
                        for line_total in _candidate_totals(supply, tax, total_candidates):
                            candidate = {
                                "quantity": quantity,
                                "unit_price": unit_price,
                                "supply_amount": supply,
                                "tax_amount": tax,
                                "line_total": line_total,
                            }
                            score = _amount_candidate_score_for_optional(candidate, item_name=identity.get("item_name"), specification=identity.get("specification"), item_code=identity.get("item_code"))
                            score += _raw_amount_alignment_score(supply, tax, line_total, supply_raw, tax_raw, line_total_raw)
                            if unit_price_token and unit_price in explicit_price_candidates:
                                score += 8
                            if quantity_token and quantity in quantity_candidates:
                                score += 2
                            scored.append((score, candidate))
    valid = [(score, candidate) for score, candidate in scored if score >= 14]
    if not valid:
        return None
    valid.sort(key=lambda entry: (-entry[0], _candidate_sort_quantity(entry[1].get("quantity"))))
    amount = valid[0][1]
    item = {
        "item_name": identity.get("item_name"),
        "item_code": identity.get("item_code"),
        "specification": identity.get("specification"),
        "quantity": _number_value(amount.get("quantity")),
        "unit": unit,
        "unit_price": _number_value(amount.get("unit_price")),
        "supply_amount": _number_value(amount.get("supply_amount")),
        "tax_amount": _number_value(amount.get("tax_amount")),
        "line_total": _number_value(amount.get("line_total")),
    }
    if quantity_token is None and amount.get("quantity") is not None:
        item["_quantity_inferred_without_cell"] = True
    return {key: value for key, value in item.items() if value not in (None, "")}


def _has_strong_numeric_evidence(value: Decimal, raw_cells: list[object | None]) -> bool:
    """Allow arithmetic repair only when OCR exposed a nearby numeric clue."""
    for raw_cell in raw_cells:
        if raw_cell is None:
            continue
        for candidate in _amount_value_candidates(raw_cell) + _quantity_candidates(raw_cell):
            if candidate == value:
                return True
            if candidate > 0 and value > 0:
                ratio = value / candidate
                if ratio in {Decimal("10"), Decimal("100"), Decimal("0.1"), Decimal("0.01")}:
                    return True
    return False


def _looks_like_corrupted_quantity_token(value: object | None) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d+[A-Za-z&\[\]]", text))


def _identity_from_vertical_cells(cells: list[str], *, allow_item_code: bool) -> dict[str, str | None]:
    normalized_cells = [cell for cell in cells if cell and not _unit(cell)]
    normalized_cells = _remove_leading_amount_noise_cells(normalized_cells)
    normalized_cells = _remove_vertical_row_number_cells(normalized_cells)
    code_index = next((index for index, cell in enumerate(normalized_cells) if _looks_like_code(_normalize_ocr_code(cell))), None) if allow_item_code else None
    item_code = _normalize_ocr_code(normalized_cells[code_index]) if code_index is not None else None
    spec_index = None
    for index in range(len(normalized_cells) - 1):
        if index == code_index:
            continue
        if _extend_spec_with_next_cell(normalized_cells[index], normalized_cells[index + 1]):
            spec_index = index
            break
    if spec_index is None:
        for index, cell in enumerate(normalized_cells):
            if index == code_index:
                continue
            if _looks_like_spec_token(_normalize_spec(cell)):
                spec_index = index
    if spec_index is not None and code_index is not None and spec_index < code_index:
        spec_index = None
    if spec_index is None and code_index is not None and code_index + 1 < len(normalized_cells):
        spec_index = code_index + 1
    specification = _combine_specification_cells(normalized_cells, spec_index)
    excluded = {index for index in [code_index, spec_index] if index is not None}
    if spec_index is not None and spec_index + 1 < len(normalized_cells) and _extend_spec_with_next_cell(normalized_cells[spec_index], normalized_cells[spec_index + 1]):
        excluded.add(spec_index + 1)
    name_cells = [cell for index, cell in enumerate(normalized_cells) if index not in excluded]
    return {
        "item_name": _normalize_item_name(" ".join(name_cells)),
        "item_code": item_code,
        "specification": specification,
    }


def _remove_leading_amount_noise_cells(cells: list[str]) -> list[str]:
    """Remove money cells leaked from the previous row before item identity text."""
    cleaned = list(cells)
    while len(cleaned) >= 2:
        first = cleaned[0].strip()
        if not re.fullmatch(r"\d{4,}(?:\.\d+)?", first):
            break
        if not any(re.search(r"[A-Za-z가-힣]", cell) for cell in cleaned[1:]):
            break
        cleaned.pop(0)
    return cleaned


def _remove_vertical_row_number_cells(cells: list[str]) -> list[str]:
    """Drop OCR table line numbers without removing real quantity/spec cells."""
    if len(cells) < 2:
        return cells
    filtered: list[str] = []
    for index, cell in enumerate(cells):
        if _is_vertical_row_number_cell(cell, cells, index):
            continue
        filtered.append(cell)
    return filtered


def _is_vertical_row_number_cell(cell: str, cells: list[str], index: int) -> bool:
    if not re.fullmatch(r"\d{1,3}", cell.strip()):
        return False
    previous_cell = cells[index - 1] if index > 0 else ""
    next_cell = cells[index + 1] if index + 1 < len(cells) else ""
    previous_has_name = bool(re.search(r"[A-Za-z가-힣]", previous_cell))
    next_has_name_or_code = bool(re.search(r"[A-Za-z가-힣]", next_cell))
    next_is_code = _looks_like_code(_normalize_ocr_code(next_cell)) if next_cell else False
    previous_is_code = _looks_like_code(_normalize_ocr_code(previous_cell)) if previous_cell else False
    if previous_is_code:
        return False
    if index == 0 and next_has_name_or_code:
        return True
    if previous_has_name and (next_is_code or _looks_like_spec_token(_normalize_spec(next_cell))):
        return True
    if previous_has_name and index == len(cells) - 1:
        return True
    return False


def _raw_amount_tail_is_plausible(supply: Decimal, tax: Decimal, total: Decimal) -> bool:
    if supply <= 0 or tax < 0 or total <= 0:
        return False
    if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.03")):
        return True
    if abs(tax - (supply * Decimal("0.1"))) <= max(Decimal("1"), abs(supply) * Decimal("0.03")):
        return True
    if tax > supply * Decimal("2") and total < tax * Decimal("2"):
        return False
    if tax > total and tax > supply * Decimal("2"):
        return False
    if supply + tax > total * Decimal("2") and total < max(supply, tax):
        return False
    return True


def _combine_specification_cells(cells: list[str], spec_index: int | None) -> str | None:
    if spec_index is None:
        return None
    first = _normalize_spec(cells[spec_index])
    if spec_index + 1 < len(cells) and _extend_spec_with_next_cell(cells[spec_index], cells[spec_index + 1]):
        second = _normalize_spec(cells[spec_index + 1])
        return f"{first} x {second}"
    return first


def _extend_spec_with_next_cell(first: str, second: str) -> bool:
    return bool(re.search(r"mm$", first, flags=re.IGNORECASE) and re.fullmatch(r"\d+(?:\.\d+)?\s*mm", second, flags=re.IGNORECASE))


def _amount_value_candidates(raw_cell: object) -> list[Decimal]:
    text = str(raw_cell or "").strip()
    candidates: list[Decimal] = []
    normalized = text.replace(",", "")
    substitutions = [
        normalized,
        re.sub(r"^5(\d{3})6$", r"6\g<1>0", normalized),
        re.sub(r"^5(\d{4})6$", r"6\g<1>0", normalized),
        normalized.replace("O", "0").replace("o", "0"),
        re.sub(r"[lI]$", "0", normalized),
        re.sub(r"[Gg]$", "0", normalized),
        re.sub(r"[Ll]$", "0", normalized),
        re.sub(r"^5(?=\d{4}$)", "6", normalized),
        re.sub(r"(?<=\d)6$", "0", normalized),
    ]
    for candidate_text in substitutions:
        value = _to_decimal(candidate_text)
        if value is not None and value > 0 and value not in candidates:
            candidates.append(value)
    value = _to_decimal(normalized)
    if value is not None and re.search(r"[CGL\[]$", normalized, flags=re.IGNORECASE):
        for multiplier in [Decimal("10"), Decimal("100")]:
            scaled = value * multiplier
            if scaled not in candidates:
                candidates.append(scaled)
    return candidates


def _candidate_taxes(supply: Decimal, tax_candidates: list[Decimal], total_candidates: list[Decimal]) -> list[Decimal]:
    candidates = list(tax_candidates)
    expected = supply * Decimal("0.1")
    if expected == expected.to_integral_value() and expected not in candidates:
        candidates.append(expected)
    for total in total_candidates:
        derived = total - supply
        if derived > 0 and derived not in candidates:
            candidates.append(derived)
    return [candidate for candidate in candidates if candidate is not None and candidate >= 0]


def _candidate_totals(supply: Decimal, tax: Decimal, total_candidates: list[Decimal]) -> list[Decimal]:
    candidates = list(total_candidates)
    expected = supply + tax
    if expected not in candidates:
        candidates.append(expected)
    return [candidate for candidate in candidates if candidate is not None and candidate > 0]


def _amount_candidate_score_for_optional(candidate: dict[str, Decimal | None], *, item_name: str | None, specification: str | None, item_code: str | None) -> int:
    quantity = candidate.get("quantity")
    unit_price = candidate.get("unit_price")
    supply = candidate.get("supply_amount")
    tax = candidate.get("tax_amount")
    total = candidate.get("line_total")
    if supply is None or tax is None or total is None:
        return 0
    score = 0
    if quantity is not None and quantity > 0 and quantity == quantity.to_integral_value():
        score += 4
    if unit_price is not None and unit_price > 0 and unit_price == unit_price.to_integral_value():
        score += 3
    if quantity is not None and unit_price is not None and abs((quantity * unit_price) - supply) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
        score += 8
    if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.01")):
        score += 7
    if abs(tax - (supply * Decimal("0.1"))) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
        score += 6
    if quantity is not None and unit_price is not None:
        score += _quantity_context_score(quantity, unit_price, item_code=item_code, item_name=item_name, specification=specification)
    if quantity is not None and quantity > 10000:
        score -= 20
    elif quantity is not None and quantity > 5000:
        score -= 45
    if quantity is not None and quantity != quantity.to_integral_value():
        score -= 35
    if unit_price is not None and unit_price < 1:
        score -= 20
    elif unit_price is not None and unit_price < 10:
        score -= 8
    if quantity is not None and unit_price is not None and supply is not None and quantity == 1 and unit_price == supply and supply >= 50000:
        score -= 30
    return score


def _raw_amount_alignment_score(supply: Decimal | None, tax: Decimal | None, total: Decimal | None, supply_raw: Decimal | None, tax_raw: Decimal | None, total_raw: Decimal | None) -> int:
    score = 0
    for value, raw in [(supply, supply_raw), (tax, tax_raw), (total, total_raw)]:
        if value is None or raw is None:
            continue
        if value == raw:
            score += 3
        elif raw > 0 and (value / raw in {Decimal("10"), Decimal("100")} or raw / value in {Decimal("10"), Decimal("100")}):
            score += 1
    return score


def _candidate_sort_quantity(quantity: Decimal | None) -> Decimal:
    return quantity if quantity is not None else Decimal("999999999")


def _field_for_vertical_header(value: str) -> str | None:
    key = re.sub(r"[^0-9a-z가-힣]+", "", value.lower())
    if key in {"품목명", "품복명", "품명", "itemname", "itemdescription", "description"}:
        return "item_name"
    if key in {"품목코드", "문서품목코드", "품번", "거래처코드", "거래처품목코드", "itemcode", "vendorsku", "sku", "partno", "partnumber"}:
        return "item_code"
    if key in {"규격", "사양", "spec", "specification", "size", "dimension"}:
        return "specification"
    if key in {"수량", "수링", "논량", "주문수량", "납품수량", "qty", "oty", "quantity"}:
        return "quantity"
    if key in {"단위", "unit"}:
        return "unit"
    if key in {"단가", "unitprice"}:
        return "unit_price"
    if key in {"공급가액", "공급액", "공급금액", "subtotal", "supplyamount", "supplytotal"}:
        return "supply_amount"
    if key in {"세액", "세악", "부가세", "vat", "tax"}:
        return "tax_amount"
    if key in {"합계", "합겨", "합계금액", "총액", "linetotal", "total", "tota"}:
        return "line_total"
    if key in {"비고", "note", "remark", "remarks"}:
        return "note"
    return None


def _split_leaked_amount_prefix(line: str) -> list[str]:
    parts: list[str] = []
    remaining = line.strip()
    while True:
        match = re.match(r"^(\d{4,}(?:\.\d+)?)\s+(.+)$", remaining)
        if not match:
            break
        tail = match.group(2).strip()
        if not re.search(r"[A-Za-z가-힣]", tail):
            break
        parts.append(match.group(1))
        remaining = tail
    parts.append(remaining)
    return parts


def _looks_like_vertical_table_boundary(line: str) -> bool:
    key = re.sub(r"[^0-9a-z가-힣]+", "", line.lower())
    return bool(
        re.fullmatch(
            r"(공급가액|공급가액합계|공급액합계|세액|세액합계|부가세|합계금액|총액|총합계|subtotal|tax|total|grandtotal|invoicetotal)",
            key,
            flags=re.IGNORECASE,
        )
    )


def _vertical_row_starts(cells: list[str]) -> list[int]:
    code_positions = [index for index, cell in enumerate(cells) if _looks_like_code(_normalize_ocr_code(cell))]
    starts: list[int] = []
    for code_index in code_positions:
        start = code_index
        while start > 0 and not _is_numericish_cell(cells[start - 1]):
            previous = cells[start - 1]
            if _looks_like_code(_normalize_ocr_code(previous)):
                break
            if _unit(previous):
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
    if not any(field in header_fields for field in ["unit_price", "supply_amount", "tax_amount", "line_total"]):
        quantity = _choose_no_price_quantity(quantity_token)
        if quantity is None:
            return None
        if unit is None:
            unit = _unit(str(quantity_token or "")) or "EA"
        item_name = _normalize_item_name(" ".join(name_cells))
        item = {
            "item_name": item_name,
            "item_code": item_code,
            "specification": specification,
            "quantity": _number_value(quantity),
            "unit": unit,
        }
        return {key: value for key, value in item.items() if value not in (None, "")}
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


def _reconstruct_vertical_table_without_item_codes(cells: list[str]) -> list[OCRLineItemCandidate]:
    candidates: list[OCRLineItemCandidate] = []
    index = 0
    while index < len(cells):
        parsed: tuple[dict, int] | None = None
        max_end = min(len(cells), index + 10)
        for end in range(index + 5, max_end + 1):
            item = _parse_vertical_row_without_code(cells[index:end])
            if item:
                parsed = (item, end)
                break
        if not parsed:
            index += 1
            continue
        item, next_index = parsed
        confidence = max(_candidate_confidence(item), 0.70)
        candidates.append(OCRLineItemCandidate(item=item, confidence=confidence, source_line=" / ".join(cells[index:next_index])))
        index = next_index
    return candidates


def _parse_vertical_row_without_code(cells: list[str]) -> dict | None:
    if len(cells) < 5:
        return None
    spec_index = next((index for index, cell in enumerate(cells) if _looks_like_spec_token(_normalize_spec(cell))), None)
    if spec_index is None or spec_index == 0:
        return None
    name_cells = cells[:spec_index]
    specification = _normalize_spec(cells[spec_index])
    tail_cells = cells[spec_index + 1:]
    if len(tail_cells) < 3:
        return None
    amount = _best_amount_tail_candidate(tail_cells, item_name=" ".join(name_cells), specification=specification)
    if not amount:
        return None
    item = {
        "item_name": _normalize_item_name(" ".join(name_cells)),
        "specification": specification,
        "quantity": _number_value(amount["quantity"]),
        "unit": "EA",
        "unit_price": _number_value(amount["unit_price"]),
        "supply_amount": _number_value(amount["supply_amount"]),
        "tax_amount": _number_value(amount["tax_amount"]),
        "line_total": _number_value(amount["line_total"]),
    }
    return {key: value for key, value in item.items() if value not in (None, "")}


def _best_amount_tail_candidate(tail_cells: list[str], *, item_name: str | None, specification: str | None) -> dict[str, Decimal] | None:
    values = [_to_decimal(cell) for cell in tail_cells]
    if len(values) < 3 or any(value is None for value in values[-3:]):
        return None
    total = values[-1]
    tax = values[-2]
    if total is None or tax is None:
        return None
    supply_from_total = total - tax
    if supply_from_total <= 0:
        return None
    candidates: list[tuple[int, dict[str, Decimal]]] = []

    if len(values) >= 4 and values[-3] is not None:
        supply = _repair_supply_amount(values[-3], tax, total)
        for unit_price in _price_candidates(tail_cells[-4], values[-4]):
            if unit_price and unit_price > 0:
                quantity = supply / unit_price
                candidate = {
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "supply_amount": supply,
                    "tax_amount": tax,
                    "line_total": total,
                }
                candidates.append((_amount_candidate_score(candidate, item_name=item_name, specification=specification), candidate))

    if len(values) >= 4 and values[-3] is not None:
        unit_price = values[-3]
        supply = supply_from_total
        if unit_price and unit_price > 0:
            quantity = supply / unit_price
            candidate = {
                "quantity": quantity,
                "unit_price": unit_price,
                "supply_amount": supply,
                "tax_amount": tax,
                "line_total": total,
            }
            candidates.append((_amount_candidate_score(candidate, item_name=item_name, specification=specification) + 1, candidate))

    valid = [(score, candidate) for score, candidate in candidates if score >= 6]
    if not valid:
        return None
    valid.sort(key=lambda entry: (-entry[0], entry[1]["quantity"]))
    return valid[0][1]


def _price_candidates(raw_cell: object, value: Decimal | None) -> list[Decimal]:
    candidates: list[Decimal] = []
    if value is not None and value > 0:
        candidates.append(value)
        if value < 1000:
            candidates.append(value * 10)
    text = str(raw_cell or "").strip()
    if re.search(r"[\[C]$", text, flags=re.IGNORECASE) and value is not None and value > 0:
        candidates.append(value * 10)
    repaired = _to_decimal(text.replace("B", "8").replace("b", "8").replace("O", "0").replace("o", "0"))
    if repaired is not None and repaired > 0:
        candidates.append(repaired)
        if repaired < 1000:
            candidates.append(repaired * 10)
    deduped: list[Decimal] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _repair_supply_amount(supply: Decimal | None, tax: Decimal | None, total: Decimal | None) -> Decimal:
    if tax is None or total is None:
        return supply or Decimal("0")
    expected = total - tax
    if supply is None or expected <= 0:
        return expected
    if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.02")):
        return supply
    if supply * 10 == expected:
        return expected
    return expected


def _amount_candidate_score(candidate: dict[str, Decimal], *, item_name: str | None, specification: str | None) -> int:
    quantity = candidate["quantity"]
    unit_price = candidate["unit_price"]
    supply = candidate["supply_amount"]
    tax = candidate["tax_amount"]
    total = candidate["line_total"]
    score = 0
    if quantity > 0 and quantity == quantity.to_integral_value():
        score += 4
    if unit_price > 0 and unit_price == unit_price.to_integral_value():
        score += 3
    if abs((quantity * unit_price) - supply) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
        score += 5
    if abs((supply + tax) - total) <= max(Decimal("1"), abs(total) * Decimal("0.01")):
        score += 4
    if abs(tax - (supply * Decimal("0.1"))) <= max(Decimal("1"), abs(supply) * Decimal("0.01")):
        score += 4
    score += _quantity_context_score(quantity, unit_price, item_code=None, item_name=item_name, specification=specification)
    if quantity == 1 and unit_price == supply and supply >= 50000:
        score -= 30
    if quantity > 5000 or unit_price < 10:
        score -= 3
    return score


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
    text = re.sub(r"(?<=\d)[Oo]{2}\b", "00", text)
    text = re.sub(r"(?<=-)[Oo](?=\d)", "0", text)
    text = re.sub(r"(?<=\d)[Oo](?=$|-)", "0", text)
    text = re.sub(r"(?<=\d)[Oo](?=[A-Za-z]$)", "0", text)
    return text


def _normalize_spec(value: str) -> str:
    text = value.strip().replace("×", "x")
    text = re.sub(r"^[\[\(lI](?=\d|[Oo])", "1", text)
    text = re.sub(r"[Oo](?=\d|[xX]|$)", "0", text)
    text = re.sub(r"[&]", "8", text)
    text = re.sub(r"(?<=\d)[\]\)]$", "T", text)
    text = re.sub(r"(?<=x\d)7$", "T", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)1$", "T", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "x", text)
    return text


def _normalize_item_name(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    if re.fullmatch(r"(?:amount due|grand total|invoice total|total amount|total|subtotal|tax|vat|총액|합계금액|공급가액|부가세)", text, flags=re.IGNORECASE):
        return None
    text = re.sub(r"^(?:\d{4,}(?:\.\d+)?\s+)+(?=\S*[A-Za-z가-힣])", "", text)
    text = re.sub(r"\s+(?:\d{3,}(?:\.\d+)?\s*){2,}$", "", text)
    text = re.sub(r"\s+\d{3,}(?:\.\d+)?$", "", text)
    if re.fullmatch(r"(?:amount due|grand total|invoice total|total amount|total|subtotal|tax|vat|총액|합계금액|공급가액|부가세)", text, flags=re.IGNORECASE):
        return None
    text = re.sub(r"^(?:Supply\s+Tota!?|Supply\s+Total|Subtotal|VAT|Tax|Total)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:Supply|Tota!?|Total)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:공급가액|세액|합계금액|총액)\s+", "", text)
    text = text.replace("스텍", "스텐")
    text = re.sub(r"2\s*O\s*T\b", "2.0T", text, flags=re.IGNORECASE)
    text = re.sub(r"철판\s*3[7T]$", "철판 3T", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[A-Za-z가-힣])(?=\d)|(?<=\d)(?=[가-힣A-Za-z])", " ", text)
    text = re.sub(r"\b([A-Z])\s+(\d{2})\s+([A-Z])\b", r"\1\2\3", text)
    text = re.sub(r"\b([A-Z]{1,5})\s+(\d{1,4})\b", r"\1\2", text)
    text = re.sub(r"\b(\d{1,4})\s+([A-Z])\b", r"\1\2", text)
    text = re.sub(r"\b(\d+)\s+[xX]\s+(\d+)\b", r"\1X\2", text)
    text = re.sub(r"(?<=\d)\s+(?=T\b)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(PIN)\s*(\d+)\s*[xX]\s*(\d+)\b", r"\1 \2X\3", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(M)\s+(\d+)(?=\s*x|\b)", r"\1\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*x\s*(\d+)\b", r"\1x\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bM\s*(\d+)\s*x\s*(\d+)\b", r"M\1x\2", text, flags=re.IGNORECASE)
    text = re.sub(r"(스텐판|철판|판재|고정판)\s*(\d+(?:\.\d+)?T)\b", r"\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"(판)\s*(\d+(?:\.\d+)?T)\b", r"\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"(환봉)\s+(\d+)", r"\1\2", text)
    text = re.sub(r"(하네스)\s+(\d+)\s*(m|mm)\b", r"\1\2\3", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(PIN)\s+(\d+)x(\d+)\b", r"\1 \2X\3", text, flags=re.IGNORECASE)
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


def _choose_no_price_quantity(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if re.fullmatch(r"[TIl]\d{3,}", text, flags=re.IGNORECASE):
        text = "1" + text[1:]
    candidates = _quantity_candidates(text)
    return candidates[0] if candidates else None


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
    small_component_like = bool(re.search(r"(connector|pcb|cable|harness|커넥터|하네스)", identity, flags=re.IGNORECASE))
    plate_like = bool(re.search(r"(plate|plt|bracket|plate|플레이트|판재|철판|고정판|브라켓|판)", identity, flags=re.IGNORECASE))
    has_large_flat_spec = bool(re.search(r"\d{2,4}\s*x\s*\d{2,4}(?:\s*x\s*\d+(?:\.\d+)?t?)?", identity, flags=re.IGNORECASE))
    if fastener_like:
        if quantity >= 500 and unit_price <= 500:
            return 8
        if quantity <= 100 and unit_price >= 500:
            return -6
        if quantity < 500 and unit_price >= 50:
            return -8
    if small_component_like:
        if quantity >= 300 and unit_price <= 5000:
            return 6
        if quantity <= 100 and unit_price >= 10000:
            return -3
    if plate_like or has_large_flat_spec:
        if quantity <= 50 and unit_price >= 2000:
            return 12
        if quantity <= 100 and unit_price >= 1000:
            return 5
        if quantity > 100 and unit_price < 1000:
            return -8
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
    if token.strip() == "가":
        return "EA"
    if token.strip() == "-":
        return None
    if re.search(r"\d|&", token.strip()):
        return None
    normalized = re.sub(r"[^A-Za-z가-힣]", "", token.strip())
    if normalized == "롤":
        return "ROLL"
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
