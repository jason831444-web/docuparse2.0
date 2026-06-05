import logging
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.document import Document, ProcessingStatus
from app.services.ai_document_understanding import LocalDocumentAIService, get_document_ai_service
from app.services.ai_escalation import should_escalate_to_ai
from app.services.ai_merge import AIResultMerger
from app.services.category_interpretation import CategoryInterpretation, CategoryInterpretationService
from app.services.category_taxonomy import clean_tags_for_context, normalize_category
from app.services.document_router import LightweightDocumentRouter
from app.services.document_interpretation_service import DocumentInterpretationService
from app.services.file_ingestion import FileIngestionService, NormalizedDocument
from app.services.item_master_matcher import ItemMasterMatcher
from app.services.ocr import OCRService
from app.services.parser import DocumentParser
from app.services.persistence_safety import sanitize_for_postgres
from app.services.quality_evaluation import DocumentQualityEvaluator, QualityEvaluation
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


logger = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(self, ocr: OCRService | None = None, parser: DocumentParser | None = None) -> None:
        self.ocr = ocr or OCRService()
        self.parser = parser or DocumentParser()
        self.ingestion = FileIngestionService(ocr=self.ocr)
        self.quality = DocumentQualityEvaluator()
        self.router = LightweightDocumentRouter()
        self.lightweight_ai = LocalDocumentAIService()
        self.heuristic_interpreter = CategoryInterpretationService()
        self.category_interpreter = DocumentInterpretationService()
        self.workflow_enrichment = DocumentWorkflowEnrichmentService()
        self.item_master_matcher = ItemMasterMatcher()
        self.ai_merger = AIResultMerger()

    def process(self, db: Session, document: Document) -> Document:
        document.processing_status = ProcessingStatus.processing
        document.processing_error = None
        db.add(document)
        db.commit()
        db.refresh(document)
        try:
            stored_path = Path(document.stored_file_path)
            normalized = self.ingestion.ingest(stored_path, document.original_filename, document.mime_type)
            raw_text = normalized.normalized_text
            parsed = self.parser.parse(raw_text, document.original_filename)
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
                except Exception as exc:
                    ai_fallback_notes.append(f"AI extraction failed; parser result used: {exc}")
                    ai_provider_diagnostics["document_ai_failed_reason"] = str(exc)
                    ai_result = self.lightweight_ai.analyze(analysis_path, raw_text, parsed, document.original_filename)
                    ai_provider_diagnostics["document_ai_fallback_provider"] = ai_result.provider
            else:
                ai_result = self.lightweight_ai.analyze(analysis_path, raw_text, parsed, document.original_filename)
                ai_result.extraction_provider = normalized.extraction_method or route.route_label
                ai_result.provider = ai_result.extraction_provider
                ai_result.provider_chain = [normalized.extraction_method or route.route_label, "heuristic_fallback"]
                ai_result.merge_strategy = route.route_label
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
            document.extracted_amount = parsed.extracted_amount or ai_result.extracted_amount if deterministic_first else ai_result.extracted_amount or parsed.extracted_amount
            document.subtotal = (parsed.subtotal or ai_result.subtotal) if deterministic_first else (ai_result.subtotal or parsed.subtotal)
            document.tax = (parsed.tax or ai_result.tax) if deterministic_first else (ai_result.tax or parsed.tax)
            document.currency = ai_result.currency or parsed.currency
            document.merchant_name = sanitize_for_postgres(ai_result.merchant_name or parsed.merchant_name)
            document.vendor_name = sanitize_for_postgres((parsed.vendor_name or ai_result.vendor_name) if deterministic_first else (ai_result.vendor_name or parsed.vendor_name) or document.merchant_name)
            document.customer_name = sanitize_for_postgres((parsed.customer_name or ai_result.customer_name) if deterministic_first else (ai_result.customer_name or parsed.customer_name))
            document.document_number = sanitize_for_postgres((parsed.document_number or ai_result.document_number) if deterministic_first else (ai_result.document_number or parsed.document_number))
            document.issue_date = (parsed.issue_date or ai_result.issue_date or document.extracted_date) if deterministic_first else (ai_result.issue_date or parsed.issue_date or document.extracted_date)
            document.due_date = (parsed.due_date or ai_result.due_date) if deterministic_first else (ai_result.due_date or parsed.due_date)
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                document.issue_date, document.due_date = self._normalize_manufacturing_dates(parsed, document.issue_date, document.due_date)
            document.line_items = sanitize_for_postgres((parsed.line_items or ai_result.line_items) if deterministic_first else (ai_result.line_items or parsed.line_items or []))
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
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                document.document_type = parsed.document_type
                document.ai_document_type = parsed.document_type
                document.category = parsed.category or parsed.document_type.value
                document.tags = [parsed.document_type.value]
                document.line_items = sanitize_for_postgres(self.item_master_matcher.match_line_items(db, document.line_items or []))
            document.title = self._clean_final_title(document.title, interpretation)
            document.merchant_name = self._clean_final_merchant(document.merchant_name)
            if interpretation.summary_hint:
                document.summary = sanitize_for_postgres(interpretation.summary_hint)
            document.tags = self._merge_tags(document.tags, interpretation, document.document_type)
            if deterministic_first and self._is_manufacturing_parsed_type(parsed):
                document.tags = [parsed.document_type.value]
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
            ))
            document.review_required = document.review_required or self._manufacturing_review_required(document)
            workflow = self.workflow_enrichment.enrich(document, ai_result.cleaned_raw_text or raw_text, interpretation)
            document.workflow_summary = sanitize_for_postgres(workflow.workflow_summary)
            if self._is_manufacturing_type(document):
                document.summary = sanitize_for_postgres(workflow.workflow_summary)
            document.action_items = sanitize_for_postgres(workflow.action_items)
            document.warnings = sanitize_for_postgres(workflow.warnings)
            document.key_dates = sanitize_for_postgres(workflow.key_dates)
            document.urgency_level = workflow.urgency_level
            document.follow_up_required = workflow.follow_up_required
            document.workflow_metadata = sanitize_for_postgres(workflow.workflow_metadata or None)
            workflow_review_required = bool((workflow.workflow_metadata or {}).get("review_required"))
            document.review_required = workflow_review_required if self._is_manufacturing_type(document) else document.review_required or workflow_review_required
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

    def _notes(self, notes: list[str]) -> str | None:
        if not notes:
            return None
        return "\n".join(dict.fromkeys(note for note in notes if note))

    def _parsed_manufacturing_has_business_data(self, parsed: NormalizedDocument | object) -> bool:
        doc_type = getattr(getattr(parsed, "document_type", None), "value", str(getattr(parsed, "document_type", "") or ""))
        return doc_type in {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
        } and bool(getattr(parsed, "line_items", None))

    def _is_manufacturing_parsed_type(self, parsed: object) -> bool:
        doc_type = getattr(getattr(parsed, "document_type", None), "value", str(getattr(parsed, "document_type", "") or ""))
        return doc_type in {
            "purchase_order",
            "quotation",
            "transaction_statement",
            "delivery_note",
            "invoice",
            "packing_list",
        }

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
            if doc_type != "delivery_note" and item.get("unit_price") in (None, "", []) and item.get("line_total") in (None, "", []):
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
        if doc_type == "purchase_order" and not document.due_date:
            low_confidence.append("missing_due_date")
        if doc_type == "invoice" and not document.due_date:
            low_confidence.append("missing_payment_due_date")
        document.low_confidence_fields = sorted(set(low_confidence))
        return bool(document.low_confidence_fields)

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
