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
_PROCESSING_SEMAPHORE = threading.BoundedSemaphore(3)


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
            if vl_primary_attempt and vl_primary_attempt.get("promoted") and vl_primary_attempt.get("text"):
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
            vl_promoted_structured = self._vl_promoted_structured_candidate(vl_primary_attempt)
            if vl_promoted_structured:
                self._apply_vl_structured_candidate_to_parsed(parsed, vl_promoted_structured)
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
            selected_line_items = (parsed.line_items or ai_result.line_items) if deterministic_first else (ai_result.line_items or parsed.line_items or [])
            document.line_items = sanitize_for_postgres(
                self._line_items_for_extraction_method(selected_line_items, normalized.extraction_method)
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
                        self._line_items_for_extraction_method(parsed.line_items, normalized.extraction_method)
                    )
                document.category = "return_note"
                document.tags = ["return_note"]
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
                        self._line_items_for_extraction_method(document.line_items or [], normalized.extraction_method),
                    )
                )
            document.title = self._clean_final_title(document.title, interpretation)
            document.merchant_name = self._clean_final_merchant(document.merchant_name)
            if interpretation.summary_hint:
                document.summary = sanitize_for_postgres(interpretation.summary_hint)
            document.tags = self._merge_tags(document.tags, interpretation, document.document_type)
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                document.tags = ["internal_transfer"] if self._is_internal_transfer_parsed_document(parsed, raw_text) else [parsed.document_type.value]
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
        result = self.vl_worker.analyze(stored_path, original_filename=document.original_filename)
        metadata = self._vl_primary_reader_metadata_from_result(result, document, workflow_metadata)
        text = result.get("text") or result.get("text_preview")
        if not isinstance(text, str):
            text = ""
        return {
            "metadata": metadata,
            "text": text,
            "promoted": bool((metadata.get("vl_candidate_summary") or {}).get("promotion_applied")),
        }

    def _vl_primary_normalized_document(
        self,
        stored_path: Path,
        document: Document,
        text: str,
        metadata: dict,
    ) -> NormalizedDocument:
        provider_metadata = metadata.get("vl_provider_metadata") if isinstance(metadata.get("vl_provider_metadata"), dict) else {}
        return NormalizedDocument(
            source_file_type=stored_path.suffix.casefold().lstrip(".") or "file",
            mime_type=document.mime_type or "application/octet-stream",
            extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
            normalized_text="\n".join(line.strip() for line in text.splitlines() if line.strip()),
            raw_extracted_blocks=[{
                "type": "vl_primary_reader_text",
                "provider": provider_metadata.get("provider") or "paddleocr_vl_1_6_gguf",
                "content": text[:20000],
                "parser_integrated": True,
                "confirmed_promotion": True,
            }],
            extraction_warnings=[],
            file_metadata={
                "vl_primary_reader": True,
                "vl_worker_elapsed_ms": provider_metadata.get("elapsed_ms"),
                "vl_worker_status": provider_metadata.get("status"),
                "vl_worker_provider": provider_metadata.get("provider"),
                "ocr_fallback_used": False,
            },
            ocr_confidence=None,
            primary_image_path=None,
            heavy_ai_candidate=False,
            partial_support=False,
        )

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
            value = candidate_doc.get(key)
            if value not in (None, "", []):
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
        for attr, key in (("subtotal", "subtotal"), ("tax", "tax"), ("extracted_amount", "total")):
            value = self._vl_decimal(candidate_doc.get(key))
            if value is not None:
                if attr in {"subtotal", "tax", "extracted_amount"} and value < 0:
                    continue
                setattr(parsed, attr, value)
        line_items = structured.get("line_items") if isinstance(structured.get("line_items"), list) else []
        if line_items:
            parsed.line_items = self._safe_vl_promoted_line_items(line_items)

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
        }
        text = result.get("text") or result.get("text_preview")
        if not isinstance(text, str) or not text.strip():
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
            validation=result.get("validation") if isinstance(result.get("validation"), dict) else None,
        )
        candidate = {
            "source": "vl_worker_api",
            "provider": provider_metadata["provider"],
            "candidate_only": True,
            "parser_integrated": False,
            "parser_evaluated": bool(structured),
            "provider_available_candidate": bool(result.get("ok")),
            "validation_severity": result.get("classification"),
            "issue_codes": list((structured or {}).get("issue_codes") or []),
            "review_flags": list((structured or {}).get("review_flags") or (structured or {}).get("issue_codes") or []),
            "text_preview": text[:1200],
            "inference_time_ms": result.get("elapsed_ms"),
            "structured_candidate": structured,
        }
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
                "provider_available_candidate": bool(result.get("ok")),
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
        for key in ("vl_provider_metadata", "vl_candidates", "vl_candidate_summary"):
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
            value = candidate_doc.get(key)
            if value not in (None, "", []):
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

    def _safe_vl_promoted_line_items(self, line_items: list[dict]) -> list[dict]:
        safe_items: list[dict] = []
        for item in line_items:
            if not isinstance(item, dict):
                continue
            safe_item = dict(item)
            warnings = list(safe_item.get("validation_warnings") or [])
            warning_set = {str(warning) for warning in warnings}
            if "explicit_quantity_price_amount_mismatch" in warning_set:
                for field in ("supply_amount", "tax_amount", "line_total"):
                    safe_item.pop(field, None)
                if "vl_amount_suppressed_due_to_arithmetic_mismatch" not in warning_set:
                    warnings.append("vl_amount_suppressed_due_to_arithmetic_mismatch")
                safe_item["validation_warnings"] = sorted(set(warnings))
                review_flags = list(safe_item.get("review_flags") or [])
                if "vl_amount_suppressed_due_to_arithmetic_mismatch" not in review_flags:
                    review_flags.append("vl_amount_suppressed_due_to_arithmetic_mismatch")
                safe_item["review_flags"] = sorted(set(review_flags))
            safe_items.append(safe_item)
        return safe_items

    def _line_items_for_extraction_method(self, line_items: list[dict], extraction_method: str | None) -> list[dict]:
        if extraction_method == "paddleocr_vl_1_6_gguf_primary_reader":
            return self._safe_vl_promoted_line_items(line_items or [])
        return line_items or []

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
            re.search(r"^RTN[-_ ]?\d{4}", doc_number, flags=re.IGNORECASE)
            or re.search(r"(반품\s*/?\s*차감|반품\s*요청|차감\s*요청|return\s+note|credit\s+(?:note|memo))", text, flags=re.IGNORECASE)
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
            if item.get("validation_warnings"):
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
