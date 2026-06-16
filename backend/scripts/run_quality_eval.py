from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.document import Document, ProcessingStatus
from app.services.ai_document_understanding import get_document_ai_service
from app.services.document_processor import DocumentProcessor
from scripts.generate_eval_corpus import generate_corpus, load_spec


DEFAULT_SPEC = ROOT / "eval" / "specs" / "eval_documents.json"
DEFAULT_CORPUS = ROOT / "eval" / "corpus"
DEFAULT_REPORTS = ROOT / "eval" / "reports"
DEFAULT_BACKEND_URL = "http://localhost:8001"
API_PREFIX = "/api/documents"
GEMMA_PROVIDER_MARKERS = {
    "ai_interpretation_gemma",
    "ai_interpretation_gemma_fallback_small",
    "ai_interpretation_gemma_gguf",
}
GEMMA_FALLBACK_MARKERS = {
    "ai_interpretation_gemma_unavailable",
    "interpretation_fallback_heuristic",
    "ai_interpretation_skipped_low_text",
    "ai_interpretation_skipped_trivial",
}


@dataclass
class EvalIssue:
    severity: str
    code: str
    message: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Docparse quality evaluation on a representative corpus.")
    parser.add_argument("--mode", choices=["fallback", "gemma"], default="fallback")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--label", default="manual")
    parser.add_argument("--compare-to", type=Path, default=None)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--poll-timeout", type=int, default=120)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--upload-timeout", type=int, default=900)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N documents from the spec.")
    parser.add_argument("--ids", default=None, help="Comma-separated spec document ids to evaluate.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    spec = load_spec(args.spec)
    generate_corpus(args.spec, args.corpus_dir)

    if args.mode == "fallback":
        runner: BaseEvalRunner = FallbackEvalRunner()
    else:
        runner = GemmaApiEvalRunner(
            backend_url=args.backend_url,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
            upload_timeout=args.upload_timeout,
            cleanup=args.cleanup,
        )

    results = []
    documents = spec["documents"]
    if args.ids:
        requested_ids = {item.strip() for item in args.ids.split(",") if item.strip()}
        documents = [item for item in documents if item["id"] in requested_ids]
        missing_ids = requested_ids - {item["id"] for item in documents}
        if missing_ids:
            raise SystemExit(f"Unknown eval document id(s): {', '.join(sorted(missing_ids))}")
    if args.limit:
        documents = documents[: args.limit]
    for item in documents:
        path = args.corpus_dir / item["filename"]
        results.append(runner.evaluate_document(item, path))

    report = build_report(spec, results, args.label, args.mode, args.backend_url if args.mode == "gemma" else None)
    if args.compare_to:
        report["comparison"] = compare_reports(args.compare_to, report)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{timestamp}-{args.mode}-{args.label}"
    json_path = args.output_dir / f"{stem}.json"
    md_path = args.output_dir / f"{stem}.md"
    latest_path = args.output_dir / f"latest-{args.mode}.json"
    latest_md_path = args.output_dir / f"latest-{args.mode}.md"
    json_path.write_text(json.dumps(report, indent=2, default=json_safe), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    latest_path.write_text(json.dumps(report, indent=2, default=json_safe), encoding="utf-8")
    latest_md_path.write_text(render_markdown_report(report), encoding="utf-8")
    print(f"Wrote JSON report: {json_path}")
    print(f"Wrote Markdown report: {md_path}")
    print(render_terminal_summary(report))


class BaseEvalRunner:
    def evaluate_document(self, item: dict[str, Any], path: Path) -> dict[str, Any]:
        raise NotImplementedError


class FallbackEvalRunner(BaseEvalRunner):
    def __init__(self) -> None:
        self.processor = DocumentProcessor()

    def evaluate_document(self, item: dict[str, Any], path: Path) -> dict[str, Any]:
        document = Document(
            original_filename=item["filename"],
            stored_file_path=str(path),
            mime_type=item.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            tags=[],
            action_items=[],
            warnings=[],
            key_dates=[],
            processing_status=ProcessingStatus.uploaded,
        )
        actual: dict[str, Any] = {}
        issues: list[EvalIssue] = []

        try:
            normalized = self.processor.ingestion.ingest(path, document.original_filename, document.mime_type)
            raw_text = normalized.normalized_text
            parsed = self.processor.parser.parse(raw_text, document.original_filename)
            extraction_quality = self.processor.quality.evaluate_extraction(normalized, parsed)
            route = self.processor.router.route(normalized, parsed, extraction_quality)
            analysis_path = normalized.primary_image_path or path
            if route.heavy_ai_required and normalized.primary_image_path:
                ai_result = get_document_ai_service().analyze(analysis_path, raw_text, parsed, document.original_filename)
            else:
                ai_result = self.processor.lightweight_ai.analyze(analysis_path, raw_text, parsed, document.original_filename)
                ai_result.extraction_provider = normalized.extraction_method or route.route_label
                ai_result.provider = ai_result.extraction_provider
                ai_result.provider_chain = [normalized.extraction_method or route.route_label, "heuristic_fallback"]
                ai_result.merge_strategy = route.route_label
            structured_quality = self.processor.quality.evaluate_structured_result(document, ai_result, extraction_quality)
            ingestion_notes = self.processor._ingestion_notes(normalized, route)

            document.raw_text = raw_text
            document.mime_type = normalized.mime_type or document.mime_type
            document.source_file_type = normalized.source_file_type
            document.extraction_method = normalized.extraction_method
            document.ingestion_metadata = self.processor._ingestion_metadata(normalized, route, extraction_quality, structured_quality)
            document.confidence_score = ai_result.confidence_score or self.processor._confidence(normalized)
            document.ai_document_type = ai_result.document_type
            document.ai_confidence_score = ai_result.confidence_score
            quality_notes = self.processor._quality_notes(extraction_quality, structured_quality)
            document.ai_extraction_notes = self.processor._notes(ingestion_notes + quality_notes + ai_result.extraction_notes)
            document.review_required = (
                ai_result.review_required
                or route.review_required
                or extraction_quality.review_required
                or structured_quality.review_required
                or bool(normalized.extraction_warnings)
            )
            document.summary = ai_result.summary
            document.extraction_provider = ai_result.extraction_provider or ai_result.provider
            document.refinement_provider = ai_result.refinement_provider
            provider_chain = self.processor._provider_chain(normalized, route, ai_result.provider_chain or [ai_result.provider])
            document.provider_chain = "+".join(provider_chain)
            document.merge_strategy = ai_result.merge_strategy
            document.field_sources = ai_result.field_sources or None
            document.document_type = ai_result.document_type or parsed.document_type
            document.title = ai_result.title or parsed.title
            document.extracted_date = ai_result.extracted_date or parsed.extracted_date
            document.extracted_amount = ai_result.extracted_amount or parsed.extracted_amount
            document.subtotal = ai_result.subtotal
            document.tax = ai_result.tax
            document.currency = ai_result.currency or parsed.currency
            document.merchant_name = ai_result.merchant_name or parsed.merchant_name
            document.category = ai_result.category or parsed.category
            document.tags = ai_result.tags or parsed.tags

            interpretation = self.processor.category_interpreter.interpret(document, ai_result.cleaned_raw_text or raw_text)
            provider_chain = self.processor._provider_chain(
                normalized,
                route,
                ai_result.provider_chain or [ai_result.provider],
                interpretation.provider_chain,
            )
            document.provider_chain = "+".join(provider_chain)
            document.title = self.processor._apply_title_hint(document.title, interpretation)
            document.category = self.processor._apply_category_hint(document.category, interpretation)
            document.document_type = self.processor._refined_document_type(document.document_type, interpretation)
            document.title = self.processor._clean_final_title(document.title, interpretation)
            document.merchant_name = self.processor._clean_final_merchant(document.merchant_name)
            if interpretation.summary_hint:
                document.summary = interpretation.summary_hint
            document.tags = self.processor._merge_tags(document.tags, interpretation, document.document_type)
            document.ai_extraction_notes = self.processor._notes(
                (ingestion_notes + quality_notes + ai_result.extraction_notes) + self.processor._interpretation_notes(interpretation)
            )
            document.ingestion_metadata = self.processor._ingestion_metadata(
                normalized,
                route,
                extraction_quality,
                structured_quality,
                interpretation,
            )
            workflow = self.processor.workflow_enrichment.enrich(document, ai_result.cleaned_raw_text or raw_text, interpretation)
            document.workflow_summary = workflow.workflow_summary
            document.action_items = workflow.action_items
            document.warnings = workflow.warnings
            document.key_dates = workflow.key_dates
            document.urgency_level = workflow.urgency_level
            document.follow_up_required = workflow.follow_up_required
            document.workflow_metadata = workflow.workflow_metadata or None
            document.review_required = document.review_required or bool(workflow.warnings)
            document.processing_status = ProcessingStatus.needs_review if document.review_required else ProcessingStatus.ready

            actual = extract_actual(
                status=document.processing_status.value if document.processing_status else None,
                title=document.title,
                broad_type=getattr(document.document_type, "value", str(document.document_type)),
                category=document.category,
                profile=interpretation.profile,
                subtype=interpretation.subtype,
                summary_short=((document.workflow_metadata or {}).get("summaries") or {}).get("short"),
                summary_detailed=((document.workflow_metadata or {}).get("summaries") or {}).get("detailed") or document.workflow_summary,
                action_items=document.action_items or [],
                warnings=document.warnings or [],
                important_points=(document.workflow_metadata or {}).get("important_points", []),
                review_focus=(document.workflow_metadata or {}).get("review_focus", []),
                tags=document.tags or [],
                provider_chain=document.provider_chain,
                route=route.route_label,
                extraction_method=normalized.extraction_method,
                processing_path=(document.ingestion_metadata or {}).get("processing_path"),
                review_required=document.review_required,
                merchant_name=document.merchant_name,
                extracted_amount=str(document.extracted_amount) if document.extracted_amount is not None else None,
                extracted_date=document.extracted_date.isoformat() if document.extracted_date else None,
                interpretation_provider_chain=((document.ingestion_metadata or {}).get("category_interpretation") or {}).get("provider_chain", []),
                refinement_status=((document.ingestion_metadata or {}).get("category_interpretation") or {}).get("refinement_status"),
            )
            issues.extend(run_quality_checks(item, actual, mode="fallback"))
        except Exception as exc:
            actual = {
                "status": "failed",
                "error": str(exc),
                "provider_chain": document.provider_chain,
                "title": document.title,
            }
            issues.append(EvalIssue("fail", "runtime_failure", f"Pipeline raised an exception: {exc}"))

        return {
            "id": item["id"],
            "filename": item["filename"],
            "format": item["format"],
            "expectations": item.get("expectations", {}),
            "actual": actual,
            "issues": [asdict(issue) for issue in issues],
            "score": score_issues(issues),
        }


class GemmaApiEvalRunner(BaseEvalRunner):
    def __init__(
        self,
        backend_url: str,
        poll_timeout: int,
        poll_interval: float,
        upload_timeout: int,
        cleanup: bool,
    ) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.upload_timeout = upload_timeout
        self.cleanup = cleanup
        self.session = requests.Session()

    def evaluate_document(self, item: dict[str, Any], path: Path) -> dict[str, Any]:
        issues: list[EvalIssue] = []
        actual: dict[str, Any]
        created_id: str | None = None
        try:
            self._health_check()
            uploaded = self._upload(path)
            created_id = str(uploaded["id"])
            document = self._poll_until_finished(created_id)
            actual = extract_actual(
                status=document.get("processing_status"),
                title=document.get("title"),
                broad_type=document.get("document_type"),
                category=document.get("category"),
                profile=self._interpretation_meta(document).get("profile"),
                subtype=self._interpretation_meta(document).get("subtype"),
                summary_short=(((document.get("workflow_metadata") or {}).get("summaries")) or {}).get("short"),
                summary_detailed=(((document.get("workflow_metadata") or {}).get("summaries")) or {}).get("detailed") or document.get("workflow_summary"),
                action_items=document.get("action_items") or [],
                warnings=document.get("warnings") or [],
                important_points=(document.get("workflow_metadata") or {}).get("important_points", []),
                review_focus=(document.get("workflow_metadata") or {}).get("review_focus", []),
                tags=document.get("tags") or [],
                provider_chain=document.get("provider_chain"),
                route=(document.get("ingestion_metadata") or {}).get("route"),
                extraction_method=document.get("extraction_method"),
                processing_path=(document.get("ingestion_metadata") or {}).get("processing_path"),
                review_required=document.get("review_required"),
                merchant_name=document.get("merchant_name"),
                extracted_amount=str(document.get("extracted_amount")) if document.get("extracted_amount") is not None else None,
                extracted_date=document.get("extracted_date"),
                interpretation_provider_chain=self._interpretation_meta(document).get("provider_chain", []),
                refinement_status=self._interpretation_meta(document).get("refinement_status"),
            )
            issues.extend(run_quality_checks(item, actual, mode="gemma"))
        except Exception as exc:
            actual = {
                "status": "failed",
                "error": str(exc),
                "provider_chain": None,
                "title": None,
            }
            issues.append(EvalIssue("fail", "runtime_failure", f"Gemma API evaluation failed: {exc}"))
        finally:
            if created_id and self.cleanup:
                self._delete_best_effort(created_id)

        return {
            "id": item["id"],
            "filename": item["filename"],
            "format": item["format"],
            "expectations": item.get("expectations", {}),
            "actual": actual,
            "issues": [asdict(issue) for issue in issues],
            "score": score_issues(issues),
        }

    def _health_check(self) -> None:
        try:
            response = self.session.get(f"{self.backend_url}{API_PREFIX}", params={"page_size": 1}, timeout=10)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Could not reach backend at {self.backend_url}. Start the API server before running --mode gemma."
            ) from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"Backend at {self.backend_url} returned {response.status_code} for document list endpoint."
            )

    def _upload(self, path: Path) -> dict[str, Any]:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            response = self.session.post(
                f"{self.backend_url}{API_PREFIX}/upload",
                files={"file": (path.name, handle, mime_type)},
                timeout=self.upload_timeout,
            )
        response.raise_for_status()
        return response.json()

    def _poll_until_finished(self, document_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout
        last_payload: dict[str, Any] | None = None
        while time.time() < deadline:
            response = self.session.get(f"{self.backend_url}{API_PREFIX}/{document_id}", timeout=20)
            response.raise_for_status()
            last_payload = response.json()
            status = last_payload.get("processing_status")
            if status in {"ready", "needs_review", "confirmed", "failed", "completed"}:
                return last_payload
            time.sleep(self.poll_interval)
        raise RuntimeError(
            f"Timed out waiting for processing to complete for document {document_id}. Last status: {last_payload.get('processing_status') if last_payload else 'unknown'}"
        )

    def _delete_best_effort(self, document_id: str) -> None:
        try:
            self.session.delete(f"{self.backend_url}{API_PREFIX}/{document_id}", timeout=20)
        except requests.RequestException:
            return

    def _interpretation_meta(self, document: dict[str, Any]) -> dict[str, Any]:
        return ((document.get("ingestion_metadata") or {}).get("category_interpretation") or {})


def extract_actual(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def run_quality_checks(item: dict[str, Any], actual: dict[str, Any], mode: str) -> list[EvalIssue]:
    issues: list[EvalIssue] = []
    expectations = item.get("expectations", {})
    title = actual.get("title") or ""
    profile = (actual.get("profile") or "").strip()
    category = (actual.get("category") or "").strip()
    broad_type = (actual.get("broad_type") or "").strip()
    summary_short = actual.get("summary_short") or ""
    summary_detailed = actual.get("summary_detailed") or ""
    important_points = actual.get("important_points") or []
    action_items = actual.get("action_items") or []
    provider_chain = actual.get("provider_chain") or ""
    combined_summary = f"{summary_short}\n{summary_detailed}\n" + "\n".join(important_points)

    if actual.get("status") == "failed":
        return issues
    if not provider_chain:
        issues.append(EvalIssue("fail", "provider_chain_missing", "Provider chain is missing after successful processing."))
    if not profile:
        issues.append(EvalIssue("fail", "profile_missing", "Interpretation profile is missing."))

    if mode == "gemma":
        provider_tokens = set(filter(None, provider_chain.split("+")))
        if not provider_tokens & GEMMA_PROVIDER_MARKERS:
            if provider_tokens & GEMMA_FALLBACK_MARKERS:
                issues.append(EvalIssue("fail", "gemma_not_used", f"Gemma mode was requested, but provider chain shows fallback path: {provider_chain}"))
            else:
                issues.append(EvalIssue("fail", "gemma_provider_missing", f"Gemma mode was requested, but provider chain does not prove Gemma participation: {provider_chain}"))

    if expectations.get("generic_not_allowed") and profile in {"generic_document", "", None}:
        issues.append(EvalIssue("fail", "generic_profile", "Document stayed generic even though stronger evidence was expected."))
    if expectations.get("expected_profiles") and profile not in expectations["expected_profiles"]:
        issues.append(EvalIssue("fail", "profile_mismatch", f"Expected one of {expectations['expected_profiles']}, got {profile or 'none'}."))    
    if expectations.get("expected_categories") and category not in expectations["expected_categories"]:
        issues.append(EvalIssue("warn", "category_mismatch", f"Expected category near {expectations['expected_categories']}, got {category or 'none'}."))    
    if expectations.get("expected_broad_types") and broad_type not in expectations["expected_broad_types"]:
        issues.append(EvalIssue("warn", "broad_type_mismatch", f"Expected broad type near {expectations['expected_broad_types']}, got {broad_type or 'none'}."))    

    if not title:
        issues.append(EvalIssue("fail", "title_missing", "Title is missing."))
    if re.fullmatch(r"(page|slide)\s+\d+", title.strip(), flags=re.IGNORECASE):
        issues.append(EvalIssue("fail", "title_placeholder", "Title is still a placeholder heading."))
    if looks_like_sentence(title):
        issues.append(EvalIssue("warn", "title_body_like", "Title looks like a body sentence rather than a document name."))
    for pattern in expectations.get("title_forbidden_patterns", []):
        if re.search(pattern, title, flags=re.IGNORECASE):
            issues.append(EvalIssue("fail", "title_forbidden_pattern", f"Title matches forbidden pattern `{pattern}`."))

    if not summary_short:
        issues.append(EvalIssue("fail", "summary_short_missing", "Short summary is missing."))
    elif len(summary_short.split()) < 4:
        issues.append(EvalIssue("warn", "summary_short_thin", "Short summary is too thin to be useful."))
    if not summary_detailed:
        issues.append(EvalIssue("fail", "summary_detailed_missing", "Detailed summary is missing."))
    elif len(summary_detailed.split()) < 12:
        issues.append(EvalIssue("warn", "summary_detailed_thin", "Detailed summary is too thin given available evidence."))

    if is_generic_summary(summary_detailed):
        issues.append(EvalIssue("warn", "summary_generic", "Detailed summary still sounds generic."))
    if has_malformed_summary(summary_detailed):
        issues.append(EvalIssue("fail", "summary_malformed", "Detailed summary has malformed joins or dangling phrasing."))
    if has_repetitive_summary(summary_detailed):
        issues.append(EvalIssue("warn", "summary_repetitive", "Detailed summary repeats concepts or phrasing in a mechanical way."))
    if has_fragment_dump_feel(summary_detailed):
        issues.append(EvalIssue("warn", "summary_fragment_dump", "Detailed summary still feels like ranked fragments pasted into prose."))
    if lacks_document_explanation(summary_detailed):
        issues.append(EvalIssue("warn", "summary_missing_identity", "Detailed summary does not clearly explain what the document is."))
    if lacks_meaningful_detail(summary_detailed, expectations):
        issues.append(EvalIssue("warn", "summary_missing_substance", "Detailed summary does not surface enough central document meaning."))

    expected_keywords = [keyword.lower() for keyword in expectations.get("summary_keywords", [])]
    if expected_keywords:
        matched = sum(1 for keyword in expected_keywords if keyword in combined_summary.lower())
        required = min(2, len(expected_keywords))
        if matched < required:
            issues.append(EvalIssue("warn", "summary_keyword_coverage", "Summary and highlights are not surfacing enough expected central details."))

    important_keywords = [keyword.lower() for keyword in expectations.get("important_keywords", expectations.get("summary_keywords", []))]
    if not important_points:
        issues.append(EvalIssue("warn", "important_points_missing", "Important points are missing."))
    else:
        joined_points = " ".join(important_points).lower()
        if important_keywords and not any(keyword in joined_points for keyword in important_keywords):
            issues.append(EvalIssue("warn", "important_points_weak", "Important points miss expected core document signals."))
        if important_points_too_generic(important_points):
            issues.append(EvalIssue("warn", "important_points_generic", "Important points are too generic for the available evidence."))

    if expectations.get("require_action_items") and not action_items:
        issues.append(EvalIssue("warn", "action_items_missing", "No action items were produced for a document that should surface review cues."))
    for item_text in action_items:
        if len(item_text) > 110:
            issues.append(EvalIssue("warn", "action_item_too_long", f"Action item is too long: `{item_text[:80]}...`"))
        if looks_like_raw_fragment(item_text):
            issues.append(EvalIssue("warn", "action_item_raw_fragment", f"Action item still looks too close to copied source text: `{item_text}`"))
        if not looks_review_oriented(item_text):
            issues.append(EvalIssue("warn", "action_item_weak", f"Action item is not very user-facing or review-oriented: `{item_text}`"))

    stale_tag = conflicting_tag(profile, actual.get("tags") or [])
    if stale_tag:
        issues.append(EvalIssue("warn", "stale_conflicting_tag", f"Tag `{stale_tag}` conflicts with the stronger final interpretation `{profile}`."))
    if profile == "generic_document" and category not in {"other", "", None}:
        issues.append(EvalIssue("warn", "generic_profile_specific_category", "Category is specific but profile stayed generic."))
    if profile in {"syllabus", "course_guide"} and broad_type == "notice":
        issues.append(EvalIssue("warn", "broad_type_outdated", "Broad type still reads like notice even though the interpreted document is a course guide."))
    if mode == "gemma" and final_output_feels_fallback_like(actual):
        issues.append(EvalIssue("warn", "gemma_output_fallback_like", "Gemma was requested, but the final output still feels too fallback-like or under-explained."))
    return issues


def looks_like_sentence(value: str) -> bool:
    lowered = value.lower().strip()
    return len(lowered.split()) >= 8 and bool(re.search(r"\b(is|are|will|introduces|provides|covers|describes|contains)\b", lowered))


def is_generic_summary(value: str) -> bool:
    lowered = value.lower()
    return any(
        phrase in lowered
        for phrase in [
            "generic document",
            "this is a document",
            "this is a generic",
            "document with general information",
        ]
    )


def has_malformed_summary(value: str) -> bool:
    return bool(
        re.search(r",\s*and\.$", value)
        or re.search(r";\s*and\b", value)
        or re.search(r"\band\.\s*$", value)
        or re.search(r"\.\s*\.\s*", value)
    )


def has_repetitive_summary(value: str) -> bool:
    lowered = value.lower()
    repeated_openers = ["it highlights", "it mainly", "what matters most", "the most important details"]
    return any(lowered.count(opener) > 1 for opener in repeated_openers) or len(re.findall(r"\b(review|document|details)\b", lowered)) >= 5


def has_fragment_dump_feel(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.count(", along with ") >= 2
        or lowered.count(" it highlights ") >= 2
        or bool(re.search(r"\b[A-Za-z].+?, .+?, and .+?\.", value)) and len(value.split()) < 18
    )


def lacks_document_explanation(value: str) -> bool:
    lowered = value.lower()
    return not any(token in lowered for token in ["this is", "invoice", "receipt", "guide", "syllabus", "resume", "profile", "notice", "memo", "bill"])


def lacks_meaningful_detail(value: str, expectations: dict[str, Any]) -> bool:
    keywords = [keyword.lower() for keyword in expectations.get("summary_keywords", [])]
    if not keywords:
        return False
    hits = sum(keyword in value.lower() for keyword in keywords)
    return hits == 0


def important_points_too_generic(points: list[str]) -> bool:
    generic = {
        "generic document",
        "review the document",
        "important date detected",
        "follow-up details",
    }
    lowered = [point.lower() for point in points]
    return sum(any(g in point for g in generic) for point in lowered) >= max(2, len(points) // 2)


def looks_like_raw_fragment(value: str) -> bool:
    lowered = value.lower()
    if lowered.startswith(("review ", "check ", "confirm ")) and len(value.split()) <= 8:
        return False
    return (
        len(value.split()) > 14
        or value.count(":") >= 2
        or ";" in value
        or bool(re.search(r"\b(attendance|policy|students should|late work|must submit|academic integrity)\b", lowered))
    )


def looks_review_oriented(value: str) -> bool:
    lowered = value.lower()
    return any(lowered.startswith(prefix) for prefix in ["review", "check", "confirm", "pay", "file", "prepare", "handle"])


def conflicting_tag(profile: str, tags: list[str]) -> str | None:
    conflicts = {
        "syllabus": {"memo", "notice", "office", "generic_document", "other"},
        "course_guide": {"memo", "notice", "office", "generic_document", "other"},
        "presentation_guide": {"receipt", "retail", "food_drink", "repair_service", "utilities", "notice", "generic_document", "other"},
        "resume_profile": {"receipt", "retail", "food_drink", "utilities", "memo", "notice", "profile_record", "generic_document", "other"},
        "profile_record": {"receipt", "retail", "food_drink", "utilities", "memo", "notice", "generic_document", "other"},
        "repair_service_receipt": {"utilities", "notice", "memo", "generic_document", "other"},
        "utility_bill": {"invoice", "repair_service", "retail", "receipt", "notice", "memo", "time-sensitive", "generic_document", "other"},
        "invoice": {"retail", "food_drink", "utilities", "receipt", "notice", "memo", "time-sensitive", "generic_document", "other"},
        "meeting_notice": {"receipt", "retail", "food_drink", "utilities", "generic_document", "other"},
        "instructional_memo": {"receipt", "retail", "food_drink", "repair_service", "utilities", "notice", "generic_document", "other"},
    }
    for tag in tags:
        if tag in conflicts.get(profile, set()):
            return tag
    return None


def final_output_feels_fallback_like(actual: dict[str, Any]) -> bool:
    summary = (actual.get("summary_detailed") or "").lower()
    important_points = " ".join(actual.get("important_points") or []).lower()
    return (
        "generic document" in summary
        or "file this receipt for spending review" in summary
        or ("generic document" in important_points and actual.get("profile") not in {"generic_document", "other"})
    )


def score_issues(issues: list[EvalIssue]) -> int:
    score = 100
    for issue in issues:
        score -= 18 if issue.severity == "fail" else 7
    return max(0, score)


def build_report(spec: dict[str, Any], results: list[dict[str, Any]], label: str, mode: str, backend_url: str | None) -> dict[str, Any]:
    issue_counts = Counter()
    severity_counts = Counter()
    status_counts = Counter(result["actual"].get("status", "unknown") for result in results)
    for result in results:
        for issue in result["issues"]:
            issue_counts[issue["code"]] += 1
            severity_counts[issue["severity"]] += 1
    avg_score = round(sum(result["score"] for result in results) / len(results), 2) if results else 0.0
    problematic = sorted(results, key=lambda entry: (len(entry["issues"]), -entry["score"]), reverse=True)[:5]
    return {
        "label": label,
        "mode": mode,
        "backend_url": backend_url,
        "generated_at": datetime.now().isoformat(),
        "spec_version": spec.get("version"),
        "documents_total": len(results),
        "average_score": avg_score,
        "status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "issue_counts": dict(issue_counts.most_common()),
        "problem_documents": [
            {
                "id": entry["id"],
                "filename": entry["filename"],
                "score": entry["score"],
                "issue_codes": [issue["code"] for issue in entry["issues"]],
            }
            for entry in problematic
        ],
        "results": results,
    }


def compare_reports(previous_path: Path, current_report: dict[str, Any]) -> dict[str, Any]:
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    prev_by_id = {entry["id"]: entry for entry in previous.get("results", [])}
    improved = []
    regressed = []
    unchanged = []
    for entry in current_report.get("results", []):
        prior = prev_by_id.get(entry["id"])
        if not prior:
            continue
        delta = entry["score"] - prior.get("score", 0)
        if delta > 0:
            improved.append({"id": entry["id"], "delta": delta})
        elif delta < 0:
            regressed.append({"id": entry["id"], "delta": delta})
        else:
            unchanged.append(entry["id"])
    return {
        "previous_mode": previous.get("mode"),
        "current_mode": current_report.get("mode"),
        "previous_average_score": previous.get("average_score"),
        "current_average_score": current_report.get("average_score"),
        "average_score_delta": round(current_report.get("average_score", 0) - previous.get("average_score", 0), 2),
        "improved_documents": improved,
        "regressed_documents": regressed,
        "unchanged_documents": unchanged,
        "previous_issue_counts": previous.get("issue_counts", {}),
        "current_issue_counts": current_report.get("issue_counts", {}),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# Docparse Quality Report ({report['label']})",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Documents: `{report['documents_total']}`",
        f"- Average score: `{report['average_score']}`",
        f"- Status counts: `{report['status_counts']}`",
        f"- Severity counts: `{report['severity_counts']}`",
    ]
    if report.get("backend_url"):
        lines.append(f"- Backend URL: `{report['backend_url']}`")
    lines.extend(["", "## Top Issue Patterns", ""])
    for code, count in list(report.get("issue_counts", {}).items())[:12]:
        lines.append(f"- `{code}`: {count}")
    lines.extend(["", "## Most Problematic Documents", ""])
    for problem in report.get("problem_documents", []):
        lines.append(f"- `{problem['filename']}` score `{problem['score']}` issues `{', '.join(problem['issue_codes']) or 'none'}`")
    if report.get("comparison"):
        cmp = report["comparison"]
        lines.extend(
            [
                "",
                "## Comparison",
                "",
                f"- Previous mode: `{cmp['previous_mode']}`",
                f"- Current mode: `{cmp['current_mode']}`",
                f"- Previous average score: `{cmp['previous_average_score']}`",
                f"- Current average score: `{cmp['current_average_score']}`",
                f"- Delta: `{cmp['average_score_delta']}`",
                f"- Improved docs: `{cmp['improved_documents']}`",
                f"- Regressed docs: `{cmp['regressed_documents']}`",
            ]
        )
    lines.extend(["", "## Per-Document Results", ""])
    for result in report.get("results", []):
        lines.append(f"### `{result['filename']}`")
        lines.append("")
        lines.append(f"- Score: `{result['score']}`")
        lines.append(f"- Profile: `{result['actual'].get('profile')}`")
        lines.append(f"- Category: `{result['actual'].get('category')}`")
        lines.append(f"- Broad type: `{result['actual'].get('broad_type')}`")
        lines.append(f"- Title: `{result['actual'].get('title')}`")
        lines.append(f"- Provider chain: `{result['actual'].get('provider_chain')}`")
        if result["issues"]:
            lines.append("- Issues:")
            for issue in result["issues"]:
                lines.append(f"  - [{issue['severity']}] `{issue['code']}` {issue['message']}")
        else:
            lines.append("- Issues: none")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_terminal_summary(report: dict[str, Any]) -> str:
    summary = [
        f"Mode: {report['mode']}",
        f"Average score: {report['average_score']}",
        f"Status counts: {report['status_counts']}",
        f"Severity counts: {report['severity_counts']}",
        "Top issue patterns:",
    ]
    for code, count in list(report.get("issue_counts", {}).items())[:10]:
        summary.append(f"  - {code}: {count}")
    return "\n".join(summary)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    main()
