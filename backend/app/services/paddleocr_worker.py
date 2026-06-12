from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "image path is required"}))
        return 2
    image_path = Path(sys.argv[1])
    try:
        from paddleocr import PaddleOCR

        try:
            ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
        except TypeError:
            ocr = PaddleOCR(use_angle_cls=False)
        output = ocr.predict(str(image_path)) if hasattr(ocr, "predict") else ocr.ocr(str(image_path), cls=False)
        text, confidence, table_blocks, line_candidates = normalize_paddle_output(output)
        print(json.dumps({
            "text": text,
            "confidence": confidence,
            "table_blocks": table_blocks,
            "line_candidates": line_candidates,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


def normalize_paddle_output(output: Any) -> tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]:
    lines: list[str] = []
    confidences: list[float] = []
    table_blocks: list[dict[str, Any]] = []
    line_candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, tuple[tuple[float, float], ...] | None]] = set()
    for item in walk(output):
        if isinstance(item, dict):
            text = item.get("text") or item.get("rec_text") or item.get("label")
            score = item.get("score") or item.get("confidence") or item.get("rec_score")
            if isinstance(item.get("res"), dict):
                table_blocks.append(item["res"])
            if text:
                lines.append(str(text))
                append_unique_line_candidate(line_candidates, seen_candidates, ocr_line_candidate_from_mapping(item, str(text), score))
            if score is not None:
                confidences.append(clamp_confidence(score))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            maybe_text = item[1]
            if isinstance(maybe_text, (list, tuple)) and maybe_text:
                lines.append(str(maybe_text[0]))
                append_unique_line_candidate(
                    line_candidates,
                    seen_candidates,
                    ocr_line_candidate(str(maybe_text[0]), maybe_text[1] if len(maybe_text) > 1 else None, item[0]),
                )
                if len(maybe_text) > 1:
                    confidences.append(clamp_confidence(maybe_text[1]))
    confidence = sum(confidences) / len(confidences) if confidences else (0.80 if lines else 0.0)
    return "\n".join(line for line in lines if line.strip()), confidence, table_blocks, line_candidates


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, (list, tuple)):
        yield value
        for nested in value:
            yield from walk(nested)


def clamp_confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))


def ocr_line_candidate_from_mapping(item: dict[str, Any], text: str, score: object) -> dict[str, Any] | None:
    bbox = (
        item.get("bbox")
        or item.get("box")
        or item.get("points")
        or item.get("poly")
        or item.get("polygon")
        or item.get("dt_poly")
        or item.get("dt_polys")
        or item.get("text_region")
    )
    return ocr_line_candidate(text, score, bbox)


def ocr_line_candidate(text: str, score: object, bbox: object) -> dict[str, Any] | None:
    normalized_bbox = normalize_bbox_points(bbox)
    stripped = str(text or "").strip()
    if not stripped:
        return None
    candidate: dict[str, Any] = {"text": stripped, "confidence": clamp_confidence(score)}
    if normalized_bbox:
        candidate["bbox"] = normalized_bbox
        xs = [point[0] for point in normalized_bbox]
        ys = [point[1] for point in normalized_bbox]
        candidate.update({
            "x_min": min(xs),
            "y_min": min(ys),
            "x_max": max(xs),
            "y_max": max(ys),
        })
    return candidate


def normalize_bbox_points(value: object) -> list[list[float]] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if all(key in value for key in ("x_min", "y_min", "x_max", "y_max")):
            try:
                x_min = float(value["x_min"])
                y_min = float(value["y_min"])
                x_max = float(value["x_max"])
                y_max = float(value["y_max"])
            except (TypeError, ValueError):
                return None
            return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
        for key in ("bbox", "box", "points", "poly", "polygon"):
            if key in value:
                return normalize_bbox_points(value[key])
        return None
    if not isinstance(value, (list, tuple)):
        return None
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            points.append([float(point[0]), float(point[1])])
        except (TypeError, ValueError):
            continue
    return points if len(points) >= 2 else None


def append_unique_line_candidate(
    candidates: list[dict[str, Any]],
    seen: set[tuple[str, tuple[tuple[float, float], ...] | None]],
    candidate: dict[str, Any] | None,
) -> None:
    if not candidate:
        return
    bbox = candidate.get("bbox")
    bbox_key = tuple((round(float(point[0]), 2), round(float(point[1]), 2)) for point in bbox) if isinstance(bbox, list) else None
    key = (str(candidate.get("text") or ""), bbox_key)
    if key in seen:
        return
    seen.add(key)
    candidates.append(candidate)


if __name__ == "__main__":
    raise SystemExit(main())
