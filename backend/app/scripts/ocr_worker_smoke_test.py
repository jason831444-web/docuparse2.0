from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    worker_url = os.getenv("OCR_WORKER_URL", "http://ocr-worker:8010").rstrip("/")
    upload_dir = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_path = upload_dir / f"ocr-worker-smoke-{next(tempfile._get_candidate_names())}.png"
    image = Image.new("RGB", (720, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 70), "PO-123 TEST", fill="black")
    image.save(image_path)

    payload = json.dumps({"image_path": str(image_path)}).encode("utf-8")
    request = urllib.request.Request(
        f"{worker_url}/ocr",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("OCR_WORKER_TIMEOUT_SECONDS", "120"))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(json.dumps({"worker_url": worker_url, "image_path": str(image_path), "http_status": exc.code, "error_body": body[-2000:]}, ensure_ascii=False))
        raise
    except Exception as exc:
        print(json.dumps({"worker_url": worker_url, "image_path": str(image_path), "error": str(exc)}, ensure_ascii=False))
        raise
    text = str(data.get("text") or "")
    print(json.dumps({"worker_url": worker_url, "image_path": str(image_path), "ok": data.get("ok"), "text": text, "confidence": data.get("confidence")}, ensure_ascii=False))
    if not data.get("ok"):
        raise SystemExit("OCR worker returned ok=false")
    if not text.strip():
        raise SystemExit("OCR worker returned empty text")


if __name__ == "__main__":
    main()
