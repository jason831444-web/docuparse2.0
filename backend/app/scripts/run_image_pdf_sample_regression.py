from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.scripts.extract_image_pdf_sample_fixtures import (
    DEFAULT_BACKEND_API_BASE,
    DEFAULT_FIXTURE_DIR,
    DEFAULT_OCR_WORKER_URL,
    DEFAULT_SAMPLE_DIR,
    extract_fixtures,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sequential image-PDF OCR regression using the real OCR path.")
    parser.add_argument("--sample-dir", "--samples-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--mode", choices=["api", "worker"], default=os.getenv("DOCUPARSE_FIXTURE_MODE", "api"))
    parser.add_argument("--api-base", default=DEFAULT_BACKEND_API_BASE)
    parser.add_argument("--ocr-worker-url", default=DEFAULT_OCR_WORKER_URL)
    parser.add_argument("--render-dir", type=Path, default=Path(os.getenv("DOCUPARSE_FIXTURE_RENDER_DIR", "/app/uploads/fixture_rendered_pages")))
    parser.add_argument("--cooldown-seconds", type=float, default=float(os.getenv("DOCUPARSE_FIXTURE_COOLDOWN_SECONDS", "1.0")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("DOCUPARSE_FIXTURE_TIMEOUT_SECONDS", "300")))
    parser.add_argument("--progress", action="store_true")
    parser.add_argument(
        "--detail-prefixes",
        default=os.getenv("DOCUPARSE_REGRESSION_DETAIL_PREFIXES", "06,07,08"),
        help="Comma-separated sample prefixes whose line items should be printed in detail.",
    )
    args = parser.parse_args()
    detail_prefixes = {prefix.strip() for prefix in args.detail_prefixes.split(",") if prefix.strip()}

    summaries = extract_fixtures(args)
    print("\nImage PDF regression summary")
    print("============================")
    for summary in summaries:
        print(
            "{prefix} {filename}: ok={ok} id={document_id} status={processing_status} "
            "review_required={review_required} type={document_type} ai_type={ai_document_type} "
            "profile={profile} doc_no={document_number} vendor={vendor_name} customer={customer_name} "
            "currency={currency} total={extracted_amount} "
            "line_items={line_items_count} item_sum={line_items_total} provider={ocr_provider_succeeded} fallback={ocr_fallback_used} "
            "elapsed_ms={processing_time_ms}".format(**summary)
        )
        if summary.get("review_reasons"):
            print(f"  review_reasons: {summary['review_reasons']}")
        if summary.get("prefix") in detail_prefixes:
            print("  line_items:")
            for index, item in enumerate(summary.get("line_items") or [], start=1):
                print(f"    {index}. {json.dumps(item, ensure_ascii=False, default=str)}")
        if summary.get("error"):
            print(f"  error: {summary['error']}")
    print("\nJSON")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
