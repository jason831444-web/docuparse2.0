import logging
import re
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.ai_document_understanding import LocalDocumentAIService, get_document_ai_service
from app.services.ai_escalation import should_escalate_to_ai
from app.services.ai_merge import AIResultMerger
from app.services.category_interpretation import CategoryInterpretation, CategoryInterpretationService
from app.services.category_taxonomy import clean_tags_for_context, normalize_category
from app.services.document_router import LightweightDocumentRouter
from app.services.document_interpretation_service import DocumentInterpretationService
from app.services.document_taxonomy import DocumentTaxonomyService
from app.services.file_ingestion import FileIngestionService, NormalizedDocument
from app.services.image_preprocessor import ImagePreprocessor
from app.services.item_master_matcher import ItemMasterMatcher
from app.services.ocr import OCRService
from app.services.parser import DocumentParser
from app.services.persistence_safety import sanitize_for_postgres
from app.services.quality_evaluation import DocumentQualityEvaluator, QualityEvaluation
from app.services.table_layout import BBoxTableReconstructor
from app.services.vl_candidate_client import VLCandidateWorkerClient
from app.services.vl_candidate_parser import VLCandidateParser
from app.services.vl_candidate_validation import VLCandidateValidationGate
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


logger = logging.getLogger(__name__)
_PROCESSING_SEMAPHORE = threading.BoundedSemaphore(get_settings().document_processing_concurrency)


def _safe_remote_upload_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key in ("mode", "uploaded_bytes", "content_type"):
        if value.get(key) not in (None, "", []):
            metadata[key] = value.get(key)
    if value.get("saved_path"):
        metadata["saved_path_present"] = True
    return metadata


class DocumentProcessor:
    def __init__(self, ocr: OCRService | None = None, parser: DocumentParser | None = None) -> None:
        self.settings = get_settings()
        self.ocr = ocr or OCRService()
        self.parser = parser or DocumentParser()
        self.ingestion = FileIngestionService(ocr=self.ocr)
        self.quality = DocumentQualityEvaluator()
        self.router = LightweightDocumentRouter()
        self.lightweight_ai = LocalDocumentAIService()
        self.heuristic_interpreter = CategoryInterpretationService()
        self.category_interpreter = DocumentInterpretationService()
        self.taxonomy = DocumentTaxonomyService()
        self.workflow_enrichment = DocumentWorkflowEnrichmentService()
        self.item_master_matcher = ItemMasterMatcher()
        self.ai_merger = AIResultMerger()
        self.bbox_table_reconstructor = BBoxTableReconstructor()
        self.vl_worker = VLCandidateWorkerClient()
        self.vl_candidate_parser = VLCandidateParser(parser=self.parser)
        self.vl_candidate_gate = VLCandidateValidationGate()
        self.image_preprocessor = ImagePreprocessor(self.ingestion.document_quality)

    def process(self, db: Session, document: Document) -> Document:
        with _PROCESSING_SEMAPHORE:
            return self._process_locked(db, document)

    def _process_locked(self, db: Session, document: Document) -> Document:
        document.processing_status = ProcessingStatus.processing
        document.processing_error = None
        db.add(document)
        db.commit()
        db.refresh(document)
        try:
            stored_path = Path(document.stored_file_path)
            vl_primary_attempt = self._vl_primary_reader_attempt(stored_path, document, {})
            vl_primary_drives_reader = self._vl_primary_attempt_should_drive_reader(vl_primary_attempt)
            if vl_primary_drives_reader:
                normalized = self._vl_primary_normalized_document(
                    stored_path,
                    document,
                    str(vl_primary_attempt["text"]),
                    vl_primary_attempt.get("metadata") or {},
                )
            else:
                normalized = self.ingestion.ingest(stored_path, document.original_filename, document.mime_type)
            raw_text = normalized.normalized_text
            parsed = self.parser.parse(raw_text, document.original_filename)
            vl_promoted_structured = self._vl_primary_structured_candidate(vl_primary_attempt) if vl_primary_drives_reader else None
            if vl_promoted_structured:
                self._apply_vl_structured_candidate_to_parsed(parsed, vl_promoted_structured)
                self._reconcile_vl_parsed_with_pdf_text_layer(parsed, stored_path, normalized)
            extraction_quality = self.quality.evaluate_extraction(normalized, parsed)
            route = self.router.route(normalized, parsed, extraction_quality)
            analysis_path = normalized.primary_image_path or stored_path
            ai_fallback_notes: list[str] = []
            ai_provider_diagnostics = {
                "document_ai_attempted": bool(route.heavy_ai_required and normalized.primary_image_path),
                "document_ai_succeeded": False,
                "document_ai_provider": None,
                "document_ai_failed_reason": None,
                "document_ai_fallback_provider": None,
                "primary_provider": None,
                "primary_provider_status": None,
                "primary_provider_failed_reason": None,
            }
            if route.heavy_ai_required and normalized.primary_image_path:
                try:
                    ai_result = get_document_ai_service().analyze(
                        analysis_path,
                        raw_text,
                        parsed,
                        document.original_filename,
                    )
                    ai_provider_diagnostics["document_ai_succeeded"] = True
                    ai_provider_diagnostics["document_ai_provider"] = ai_result.provider
                    self._apply_ai_provider_chain_diagnostics(ai_provider_diagnostics, ai_result.provider_chain or [])
                except Exception as exc:
                    ai_fallback_notes.append(f"AI extraction failed; parser result used: {exc}")
                    ai_provider_diagnostics["document_ai_failed_reason"] = str(exc)
                    ai_result = self.lightweight_ai.analyze(analysis_path, raw_text, parsed, document.original_filename)
                    ai_provider_diagnostics["document_ai_fallback_provider"] = ai_result.provider
                    ai_provider_diagnostics["primary_provider_status"] = "failed"
                    ai_provider_diagnostics["primary_provider_failed_reason"] = str(exc)
            else:
                ai_result = self.lightweight_ai.analyze(analysis_path, raw_text, parsed, document.original_filename)
                ai_result.extraction_provider = normalized.extraction_method or route.route_label
                ai_result.provider = ai_result.extraction_provider
                ai_result.provider_chain = [normalized.extraction_method or route.route_label, "heuristic_fallback"]
                ai_result.merge_strategy = route.route_label
                self._apply_ai_provider_chain_diagnostics(ai_provider_diagnostics, ai_result.provider_chain or [])
            if self._is_manufacturing_parsed_type(parsed):
                merge = self.ai_merger.merge(parsed, ai_result)
                ai_result = merge.result
            structured_quality = self.quality.evaluate_structured_result(document, ai_result, extraction_quality)
            ingestion_notes = self._ingestion_notes(normalized, route)

            document.raw_text = sanitize_for_postgres(raw_text)
            document.mime_type = normalized.mime_type or document.mime_type
            document.source_file_type = normalized.source_file_type
            document.extraction_method = normalized.extraction_method
            document.ingestion_metadata = sanitize_for_postgres(self._ingestion_metadata(normalized, route, extraction_quality, structured_quality))
            document.confidence_score = ai_result.confidence_score or self._confidence(normalized)
            document.ai_document_type = ai_result.document_type
            document.ai_confidence_score = ai_result.confidence_score
            quality_notes = self._quality_notes(extraction_quality, structured_quality)
            document.ai_extraction_notes = sanitize_for_postgres(self._notes(ingestion_notes + quality_notes + ai_result.extraction_notes))
            document.review_required = (
                ai_result.review_required
                or route.review_required
                or extraction_quality.review_required
                or structured_quality.review_required
                or bool(normalized.extraction_warnings)
            )
            document.summary = sanitize_for_postgres(ai_result.summary)
            document.extraction_provider = ai_result.extraction_provider or ai_result.provider
            document.refinement_provider = ai_result.refinement_provider
            provider_chain = self._provider_chain(normalized, route, ai_result.provider_chain or [ai_result.provider])
            document.provider_chain = "+".join(provider_chain)
            document.merge_strategy = ai_result.merge_strategy
            document.field_sources = sanitize_for_postgres(ai_result.field_sources or None)
            document.document_type = ai_result.document_type or parsed.document_type
            document.title = sanitize_for_postgres(ai_result.title or parsed.title)
            deterministic_first = self._parsed_manufacturing_has_business_data(parsed)
            document.extracted_date = parsed.extracted_date or ai_result.extracted_date if deterministic_first else ai_result.extracted_date or parsed.extracted_date
            selected_extracted_amount = parsed.extracted_amount or ai_result.extracted_amount if deterministic_first else ai_result.extracted_amount or parsed.extracted_amount
            selected_subtotal = (parsed.subtotal or ai_result.subtotal) if deterministic_first else (ai_result.subtotal or parsed.subtotal)
            selected_tax = (parsed.tax or ai_result.tax) if deterministic_first else (ai_result.tax or parsed.tax)
            document.extracted_amount = self._nonnegative_document_amount(selected_extracted_amount)
            document.subtotal = self._nonnegative_document_amount(selected_subtotal)
            document.tax = self._nonnegative_document_amount(selected_tax)
            document.currency = ai_result.currency or parsed.currency
            document.merchant_name = sanitize_for_postgres(ai_result.merchant_name or parsed.merchant_name)
            document.vendor_name = sanitize_for_postgres((parsed.vendor_name or ai_result.vendor_name) if deterministic_first else (ai_result.vendor_name or parsed.vendor_name) or document.merchant_name)
            document.customer_name = sanitize_for_postgres((parsed.customer_name or ai_result.customer_name) if deterministic_first else (ai_result.customer_name or parsed.customer_name))
            document.document_number = sanitize_for_postgres((parsed.document_number or ai_result.document_number) if deterministic_first else (ai_result.document_number or parsed.document_number))
            document.issue_date = (parsed.issue_date or ai_result.issue_date or document.extracted_date) if deterministic_first else (ai_result.issue_date or parsed.issue_date or document.extracted_date)
            document.due_date = (parsed.due_date or ai_result.due_date) if deterministic_first else (ai_result.due_date or parsed.due_date)
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                document.issue_date, document.due_date = self._normalize_manufacturing_dates(parsed, document.issue_date, document.due_date)
            preserve_return_credit_amounts = self._is_return_or_credit_parsed_document(parsed, raw_text)
            selected_line_items = (parsed.line_items or ai_result.line_items) if deterministic_first else (ai_result.line_items or parsed.line_items or [])
            document.line_items = sanitize_for_postgres(
                self._line_items_for_extraction_method(
                    selected_line_items,
                    normalized.extraction_method,
                    preserve_signed_amount_rows=preserve_return_credit_amounts,
                )
            )
            document.low_confidence_fields = sanitize_for_postgres([] if deterministic_first and parsed.line_items else ai_result.low_confidence_fields or [])
            document.category = ai_result.category or parsed.category
            document.tags = sanitize_for_postgres(ai_result.tags or parsed.tags)
            ai_escalation = should_escalate_to_ai(normalized, parsed, extraction_quality)
            interpretation = self._interpret_document(
                document,
                ai_result.cleaned_raw_text or raw_text,
                normalized,
                parsed,
                deterministic_first,
                ai_escalation.should_escalate and self._is_ai_escalation_source(normalized),
            )
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                interpretation = self._normalize_manufacturing_interpretation(interpretation, parsed)
            parser_only = self._is_parser_only_interpretation(interpretation)
            provider_chain = self._provider_chain(
                normalized,
                route,
                [] if parser_only else ai_result.provider_chain or [ai_result.provider],
                interpretation.provider_chain,
            )
            document.provider_chain = "+".join(provider_chain)
            document.refinement_provider = self._refinement_provider_for_interpretation(interpretation)
            document.title = self._apply_title_hint(document.title, interpretation)
            document.category = self._apply_category_hint(document.category, interpretation)
            document.document_type = self._refined_document_type(document.document_type, interpretation)
            if self._is_return_or_credit_parsed_document(parsed, raw_text):
                document.document_type = parsed.document_type
                document.ai_document_type = parsed.document_type
                document.document_number = sanitize_for_postgres(parsed.document_number or document.document_number)
                if getattr(parsed, "line_items", None):
                    document.line_items = sanitize_for_postgres(
                        self._line_items_for_extraction_method(
                            parsed.line_items,
                            normalized.extraction_method,
                            preserve_signed_amount_rows=True,
                        )
                    )
                document.category = self._return_or_credit_category(parsed, raw_text)
                document.tags = [document.category]
            if self._is_internal_transfer_parsed_document(parsed, raw_text):
                internal_transfer_type = self._internal_transfer_document_type(parsed)
                document.document_type = internal_transfer_type
                document.ai_document_type = internal_transfer_type
                document.document_number = sanitize_for_postgres(parsed.document_number or document.document_number)
                document.extracted_amount = None
                document.subtotal = None
                document.tax = None
                document.currency = None
                if getattr(parsed, "line_items", None):
                    document.line_items = sanitize_for_postgres(
                        self._line_items_for_extraction_method(parsed.line_items, normalized.extraction_method)
                    )
                document.category = "internal_transfer"
                document.tags = ["internal_transfer"]
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                if self._is_internal_transfer_parsed_document(parsed, raw_text):
                    internal_transfer_type = self._internal_transfer_document_type(parsed)
                    document.document_type = internal_transfer_type
                    document.ai_document_type = internal_transfer_type
                    document.category = parsed.category or "internal_transfer"
                    document.tags = ["internal_transfer"]
                else:
                    document.document_type = parsed.document_type
                    document.ai_document_type = parsed.document_type
                    document.category = parsed.category or parsed.document_type.value
                    document.tags = [parsed.document_type.value]
                document.line_items = sanitize_for_postgres(
                    self.item_master_matcher.match_line_items(
                        db,
                        self._line_items_for_extraction_method(
                            document.line_items or [],
                            normalized.extraction_method,
                            preserve_signed_amount_rows=preserve_return_credit_amounts,
                        ),
                    )
                )
                if preserve_return_credit_amounts:
                    document.line_items = sanitize_for_postgres(
                        self._restore_return_credit_visible_amounts(
                            document.line_items or [],
                            getattr(parsed, "line_items", None) or [],
                        )
                    )
            document.title = self._clean_final_title(document.title, interpretation)
            document.merchant_name = self._clean_final_merchant(document.merchant_name)
            if interpretation.summary_hint:
                document.summary = sanitize_for_postgres(interpretation.summary_hint)
            document.tags = self._merge_tags(document.tags, interpretation, document.document_type)
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                if self._is_internal_transfer_parsed_document(parsed, raw_text):
                    document.tags = ["internal_transfer"]
                elif preserve_return_credit_amounts:
                    document.tags = [self._return_or_credit_category(parsed, raw_text)]
                else:
                    document.tags = [parsed.document_type.value]
            business_safety_issues = self._apply_final_business_safety_overrides(document, raw_text)
            taxonomy = self.taxonomy.classify(
                document,
                ai_result.cleaned_raw_text or raw_text,
                extraction_method=normalized.extraction_method,
                file_metadata=normalized.file_metadata,
            )
            document.category = self._apply_taxonomy_category(document.category, taxonomy.to_metadata())
            document.tags = sanitize_for_postgres(self._merge_taxonomy_tags(document.tags, taxonomy.to_metadata()))
            document.ai_extraction_notes = sanitize_for_postgres(self._notes(
                (ingestion_notes + quality_notes + ai_fallback_notes + ai_result.extraction_notes)
                + self._interpretation_notes(interpretation)
            ))
            document.ingestion_metadata = sanitize_for_postgres(self._ingestion_metadata(
                normalized,
                route,
                extraction_quality,
                structured_quality,
                interpretation,
                ai_escalation,
                ai_provider_diagnostics,
                taxonomy.to_metadata(),
            ))
            document.review_required = document.review_required or self._manufacturing_review_required(document)
            vl_pipeline_metadata = (vl_primary_attempt or {}).get("metadata")
            workflow = self.workflow_enrichment.enrich(document, ai_result.cleaned_raw_text or raw_text, interpretation)
            document.workflow_summary = sanitize_for_postgres(workflow.workflow_summary)
            if self._is_manufacturing_type(document):
                document.summary = sanitize_for_postgres(workflow.workflow_summary)
            document.action_items = sanitize_for_postgres(workflow.action_items)
            document.warnings = sanitize_for_postgres(workflow.warnings)
            document.key_dates = sanitize_for_postgres(workflow.key_dates)
            document.urgency_level = workflow.urgency_level
            document.follow_up_required = workflow.follow_up_required
            workflow_metadata = workflow.workflow_metadata or {}
            if business_safety_issues:
                issues = list(workflow_metadata.get("normalized_review_issues") or [])
                existing_codes = {issue.get("code") for issue in issues if isinstance(issue, dict)}
                for issue in business_safety_issues:
                    if issue.get("code") not in existing_codes:
                        issues.append(issue)
                        existing_codes.add(issue.get("code"))
                workflow_metadata["normalized_review_issues"] = issues
                workflow_metadata["review_required"] = True
                workflow_metadata["business_safety_sanitizer"] = {
                    "source": "final_business_safety_overrides",
                    "issue_codes": [issue.get("code") for issue in business_safety_issues],
                }
            if vl_pipeline_metadata:
                workflow_metadata = self._merge_vl_pipeline_metadata(workflow_metadata, vl_pipeline_metadata)
            layout_debug = self._bbox_layout_debug_metadata(normalized, document, workflow_metadata)
            if layout_debug:
                workflow_metadata["layout_debug"] = layout_debug
                layout_issue = self._bbox_layout_review_issue(layout_debug)
                if layout_issue:
                    issues = list(workflow_metadata.get("normalized_review_issues") or [])
                    if not any(issue.get("code") == layout_issue["code"] for issue in issues if isinstance(issue, dict)):
                        issues.append(layout_issue)
                    workflow_metadata["normalized_review_issues"] = issues
            document_quality = self._document_quality_workflow_metadata(normalized)
            if document_quality:
                workflow_metadata["document_quality"] = document_quality
                quality_issues = self._document_quality_review_issues(document_quality)
                if quality_issues:
                    issues = list(workflow_metadata.get("normalized_review_issues") or [])
                    existing_codes = {issue.get("code") for issue in issues if isinstance(issue, dict)}
                    for issue in quality_issues:
                        if issue.get("code") not in existing_codes:
                            issues.append(issue)
                            existing_codes.add(issue.get("code"))
                    workflow_metadata["normalized_review_issues"] = issues
                    workflow_metadata["review_required"] = True
            field_provenance = self._field_provenance_metadata(document, normalized, workflow_metadata)
            if field_provenance:
                workflow_metadata["field_provenance"] = field_provenance
            document.workflow_metadata = sanitize_for_postgres(workflow_metadata or None)
            workflow_review_required = bool((workflow.workflow_metadata or {}).get("review_required"))
            document.review_required = workflow_review_required if self._is_manufacturing_type(document) else document.review_required or workflow_review_required
            document.review_required = document.review_required or bool((workflow_metadata.get("vl_candidate_summary") or {}).get("requires_review"))
            document.processing_status = ProcessingStatus.needs_review if document.review_required else ProcessingStatus.ready
            if parser_only:
                logger.info("Parser-only processing completed for document %s.", document.id)
        except Exception as exc:
            db.rollback()
            document = db.get(Document, document.id) or document
            document.processing_status = ProcessingStatus.failed
            document.processing_error = sanitize_for_postgres(str(exc))
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    def _vl_primary_reader_attempt(
        self,
        stored_path: Path,
        document: Document,
        workflow_metadata: dict,
    ) -> dict | None:
        if not self.vl_worker.enabled():
            return None
        input_variant = self._select_vl_input_variant(stored_path, document)
        input_path = stored_path
        if input_variant.get("processed_path"):
            input_path = Path(str(input_variant["processed_path"]))
        result = self.vl_worker.analyze(input_path, original_filename=document.original_filename)
        result["input_variant"] = input_variant
        metadata = self._vl_primary_reader_metadata_from_result(result, document, workflow_metadata)
        text = result.get("text") or result.get("text_preview")
        if not isinstance(text, str):
            text = ""
        metadata["vl_preprocess_mode"] = input_variant.get("variant_name") or "original_file"
        metadata["vl_preprocess_input"] = {
            "variant_name": input_variant.get("variant_name"),
            "operations": list(input_variant.get("operations") or []),
            "warnings": list(input_variant.get("warnings") or []),
            "processed_path_present": bool(input_variant.get("processed_path")),
            "error": input_variant.get("error"),
            "metadata": input_variant.get("metadata") if isinstance(input_variant.get("metadata"), dict) else {},
        }
        if isinstance(input_variant.get("vl_preprocess_policy"), dict):
            metadata["vl_preprocess_policy"] = input_variant["vl_preprocess_policy"]
        has_structured_candidate = bool(self._vl_primary_structured_candidate({"metadata": metadata}))
        return {
            "metadata": metadata,
            "text": text,
            "promoted": bool((metadata.get("vl_candidate_summary") or {}).get("promotion_applied")),
            "has_structured_candidate": has_structured_candidate,
        }

    def _select_vl_input_variant(self, stored_path: Path, document: Document) -> dict[str, Any]:
        suffix = stored_path.suffix.casefold()
        image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
        available_candidates = ["original"]
        policy: dict[str, Any] = {
            "selected_mode": "original",
            "reason": "default_original_vl_input",
            "available_candidates": available_candidates,
            "hidden_cropped_guardrail": False,
            "page_crop_applied": False,
            "page_crop_confidence": 0.0,
            "deskew_applied": False,
            "upscale_factor": None,
            "contrast_mode": None,
            "skipped_reasons": [],
            "current_standard": {
                "available": suffix in image_suffixes,
                "used": False,
                "skip_reason": "legacy_debug_only_not_used_by_default",
            },
        }

        if suffix == ".pdf":
            policy.update({
                "selected_mode": "pdf_render_only",
                "reason": "pdf_uses_worker_render_only",
                "available_candidates": ["pdf_render_only"],
            })
            return self._original_vl_input_variant(stored_path, policy, operations=["pdf_render_only_worker_input"])

        if suffix not in image_suffixes:
            policy.update({
                "reason": "non_image_original_file",
                "available_candidates": ["original_file"],
            })
            return self._original_vl_input_variant(stored_path, policy, operations=["original_file"])

        available_candidates.extend(["light_page_preprocess", "contrast_only", "current_standard_debug"])
        quality = self._safe_quality_for_vl_input(stored_path)
        if quality:
            policy["quality_summary"] = self._vl_preprocess_quality_summary(quality)

        hidden_or_cropped = bool(
            quality
            and (
                quality.get("possible_right_column_crop")
                or (quality.get("hidden_or_cropped_columns") or [])
            )
        )
        if hidden_or_cropped:
            policy.update({
                "selected_mode": "light_page_preprocess",
                "reason": "hidden_or_cropped_column_risk_crop_free_light_page",
                "hidden_cropped_guardrail": True,
            })
            return self._light_page_vl_input_variant(stored_path, document, policy, quality, avoid_page_crop=True)

        policy.update({
            "selected_mode": "light_page_preprocess",
            "reason": self._light_page_vl_reason_from_quality(quality),
        })
        return self._light_page_vl_input_variant(stored_path, document, policy, quality, avoid_page_crop=False)

    def _light_page_vl_input_variant(
        self,
        stored_path: Path,
        document: Document,
        policy: dict[str, Any],
        quality: dict[str, Any] | None,
        *,
        avoid_page_crop: bool,
    ) -> dict[str, Any]:
        output_dir = self.settings.upload_dir / "vl_preprocess_inputs" / str(document.id)
        variant = self.image_preprocessor.prepare_light_page_vl_input(
            stored_path,
            output_dir,
            quality=quality,
            avoid_page_crop=avoid_page_crop,
        )
        metadata = variant.get("metadata") if isinstance(variant.get("metadata"), dict) else {}
        policy.update({
            "page_crop_applied": bool(metadata.get("page_crop_applied")),
            "page_crop_confidence": metadata.get("page_crop_confidence"),
            "deskew_applied": bool(metadata.get("deskew_applied")),
            "upscale_factor": metadata.get("upscale_factor"),
            "contrast_mode": metadata.get("contrast_mode"),
            "skipped_reasons": list(metadata.get("skipped_reasons") or []),
        })
        if variant.get("processed_path"):
            variant["vl_preprocess_policy"] = policy
            return variant
        policy.update({
            "selected_mode": "original",
            "reason": "light_page_preprocess_unavailable_fallback_original",
            "light_page_error": variant.get("error"),
        })
        return self._original_vl_input_variant(stored_path, policy)

    def _original_vl_input_variant(
        self,
        stored_path: Path,
        policy: dict[str, Any],
        *,
        operations: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "variant_name": policy.get("selected_mode") or "original",
            "original_path": str(stored_path),
            "processed_path": None,
            "operations": operations or ["original_vl_input"],
            "warnings": ["current_standard_preprocess_not_used_by_default"],
            "vl_preprocess_policy": policy,
        }

    def _safe_quality_for_vl_input(self, stored_path: Path) -> dict[str, Any] | None:
        try:
            if not stored_path.exists():
                return None
            return self.ingestion.document_quality.analyze_document_quality([stored_path]).to_dict()
        except Exception:
            return None

    def _vl_preprocess_quality_summary(self, quality: dict[str, Any]) -> dict[str, Any]:
        pages = quality.get("pages") if isinstance(quality.get("pages"), list) else []
        first_page = pages[0] if pages and isinstance(pages[0], dict) else {}
        return {
            "likely_scan_type": quality.get("likely_scan_type"),
            "overall_quality_score": quality.get("overall_quality_score"),
            "possible_right_column_crop": quality.get("possible_right_column_crop"),
            "hidden_or_cropped_columns": list(quality.get("hidden_or_cropped_columns") or []),
            "has_blurry_pages": quality.get("has_blurry_pages"),
            "has_skewed_pages": quality.get("has_skewed_pages"),
            "contrast_score": first_page.get("contrast_score"),
            "blur_score": first_page.get("blur_score"),
        }

    def _should_use_contrast_only_for_vl(self, quality: dict[str, Any] | None) -> bool:
        if not isinstance(quality, dict):
            return False
        if quality.get("possible_right_column_crop") or (quality.get("hidden_or_cropped_columns") or []):
            return False
        pages = quality.get("pages") if isinstance(quality.get("pages"), list) else []
        first_page = pages[0] if pages and isinstance(pages[0], dict) else {}
        scan_type = str(quality.get("likely_scan_type") or first_page.get("likely_scan_type") or "")
        contrast = first_page.get("contrast_score")
        blur = first_page.get("blur_score")
        low_contrast = isinstance(contrast, (int, float)) and contrast < 0.105
        blurry = quality.get("has_blurry_pages") or (isinstance(blur, (int, float)) and blur < 55.0)
        return scan_type in {"photo", "fax_like"} and (low_contrast or blurry)

    def _original_vl_reason_from_quality(self, quality: dict[str, Any] | None) -> str:
        if not isinstance(quality, dict):
            return "quality_unavailable_original_safe_default"
        scan_type = quality.get("likely_scan_type") or "unknown"
        if scan_type in {"scan", "digital_pdf", "unknown"}:
            return f"{scan_type}_original_safe_default"
        return "quality_does_not_require_contrast_only_original_selected"

    def _light_page_vl_reason_from_quality(self, quality: dict[str, Any] | None) -> str:
        if not isinstance(quality, dict):
            return "quality_unavailable_light_page_safe_default"
        scan_type = quality.get("likely_scan_type") or "unknown"
        if scan_type in {"scan", "digital_pdf", "unknown"}:
            return f"{scan_type}_light_page_minimal_default"
        if self._should_use_contrast_only_for_vl(quality):
            return "photo_or_low_contrast_light_page_preprocess_default"
        return "image_upload_light_page_preprocess_default"

    def _vl_primary_normalized_document(
        self,
        stored_path: Path,
        document: Document,
        text: str,
        metadata: dict,
    ) -> NormalizedDocument:
        provider_metadata = metadata.get("vl_provider_metadata") if isinstance(metadata.get("vl_provider_metadata"), dict) else {}
        quality_metadata, quality_page_images = self._document_quality_for_source(stored_path)
        header_supplement = self._vl_visual_header_ocr_supplement(text, quality_page_images)
        normalized_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if header_supplement:
            normalized_text = "\n".join([header_supplement, normalized_text])
        raw_blocks = [{
            "type": "vl_primary_reader_text",
            "provider": provider_metadata.get("provider") or "paddleocr_vl_1_6_gguf",
            "content": text[:20000],
            "parser_integrated": True,
            "confirmed_promotion": True,
        }]
        if header_supplement:
            raw_blocks.append({
                "type": "visual_header_ocr_supplement",
                "provider": "paddleocr_ppocrv4",
                "content": header_supplement[:4000],
                "scope": "visible_header_only",
                "parser_integrated": True,
                "confirmed_promotion": False,
            })
        return NormalizedDocument(
            source_file_type=stored_path.suffix.casefold().lstrip(".") or "file",
            mime_type=document.mime_type or "application/octet-stream",
            extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
            normalized_text=normalized_text,
            raw_extracted_blocks=raw_blocks,
            extraction_warnings=[],
            file_metadata={
                "vl_primary_reader": True,
                "vl_worker_elapsed_ms": provider_metadata.get("elapsed_ms"),
                "vl_worker_status": provider_metadata.get("status"),
                "vl_worker_provider": provider_metadata.get("provider"),
                "ocr_fallback_used": False,
                "visual_header_ocr_supplement_used": bool(header_supplement),
                **({"document_quality": quality_metadata} if quality_metadata else {}),
            },
            ocr_confidence=None,
            primary_image_path=quality_page_images[0] if quality_page_images else None,
            rendered_image_paths=quality_page_images,
            heavy_ai_candidate=False,
            partial_support=False,
        )

    def _document_quality_for_source(self, stored_path: Path) -> tuple[dict | None, list[Path]]:
        suffix = stored_path.suffix.casefold().lstrip(".")
        image_suffixes = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"}
        if suffix in image_suffixes and stored_path.exists():
            quality = self.ingestion.document_quality.analyze_document_quality([stored_path]).to_dict()
            return quality, [stored_path]
        if suffix != "pdf" or not stored_path.exists():
            return None, []
        try:
            import fitz

            output_dir = self.settings.upload_dir / "quality_rendered_pages"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{stored_path.stem}-quality-page-1.png"
            with fitz.open(stored_path) as pdf:
                if len(pdf) <= 0:
                    return None, []
                page = pdf.load_page(0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pixmap.save(output_path)
            quality = self.ingestion.document_quality.analyze_document_quality([output_path]).to_dict()
            return quality, [output_path]
        except Exception:
            return None, []

    def _vl_visual_header_ocr_supplement(self, vl_text: str, page_images: list[Path]) -> str | None:
        if not page_images:
            return None
        if self.parser._extract_document_number(vl_text):
            return None
        image_path = page_images[0]
        if not image_path.exists():
            return None
        try:
            result = self.ocr.extract(image_path)
        except Exception:
            return None
        lines = [line.strip() for line in str(result.text or "").splitlines() if line.strip()]
        header_lines = self._visible_header_lines_only(lines)
        if not header_lines:
            return None
        header_text = "\n".join(header_lines)
        if not self.parser._extract_document_number(header_text):
            return None
        return header_text

    def _visible_header_lines_only(self, lines: list[str]) -> list[str]:
        header: list[str] = []
        for line in lines[:40]:
            if re.search(r"(품목명|description|vendor\s+sku|unit\s+price|amount|공급가액|세액|합계|total\s+usd)", line, flags=re.IGNORECASE):
                break
            header.append(line)
        useful: list[str] = []
        keep_next = 0
        for line in header:
            if keep_next > 0:
                useful.append(line)
                keep_next -= 1
                continue
            if re.search(
                r"(문서번호|발행일|작성일|invoice\s*(?:no|number)|document\s*(?:no|number)|vendor|customer|공급업체|고객사|currency|통화)",
                line,
                flags=re.IGNORECASE,
            ):
                useful.append(line)
                keep_next = 1
                continue
            if re.search(r"\b(?:INV|PO|QT|DN|TS|IQC|QC|RTN|RCM|TRF)[-_ ][A-Z0-9][A-Z0-9-_ ]*\d", line, flags=re.IGNORECASE):
                useful.append(line)
        return useful[:16]

    def _vl_primary_reader_metadata(
        self,
        stored_path: Path,
        document: Document,
        workflow_metadata: dict,
    ) -> dict | None:
        attempt = self._vl_primary_reader_attempt(stored_path, document, workflow_metadata)
        return attempt.get("metadata") if attempt else None

    def _vl_promoted_structured_candidate(self, attempt: dict | None) -> dict | None:
        if not isinstance(attempt, dict) or not attempt.get("promoted"):
            return None
        metadata = attempt.get("metadata") if isinstance(attempt.get("metadata"), dict) else {}
        for candidate in metadata.get("vl_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else None
            if structured and (
                candidate.get("confirmed_promotion")
                or (metadata.get("vl_candidate_summary") or {}).get("promotion_applied")
            ):
                return structured
        return None

    def _vl_primary_structured_candidate(self, attempt: dict | None) -> dict | None:
        if not isinstance(attempt, dict):
            return None
        promoted = self._vl_promoted_structured_candidate(attempt)
        if promoted:
            return promoted
        metadata = attempt.get("metadata") if isinstance(attempt.get("metadata"), dict) else {}
        summary = metadata.get("vl_candidate_summary") if isinstance(metadata.get("vl_candidate_summary"), dict) else {}
        if summary.get("provider_available_candidate") is False:
            return None
        for candidate in metadata.get("vl_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else None
            if structured and (candidate.get("parser_evaluated") or structured.get("line_items") or structured.get("document")):
                return structured
        return None

    def _vl_primary_attempt_should_drive_reader(self, attempt: dict | None) -> bool:
        if not isinstance(attempt, dict):
            return False
        metadata = attempt.get("metadata") if isinstance(attempt.get("metadata"), dict) else {}
        summary = metadata.get("vl_candidate_summary") if isinstance(metadata.get("vl_candidate_summary"), dict) else {}
        if summary.get("provider_available_candidate") is False:
            return False
        provider_metadata = metadata.get("vl_provider_metadata") if isinstance(metadata.get("vl_provider_metadata"), dict) else {}
        table_count = int(provider_metadata.get("table_count") or 0)
        if summary.get("gate_decision") == "reject":
            return False
        if summary.get("promotion_applied") or summary.get("promotion_mode") in {"full", "partial"}:
            return True
        if table_count and self._vl_primary_structured_candidate(attempt):
            return True
        if self._vl_primary_structured_candidate(attempt):
            return summary.get("gate_decision") not in {"review_required", "reject"}
        return False

    def _apply_vl_structured_candidate_to_parsed(self, parsed: Any, structured: dict) -> None:
        candidate_doc = structured.get("document") if isinstance(structured.get("document"), dict) else {}
        if candidate_doc.get("document_type"):
            try:
                parsed.document_type = DocumentType(str(candidate_doc["document_type"]))
            except Exception:
                pass
        for attr, key in (
            ("document_number", "document_number"),
            ("vendor_name", "vendor_name"),
            ("customer_name", "customer_name"),
            ("currency", "currency"),
        ):
            if attr == "currency" and self._vl_structured_candidate_is_amountless(structured):
                continue
            value = candidate_doc.get(key)
            if value not in (None, "", []) and self._should_apply_vl_scalar_field(getattr(parsed, attr, None), value):
                setattr(parsed, attr, value)
        issue_date = self._vl_date(candidate_doc.get("issue_date"))
        due_date = self._vl_date(candidate_doc.get("due_date"))
        if self._should_apply_vl_issue_date(
            getattr(parsed, "issue_date", None) or getattr(parsed, "extracted_date", None),
            issue_date,
            due_date or getattr(parsed, "due_date", None),
        ):
            parsed.issue_date = issue_date
            parsed.extracted_date = issue_date
        if due_date:
            parsed.due_date = due_date
        if not self._vl_structured_candidate_is_amountless(structured):
            for attr, key in (("subtotal", "subtotal"), ("tax", "tax"), ("extracted_amount", "total")):
                value = self._vl_decimal(candidate_doc.get(key))
                if value is not None:
                    if attr in {"subtotal", "tax", "extracted_amount"} and value < 0:
                        continue
                    setattr(parsed, attr, value)
        line_items = structured.get("line_items") if isinstance(structured.get("line_items"), list) else []
        if line_items:
            preserve_signed_amount_rows = self._structured_or_parsed_return_credit_signal(structured, parsed)
            parsed.line_items = self._safe_vl_promoted_line_items(
                line_items,
                preserve_signed_amount_rows=preserve_signed_amount_rows,
            )
            if preserve_signed_amount_rows:
                parsed.line_items = self._restore_return_credit_visible_amounts(parsed.line_items, line_items)

    def _reconcile_vl_parsed_with_pdf_text_layer(
        self,
        parsed: Any,
        stored_path: Path,
        normalized: NormalizedDocument,
    ) -> None:
        if stored_path.suffix.casefold() != ".pdf":
            return
        text = self._extract_pdf_text_layer_text(stored_path)
        if not text or len(text.strip()) < 40:
            return
        text_layer = self.parser.parse(text, stored_path.name)
        reconciled_fields: list[str] = []
        for attr in ("document_number", "vendor_name", "customer_name", "currency"):
            current = getattr(parsed, attr, None)
            candidate = getattr(text_layer, attr, None)
            if current in (None, "", []) and candidate not in (None, "", []):
                setattr(parsed, attr, candidate)
                reconciled_fields.append(attr)
        for attr in ("issue_date", "due_date"):
            current = getattr(parsed, attr, None)
            candidate = getattr(text_layer, attr, None)
            if current is None and candidate is not None:
                setattr(parsed, attr, candidate)
                reconciled_fields.append(attr)
        if getattr(parsed, "extracted_date", None) is None and getattr(text_layer, "extracted_date", None) is not None:
            parsed.extracted_date = text_layer.extracted_date
            reconciled_fields.append("extracted_date")
        self._reconcile_vl_item_names_with_text_layer(parsed, text_layer)
        if reconciled_fields:
            metadata = dict(normalized.file_metadata or {})
            reconciliation = dict(metadata.get("text_layer_reconciliation") or {})
            reconciliation["source"] = "pdf_text_layer"
            reconciliation["fields"] = sorted(set([*reconciliation.get("fields", []), *reconciled_fields]))
            metadata["text_layer_reconciliation"] = reconciliation
            normalized.file_metadata = metadata
            normalized.raw_extracted_blocks.append({
                "type": "pdf_text_layer_reconciliation",
                "content": text[:20000],
                "fields": sorted(set(reconciled_fields)),
            })

    def _extract_pdf_text_layer_text(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        try:
            reader = PdfReader(str(path))
            page_texts = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(f"Page {index}\n{text.strip()}")
            return "\n\n".join(page_texts)
        except Exception:
            return ""

    def _reconcile_vl_item_names_with_text_layer(self, parsed: Any, text_layer: Any) -> None:
        vl_items = getattr(parsed, "line_items", None) or []
        text_items = getattr(text_layer, "line_items", None) or []
        if not vl_items or not text_items:
            return
        for item in vl_items:
            if not isinstance(item, dict):
                continue
            match = self._matching_text_layer_item(item, text_items)
            if not match:
                continue
            current_name = str(item.get("item_name") or "").strip()
            text_name = str(match.get("item_name") or "").strip()
            if not current_name or not text_name or current_name == text_name:
                continue
            if not self._should_reconcile_item_name(current_name, text_name):
                continue
            item["vl_item_name"] = current_name
            item["item_name"] = text_name
            warnings = list(item.get("validation_warnings") or [])
            if "text_layer_item_name_reconciled" not in warnings:
                warnings.append("text_layer_item_name_reconciled")
            item["validation_warnings"] = warnings
            review_flags = list(item.get("review_flags") or [])
            if "text_layer_item_name_reconciled" not in review_flags:
                review_flags.append("text_layer_item_name_reconciled")
            item["review_flags"] = review_flags

    def _matching_text_layer_item(self, item: dict, text_items: list[dict]) -> dict | None:
        item_codes = self._item_match_codes(item)
        for candidate in text_items:
            if not isinstance(candidate, dict):
                continue
            if item_codes and item_codes.intersection(self._item_match_codes(candidate)):
                return candidate
        quantity = self._vl_decimal(item.get("quantity"))
        line_total = self._vl_decimal(item.get("line_total"))
        for candidate in text_items:
            if not isinstance(candidate, dict):
                continue
            if quantity is not None and quantity == self._vl_decimal(candidate.get("quantity")):
                candidate_total = self._vl_decimal(candidate.get("line_total"))
                if line_total is None or candidate_total is None or line_total == candidate_total:
                    return candidate
        return None

    def _item_match_codes(self, item: dict) -> set[str]:
        codes = set()
        for key in ("document_item_code", "item_code", "internal_item_code", "source_item_code"):
            value = item.get(key)
            if value not in (None, "", []):
                codes.add(re.sub(r"[\s_-]+", "", str(value)).casefold())
        return codes

    def _should_reconcile_item_name(self, current: str, candidate: str) -> bool:
        current_key = re.sub(r"[\s_/.-]+", "", current).casefold()
        candidate_key = re.sub(r"[\s_/.-]+", "", candidate).casefold()
        if current_key == candidate_key:
            return False
        if abs(len(current_key) - len(candidate_key)) > 4:
            return False
        try:
            from difflib import SequenceMatcher
            return SequenceMatcher(None, current_key, candidate_key).ratio() >= 0.72
        except Exception:
            return False

    def _vl_primary_reader_metadata_from_result(
        self,
        result: dict,
        document: Document,
        workflow_metadata: dict,
    ) -> dict:
        provider_metadata = {
            "source": "vl_worker_api",
            "provider": result.get("provider") or "paddleocr_vl_1_6_gguf",
            "ok": bool(result.get("ok")),
            "status": result.get("status") or result.get("classification"),
            "fallback_reason": result.get("fallback_reason"),
            "elapsed_ms": result.get("elapsed_ms"),
            "provider_available_decision_reason": result.get("provider_available_decision_reason"),
            "worker_transport": result.get("worker_transport"),
            "worker_location": result.get("worker_location"),
            "worker_provider": result.get("worker_provider"),
            "worker_url_host": result.get("worker_url_host"),
            "timeout_seconds": result.get("timeout_seconds"),
            "model_name": result.get("model_name") or result.get("model") or "PaddleOCR-VL-1.6-GGUF",
            "n_predict": result.get("n_predict"),
            "remote_upload": _safe_remote_upload_metadata(result.get("remote_upload")),
        }
        if isinstance(result.get("structured_schema"), dict):
            provider_metadata["structured_schema"] = result.get("structured_schema")
        if isinstance(result.get("schema_prompt"), dict):
            provider_metadata["schema_prompt"] = result.get("schema_prompt")
        if isinstance(result.get("tables"), list):
            provider_metadata["table_count"] = len(result.get("tables") or [])
        input_variant = result.get("input_variant") if isinstance(result.get("input_variant"), dict) else None
        if input_variant:
            provider_metadata["input_variant"] = {
                "variant_name": input_variant.get("variant_name"),
                "operations": list(input_variant.get("operations") or []),
                "warnings": list(input_variant.get("warnings") or []),
                "processed_path_present": bool(input_variant.get("processed_path")),
            }
        text = result.get("text") or result.get("text_preview")
        if not isinstance(text, str):
            text = ""
        tables = result.get("tables") if isinstance(result.get("tables"), list) else None
        provider_available_candidate = bool(result.get("ok") or tables)
        if not text.strip() and not tables:
            fallback_reason = provider_metadata.get("fallback_reason") or "vl_worker_empty_or_unreadable_output"
            return {
                "vl_provider_metadata": provider_metadata,
                "vl_candidate_summary": {
                    "candidate_count": 0,
                    "warning_count": 0,
                    "failure_count": 1 if not provider_metadata.get("ok") else 0,
                    "issue_codes": [],
                    "provider": provider_metadata["provider"],
                    "provider_available_candidate": False,
                    "parser_evaluated": False,
                    "requires_review": False,
                    "promotion_applied": False,
                    "partial_promotion_applied": False,
                    "promotion_mode": "none",
                    "parser_integrated": False,
                    "fallback_used": True,
                    "fallback_reason": fallback_reason,
                    "gate_decision": None,
                    "gate_reasons": [],
                },
                "vl_candidates": [],
            }
        structured = self.vl_candidate_parser.parse_text(
            text,
            filename=document.original_filename,
            tables=tables,
            validation=result.get("validation") if isinstance(result.get("validation"), dict) else None,
        )
        candidate = {
            "source": "vl_worker_api",
            "provider": provider_metadata["provider"],
            "candidate_only": True,
            "parser_integrated": False,
            "parser_evaluated": bool(structured),
            "provider_available_candidate": provider_available_candidate,
            "validation_severity": result.get("classification"),
            "issue_codes": list((structured or {}).get("issue_codes") or []),
            "review_flags": list((structured or {}).get("review_flags") or (structured or {}).get("issue_codes") or []),
            "text_preview": text[:1200],
            "inference_time_ms": result.get("elapsed_ms"),
            "structured_candidate": structured,
        }
        if isinstance(result.get("tables"), list):
            candidate["tables"] = result.get("tables")
        original_workflow_metadata = document.workflow_metadata
        document.workflow_metadata = workflow_metadata
        try:
            gate = self.vl_candidate_gate.evaluate(document, candidate)
        finally:
            document.workflow_metadata = original_workflow_metadata
        candidate["promotion_gate"] = gate
        promotion_applied = False
        if gate.get("auto_promote") and structured:
            self._apply_vl_structured_candidate(document, structured)
            structured["candidate_only"] = False
            structured["parser_integrated"] = True
            structured["confirmed_promotion"] = True
            structured["promotion_mode"] = gate.get("promotion_mode") or "full"
            candidate["candidate_only"] = False
            candidate["parser_integrated"] = True
            candidate["confirmed_promotion"] = True
            candidate["promotion_mode"] = gate.get("promotion_mode") or "full"
            promotion_applied = True
        issue_codes = list(dict.fromkeys((candidate.get("issue_codes") or []) + (gate.get("issue_codes") or [])))
        requires_review = gate.get("decision") in {"review_required", "reject"}
        return {
            "vl_provider_metadata": provider_metadata,
            "vl_candidates": [candidate],
            "vl_candidate_summary": {
                "candidate_count": 1,
                "warning_count": 1 if gate.get("decision") == "review_required" else 0,
                "failure_count": 1 if gate.get("decision") == "reject" else 0,
                "issue_codes": issue_codes,
                "parser_integrated": promotion_applied,
                "parser_evaluated": bool(structured),
                "parsed_line_item_count": (structured or {}).get("line_item_count"),
                "provider": provider_metadata["provider"],
                "provider_available_candidate": provider_available_candidate,
                "gate_decision": gate.get("decision"),
                "gate_reasons": gate.get("reasons") or [],
                "promotion_mode": gate.get("promotion_mode") or "none",
                "partial_promotion_applied": promotion_applied and gate.get("promotion_mode") == "partial",
                "fallback_used": not promotion_applied,
                "requires_review": requires_review,
                "promotion_applied": promotion_applied,
            },
            "normalized_review_issues": [self._vl_candidate_review_issue(gate)] if requires_review else [],
        }

    def _merge_vl_pipeline_metadata(self, workflow_metadata: dict, vl_metadata: dict) -> dict:
        merged = dict(workflow_metadata or {})
        for key in (
            "vl_provider_metadata",
            "vl_candidates",
            "vl_candidate_summary",
            "vl_preprocess_mode",
            "vl_preprocess_input",
            "vl_preprocess_policy",
        ):
            if key in vl_metadata:
                merged[key] = vl_metadata[key]
        incoming_issues = [issue for issue in vl_metadata.get("normalized_review_issues") or [] if isinstance(issue, dict)]
        if incoming_issues:
            issues = list(merged.get("normalized_review_issues") or [])
            existing_codes = {issue.get("code") for issue in issues if isinstance(issue, dict)}
            for issue in incoming_issues:
                if issue.get("code") not in existing_codes:
                    issues.append(issue)
            merged["normalized_review_issues"] = issues
            merged["review_required"] = True
        return merged

    def _vl_candidate_review_issue(self, gate: dict) -> dict:
        decision = gate.get("decision") or "review_required"
        return {
            "code": "vl_candidate_review_required" if decision != "reject" else "vl_candidate_rejected",
            "message_ko": "VL 추출 후보가 검증을 통과하지 못했습니다. 원본과 후보 값을 확인하세요.",
            "field": "workflow_metadata.vl_candidates",
            "severity": "warning" if decision != "reject" else "critical",
            "blocking": False,
            "flags": gate.get("issue_codes") or [],
            "gate_decision": decision,
            "gate_reasons": gate.get("reasons") or [],
        }

    def _apply_vl_structured_candidate(self, document: Document, structured: dict) -> None:
        candidate_doc = structured.get("document") if isinstance(structured.get("document"), dict) else {}
        if candidate_doc.get("document_type"):
            try:
                document.document_type = DocumentType(str(candidate_doc["document_type"]))
                document.ai_document_type = document.document_type
            except Exception:
                pass
        for attr, key in (
            ("document_number", "document_number"),
            ("vendor_name", "vendor_name"),
            ("customer_name", "customer_name"),
            ("currency", "currency"),
        ):
            if attr == "currency" and self._vl_structured_candidate_is_amountless(structured):
                continue
            value = candidate_doc.get(key)
            if value not in (None, "", []) and self._should_apply_vl_scalar_field(getattr(document, attr, None), value):
                setattr(document, attr, sanitize_for_postgres(value))
        issue_date = self._vl_date(candidate_doc.get("issue_date"))
        due_date = self._vl_date(candidate_doc.get("due_date"))
        if self._should_apply_vl_issue_date(document.issue_date or document.extracted_date, issue_date, due_date or document.due_date):
            document.issue_date = issue_date
            document.extracted_date = issue_date
        if due_date:
            document.due_date = due_date
        for attr, key in (("subtotal", "subtotal"), ("tax", "tax"), ("extracted_amount", "total")):
            value = self._vl_decimal(candidate_doc.get(key))
            if value is not None:
                if attr in {"subtotal", "tax", "extracted_amount"} and value < 0:
                    continue
                setattr(document, attr, value)
        line_items = structured.get("line_items") if isinstance(structured.get("line_items"), list) else []
        if line_items:
            document.line_items = sanitize_for_postgres(self._safe_vl_promoted_line_items(line_items))
            document.low_confidence_fields = []

    def _should_apply_vl_scalar_field(self, existing: Any, candidate: Any) -> bool:
        if candidate in (None, "", []):
            return False
        if existing in (None, "", []):
            return True
        existing_text = str(existing).strip()
        candidate_text = str(candidate).strip()
        if not existing_text:
            return True
        if not candidate_text:
            return False
        if existing_text == candidate_text:
            return True
        if self.parser._looks_like_line_item_header_text(candidate_text):
            return False
        if re.search(
            r"(vendor\s+sku|unit\s+price|amount|qty|spec|품목|규격|수량|단가|공급가액|세액|합계)",
            candidate_text,
            flags=re.IGNORECASE,
        ):
            return False
        return False
        field_sources = dict(document.field_sources or {})
        for field in ("document_number", "vendor_name", "customer_name", "issue_date", "due_date", "currency", "subtotal", "tax", "extracted_amount", "line_items"):
            field_sources[field] = "paddleocr_vl_1_6_gguf"
        document.field_sources = sanitize_for_postgres(field_sources)

    def _should_apply_vl_issue_date(
        self,
        current_issue_date: date | None,
        candidate_issue_date: date | None,
        candidate_due_date: date | None,
    ) -> bool:
        if not candidate_issue_date:
            return False
        if (
            current_issue_date
            and candidate_due_date
            and candidate_issue_date == candidate_due_date
            and current_issue_date != candidate_issue_date
        ):
            return False
        return True

    def _vl_date(self, value: Any) -> date | None:
        if value in (None, "", []):
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None

    def _vl_decimal(self, value: Any) -> Decimal | None:
        if value in (None, "", []):
            return None
        try:
            return Decimal(str(value).replace(",", ""))
        except Exception:
            return None

    def _safe_vl_promoted_line_items(
        self,
        line_items: list[dict],
        *,
        preserve_signed_amount_rows: bool = False,
    ) -> list[dict]:
        safe_items: list[dict] = []
        for item in line_items:
            if not isinstance(item, dict):
                continue
            safe_item = dict(item)
            try:
                safe_item = self.parser._clean_ocr_line_item_artifacts(safe_item)
                if safe_item.get("specification") not in (None, "", []):
                    safe_item["specification"] = self.parser._normalize_specification_value(safe_item.get("specification"))
            except Exception:
                pass
            warnings = list(safe_item.get("validation_warnings") or [])
            warning_set = {str(warning) for warning in warnings}
            review_flags = list(safe_item.get("review_flags") or [])
            hidden_amount_review = self._vl_line_item_has_hidden_amount_review_signal(safe_item, warning_set, review_flags)
            if (
                preserve_signed_amount_rows
                and hidden_amount_review
                and self._vl_line_item_has_signed_amount_context(safe_item)
                and not self._vl_line_item_has_hard_hidden_amount_signal(safe_item, warning_set, review_flags)
            ):
                hidden_amount_review = False
            if "explicit_quantity_price_amount_mismatch" in warning_set or hidden_amount_review:
                for field in ("supply_amount", "tax_amount", "line_total"):
                    safe_item.pop(field, None)
                suppression_code = (
                    "vl_amount_suppressed_due_to_hidden_or_unverified_column"
                    if hidden_amount_review
                    else "vl_amount_suppressed_due_to_arithmetic_mismatch"
                )
                if suppression_code not in warning_set:
                    warnings.append(suppression_code)
                safe_item["validation_warnings"] = sorted(set(warnings))
                if suppression_code not in review_flags:
                    review_flags.append(suppression_code)
                safe_item["review_flags"] = sorted(set(review_flags))
            safe_items.append(safe_item)
        return safe_items

    def _vl_line_item_has_hidden_amount_review_signal(
        self,
        item: dict[str, Any],
        warning_set: set[str],
        review_flags: list[Any],
    ) -> bool:
        codes = set(warning_set)
        codes.update(str(flag) for flag in review_flags if flag not in (None, ""))
        codes.update(str(code) for code in item.get("issue_codes") or [] if code not in (None, ""))
        hidden_fragments = (
            "hidden",
            "cropped",
            "truncated",
            "not_visible",
            "not-visible",
            "do_not_infer",
            "missing_line_amount",
            "amount_column_not_visible",
            "row_amount_hidden",
            "line_total_not_visible",
        )
        return any(any(fragment in code for fragment in hidden_fragments) for code in codes)

    def _vl_line_item_has_hard_hidden_amount_signal(
        self,
        item: dict[str, Any],
        warning_set: set[str],
        review_flags: list[Any],
    ) -> bool:
        codes = set(warning_set)
        codes.update(str(flag) for flag in review_flags if flag not in (None, ""))
        codes.update(str(code) for code in item.get("issue_codes") or [] if code not in (None, ""))
        hard_fragments = (
            "hidden",
            "cropped",
            "truncated",
            "amount_column_not_visible",
            "row_amount_hidden",
            "visual_crop",
            "right_column_crop",
        )
        stale_suppression_code = "vl_amount_suppressed_due_to_hidden_or_unverified_column"
        return any(
            code != stale_suppression_code and any(fragment in code for fragment in hard_fragments)
            for code in codes
        )

    def _vl_line_item_has_signed_amount_context(self, item: dict[str, Any]) -> bool:
        for field in ("quantity", "supply_amount", "tax_amount", "line_total"):
            value = self._vl_decimal(item.get(field))
            if value is not None and value < 0:
                return True
        return False

    def _restore_return_credit_visible_amounts(
        self,
        line_items: list[dict],
        parsed_line_items: list[dict],
    ) -> list[dict]:
        parsed_candidates = [dict(item) for item in parsed_line_items or [] if isinstance(item, dict)]
        if not parsed_candidates:
            return line_items or []
        restored_items: list[dict] = []
        used_indexes: set[int] = set()
        for item in line_items or []:
            safe_item = dict(item)
            match_index, parsed_item = self._match_return_credit_visible_amount_source(safe_item, parsed_candidates, used_indexes)
            if parsed_item:
                used_indexes.add(match_index)
                for field in ("supply_amount", "tax_amount", "line_total"):
                    if parsed_item.get(field) not in (None, "", []) and safe_item.get(field) in (None, "", []):
                        safe_item[field] = parsed_item.get(field)
                if any(safe_item.get(field) not in (None, "", []) for field in ("supply_amount", "tax_amount", "line_total")):
                    self._remove_review_code(safe_item, "vl_amount_suppressed_due_to_hidden_or_unverified_column")
            restored_items.append(safe_item)
        return restored_items

    def _match_return_credit_visible_amount_source(
        self,
        item: dict[str, Any],
        parsed_candidates: list[dict],
        used_indexes: set[int],
    ) -> tuple[int, dict | None]:
        item_name = self._row_match_text(item.get("source_item_name") or item.get("item_name"))
        item_spec = self._row_match_text(item.get("specification") or item.get("spec"))
        quantity = self._vl_decimal(item.get("quantity"))
        unit_price = self._vl_decimal(item.get("unit_price"))
        for index, candidate in enumerate(parsed_candidates):
            if index in used_indexes:
                continue
            candidate_name = self._row_match_text(candidate.get("source_item_name") or candidate.get("item_name"))
            candidate_spec = self._row_match_text(candidate.get("specification") or candidate.get("spec"))
            name_compatible = self._row_match_text_compatible(item_name, candidate_name)
            spec_compatible = self._row_match_text_compatible(item_spec, candidate_spec)
            candidate_quantity = self._vl_decimal(candidate.get("quantity"))
            candidate_unit_price = self._vl_decimal(candidate.get("unit_price"))
            if quantity is not None and candidate_quantity is not None and quantity != candidate_quantity:
                continue
            if unit_price is not None and candidate_unit_price is not None and unit_price != candidate_unit_price:
                continue
            if not (name_compatible or spec_compatible):
                continue
            if not name_compatible and item_spec and candidate_spec and not spec_compatible:
                continue
            if any(candidate.get(field) not in (None, "", []) for field in ("supply_amount", "tax_amount", "line_total")):
                return index, candidate
        return -1, None

    def _row_match_text(self, value: Any) -> str:
        text = str(value or "").lower().strip()
        text = text.replace("×", "x").replace("*", "x")
        text = re.sub(r"[^0-9a-z가-힣x]+", "", text)
        return text

    def _row_match_text_compatible(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right or left in right or right in left:
            return True
        left_tokens = {token for token in re.split(r"(?<=[a-z가-힣])(?=\d)|(?<=\d)(?=[a-z가-힣])", left) if token}
        right_tokens = {token for token in re.split(r"(?<=[a-z가-힣])(?=\d)|(?<=\d)(?=[a-z가-힣])", right) if token}
        return bool(left_tokens and right_tokens and len(left_tokens & right_tokens) >= 2)

    def _remove_review_code(self, item: dict[str, Any], code: str) -> None:
        for field in ("validation_warnings", "review_flags", "issue_codes"):
            values = [str(value) for value in item.get(field) or [] if str(value) != code]
            if values:
                item[field] = sorted(set(values))
            else:
                item.pop(field, None)

    def _line_items_for_extraction_method(
        self,
        line_items: list[dict],
        extraction_method: str | None,
        *,
        preserve_signed_amount_rows: bool = False,
    ) -> list[dict]:
        if extraction_method == "paddleocr_vl_1_6_gguf_primary_reader":
            return self._safe_vl_promoted_line_items(
                line_items or [],
                preserve_signed_amount_rows=preserve_signed_amount_rows,
            )
        return line_items or []

    def _apply_final_business_safety_overrides(self, document: Document, raw_text: str) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        line_items = [dict(item) for item in (document.line_items or []) if isinstance(item, dict)]

        if self._looks_like_pos_settlement_document(document, raw_text, line_items):
            if line_items:
                document.line_items = []
                issues.append(self._business_safety_issue(
                    "unsupported_pos_daily_settlement_review_required",
                    "POS 일일정산 화면은 제조업 품목 거래 문서가 아니므로 품목 행으로 확정하지 않았습니다.",
                    "line_items",
                ))
            document.document_type = DocumentType.general_document
            document.ai_document_type = DocumentType.general_document
            document.category = "unsupported_pos_settlement"
            document.tags = sorted(set([*(document.tags or []), "unsupported_pos_settlement"]))
            document.review_required = True
            document.low_confidence_fields = sorted(set([*(document.low_confidence_fields or []), "unsupported_pos_settlement"]))
            return issues

        self._normalize_party_fields(document)

        filtered_items: list[dict] = []
        removed_summary_rows = 0
        for item in line_items:
            if self._line_item_has_summary_footer_signal(item):
                removed_summary_rows += 1
                continue
            filtered_items.append(item)
        if removed_summary_rows:
            issues.append(self._business_safety_issue(
                "summary_total_not_line_item",
                "합계/요약/정산 행은 실제 품목이 아니므로 품목 정보에서 제외했습니다.",
                "line_items",
            ))
            document.review_required = True

        if self._has_inspection_no_price_document_signal(document, raw_text, filtered_items):
            internal_transfer = self._is_internal_transfer_document(document)
            if (
                not internal_transfer
                and getattr(document.document_type, "value", str(document.document_type or "")) not in {"delivery_note", "inspection_report"}
            ):
                document.document_type = DocumentType.inspection_report
                document.ai_document_type = DocumentType.inspection_report
            removed_amount = any(
                getattr(document, field, None) is not None
                for field in ("extracted_amount", "subtotal", "tax")
            ) or bool(document.currency)
            document.extracted_amount = None
            document.subtotal = None
            document.tax = None
            document.currency = None
            sanitized_items = []
            for item in filtered_items:
                safe_item = dict(item)
                item_removed_amount = False
                for field in ("unit_price", "supply_amount", "tax_amount", "line_total"):
                    if safe_item.get(field) not in (None, "", []):
                        item_removed_amount = True
                    safe_item.pop(field, None)
                if item_removed_amount:
                    flags = set(str(flag) for flag in safe_item.get("review_flags") or [])
                    flags.add("no_price_document_amount_removed")
                    safe_item["review_flags"] = sorted(flags)
                    removed_amount = True
                sanitized_items.append(safe_item)
            filtered_items = sanitized_items
            if removed_amount:
                issues.append(self._business_safety_issue(
                    "no_price_document_amount_blocker",
                    "검사/납품/이동처럼 금액 없는 수량 확인 문서로 판단되어 금액을 확정값에서 제거했습니다.",
                    "line_items",
                ))
                document.review_required = True
            document.category = document.category or ("internal_transfer" if internal_transfer else "inspection_report")
            tags = [*(document.tags or []), "no_price_document"]
            if not internal_transfer:
                tags.append("inspection_report")
            document.tags = sorted(set(tags))

        if filtered_items != line_items:
            document.line_items = sanitize_for_postgres(filtered_items)
        return issues

    def _normalize_party_fields(self, document: Document) -> None:
        document.vendor_name = self._normalize_party_name(document.vendor_name)
        document.customer_name = self._normalize_party_name(document.customer_name)

    def _normalize_party_name(self, value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if re.search(r"^(담당|담당자|회계|검사자|작성자|검수자)\s*[:：]", text):
            return None
        text = re.sub(r"^(상호|업체|거래처|공급자|공급업체|공급받는자|고객사|수신|받는곳)\s*[:：]?\s*", "", text).strip()
        text = re.sub(r"^(?:\(?주\)?|주식회사)\s*", "", text).strip()
        text = re.sub(r"\s*(?:\(?주\)?|주식회사)$", "", text).strip()
        text = re.sub(r"\s*/\s*(회계팀|구매팀|품질팀|생산관리|담당.*)$", "", text).strip()
        return text or None

    def _business_safety_issue(self, code: str, message: str, field: str) -> dict[str, Any]:
        return {
            "code": code,
            "message_ko": message,
            "field": field,
            "severity": "warning",
            "blocking": False,
            "source": "final_business_safety_overrides",
        }

    def _line_item_has_summary_footer_signal(self, item: dict) -> bool:
        text = self._line_item_business_text(item)
        return bool(self._summary_footer_pattern().search(text))

    def _line_item_business_text(self, item: dict) -> str:
        fields = (
            "item_name",
            "name",
            "description",
            "specification",
            "spec",
            "unit",
            "note",
            "remarks",
            "memo",
            "document_item_code",
            "item_code",
        )
        return " ".join(str(item.get(field) or "") for field in fields)

    def _summary_footer_pattern(self) -> re.Pattern:
        return re.compile(
            r"("
            r"크레[딧뒷]\s*합계|반품\s*합계|차감\s*합계|조정\s*합계|"
            r"예상\s*합계|총\s*옵션\s*항목|옵션\s*선택\s*후|"
            r"total\s+usd|krw\s+converted|converted\s+krw|"
            r"순\s*판매\s*금액|총\s*판매\s*금액|할인\s*금액|취소\s*금액|"
            r"과세\s*합계|면세\s*금액|vat\b|v\.a\.t|부가세|"
            r"결제\s*합계|현금\s*합계|카드\s*합계|온라인\s*결제|"
            r"주문\s*횟수|매장\s*판매|배달\s*판매|평균\s*단가|"
            r"공급\s*가액\s*합계|세액\s*합계|청구\s*금액|합계\s*금액"
            r")",
            flags=re.IGNORECASE,
        )

    def _looks_like_pos_settlement_document(self, document: Document, raw_text: str, line_items: list[dict]) -> bool:
        text = " ".join([
            str(raw_text or ""),
            str(document.title or ""),
            str(document.document_number or ""),
            str(document.vendor_name or ""),
            str(document.customer_name or ""),
            " ".join(str(tag or "") for tag in (document.tags or [])),
        ])
        manufacturing_document_signal = re.search(
            r"(거래\s*명세서|발주서|견적서|세금\s*계산서|인보이스|invoice|납품서|입고\s*검사|자재\s*이동|반품|크레딧)",
            text,
            flags=re.IGNORECASE,
        )
        if re.search(r"(pos|일\s*정산|매출\s*정산)", text, flags=re.IGNORECASE):
            if manufacturing_document_signal and not re.search(r"(pos\s*메인|pos\s*일\s*정산|일\s*정산|매출\s*정산)", text, flags=re.IGNORECASE):
                return False
            return True
        if manufacturing_document_signal:
            return False
        if re.search(r"(영수증|승인번호|카드사)", text, flags=re.IGNORECASE) and re.search(
            r"(결제|카드|현금|승인|부가세|vat)", text, flags=re.IGNORECASE
        ):
            return True
        if not line_items:
            return False
        pos_metric_count = sum(1 for item in line_items if self._pos_metric_pattern().search(self._line_item_business_text(item)))
        return pos_metric_count >= 3 and pos_metric_count >= max(2, int(len(line_items) * 0.6))

    def _pos_metric_pattern(self) -> re.Pattern:
        return re.compile(
            r"(순\s*판매\s*금액|총\s*판매\s*금액|과세\s*합계|면세\s*금액|"
            r"결제\s*합계|현금\s*합계|카드\s*합계|온라인\s*결제|"
            r"주문\s*횟수|매장\s*판매|배달\s*판매|평균\s*단가|승인번호|카드사)",
            flags=re.IGNORECASE,
        )

    def _has_inspection_no_price_document_signal(self, document: Document, raw_text: str, line_items: list[dict]) -> bool:
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        if doc_type in {"delivery_note", "inspection_report"} or self._is_internal_transfer_document(document):
            return True
        text = " ".join([
            str(raw_text or ""),
            str(document.title or ""),
            str(document.document_number or ""),
            " ".join(str(tag or "") for tag in (document.tags or [])),
            " ".join(self._line_item_business_text(item) for item in line_items),
        ])
        inspection_signal = re.search(
            r"(입고\s*검사|검사\s*기록|검사\s*성적|검사번호|검사일|"
            r"입고\s*수량|합격\s*수량|불량\s*수량|판정|조건부\s*합격|"
            r"금액\s*항목\s*없음|금액\s*정보\s*없음|품질\s*확인)",
            text,
            flags=re.IGNORECASE,
        )
        if not inspection_signal:
            return False
        priced_signal = re.search(r"(세금계산서|청구금액|합계금액|공급가액\s*합계|세액\s*합계)", text, flags=re.IGNORECASE)
        return not bool(priced_signal)

    def _nonnegative_document_amount(self, value: Any) -> Any:
        if value is None:
            return None
        try:
            if Decimal(str(value)) < 0:
                return None
        except Exception:
            return value
        return value

    def _bbox_layout_debug_metadata(
        self,
        normalized: NormalizedDocument,
        document: Document,
        workflow_metadata: dict,
    ) -> dict | None:
        line_candidates = self._ocr_line_candidates(normalized)
        bbox_count = sum(1 for candidate in line_candidates if candidate.get("bbox") or all(key in candidate for key in ("x_min", "y_min", "x_max", "y_max")))
        if bbox_count < 6:
            return None
        rows = self.bbox_table_reconstructor.group_rows_by_y(line_candidates)
        if not rows:
            return None
        columns = self.bbox_table_reconstructor.infer_columns(rows)
        structured_rows = self.bbox_table_reconstructor.map_tokens_to_columns(rows, columns)
        if not structured_rows:
            return None
        profile = (
            workflow_metadata.get("document_profile")
            or (workflow_metadata.get("taxonomy") or {}).get("document_profile")
            or workflow_metadata.get("content_profile")
        )
        candidates = self.bbox_table_reconstructor.build_line_item_candidates(structured_rows, str(profile or "priced_document"))
        confirmed_count = len(document.line_items or [])
        profile_values = self._metadata_profile_values(workflow_metadata, profile)
        layout_attention_flags = {
            "missing_item_name_from_ocr",
            "row_boundary_uncertain",
            "fax_row_boundary_uncertain",
            "low_ocr_confidence",
        }
        extra_candidates = candidates[confirmed_count:] if len(candidates) > confirmed_count else []
        stored_candidates = [
            candidate for candidate in extra_candidates
            if self._should_store_bbox_extra_candidate(candidate, profile_values, layout_attention_flags)
            and not self._duplicates_confirmed_line_item(candidate, document.line_items or [])
        ]
        stored_candidates = self._dedupe_layout_candidates(stored_candidates)[:10]
        review_flags = sorted({
            str(flag)
            for candidate in stored_candidates
            for flag in candidate.get("review_flags", [])
            if flag
        })
        if not stored_candidates and len(candidates) <= confirmed_count:
            return {
                "source": "bbox_table_reconstructor",
                "parser_integrated": False,
                "bbox_line_candidate_count": bbox_count,
                "grouped_row_count": len(rows),
                "column_count": len(columns),
                "reconstructed_candidate_count": len(candidates),
                "candidate_count": 0,
                "confirmed_line_item_count": confirmed_count,
                "uncertain_count": 0,
                "bbox_review_flags": [],
                "bbox_table_candidates": [],
            }
        return {
            "source": "bbox_table_reconstructor",
            "parser_integrated": False,
            "bbox_line_candidate_count": bbox_count,
            "grouped_row_count": len(rows),
            "column_count": len(columns),
            "reconstructed_candidate_count": len(candidates),
            "candidate_count": len(stored_candidates),
            "confirmed_line_item_count": confirmed_count,
            "uncertain_count": len(stored_candidates),
            "bbox_review_flags": review_flags,
            "bbox_table_candidates": [self._compact_layout_candidate(candidate) for candidate in stored_candidates],
        }

    def _metadata_profile_values(self, workflow_metadata: dict, profile: object | None = None) -> set[str]:
        taxonomy = workflow_metadata.get("taxonomy") if isinstance(workflow_metadata.get("taxonomy"), dict) else {}
        values: set[str] = set()
        for value in (
            profile,
            workflow_metadata.get("document_profile"),
            workflow_metadata.get("content_profile"),
            taxonomy.get("document_profile"),
        ):
            if value:
                values.add(str(value))
        for key in ("document_profiles", "profiles"):
            for source in (workflow_metadata, taxonomy):
                profile_list = source.get(key) if isinstance(source, dict) else None
                if isinstance(profile_list, list):
                    values.update(str(item) for item in profile_list if item)
        return values

    def _should_store_bbox_layout_candidate(
        self,
        candidate: dict,
        profile_values: set[str],
        layout_attention_flags: set[str],
    ) -> bool:
        flags = {str(flag) for flag in candidate.get("review_flags", []) if flag}
        if not candidate.get("item_name"):
            return True
        if flags & layout_attention_flags:
            return True
        if candidate.get("missing_fields"):
            return True
        return False

    def _should_store_bbox_extra_candidate(
        self,
        candidate: dict,
        profile_values: set[str],
        layout_attention_flags: set[str],
    ) -> bool:
        flags = {str(flag) for flag in candidate.get("review_flags", []) if flag}
        no_price_profiles = {"no_price_document", "inventory_movement_document", "quality_document"}
        if profile_values & no_price_profiles:
            return bool((flags & layout_attention_flags) or not candidate.get("item_name") or candidate.get("missing_fields"))
        if self._should_store_bbox_layout_candidate(candidate, profile_values, layout_attention_flags):
            return True
        return bool(
            candidate.get("item_name")
            or any(candidate.get(field) is not None for field in ("quantity", "unit_price", "supply_amount", "tax_amount", "line_total"))
        )

    def _ocr_line_candidates(self, normalized: NormalizedDocument) -> list[dict]:
        candidates: list[dict] = []
        for block in normalized.raw_extracted_blocks or []:
            if not isinstance(block, dict):
                continue
            page = block.get("page")
            for candidate in block.get("line_candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                enriched = dict(candidate)
                if page and not enriched.get("page"):
                    enriched["page"] = page
                candidates.append(enriched)
        return candidates

    def _dedupe_layout_candidates(self, candidates: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[tuple] = set()
        for candidate in candidates:
            bbox = candidate.get("bbox_span") or {}
            key = (
                candidate.get("item_name"),
                candidate.get("line_total"),
                candidate.get("supply_amount"),
                round(float(bbox.get("y_min") or 0), 1),
                round(float(bbox.get("y_max") or 0), 1),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _duplicates_confirmed_line_item(self, candidate: dict, line_items: list[dict]) -> bool:
        candidate_name = self._normalized_layout_identity(candidate.get("item_name"))
        if not candidate_name:
            return False
        for item in line_items:
            if not isinstance(item, dict):
                continue
            confirmed_name = self._normalized_layout_identity(item.get("item_name") or item.get("name"))
            if not confirmed_name or len(confirmed_name) < 4:
                continue
            if confirmed_name in candidate_name or candidate_name in confirmed_name:
                return True
            if self._layout_identity_common_prefix(candidate_name, confirmed_name) >= 8:
                return True
        return False

    def _normalized_layout_identity(self, value: object) -> str:
        text = str(value or "").casefold()
        return "".join(ch for ch in text if ch.isalnum())

    def _layout_identity_common_prefix(self, left: str, right: str) -> int:
        count = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            count += 1
        return count

    def _compact_layout_candidate(self, candidate: dict) -> dict:
        source_tokens = candidate.get("source_tokens") or []
        return {
            "source": "bbox_table_reconstructor",
            "item_name": candidate.get("item_name"),
            "document_item_code": candidate.get("document_item_code"),
            "internal_item_code": candidate.get("internal_item_code"),
            "specification": candidate.get("specification"),
            "quantity": candidate.get("quantity"),
            "unit": candidate.get("unit"),
            "unit_price": candidate.get("unit_price"),
            "supply_amount": candidate.get("supply_amount"),
            "tax_amount": candidate.get("tax_amount"),
            "line_total": candidate.get("line_total"),
            "confidence": candidate.get("confidence"),
            "missing_fields": candidate.get("missing_fields") or [],
            "untrusted_fields": candidate.get("untrusted_fields") or [],
            "review_flags": candidate.get("review_flags") or [],
            "bbox_span": candidate.get("bbox_span"),
            "source_text": " ".join(str(token.get("text") or "") for token in source_tokens if isinstance(token, dict)).strip(),
        }

    def _bbox_layout_review_issue(self, layout_debug: dict) -> dict | None:
        if not layout_debug.get("uncertain_count"):
            return None
        flags = layout_debug.get("bbox_review_flags") or []
        if layout_debug.get("candidate_count", 0) <= 0:
            return None
        if "missing_item_name_from_ocr" not in flags and "fax_row_boundary_uncertain" not in flags:
            return None
        return {
            "code": "bbox_table_candidate_uncertain",
            "message_ko": f"OCR 위치 기반 추가 표 후보 {layout_debug.get('uncertain_count')}건이 있습니다. 원본 문서를 확인하세요.",
            "field": "workflow_metadata.layout_debug.bbox_table_candidates",
            "severity": "info",
            "blocking": False,
            "flags": flags,
        }

    def _document_quality_workflow_metadata(self, normalized: NormalizedDocument) -> dict | None:
        metadata = normalized.file_metadata if isinstance(normalized.file_metadata, dict) else {}
        quality = metadata.get("document_quality")
        return quality if isinstance(quality, dict) else None

    def _document_quality_review_issues(self, document_quality: dict) -> list[dict]:
        reasons = document_quality.get("review_reasons") if isinstance(document_quality.get("review_reasons"), list) else []
        page_reasons: set[str] = {str(reason) for reason in reasons if reason}
        pages = document_quality.get("pages") if isinstance(document_quality.get("pages"), list) else []
        for page in pages:
            if isinstance(page, dict):
                page_reasons.update(str(reason) for reason in page.get("review_reasons") or [] if reason)

        issue_map = {
            "document_low_resolution": (
                "document_low_resolution",
                "문서 해상도가 낮아 숫자와 품목을 원본과 함께 확인해야 합니다.",
            ),
            "document_image_blurry": (
                "document_image_blurry",
                "문서가 흐릿해 수량, 단가, 금액 값을 검토해야 합니다.",
            ),
            "document_low_contrast": (
                "document_low_contrast",
                "문서 명암이 낮아 일부 글자가 불명확할 수 있습니다.",
            ),
            "document_page_skewed": (
                "document_page_skewed",
                "문서가 기울어져 표 행과 숫자 위치를 확인해야 합니다.",
            ),
            "document_right_column_crop_risk": (
                "visual_crop_or_truncated_column",
                "문서 오른쪽 끝에 내용이 있어 금액/세액/합계 컬럼 잘림 여부를 확인해야 합니다.",
            ),
            "document_photo_source": (
                "photo_source_review_required",
                "사진으로 촬영된 문서로 보입니다. 확정 전 원본 확인이 필요합니다.",
            ),
            "document_fax_like_source": (
                "fax_like_source_review_required",
                "팩스/저품질 스캔 문서로 보입니다. 0/O, 1/I 같은 문자 혼동을 확인해야 합니다.",
            ),
        }
        issues: list[dict] = []
        for reason, (code, message_ko) in issue_map.items():
            if reason not in page_reasons:
                continue
            issues.append({
                "code": code,
                "message_ko": message_ko,
                "field": "workflow_metadata.document_quality",
                "severity": "warning",
                "blocking": False,
            })
        return issues

    def _field_provenance_metadata(
        self,
        document: Document,
        normalized: NormalizedDocument,
        workflow_metadata: dict,
    ) -> dict | None:
        quality = workflow_metadata.get("document_quality") if isinstance(workflow_metadata.get("document_quality"), dict) else {}
        hidden_columns = {
            str(column)
            for column in (quality.get("hidden_or_cropped_columns") or [])
            if isinstance(column, str)
        }
        quality_flags = [
            str(reason)
            for reason in (quality.get("review_reasons") or [])
            if isinstance(reason, str)
        ]
        extraction_method = normalized.extraction_method or document.extraction_method
        field_sources = dict(document.field_sources or {})
        fields: dict[str, dict[str, Any]] = {}
        for field_name in (
            "document_number",
            "vendor_name",
            "customer_name",
            "issue_date",
            "due_date",
            "currency",
            "subtotal",
            "tax",
            "extracted_amount",
        ):
            value = getattr(document, field_name, None)
            if value in (None, "", []):
                continue
            source = field_sources.get(field_name) or extraction_method or "unknown"
            fields[field_name] = self._field_provenance_entry(
                source=source,
                extraction_method=extraction_method,
                visible=True,
                confidence=document.confidence_score,
                quality_flags=quality_flags,
            )

        line_items: list[dict[str, dict[str, Any]]] = []
        for item in document.line_items or []:
            if not isinstance(item, dict):
                continue
            item_provenance = item.get("_provenance") if isinstance(item.get("_provenance"), dict) else {}
            item_sources: dict[str, dict[str, Any]] = {}
            for field_name in (
                "item_name",
                "item_code",
                "document_item_code",
                "internal_item_code",
                "specification",
                "quantity",
                "unit",
                "unit_price",
                "supply_amount",
                "tax_amount",
                "line_total",
            ):
                if item.get(field_name) in (None, "", []):
                    continue
                visible = field_name not in hidden_columns
                source = item_provenance.get("source") or field_sources.get("line_items") or extraction_method or "unknown"
                item_sources[field_name] = self._field_provenance_entry(
                    source=source,
                    extraction_method=item_provenance.get("mode") or extraction_method,
                    visible=visible,
                    confidence=document.confidence_score,
                    quality_flags=quality_flags,
                )
            if item_sources:
                line_items.append(item_sources)

        if not fields and not line_items:
            return None
        return {
            "version": 1,
            "summary": {
                "extraction_method": extraction_method,
                "visible_columns": quality.get("visible_columns") or [],
                "hidden_or_cropped_columns": list(hidden_columns),
                "policy": "confirmed values remain exportable; hidden/cropped column risk is recorded for review.",
            },
            "fields": fields,
            "line_items": line_items,
        }

    def _field_provenance_entry(
        self,
        *,
        source: str | None,
        extraction_method: str | None,
        visible: bool,
        confidence: Any,
        quality_flags: list[str],
    ) -> dict[str, Any]:
        source_name = str(source or "unknown")
        return {
            "source": source_name,
            "source_type": self._field_source_type(source_name, extraction_method),
            "extraction_method": extraction_method,
            "page": None,
            "bbox": None,
            "confidence": str(confidence) if confidence not in (None, "", []) else None,
            "visible": visible,
            "review_required": (not visible) or bool(quality_flags),
            "quality_flags": quality_flags,
        }

    def _field_source_type(self, source: str, extraction_method: str | None) -> str:
        combined = f"{source} {extraction_method or ''}".casefold()
        if "manual" in combined:
            return "manual_confirmed"
        if "paddleocr_vl" in combined or "vl" in combined:
            return "vl_source"
        if "text" in combined or "pdf_text" in combined:
            return "text_layer_source"
        if "ocr" in combined or "ppocr" in combined:
            return "fallback_source"
        return "parser_source"

    def _interpret_document(
        self,
        document: Document,
        text: str,
        normalized: NormalizedDocument,
        parsed: object,
        deterministic_first: bool,
        ai_escalation_required: bool = False,
    ) -> CategoryInterpretation:
        if self._should_skip_ai_interpretation(normalized, parsed, deterministic_first, ai_escalation_required):
            logger.info(
                "Skipping AI interpretation because deterministic parser result is sufficient for document %s.",
                document.id,
            )
            interpretation = self.heuristic_interpreter.interpret(document, text)
            interpretation.provider = "rule_based_structuring"
            interpretation.provider_chain = ["rule_based_structuring", "interpretation_skipped_rule_based_ready"]
            interpretation.refinement_status = "parser_only_rule_based_ready"
            interpretation.diagnostics.append("AI interpretation skipped; deterministic parser result used.")
            return interpretation
        try:
            logger.info("Running AI interpretation for document %s.", document.id)
            interpretation = self.category_interpreter.interpret(document, text)
            if self._interpretation_used_ai(interpretation):
                logger.info("AI interpretation completed for document %s with provider %s.", document.id, interpretation.provider)
            else:
                logger.info("AI interpretation skipped or fell back to heuristics for document %s.", document.id)
            return interpretation
        except Exception as exc:
            logger.warning("AI interpretation failed; using parser fallback for document %s: %s", document.id, exc)
            interpretation = self.heuristic_interpreter.interpret(document, text)
            interpretation.provider = "rule_based_structuring"
            interpretation.provider_chain = ["rule_based_structuring", "interpretation_fallback_heuristic"]
            interpretation.refinement_status = "interpretation_fallback_heuristic"
            interpretation.diagnostics.append(f"AI interpretation failed; parser result used: {exc}")
            return interpretation

    def _should_skip_ai_interpretation(self, normalized: NormalizedDocument, parsed: object, deterministic_first: bool, ai_escalation_required: bool = False) -> bool:
        if not deterministic_first:
            return False
        if ai_escalation_required:
            return False
        source_type = (normalized.source_file_type or "").lower()
        extraction_method = (normalized.extraction_method or "").lower()
        if source_type not in {"txt", "text", "pdf"} and "txt_direct" not in extraction_method and "pdf_text" not in extraction_method:
            return False
        return self._is_manufacturing_parsed_type(parsed) and bool(getattr(parsed, "line_items", None))

    def _is_ai_escalation_source(self, normalized: NormalizedDocument) -> bool:
        source_type = (normalized.source_file_type or "").lower()
        extraction_method = (normalized.extraction_method or "").lower()
        return (
            source_type in {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp"}
            and (normalized.primary_image_path is not None or "ocr" in extraction_method or normalized.heavy_ai_candidate)
            or "ocr" in extraction_method
            or normalized.heavy_ai_candidate
            or normalized.partial_support
        )

    def _is_parser_only_interpretation(self, interpretation: CategoryInterpretation) -> bool:
        chain = set(interpretation.provider_chain or [])
        return "interpretation_skipped_rule_based_ready" in chain or interpretation.provider == "rule_based_structuring"

    def _interpretation_used_ai(self, interpretation: CategoryInterpretation) -> bool:
        chain = " ".join(interpretation.provider_chain or []).lower()
        provider = (interpretation.provider or "").lower()
        if "skipped" in chain or "fallback" in chain or provider in {"rule_based_structuring", "heuristic_interpretation", "null_interpretation"}:
            return False
        return any(token in chain or token in provider for token in ["ai_interpretation_", "gemma", "gguf", "llama", "openai"])

    def _refinement_provider_for_interpretation(self, interpretation: CategoryInterpretation) -> str | None:
        if self._is_parser_only_interpretation(interpretation):
            return "rule_based_structuring"
        if self._interpretation_used_ai(interpretation):
            return interpretation.provider
        return None

    def _confidence(self, normalized: NormalizedDocument) -> Decimal | None:
        if normalized.ocr_confidence is None:
            return Decimal("0.850")
        return Decimal(str(round(normalized.ocr_confidence, 3)))

    def _ingestion_notes(self, normalized: NormalizedDocument, route) -> list[str]:
        notes = list(normalized.extraction_warnings)
        notes.extend(f"Route: {route.route_label} - {reason}" for reason in route.reasons)
        if route.heavy_ai_required:
            notes.append("Heavy AI extraction was selected for this document.")
        else:
            notes.append("Heavy AI extraction was skipped because direct/lightweight extraction was sufficient.")
        return notes

    def _ingestion_metadata(
        self,
        normalized: NormalizedDocument,
        route,
        extraction_quality: QualityEvaluation,
        structured_quality: QualityEvaluation,
        interpretation: CategoryInterpretation | None = None,
        ai_escalation=None,
        ai_provider_diagnostics: dict | None = None,
        taxonomy: dict | None = None,
    ) -> dict:
        metadata = {
            "source_file_type": normalized.source_file_type,
            "mime_type": normalized.mime_type,
            "extraction_method": normalized.extraction_method,
            "route": route.route_label,
            "processing_path": route.processing_path.value,
            "route_confidence": route.confidence,
            "route_reasons": route.reasons,
            "heavy_ai_required": route.heavy_ai_required,
            "partial_support": normalized.partial_support,
            "extraction_warnings": normalized.extraction_warnings,
            "file_metadata": normalized.file_metadata,
            "page_images": [str(path) for path in normalized.rendered_image_paths],
            "raw_block_count": len(normalized.raw_extracted_blocks),
            "quality_gates": {
                extraction_quality.stage: {
                    "score": extraction_quality.score,
                    "sufficient": extraction_quality.sufficient,
                    "review_required": extraction_quality.review_required,
                    "escalation_recommended": extraction_quality.escalation_recommended,
                    "reasons": extraction_quality.reasons,
                },
                structured_quality.stage: {
                    "score": structured_quality.score,
                    "sufficient": structured_quality.sufficient,
                    "review_required": structured_quality.review_required,
                    "escalation_recommended": structured_quality.escalation_recommended,
                    "reasons": structured_quality.reasons,
                },
            },
        }
        if interpretation:
            metadata["category_interpretation"] = {
                "category": interpretation.category,
                "profile": interpretation.profile,
                "subtype": interpretation.subtype,
                "title_hint": interpretation.title_hint,
                "summary_hint": interpretation.summary_hint,
                "key_fields": interpretation.key_fields,
                "warnings": interpretation.warnings,
                "workflow_hints": interpretation.workflow_hints,
                "reasons": interpretation.reasons,
                "confidence": interpretation.confidence,
                "provider": interpretation.provider,
                "provider_chain": interpretation.provider_chain,
                "refinement_status": interpretation.refinement_status,
                "diagnostics": interpretation.diagnostics,
                "ai_assisted": interpretation.ai_assisted,
            }
        if ai_escalation:
            metadata["ai_escalation_decision"] = {
                "should_escalate": ai_escalation.should_escalate,
                "severity": ai_escalation.severity,
                "confidence": ai_escalation.confidence,
                "reasons": ai_escalation.reasons,
                "signals": ai_escalation.signals,
            }
        if ai_provider_diagnostics:
            metadata["ai_provider_diagnostics"] = ai_provider_diagnostics
        if taxonomy:
            metadata["taxonomy"] = taxonomy
        return metadata

    def _provider_chain(
        self,
        normalized: NormalizedDocument,
        route,
        ai_chain: list[str],
        interpretation_chain: list[str] | None = None,
    ) -> list[str]:
        values = [normalized.extraction_method, route.route_label, *ai_chain, *(interpretation_chain or [])]
        return list(dict.fromkeys(value for value in values if value))

    def _apply_ai_provider_chain_diagnostics(self, diagnostics: dict, provider_chain: list[str]) -> None:
        if not diagnostics.get("document_ai_attempted"):
            diagnostics["primary_provider_status"] = "not_attempted"
            return
        diagnostics["primary_provider"] = self.settings.ai_primary_provider
        unavailable = next((provider for provider in provider_chain if provider.endswith("_unavailable") or "unavailable" in provider), None)
        if unavailable:
            diagnostics["primary_provider_status"] = "unavailable"
            diagnostics["primary_provider_failed_reason"] = unavailable
            fallback = next((provider for provider in provider_chain if provider in {"heuristic_fallback", "local"}), None)
            diagnostics["document_ai_fallback_provider"] = fallback or diagnostics.get("document_ai_fallback_provider")
            return
        if self.settings.ai_primary_provider in provider_chain or "paddleocr_vl" in provider_chain:
            diagnostics["primary_provider_status"] = "succeeded"
            return
        diagnostics["primary_provider_status"] = "not_used"

    def _notes(self, notes: list[str]) -> str | None:
        if not notes:
            return None
        return "\n".join(dict.fromkeys(note for note in notes if note))

    def _parsed_manufacturing_has_business_data(self, parsed: NormalizedDocument | object) -> bool:
        return self._is_manufacturing_parsed_type(parsed) and bool(getattr(parsed, "line_items", None))

    def _is_manufacturing_parsed_type(self, parsed: object) -> bool:
        doc_type = getattr(getattr(parsed, "document_type", None), "value", str(getattr(parsed, "document_type", "") or ""))
        if doc_type in {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
            "inspection_report",
        }:
            return True
        semantic_values = [
            getattr(parsed, "category", None),
            *(getattr(parsed, "tags", None) or []),
        ]
        semantic_text = " ".join(str(value or "") for value in semantic_values)
        return bool(re.search(r"\b(?:internal_transfer|return_note|credit_note)\b", semantic_text, flags=re.IGNORECASE))

    def _normalize_manufacturing_dates(self, parsed: object, issue_date, due_date) -> tuple:
        doc_type = getattr(getattr(parsed, "document_type", None), "value", str(getattr(parsed, "document_type", "") or ""))
        fields = getattr(parsed, "business_fields", {}) or {}
        if doc_type == "quotation":
            return issue_date, self._date_from_metadata(fields.get("valid_until")) or due_date
        if doc_type == "delivery_note":
            return issue_date, self._date_from_metadata(fields.get("delivery_date")) or due_date
        if doc_type == "invoice":
            return issue_date, self._date_from_metadata(fields.get("payment_due_date")) or due_date
        if doc_type == "transaction_statement":
            return issue_date, None
        return issue_date, due_date

    def _date_from_metadata(self, value):
        if not value:
            return None
        from datetime import date

        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    def _normalize_manufacturing_interpretation(self, interpretation: CategoryInterpretation, parsed: object) -> CategoryInterpretation:
        doc_type = getattr(getattr(parsed, "document_type", None), "value", str(getattr(parsed, "document_type", "") or ""))
        if not doc_type:
            return interpretation
        if interpretation.profile != doc_type or interpretation.category != doc_type or interpretation.subtype != doc_type:
            interpretation.diagnostics.append(
                f"Manufacturing profile normalized from category={interpretation.category}, profile={interpretation.profile}, subtype={interpretation.subtype} to {doc_type}."
            )
        interpretation.category = doc_type
        interpretation.profile = doc_type
        interpretation.subtype = doc_type
        return interpretation

    def _apply_taxonomy_category(self, category: str | None, taxonomy: dict) -> str | None:
        subtype = taxonomy.get("document_subtype")
        doc_type = taxonomy.get("document_type")
        if subtype in {"return_note", "credit_note", "internal_transfer"}:
            return str(subtype)
        if subtype == "tax_invoice":
            return category or doc_type or "invoice"
        return category

    def _merge_taxonomy_tags(self, tags: list[str] | None, taxonomy: dict) -> list[str]:
        merged = list(tags or [])
        for value in [
            taxonomy.get("document_subtype"),
            taxonomy.get("document_profile"),
            *(taxonomy.get("document_profiles") or []),
        ]:
            if value and value not in merged:
                merged.append(str(value))
        return merged

    def _is_return_or_credit_parsed_document(self, parsed: object, raw_text: str) -> bool:
        doc_number = str(getattr(parsed, "document_number", "") or "")
        text = "\n".join(line.strip() for line in str(raw_text or "").splitlines()[:10])
        return bool(
            re.search(r"^(?:RTN|RCM)[-_ ]?\d{4}", doc_number, flags=re.IGNORECASE)
            or re.search(r"(반품\s*/?\s*(?:차감|크레딧)|크레딧\s*메모|반품\s*요청|차감\s*요청|return\s+note|credit\s+(?:note|memo))", text, flags=re.IGNORECASE)
        )

    def _return_or_credit_category(self, parsed: object, raw_text: str) -> str:
        category = str(getattr(parsed, "category", "") or "")
        tags = " ".join(str(tag or "") for tag in (getattr(parsed, "tags", None) or []))
        text = f"{category} {tags}\n" + "\n".join(line.strip() for line in str(raw_text or "").splitlines()[:12])
        if re.search(r"(credit_note|크레딧|차감|credit\s+(?:note|memo))", text, flags=re.IGNORECASE):
            return "credit_note"
        return "return_note"

    def _structured_or_parsed_return_credit_signal(self, structured: dict, parsed: object) -> bool:
        candidate_doc = structured.get("document") if isinstance(structured.get("document"), dict) else {}
        values = [
            candidate_doc.get("document_type"),
            candidate_doc.get("document_subtype"),
            candidate_doc.get("document_profile"),
            candidate_doc.get("category"),
            candidate_doc.get("document_number"),
            getattr(parsed, "category", None),
            getattr(parsed, "document_number", None),
            *(getattr(parsed, "tags", None) or []),
        ]
        text = " ".join(str(value or "") for value in values)
        return bool(
            re.search(r"\b(?:return_note|credit_note|return_document|return_credit)\b", text, flags=re.IGNORECASE)
            or re.search(r"^(?:RTN|RCM)[-_ ]?\d{4}", str(candidate_doc.get("document_number") or getattr(parsed, "document_number", "") or ""), flags=re.IGNORECASE)
        )

    def _is_internal_transfer_parsed_document(self, parsed: object, raw_text: str) -> bool:
        doc_number = str(getattr(parsed, "document_number", "") or "")
        category = str(getattr(parsed, "category", "") or "")
        tags = " ".join(str(tag or "") for tag in (getattr(parsed, "tags", None) or []))
        text = "\n".join(line.strip() for line in str(raw_text or "").splitlines()[:12])
        return bool(
            re.search(r"^TRF[-_ ]?\d{4}", doc_number, flags=re.IGNORECASE)
            or re.search(r"\binternal_transfer\b", f"{category} {tags}", flags=re.IGNORECASE)
            or re.search(r"(내부\s*(?:자재\s*)?이동|자재\s*이동|출고창고|입고창고|내부품목코드|요청수량)", text, flags=re.IGNORECASE)
        )

    def _internal_transfer_document_type(self, parsed: object):
        parsed_type = getattr(parsed, "document_type", None)
        parsed_value = getattr(parsed_type, "value", str(parsed_type or ""))
        if parsed_value in {"", "other", "memo", "document", "general_document"}:
            return DocumentType.general_document
        return parsed_type

    def _sum_line_item_field(self, line_items: list[dict], field: str) -> Decimal | None:
        total = Decimal("0")
        found = False
        for item in line_items or []:
            value = item.get(field)
            if value in (None, "", []):
                continue
            try:
                total += Decimal(str(value).replace(",", ""))
                found = True
            except Exception:
                continue
        return total if found else None

    def _quality_notes(self, extraction_quality: QualityEvaluation, structured_quality: QualityEvaluation) -> list[str]:
        return [
            f"Quality gate {extraction_quality.stage}: score={extraction_quality.score}, sufficient={extraction_quality.sufficient}.",
            f"Quality gate {structured_quality.stage}: score={structured_quality.score}, sufficient={structured_quality.sufficient}.",
        ]

    def _interpretation_notes(self, interpretation: CategoryInterpretation) -> list[str]:
        return [
            f"Category interpretation: profile={interpretation.profile}, category={interpretation.category}, confidence={interpretation.confidence}."
        ] + list(interpretation.reasons) + list(interpretation.diagnostics)

    def _manufacturing_review_required(self, document: Document) -> bool:
        manufacturing_types = {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
        }
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        if doc_type not in manufacturing_types:
            return False
        taxonomy = self.taxonomy.classify(document, document.raw_text or "")
        no_price_quantity_doc = self._is_no_price_quantity_document(document) or taxonomy.amount_required is False
        party_optional_doc = no_price_quantity_doc or taxonomy.party_required is False
        low_confidence = list(document.low_confidence_fields or [])
        if not document.line_items:
            low_confidence.append("missing_line_items")
            document.low_confidence_fields = sorted(set(low_confidence))
            return True
        for index, item in enumerate(document.line_items, start=1):
            code_suffix = f":item_{index}"
            if item.get("item_name") in (None, "", []):
                low_confidence.append(f"missing_item_name{code_suffix}")
            if item.get("quantity") in (None, "", []):
                low_confidence.append(f"missing_quantity{code_suffix}")
            if not no_price_quantity_doc and doc_type != "delivery_note" and item.get("unit_price") in (None, "", []) and item.get("line_total") in (None, "", []):
                low_confidence.append(f"missing_price_or_total{code_suffix}")
            if self._line_item_warnings_require_amount_review(item.get("validation_warnings") or []):
                low_confidence.append(f"invalid_line_amount{code_suffix}")
            if item.get("item_code") in (None, "", []) and item.get("internal_item_code") in (None, "", []):
                low_confidence.append(f"missing_item_code{code_suffix}")
            match_status = item.get("item_master_match_status")
            if match_status == "skipped_no_item_master" and item.get("item_code") in (None, "", []):
                low_confidence.append("item_matching_skipped")
            elif match_status in {"ambiguous", "needs_review", "unmatched"}:
                low_confidence.append(f"item_master_match_required{code_suffix}")
        if self._manufacturing_total_mismatch(document):
            low_confidence.append("amount_mismatch")
        if not document.document_number:
            low_confidence.append("missing_document_number")
        if not (document.issue_date or document.extracted_date):
            low_confidence.append("missing_issue_date")
        if party_optional_doc:
            low_confidence = [
                field for field in low_confidence
                if field not in {"missing_vendor_name", "missing_customer_name"}
            ]
        if doc_type == "purchase_order" and not document.due_date:
            low_confidence.append("missing_due_date")
        if doc_type == "invoice" and not document.due_date:
            low_confidence.append("missing_payment_due_date")
        document.low_confidence_fields = sorted(set(low_confidence))
        return bool(document.low_confidence_fields)

    def _vl_structured_candidate_is_amountless(self, structured: dict) -> bool:
        candidate_doc = structured.get("document") if isinstance(structured.get("document"), dict) else {}
        if any(candidate_doc.get(field) not in (None, "", []) for field in ("subtotal", "tax", "total")):
            return False
        line_items = structured.get("line_items") if isinstance(structured.get("line_items"), list) else []
        for item in line_items:
            if not isinstance(item, dict):
                continue
            if any(item.get(field) not in (None, "", []) for field in ("unit_price", "supply_amount", "tax_amount", "line_total")):
                return False
        return bool(line_items) or str(candidate_doc.get("document_type") or "") in {
            "delivery_note",
            "inspection_report",
            "general_document",
            "other",
            "packing_list",
        }

    def _line_item_warnings_require_amount_review(self, warnings: list | tuple | set) -> bool:
        safe_non_amount_warnings = {
            "handwritten_vl_candidate",
            "handwritten_inspection_requires_review",
            "hold_quantity_requires_review",
            "line_total_not_visible_do_not_infer",
            "row_amount_hidden_do_not_infer",
            "missing_line_amount",
            "trailing_number_requires_review",
            "text_layer_item_name_reconciled",
        }
        return any(str(warning) not in safe_non_amount_warnings for warning in warnings or [])

    def _is_internal_transfer_document(self, document: Document) -> bool:
        values = [
            getattr(document, "category", None),
            *(getattr(document, "tags", None) or []),
            getattr(document, "document_number", None),
        ]
        text = " ".join(str(value or "") for value in values)
        return bool(re.search(r"internal[_ -]?transfer|\bTRF[-_ ]?\d{4}|사업장|자재\s*이동", text, flags=re.IGNORECASE))

    def _is_no_price_quantity_document(self, document: Document) -> bool:
        doc_type = getattr(document.document_type, "value", str(document.document_type or ""))
        if doc_type in {"delivery_note", "inspection_report"}:
            return True
        if self._is_internal_transfer_document(document):
            return True
        if document.extracted_amount is not None or document.subtotal is not None or document.tax is not None:
            return False
        return bool(document.line_items) and any(
            item.get("quantity") not in (None, "", []) or item.get("item_code") not in (None, "", []) or item.get("document_item_code") not in (None, "", [])
            for item in document.line_items or []
        )

    def _is_manufacturing_type(self, document: Document) -> bool:
        return getattr(document.document_type, "value", str(document.document_type or "")) in {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
            "inspection_report",
            "contract",
            "general_document",
        }

    def _manufacturing_total_mismatch(self, document: Document) -> bool:
        if document.extracted_amount is None or not document.line_items:
            return False
        line_total = Decimal("0")
        found = False
        for item in document.line_items:
            value = item.get("line_total")
            if value in (None, "", []):
                continue
            try:
                line_total += Decimal(str(value).replace(",", ""))
                found = True
            except Exception:
                return True
        if not found:
            return False
        tolerance = self._amount_tolerance(document.currency)
        return abs(document.extracted_amount - line_total) > tolerance

    def _amount_tolerance(self, currency: str | None) -> Decimal:
        normalized = (currency or "KRW").upper()
        if normalized == "USD":
            return Decimal("0.05")
        if normalized == "KRW":
            return Decimal("10")
        return Decimal("1")

    def _apply_title_hint(self, current_title: str | None, interpretation: CategoryInterpretation) -> str | None:
        if not interpretation.title_hint:
            return current_title
        if not current_title or current_title.lower() in {"untitled document", "profile note", "syllabus", "invoice", "statement"}:
            return interpretation.title_hint
        if current_title.lower().startswith(("page ", "slide ")):
            return interpretation.title_hint
        if "|" in current_title or re.match(r"^(title|name|invoice(?: number)?|vendor)\s*[:|]", current_title, flags=re.IGNORECASE):
            return interpretation.title_hint
        if interpretation.profile in {"installation_guide", "implementation_schedule"} and (
            self._looks_like_person_name_title(current_title)
            or "profile" in current_title.lower()
            or self._title_quality(current_title) < self._title_quality(interpretation.title_hint)
        ):
            return interpretation.title_hint
        if interpretation.profile in {"profile_record", "resume_profile"} and current_title != interpretation.title_hint:
            return interpretation.title_hint
        if interpretation.profile == "invoice" and current_title and "receipt" in current_title.lower():
            return interpretation.title_hint or current_title
        return current_title

    def _clean_final_title(self, title: str | None, interpretation: CategoryInterpretation) -> str | None:
        cleaned = self._clean_text_fragment(title)
        if not cleaned:
            return interpretation.title_hint or title
        if self._is_failed_placeholder(cleaned):
            return self._clean_text_fragment(interpretation.title_hint)
        if interpretation.profile == "invoice" and "receipt" in cleaned.lower():
            return self._clean_text_fragment(interpretation.title_hint) or "Invoice"
        if interpretation.profile in {"installation_guide", "implementation_schedule"} and self._looks_like_person_name_title(cleaned):
            return self._clean_text_fragment(interpretation.title_hint) or cleaned
        if interpretation.profile in {"receipt", "repair_service_receipt"}:
            cleaned = re.sub(r"\s+receipt\s+receipt$", " receipt", cleaned, flags=re.IGNORECASE)
        return cleaned

    def _clean_final_merchant(self, merchant: str | None) -> str | None:
        cleaned = self._clean_text_fragment(merchant)
        if not cleaned:
            return None
        if re.match(r"^(?:acct|account|ticket|customer|date|bike|invoice\s+(?:number|#)|vendor|bill to)\b", cleaned, flags=re.IGNORECASE):
            return None
        return cleaned

    def _clean_text_fragment(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        cleaned = re.sub(r"\s*[-–—]+\s*[.,:;]*\s*$", "", cleaned)
        cleaned = re.sub(r"(?:\s+[.,:;|%]+)+$", "", cleaned)
        cleaned = re.sub(r"\s+[-–—]\s+[.,:;]+$", "", cleaned)
        cleaned = cleaned.strip(" \t\r\n-–—|")
        if not cleaned or re.fullmatch(r"[.,:;/%\\-]+", cleaned):
            return None
        return cleaned[:160]

    def _title_quality(self, title: str | None) -> int:
        cleaned = self._clean_text_fragment(title)
        if not cleaned:
            return -100
        lowered = cleaned.lower()
        score = 10
        if any(keyword in lowered for keyword in ["installation guide", "setup guide", "technical guide", "implementation schedule", "project tracker", "roadmap"]):
            score += 40
        if any(keyword in lowered for keyword in ["guide", "manual", "schedule", "tracker", "roadmap", "implementation"]):
            score += 16
        if self._looks_like_person_name_title(cleaned):
            score -= 30
        if "|" in cleaned:
            score -= 15
        if len(cleaned.split()) > 12:
            score -= 12
        return score

    def _looks_like_person_name_title(self, title: str | None) -> bool:
        cleaned = self._clean_text_fragment(title)
        if not cleaned:
            return False
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}", cleaned):
            return False
        lowered = cleaned.lower()
        return not any(keyword in lowered for keyword in ["guide", "manual", "schedule", "tracker", "roadmap", "invoice", "statement", "profile", "syllabus"])

    def _is_failed_placeholder(self, value: str) -> bool:
        lowered = re.sub(r"\s+", " ", value).strip().lower()
        return bool(re.fullmatch(r"(?:연도|년도)\s*[.년]\s*월\s*[.월]\s*일\s*[.일]?", lowered))

    def _apply_category_hint(self, current_category: str | None, interpretation: CategoryInterpretation) -> str | None:
        specific_profiles = {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "packing_list",
            "inspection_report",
            "contract",
            "general_document",
            "syllabus",
            "course_guide",
            "presentation_guide",
            "speaking_notes",
            "resume_profile",
            "profile_record",
            "installation_guide",
            "implementation_schedule",
            "repair_service_receipt",
            "utility_bill",
            "meeting_notice",
            "instructional_memo",
            "invoice",
        }
        if interpretation.profile in specific_profiles:
            return normalize_category(interpretation.profile)
        return normalize_category(interpretation.category or current_category)

    def _refined_document_type(self, current_type, interpretation: CategoryInterpretation):
        profile = interpretation.profile
        manufacturing_profiles = {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
            "inspection_report",
            "contract",
            "general_document",
        }
        if profile in manufacturing_profiles:
            try:
                return type(current_type)(profile)
            except ValueError:
                return current_type
        if profile in {"syllabus", "course_guide", "resume_profile", "profile_record", "installation_guide", "implementation_schedule", "invoice", "utility_bill"}:
            return type(current_type).document
        if profile in {"presentation_guide", "speaking_notes"}:
            return type(current_type).presentation
        if profile in {"instructional_memo"}:
            return type(current_type).memo
        if profile == "meeting_notice":
            return type(current_type).notice
        if profile in {"repair_service_receipt", "receipt"}:
            return type(current_type).receipt
        return current_type

    def _merge_tags(self, current_tags: list[str], interpretation: CategoryInterpretation, document_type) -> list[str]:
        tags = list(current_tags or [])
        for value in [interpretation.profile, interpretation.category, interpretation.subtype]:
            normalized = normalize_category(value)
            if normalized and normalized not in {"generic_document", "other", "document", "notice"}:
                tags.append(normalized)
        if interpretation.profile == "presentation_guide" and interpretation.subtype == "speaking_notes":
            tags.append("script")
        return clean_tags_for_context(
            tags,
            category=interpretation.category,
            profile=interpretation.profile,
            document_type=getattr(document_type, "value", str(document_type)),
        )
