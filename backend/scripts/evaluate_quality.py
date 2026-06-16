from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.models.document import Document, ProcessingStatus
from app.services.ai_document_understanding import LocalDocumentAIService, get_document_ai_service
from app.services.document_interpretation_service import DocumentInterpretationService
from app.services.document_processor import DocumentProcessor
from app.services.document_router import LightweightDocumentRouter
from app.services.file_ingestion import FileIngestionService
from app.services.ocr import OCRService
from app.services.parser import DocumentParser
from app.services.quality_evaluation import DocumentQualityEvaluator
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "eval" / "specs" / "eval_documents.json"
CORPUS_DIR = ROOT / "eval" / "corpus"
REPORTS_DIR = ROOT / "eval" / "reports"


@dataclass
class CaseIssue:
    level: str
    code: str
    message: str


@dataclass
class CaseResult:
    case_id: str
    filename: str
    source_file_type: str | None
    extraction_method: str | None
    broad_type: str | None
    category: str | None
    profile: str | None
    subtype: str | None
    title: str | None
    summary_short: str | None
    summary_detailed: str | None
    action_items: list[str]
    tags: list[str]
    provider_chain: str | None
    processing_status: str
    processing_error: str | None
    important_points: list[str] = field(default_factory=list)
    issues: list[CaseIssue] = field(default_factory=list)


class EvalRunner:
    def __init__(self) -> None:
        self.processor = DocumentProcessor()
        self.ingestion = FileIngestionService(ocr=OCRService())
        self.parser = DocumentParser()
        self.quality = DocumentQualityEvaluator()
        self.router = LightweightDocumentRouter()
        self.lightweight_ai = LocalDocumentAIService()
        self.category_interpreter = DocumentInterpretationService()
        self.workflow = DocumentWorkflowEnrichmentService()

    def run_case(self, spec: dict[str, Any]) -> CaseResult:
        path = CORPUS_DIR / spec["filename"]
        mime_type = self._mime_from_suffix(path.suffix.lower())
        document = Document(
            id=uuid4(),
            original_filename=path.name,
            stored_file_path=str(path),
            mime_type=mime_type,
            processing_status=ProcessingStatus.uploaded,
            review_required=False,
            tags=[],
        )
        try:
            normalized = self.ingestion.ingest(path, path.name, mime_type)
            raw_text = normalized.normalized_text
            parsed = self.parser.parse(raw_text, path.name)
            extraction_quality = self.quality.evaluate_extraction(normalized, parsed)
            route = self.router.route(normalized, parsed, extraction_quality)
            analysis_path = normalized.primary_image_path or path
            if route.heavy_ai_required and normalized.primary_image_path:
                ai_result = get_document_ai_service().analyze(analysis_path, raw_text, parsed, path.name)
            else:
                ai_result = self.lightweight_ai.analyze(analysis_path, raw_text, parsed, path.name)
                ai_result.extraction_provider = normalized.extraction_method or route.route_label
                ai_result.provider = ai_result.extraction_provider
                ai_result.provider_chain = [normalized.extraction_method or route.route_label, "heuristic_fallback"]
                ai_result.merge_strategy = route.route_label

            document.raw_text = raw_text
            document.source_file_type = normalized.source_file_type
            document.extraction_method = normalized.extraction_method
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
            document.summary = ai_result.summary
            interpretation = self.category_interpreter.interpret(document, ai_result.cleaned_raw_text or raw_text)
            document.title = self.processor._apply_title_hint(document.title, interpretation)
            document.category = self.processor._apply_category_hint(document.category, interpretation)
            document.document_type = self.processor._refined_document_type(document.document_type, interpretation)
            document.title = self.processor._clean_final_title(document.title, interpretation)
            document.merchant_name = self.processor._clean_final_merchant(document.merchant_name)
            document.tags = self.processor._merge_tags(document.tags, interpretation, document.document_type)
            if interpretation.summary_hint:
                document.summary = interpretation.summary_hint
            workflow = self.workflow.enrich(document, ai_result.cleaned_raw_text or raw_text, interpretation)
            document.workflow_summary = workflow.workflow_summary
            document.action_items = workflow.action_items
            document.warnings = workflow.warnings
            document.key_dates = workflow.key_dates
            document.urgency_level = workflow.urgency_level
            document.follow_up_required = workflow.follow_up_required
            document.workflow_metadata = workflow.workflow_metadata or None
            document.provider_chain = "+".join(
                self.processor._provider_chain(
                    normalized,
                    route,
                    ai_result.provider_chain or [ai_result.provider],
                    interpretation.provider_chain,
                )
            )
            document.processing_status = ProcessingStatus.needs_review if (document.review_required or workflow.warnings) else ProcessingStatus.ready

            result = CaseResult(
                case_id=spec["id"],
                filename=path.name,
                source_file_type=document.source_file_type,
                extraction_method=document.extraction_method,
                broad_type=document.document_type.value if document.document_type else None,
                category=document.category,
                profile=interpretation.profile,
                subtype=interpretation.subtype,
                title=document.title,
                summary_short=((document.workflow_metadata or {}).get("summaries") or {}).get("short"),
                summary_detailed=((document.workflow_metadata or {}).get("summaries") or {}).get("detailed"),
                action_items=document.action_items,
                tags=document.tags,
                provider_chain=document.provider_chain,
                processing_status=document.processing_status.value,
                processing_error=None,
                important_points=((document.workflow_metadata or {}).get("important_points") or []),
            )
        except Exception as exc:
            result = CaseResult(
                case_id=spec["id"],
                filename=path.name,
                source_file_type=None,
                extraction_method=None,
                broad_type=None,
                category=None,
                profile=None,
                subtype=None,
                title=None,
                summary_short=None,
                summary_detailed=None,
                action_items=[],
                tags=[],
                provider_chain=None,
                processing_status="failed",
                processing_error=str(exc),
            )
        result.issues = self._evaluate_case(result, spec)
        return result

    def _evaluate_case(self, result: CaseResult, spec: dict[str, Any]) -> list[CaseIssue]:
        issues: list[CaseIssue] = []
        if result.processing_error:
            issues.append(CaseIssue("failure", "runtime_failure", result.processing_error))
            return issues

        forbidden_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in spec.get("title_forbidden_patterns", [])]
        if not result.title:
            issues.append(CaseIssue("failure", "missing_title", "No title was produced."))
        elif any(pattern.search(result.title) for pattern in forbidden_patterns):
            issues.append(CaseIssue("failure", "bad_title_pattern", f"Title looks weak or body-like: {result.title}"))
        elif self._looks_sentence_like(result.title):
            issues.append(CaseIssue("warning", "sentence_like_title", f"Title still looks sentence-like: {result.title}"))

        expected_profiles = set(spec.get("profile_expected", []))
        if expected_profiles and result.profile not in expected_profiles:
            issues.append(CaseIssue("warning", "profile_mismatch", f"Expected one of {sorted(expected_profiles)}, got {result.profile}"))
        if result.profile == "generic_document" and expected_profiles and "generic_document" not in expected_profiles:
            issues.append(CaseIssue("failure", "overly_generic_profile", "Profile stayed generic despite stronger expected evidence."))

        expected_broad = set(spec.get("broad_type_expected", []))
        if expected_broad and result.broad_type not in expected_broad:
            issues.append(CaseIssue("warning", "broad_type_mismatch", f"Expected broad type in {sorted(expected_broad)}, got {result.broad_type}"))

        if not result.summary_short or len(result.summary_short.split()) < 4:
            issues.append(CaseIssue("warning", "weak_summary_short", "Short summary is too thin."))
        if not result.summary_detailed or len(result.summary_detailed.split()) < 12:
            issues.append(CaseIssue("warning", "weak_summary_detailed", "Detailed summary is too thin."))
        if result.summary_detailed and self._mechanical_summary(result.summary_detailed):
            issues.append(CaseIssue("warning", "mechanical_summary", "Detailed summary still sounds mechanically assembled."))

        keywords = [keyword.lower() for keyword in spec.get("summary_keywords", [])]
        if keywords and result.summary_detailed:
            hits = sum(keyword in result.summary_detailed.lower() for keyword in keywords)
            if hits == 0:
                issues.append(CaseIssue("warning", "summary_missing_core_concepts", "Detailed summary misses expected core concepts."))

        important_keywords = [keyword.lower() for keyword in spec.get("important_keywords", [])]
        if important_keywords and result.important_points:
            joined = " ".join(result.important_points).lower()
            if not any(keyword in joined for keyword in important_keywords):
                issues.append(CaseIssue("warning", "important_points_weak", "Important points miss expected core signals."))

        for item in result.action_items:
            if len(item.split()) > 12:
                issues.append(CaseIssue("warning", "long_action_item", f"Action item is too long: {item}"))
            if self._looks_extracted_fragment(item):
                issues.append(CaseIssue("warning", "fragment_action_item", f"Action item still looks like copied source text: {item}"))

        if result.profile and result.profile not in {"generic_document", "other"}:
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
            conflicting_tags = conflicts.get(result.profile, {"generic_document", "other"})
            for tag in result.tags:
                if tag in conflicting_tags:
                    issues.append(CaseIssue("warning", "stale_tag", f"Tag conflicts with stronger final interpretation: {tag}"))

        if not result.provider_chain:
            issues.append(CaseIssue("failure", "missing_provider_chain", "Provider chain is missing."))
        return issues

    def _mime_from_suffix(self, suffix: str) -> str:
        mapping = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".png": "image/png",
            ".json": "application/json",
            ".csv": "text/csv",
            ".html": "text/html",
            ".xml": "application/xml",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        return mapping.get(suffix, "application/octet-stream")

    def _looks_sentence_like(self, value: str) -> bool:
        lowered = value.lower()
        return len(value.split()) >= 8 and bool(re.search(r"\b(is|are|will|introduces|provides|covers|describes|contains)\b", lowered))

    def _mechanical_summary(self, value: str) -> bool:
        lowered = value.lower()
        return ", and." in lowered or lowered.count("it ") >= 3 or "the most important details are" in lowered

    def _looks_extracted_fragment(self, value: str) -> bool:
        lowered = value.lower()
        return ":" in value and len(value.split()) > 8 or bool(re.match(r"^(please|students|policy|grading|attendance)\b", lowered))


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    failures = sum(1 for result in results for issue in result.issues if issue.level == "failure")
    warnings = sum(1 for result in results for issue in result.issues if issue.level == "warning")
    recurring: dict[str, int] = {}
    for result in results:
        for issue in result.issues:
            recurring[issue.code] = recurring.get(issue.code, 0) + 1
    worst_cases = sorted(
        results,
        key=lambda case: (
            -sum(1 for issue in case.issues if issue.level == "failure"),
            -len(case.issues),
        ),
    )[:5]
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "case_count": len(results),
        "failure_count": failures,
        "warning_count": warnings,
        "recurring_issue_codes": sorted(recurring.items(), key=lambda item: (-item[1], item[0])),
        "worst_cases": [
            {
                "case_id": case.case_id,
                "filename": case.filename,
                "issue_count": len(case.issues),
                "failures": [issue.code for issue in case.issues if issue.level == "failure"],
            }
            for case in worst_cases
        ],
    }


def render_markdown(summary: dict[str, Any], results: list[CaseResult]) -> str:
    lines = [
        "# Docparse Evaluation Report",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Failures: {summary['failure_count']}",
        f"- Warnings: {summary['warning_count']}",
        "",
        "## Recurring issues",
    ]
    for code, count in summary["recurring_issue_codes"][:10]:
        lines.append(f"- `{code}`: {count}")
    lines.extend(["", "## Case results"])
    for result in results:
        lines.append(f"### {result.case_id} ({result.filename})")
        lines.append(f"- Profile: `{result.profile}`")
        lines.append(f"- Category: `{result.category}`")
        lines.append(f"- Broad type: `{result.broad_type}`")
        lines.append(f"- Title: {result.title}")
        lines.append(f"- Summary short: {result.summary_short}")
        lines.append(f"- Summary detailed: {result.summary_detailed}")
        lines.append(f"- Action items: {', '.join(result.action_items) or 'None'}")
        lines.append(f"- Tags: {', '.join(result.tags) or 'None'}")
        if result.issues:
            lines.append("- Issues:")
            for issue in result.issues:
                lines.append(f"  - [{issue.level}] `{issue.code}`: {issue.message}")
        else:
            lines.append("- Issues: none")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="latest")
    args = parser.parse_args()

    specs = json.loads(SPEC_PATH.read_text())
    runner = EvalRunner()
    results = [runner.run_case(spec) for spec in specs]
    summary = summarize(results)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "results": [
            {
                **asdict(result),
                "issues": [asdict(issue) for issue in result.issues],
            }
            for result in results
        ],
    }
    json_path = REPORTS_DIR / f"{args.label}.json"
    md_path = REPORTS_DIR / f"{args.label}.md"
    json_path.write_text(json.dumps(payload, indent=2))
    md_path.write_text(render_markdown(summary, results))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
