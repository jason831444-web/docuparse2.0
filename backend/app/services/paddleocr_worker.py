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
        text, confidence, table_blocks = normalize_paddle_output(output)
        print(json.dumps({
            "text": text,
            "confidence": confidence,
            "table_blocks": table_blocks,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


def normalize_paddle_output(output: Any) -> tuple[str, float, list[dict[str, Any]]]:
    lines: list[str] = []
    confidences: list[float] = []
    table_blocks: list[dict[str, Any]] = []
    for item in walk(output):
        if isinstance(item, dict):
            text = item.get("text") or item.get("rec_text") or item.get("label")
            score = item.get("score") or item.get("confidence") or item.get("rec_score")
            if isinstance(item.get("res"), dict):
                table_blocks.append(item["res"])
            if text:
                lines.append(str(text))
            if score is not None:
                confidences.append(clamp_confidence(score))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            maybe_text = item[1]
            if isinstance(maybe_text, (list, tuple)) and maybe_text:
                lines.append(str(maybe_text[0]))
                if len(maybe_text) > 1:
                    confidences.append(clamp_confidence(maybe_text[1]))
    confidence = sum(confidences) / len(confidences) if confidences else (0.80 if lines else 0.0)
    return "\n".join(line for line in lines if line.strip()), confidence, table_blocks


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


if __name__ == "__main__":
    raise SystemExit(main())
