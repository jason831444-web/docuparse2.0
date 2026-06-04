import json
import importlib.util
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.models.document import Document
from app.services.category_interpretation import CategoryInterpretation, CategoryInterpretationService


logger = logging.getLogger(__name__)

_gguf_models: dict[tuple[str, int, int, int], Any] = {}
_gguf_model_lock = threading.Lock()
_gguf_inference_semaphore = threading.Semaphore(1)


def get_llama_cpp_gguf_model(settings: Any) -> Any:
    model_path = settings.llama_cpp_model_path
    if model_path is None:
        raise RuntimeError("LLAMA_CPP_MODEL_PATH is required when AI_INTERPRETATION_PROVIDER=llama_cpp.")
    if not model_path.exists():
        raise RuntimeError(f"Configured GGUF model file does not exist: {model_path}")
    if model_path.is_dir():
        raise RuntimeError(f"LLAMA_CPP_MODEL_PATH must point to a .gguf file, not a directory: {model_path}")

    model_ref = str(model_path)
    cache_key = (
        model_ref,
        int(settings.llama_cpp_context_window),
        int(settings.llama_cpp_threads or 0),
        int(settings.llama_cpp_gpu_layers),
    )
    cached = _gguf_models.get(cache_key)
    if cached is not None:
        return cached

    with _gguf_model_lock:
        cached = _gguf_models.get(cache_key)
        if cached is not None:
            return cached
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError("llama-cpp-python is not installed. Install backend/requirements-llama.txt.") from exc

        logger.warning("Loading GGUF interpretation model with llama.cpp: %s", model_ref)
        model = Llama(
            model_path=model_ref,
            n_ctx=settings.llama_cpp_context_window,
            n_threads=settings.llama_cpp_threads or None,
            n_gpu_layers=settings.llama_cpp_gpu_layers,
            verbose=False,
        )
        _gguf_models[cache_key] = model
        return model


class DocumentInterpretationService:
    """AI-first category-aware interpretation pipeline.

    Extraction remains format-specific. This service interprets the extracted
    text/content, using Gemma by default and heuristics as bootstrap/fallback.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.heuristic = CategoryInterpretationService()
        self.ai_provider = self._provider()

    def interpret(self, document: Document, text: str) -> CategoryInterpretation:
        heuristic_result = self.heuristic.interpret(document, text)
        heuristic_result.provider = "heuristic_interpretation"
        heuristic_result.provider_chain = ["heuristic_interpretation"]
        heuristic_result.refinement_status = "heuristic_only"

        skip_reason = self._skip_reason(document, text, heuristic_result)
        if skip_reason:
            heuristic_result.provider_chain.append(skip_reason)
            heuristic_result.refinement_status = skip_reason
            heuristic_result.diagnostics.append(f"AI interpretation was skipped: {skip_reason}.")
            return heuristic_result

        try:
            refined = self.ai_provider.interpret(document, text, heuristic_result)
            return self._merge(heuristic_result, refined)
        except Exception as exc:
            logger.warning("AI-first interpretation failed: %s", exc)
            heuristic_result.provider_chain.extend(
                [f"{self.ai_provider.provider_name}_unavailable", "interpretation_fallback_heuristic"]
            )
            heuristic_result.refinement_status = "interpretation_fallback_heuristic"
            heuristic_result.diagnostics.append(f"AI interpretation failed; heuristic interpretation used: {exc}")
            return heuristic_result

    def _provider(self) -> "BaseInterpretationProvider":
        provider = self.settings.ai_interpretation_provider.lower()
        if provider == "gemma":
            return GemmaInterpretationProvider()
        if provider in {"llama_cpp", "gemma_gguf", "gguf"}:
            return LlamaCppGemmaInterpretationProvider()
        if provider == "openai":
            return OpenAITextInterpretationProvider()
        if provider in {"heuristic", "none"}:
            return NullInterpretationProvider()
        if provider == "auto":
            if self.settings.llama_cpp_model_path:
                return LlamaCppGemmaInterpretationProvider()
            if self.settings.gemma_model_dir or self.settings.huggingface_token:
                return GemmaInterpretationProvider()
            if self.settings.openai_api_key:
                return OpenAITextInterpretationProvider()
            return NullInterpretationProvider()
        return NullInterpretationProvider()

    def _skip_reason(self, document: Document, text: str, heuristic: CategoryInterpretation) -> str | None:
        if not self.settings.ai_interpretation_enabled:
            return "ai_interpretation_disabled"
        if not self.settings.ai_interpretation_skip_trivial:
            return None

        stripped = text.strip()
        if not stripped:
            return "ai_interpretation_skipped_low_text"
        if len(stripped) < self.settings.ai_interpretation_min_chars:
            return "ai_interpretation_skipped_low_text"

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 2 and len(stripped) < 180:
            return "ai_interpretation_skipped_trivial"

        structured_ratio = self._structured_ratio(lines)
        if heuristic.profile in {"receipt", "repair_service_receipt", "utility_bill", "resume_profile", "syllabus", "presentation_guide", "profile_record"}:
            return None
        if structured_ratio >= 0.9 and len(lines) <= 4 and len(stripped) < 260:
            return "ai_interpretation_skipped_trivial"
        return None

    def _structured_ratio(self, lines: list[str]) -> float:
        if not lines:
            return 0.0
        structured = sum(1 for line in lines if ":" in line or "|" in line or re.search(r"^\{.+\}$|^\[.+\]$", line))
        return structured / len(lines)

    def _merge(self, base: CategoryInterpretation, refined: CategoryInterpretation) -> CategoryInterpretation:
        result = CategoryInterpretation(
            category=base.category,
            profile=base.profile,
            subtype=base.subtype,
            title_hint=base.title_hint,
            summary_hint=base.summary_hint,
            key_fields=dict(base.key_fields),
            warnings=list(base.warnings),
            workflow_hints=dict(base.workflow_hints),
            reasons=list(base.reasons),
            confidence=base.confidence,
            provider=refined.provider,
            provider_chain=list(dict.fromkeys(base.provider_chain + refined.provider_chain)),
            refinement_status=refined.refinement_status,
            diagnostics=base.diagnostics + refined.diagnostics,
            ai_assisted=True,
        )

        should_adopt_category = (
            refined.confidence >= max(0.62, base.confidence - 0.08)
            and (
                base.profile == "generic_document"
                or self._is_generic_category(base.category)
                or refined.profile == base.profile
                or refined.profile
                in {
                    "resume_profile",
                    "presentation_guide",
                    "speaking_notes",
                    "syllabus",
                    "course_guide",
                    "profile_record",
                    "installation_guide",
                    "implementation_schedule",
                    "repair_service_receipt",
                    "utility_bill",
                    "meeting_notice",
                    "instructional_memo",
                    "invoice",
                    "purchase_order",
                    "quotation",
                    "transaction_statement",
                    "delivery_note",
                    "packing_list",
                    "inspection_report",
                    "contract",
                    "general_document",
                }
            )
        )
        if should_adopt_category and self._specific_profile_regression(base, refined):
            should_adopt_category = False
            result.diagnostics.append(
                f"Kept specific heuristic profile {base.profile}; AI refinement proposed broader {refined.profile}."
            )
        if should_adopt_category and self._profile_record_token_regression(base, refined):
            should_adopt_category = False
            result.diagnostics.append(
                f"Kept structural heuristic profile {base.profile}; AI refinement proposed profile_record from weaker token evidence."
            )
        if should_adopt_category:
            result.category = refined.category or result.category
            result.profile = refined.profile or result.profile
            result.subtype = refined.subtype or result.subtype
            result.reasons.extend(note for note in refined.reasons if note not in result.reasons)
        elif base.profile != "generic_document" and refined.profile == "generic_document":
            result.category = base.category
            result.profile = base.profile
            result.subtype = base.subtype

        if self._weak_title(result.title_hint) and refined.title_hint:
            result.title_hint = refined.title_hint
        elif refined.title_hint and self._title_quality(refined.title_hint) > self._title_quality(result.title_hint):
            result.title_hint = refined.title_hint

        if (
            refined.summary_hint
            and not self._is_manufacturing_profile(result.profile)
            and self._better_summary(refined.summary_hint, result.summary_hint)
        ):
            result.summary_hint = refined.summary_hint

        for key, value in refined.key_fields.items():
            if value in (None, "", [], {}):
                continue
            if key not in result.key_fields or result.key_fields.get(key) in (None, "", [], {}):
                result.key_fields[key] = value
            elif isinstance(value, list) and isinstance(result.key_fields.get(key), list):
                result.key_fields[key] = list(dict.fromkeys(result.key_fields[key] + value))
            elif isinstance(value, dict) and isinstance(result.key_fields.get(key), dict):
                merged = dict(result.key_fields[key])
                merged.update({k: v for k, v in value.items() if v not in (None, "", [], {})})
                result.key_fields[key] = merged

        result.warnings = list(dict.fromkeys(result.warnings + refined.warnings))
        result.workflow_hints = self._merge_workflow_hints(result.workflow_hints, refined.workflow_hints)
        result.diagnostics.extend(note for note in refined.diagnostics if note not in result.diagnostics)
        return result

    def _merge_workflow_hints(self, base: dict[str, Any], refined: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in refined.items():
            if value in (None, "", [], {}):
                continue
            if key not in merged:
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = list(dict.fromkeys(merged[key] + value))
            else:
                merged[key] = value
        return merged

    def _weak_title(self, title: str | None) -> bool:
        if not title:
            return True
        lowered = title.strip().lower()
        return lowered in {"page 1", "page", "slide 1", "slide", "untitled document", "syllabus", "receipt"} or self._title_quality(title) <= 0

    def _title_quality(self, title: str | None) -> int:
        if not title:
            return -100
        cleaned = re.sub(r"\s+", " ", title).strip()
        lowered = cleaned.lower()
        if lowered in {"page 1", "page", "slide 1", "slide", "untitled document", "syllabus", "receipt"}:
            return -60
        score = 10
        if re.search(r"\b[A-Z]{2,5}[- ]?\d{3,4}[A-Z]?\b", cleaned):
            score += 25
        if any(keyword in lowered for keyword in ["installation guide", "setup guide", "technical guide", "implementation schedule", "project tracker", "development roadmap"]):
            score += 24
        if any(keyword in lowered for keyword in ["course", "guide", "manual", "schedule", "tracker", "roadmap", "presentation", "resume", "receipt", "invoice"]):
            score += 12
        if "profile" in lowered:
            score += 4
        if self._looks_like_person_name_title(cleaned):
            score -= 26
        if ":" in cleaned:
            score -= 8
        if re.match(r"^(course description|overview|summary|introduction|objectives?)\s*:", lowered):
            score -= 22
        if re.match(r"^(this|these|students|you will|in this course|the purpose of)\b", lowered):
            score -= 28
        if len(cleaned.split()) > 12:
            score -= 18
        if cleaned.endswith("."):
            score -= 10
        if self._looks_raw(cleaned):
            score -= 15
        return score

    def _is_generic_category(self, category: str | None) -> bool:
        return not category or category in {"other", "document", "generic_document", "notice"}

    def _is_manufacturing_profile(self, profile: str | None) -> bool:
        return profile in {
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

    def _specific_profile_regression(self, base: CategoryInterpretation, refined: CategoryInterpretation) -> bool:
        if refined.profile != "instructional_memo":
            return False
        if base.profile not in {"presentation_guide", "speaking_notes", "meeting_notice"}:
            return False
        return base.confidence >= 0.74

    def _profile_record_token_regression(self, base: CategoryInterpretation, refined: CategoryInterpretation) -> bool:
        if refined.profile != "profile_record":
            return False
        if base.profile not in {"installation_guide", "implementation_schedule", "presentation_guide", "speaking_notes", "syllabus", "course_guide"}:
            return False
        return base.confidence >= 0.78

    def _looks_like_person_name_title(self, title: str | None) -> bool:
        if not title:
            return False
        cleaned = re.sub(r"\s+", " ", title).strip()
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}", cleaned):
            return False
        lowered = cleaned.lower()
        return not any(keyword in lowered for keyword in ["guide", "manual", "schedule", "tracker", "roadmap", "invoice", "statement", "profile", "syllabus"])

    def _better_summary(self, new: str, current: str | None) -> bool:
        if not current:
            return True
        if len(new) < 18:
            return False
        generic_prefixes = (
            "course guide with title",
            "presentation guide with audience",
            "resume-style document highlighting",
            "profile-like text containing identity or affiliation facts",
            "meeting notice with time, location, purpose",
            "receipt from ",
        )
        if current.lower().startswith(generic_prefixes):
            return True
        return len(new) <= 260 and (
            len(current) > 280
            or self._looks_raw(current)
            or (new != current and len(new.split()) <= len(current.split()) + 4)
        )

    def _looks_raw(self, text: str) -> bool:
        return ";" in text or text.count(":") >= 3 or len(text.split()) > 35


class BaseInterpretationProvider:
    provider_name = "base"

    def interpret(self, document: Document, text: str, heuristic: CategoryInterpretation) -> CategoryInterpretation:
        raise NotImplementedError


class NullInterpretationProvider(BaseInterpretationProvider):
    provider_name = "null_interpretation"

    def interpret(self, document: Document, text: str, heuristic: CategoryInterpretation) -> CategoryInterpretation:
        raise RuntimeError("No AI interpretation provider is configured.")


class OpenAITextInterpretationProvider(BaseInterpretationProvider):
    provider_name = "ai_interpretation_openai"

    def __init__(self) -> None:
        self.settings = get_settings()

    def interpret(self, document: Document, text: str, heuristic: CategoryInterpretation) -> CategoryInterpretation:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI package is not installed.") from exc

        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        client = OpenAI(api_key=self.settings.openai_api_key)
        raw = self._call_model(
            client,
            model=self.settings.ai_interpretation_model,
            payload=self._payload(document, text, heuristic),
        )
        return self._normalize(raw, heuristic)

    def _call_model(self, client: Any, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = self._system_prompt()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def _payload(self, document: Document, text: str, heuristic: CategoryInterpretation) -> dict[str, Any]:
        compacted = self._compact_interpretation_text(
            document,
            text,
            heuristic,
            max_chars=self._interpretation_text_budget(),
        )
        return {
            "source_file_type": document.source_file_type,
            "mime_type": document.mime_type,
            "extraction_method": document.extraction_method,
            "document_type": document.document_type.value if document.document_type else None,
            "title": document.title,
            "merchant_name": document.merchant_name,
            "extracted_date": document.extracted_date.isoformat() if document.extracted_date else None,
            "extracted_amount": str(document.extracted_amount) if document.extracted_amount is not None else None,
            "subtotal": str(document.subtotal) if document.subtotal is not None else None,
            "tax": str(document.tax) if document.tax is not None else None,
            "category": document.category,
            "summary": document.summary,
            "interpretation_input": {
                "strategy": compacted["strategy"],
                "compacted": compacted["compacted"],
                "original_chars": compacted["original_chars"],
                "selected_chars": compacted["selected_chars"],
                "selected_line_count": compacted["selected_line_count"],
            },
            "heuristic_interpretation": {
                "category": heuristic.category,
                "profile": heuristic.profile,
                "subtype": heuristic.subtype,
                "title_hint": heuristic.title_hint,
                "summary_hint": heuristic.summary_hint,
                "key_fields": heuristic.key_fields,
                "confidence": heuristic.confidence,
                "reasons": heuristic.reasons,
            },
            "text": compacted["text"],
        }

    def _interpretation_text_budget(self) -> int:
        return self.settings.ai_interpretation_max_chars

    def _compact_interpretation_text(
        self,
        document: Document,
        text: str,
        heuristic: CategoryInterpretation,
        *,
        max_chars: int,
    ) -> dict[str, Any]:
        normalized = re.sub(r"\r\n?", "\n", text or "").strip()
        original_chars = len(normalized)
        if original_chars <= max_chars:
            lines = [line for line in normalized.splitlines() if line.strip()]
            return {
                "text": normalized,
                "strategy": "full_text_within_budget",
                "compacted": False,
                "original_chars": original_chars,
                "selected_chars": original_chars,
                "selected_line_count": len(lines),
            }

        lines = [re.sub(r"\s+", " ", line).strip() for line in normalized.splitlines()]
        lines = [line for line in lines if line]
        scored: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        for index, line in enumerate(lines):
            cleaned = self._clean_compaction_line(line)
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            score = self._compaction_line_score(cleaned, index, len(lines), document, heuristic)
            if score > 0:
                scored.append((score, index, cleaned))

        selected: list[tuple[int, str]] = []
        for index, line in self._fixed_context_lines(lines):
            cleaned = self._clean_compaction_line(line)
            if cleaned:
                selected.append((index, cleaned))
        for _, index, line in sorted(scored, key=lambda item: (-item[0], item[1])):
            selected.append((index, line))

        result_lines: list[str] = []
        result_keys: set[str] = set()
        total = 0
        for index, line in sorted(selected, key=lambda item: item[0]):
            key = line.casefold()
            if key in result_keys:
                continue
            addition = len(line) + 1
            if total + addition > max_chars:
                continue
            result_lines.append(line)
            result_keys.add(key)
            total += addition
            if total >= max_chars:
                break

        compacted_text = "\n".join(result_lines).strip()
        if not compacted_text:
            compacted_text = normalized[:max_chars].rsplit("\n", 1)[0].strip() or normalized[:max_chars]
            result_lines = [line for line in compacted_text.splitlines() if line.strip()]
        return {
            "text": compacted_text,
            "strategy": "category_aware_line_selection",
            "compacted": True,
            "original_chars": original_chars,
            "selected_chars": len(compacted_text),
            "selected_line_count": len(result_lines),
        }

    def _fixed_context_lines(self, lines: list[str]) -> list[tuple[int, str]]:
        if not lines:
            return []
        anchors: list[int] = []
        anchors.extend(range(min(24, len(lines))))
        midpoint = len(lines) // 2
        anchors.extend(range(max(0, midpoint - 8), min(len(lines), midpoint + 8)))
        anchors.extend(range(max(0, len(lines) - 18), len(lines)))
        return [(index, lines[index]) for index in sorted(set(anchors))]

    def _clean_compaction_line(self, line: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", line).strip(" \t-–—|")
        if not cleaned or len(cleaned) < 3:
            return None
        if re.fullmatch(r"[\W_]+", cleaned):
            return None
        return cleaned[:260]

    def _compaction_line_score(
        self,
        line: str,
        index: int,
        total_lines: int,
        document: Document,
        heuristic: CategoryInterpretation,
    ) -> int:
        lowered = line.lower()
        score = 0
        if index < 40:
            score += 18
        if index >= max(0, total_lines - 25):
            score += 6
        if len(line) <= 90 and re.match(r"^[A-Z0-9][A-Za-z0-9&,:;()/'’ -]{2,}$", line):
            score += 18
        if re.match(r"^(chapter|section|lecture|slide|module|unit|problem|homework|assignment|quiz|exam|overview|summary|objectives?|agenda|course|syllabus|sheet|task|feature|status)\b", lowered):
            score += 28
        if ":" in line and len(line) <= 180:
            score += 20
        for term in self._category_terms(document, heuristic):
            if term in lowered:
                score += 16
        if re.search(r"\b[A-Z]{2,6}[- ]?\d{2,4}[A-Z]?\b", line):
            score += 18
        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\b20\d{2}\b", line):
            score += 10
        if re.search(r"\b(definition|theorem|algorithm|example|steps?|deliverable|due|deadline|required|materials?|instructor|office hours|installation|setup|configuration|dependencies|testing|coverage|pipeline|claimed)\b", lowered):
            score += 14
        if len(line) > 220:
            score -= 10
        if re.fullmatch(r"(page|slide)\s+\d+", lowered):
            score -= 18
        return score

    def _category_terms(self, document: Document, heuristic: CategoryInterpretation) -> set[str]:
        profile = (heuristic.profile or "").lower()
        category = (heuristic.category or document.category or "").lower()
        base = {
            "title",
            "overview",
            "summary",
            "objective",
            "deadline",
            "due",
            "required",
        }
        if profile in {"syllabus", "course_guide"} or "course" in category:
            base.update({"course", "syllabus", "instructor", "semester", "grading", "materials", "policy", "exam", "homework"})
        if profile == "installation_guide" or any(term in category for term in ["installation", "setup", "technical"]):
            base.update({"installation", "setup", "configuration", "environment", "dependencies", "prerequisites", "commands", "docker", "api", "database"})
        if profile == "implementation_schedule" or any(term in category for term in ["implementation", "tracker", "roadmap", "planning"]):
            base.update({"implementation", "schedule", "task", "feature", "status", "testing", "coverage", "pipeline", "claimed", "owner", "milestone"})
        document_type = getattr(document.document_type, "value", str(document.document_type or ""))
        if profile in {"presentation_guide", "speaking_notes"} or document_type == "presentation":
            base.update({"presentation", "slide", "speaker", "speaking notes", "talk track", "script", "rehearse", "audience"})
        if any(term in category for term in ["education", "lecture"]) or document.source_file_type == "pdf":
            base.update({"lecture", "notes", "problem set", "homework", "assignment", "algorithm", "example", "definition", "theorem"})
        if profile in {"invoice", "utility_bill"}:
            base.update({"invoice", "bill", "amount due", "due date", "account", "payment"})
        if profile in {"receipt", "repair_service_receipt"}:
            base.update({"receipt", "total", "subtotal", "tax", "merchant", "parts", "labor"})
        return base

    def _system_prompt(self) -> str:
        return (
            "You interpret extracted document text after OCR/parsing. "
            "Do not perform OCR. Use only the extracted text and metadata. "
            "All user-facing summary_hint, warnings, workflow_hints, and reasons must be written in Korean only. "
            "Return only JSON with keys: category, profile, subtype, title_hint, summary_hint, "
            "key_fields, warnings, workflow_hints, confidence, reasons. "
            "Be category-aware and concise. "
            "Use workflow_hints.important_points to list the most important grounded details or takeaways when possible. "
            "Profiles may include: purchase_order, quotation, transaction_statement, delivery_note, invoice, packing_list, inspection_report, contract, general_document, receipt, repair_service_receipt, utility_bill, "
            "syllabus, course_guide, installation_guide, implementation_schedule, meeting_notice, presentation_guide, speaking_notes, "
            "resume_profile, profile_record, instructional_memo, generic_document. "
            "Do not choose profile_record from a single 'profile' token, API endpoint, or person-name line; require multiple labeled identity fields. "
            "For installation/setup manuals, prefer installation_guide. For task/status/testing spreadsheets, prefer implementation_schedule. "
            "Use hard extracted facts conservatively. Prefer title refinement, category/profile refinement, "
            "summary refinement, key field surfacing, and workflow hints."
        )

    def _normalize(self, raw: dict[str, Any], heuristic: CategoryInterpretation) -> CategoryInterpretation:
        return CategoryInterpretation(
            category=self._clean_text(raw.get("category")) or heuristic.category,
            profile=self._normalize_profile(raw.get("profile")) or heuristic.profile,
            subtype=self._clean_text(raw.get("subtype")) or heuristic.subtype,
            title_hint=self._clean_text(raw.get("title_hint")),
            summary_hint=self._clean_text(raw.get("summary_hint")),
            key_fields=self._clean_key_fields(raw.get("key_fields")),
            warnings=self._clean_text_list(raw.get("warnings")),
            workflow_hints=self._clean_workflow_hints(raw.get("workflow_hints")),
            reasons=self._clean_text_list(raw.get("reasons")) or ["AI interpretation improved category-aware understanding."],
            confidence=self._clean_confidence(raw.get("confidence"), heuristic.confidence),
            provider=self.provider_name,
            provider_chain=["heuristic_interpretation", self.provider_name, "ai_summary_refinement"],
            refinement_status=self.provider_name,
            diagnostics=[f"{self.provider_name} completed."],
            ai_assisted=True,
        )

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text[:400] if text else None

    def _normalize_profile(self, value: Any) -> str | None:
        cleaned = self._clean_text(value)
        if not cleaned:
            return None
        normalized = re.sub(r"[\s/>\-]+", "_", cleaned.strip().lower())
        aliases = {
            "setup_guide": "installation_guide",
            "technical_guide": "installation_guide",
            "technical_documentation": "installation_guide",
            "project_setup": "installation_guide",
            "project_tracker": "implementation_schedule",
            "engineering_planning": "implementation_schedule",
            "development_roadmap": "implementation_schedule",
        }
        return aliases.get(normalized, normalized)

    def _clean_text_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            text = self._clean_text(item)
            if text:
                cleaned.append(text)
        return cleaned[:12]

    def _clean_key_fields(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, list):
                cleaned[str(key)] = self._clean_text_list(item)
            elif isinstance(item, dict):
                nested = {str(k): self._clean_text(v) or v for k, v in item.items()}
                cleaned[str(key)] = nested
            else:
                cleaned[str(key)] = self._clean_text(item) or item
        return cleaned

    def _clean_workflow_hints(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        if "action_items" in value:
            result["action_items"] = self._clean_text_list(value.get("action_items"))
        if "warnings" in value:
            result["warnings"] = self._clean_text_list(value.get("warnings"))
        if "important_points" in value:
            result["important_points"] = self._clean_text_list(value.get("important_points"))
        if "review_focus" in value:
            result["review_focus"] = self._clean_text_list(value.get("review_focus"))
        if "urgency_level" in value:
            urgency = self._clean_text(value.get("urgency_level"))
            if urgency in {"low", "medium", "high"}:
                result["urgency_level"] = urgency
        if "follow_up_required" in value:
            result["follow_up_required"] = bool(value.get("follow_up_required"))
        return result

    def _clean_confidence(self, value: Any, fallback: float) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(0.99, confidence))


class LlamaCppGemmaInterpretationProvider(OpenAITextInterpretationProvider):
    provider_name = "ai_interpretation_gemma_gguf"

    def interpret(self, document: Document, text: str, heuristic: CategoryInterpretation) -> CategoryInterpretation:
        payload = self._payload(document, text, heuristic)
        raw = self._call_llama_cpp(payload)
        result = self._normalize(raw, heuristic)
        result.provider = self.provider_name
        result.provider_chain = ["heuristic_interpretation", self.provider_name, "ai_summary_refinement"]
        result.refinement_status = self.provider_name
        result.diagnostics.append(f"{self.provider_name} completed with llama.cpp GGUF backend.")
        input_meta = payload.get("interpretation_input") or {}
        if input_meta.get("compacted"):
            result.diagnostics.append(
                "Long document input compacted for GGUF context window: "
                f"{input_meta.get('original_chars')} -> {input_meta.get('selected_chars')} chars "
                f"using {input_meta.get('strategy')}."
            )
        return result

    def _interpretation_text_budget(self) -> int:
        token_budget = max(
            800,
            self.settings.llama_cpp_context_window - self.settings.llama_cpp_max_tokens - 900,
        )
        char_budget = int(token_budget * 2.6)
        return max(2500, min(self.settings.ai_interpretation_max_chars, char_budget, 6500))

    def _call_llama_cpp(self, payload: dict[str, Any]) -> dict[str, Any]:
        llm = self._load_model()
        with _gguf_inference_semaphore:
            output = llm(
                self._prompt(payload),
                max_tokens=self.settings.llama_cpp_max_tokens,
                temperature=self.settings.llama_cpp_temperature,
                stop=["</s>", "<end_of_turn>"],
                echo=False,
            )
        return self._extract_json(self._output_text(output))

    def _load_model(self) -> Any:
        return get_llama_cpp_gguf_model(self.settings)

    def _prompt(self, payload: dict[str, Any]) -> str:
        instruction = (
            "You are an expert document interpretation assistant. "
            "Use extracted text and metadata only. Do not perform OCR. "
            "Write all user-facing summary_hint, warnings, workflow_hints, and reasons in Korean only. "
            "Return only valid JSON with keys: category, profile, subtype, title_hint, summary_hint, "
            "key_fields, warnings, workflow_hints, confidence, reasons. "
            "Prefer concise, category-aware summaries and field emphasis. "
            "Use workflow_hints.important_points for the most important grounded facts or takeaways. "
            "Good profile options: purchase_order, quotation, transaction_statement, delivery_note, invoice, packing_list, inspection_report, contract, general_document, receipt, repair_service_receipt, utility_bill, syllabus, course_guide, "
            "installation_guide, implementation_schedule, meeting_notice, presentation_guide, speaking_notes, "
            "resume_profile, profile_record, instructional_memo, generic_document. "
            "Do not use profile_record for an API endpoint, a single profile token, or a person-name line unless the document has multiple labeled identity fields."
        )
        return (
            "<start_of_turn>user\n"
            f"{instruction}\n\nDocument payload:\n{json.dumps(payload, ensure_ascii=True)}\n\nJSON only:"
            "\n<end_of_turn>\n<start_of_turn>model\n"
        )

    def _output_text(self, output: Any) -> str:
        if isinstance(output, dict):
            choices = output.get("choices")
            if isinstance(choices, list) and choices:
                text = choices[0].get("text")
                if text:
                    return str(text)
        return str(output or "")

    def _extract_json(self, output_text: str) -> dict[str, Any]:
        candidates = [output_text.strip()]
        balanced = self._balanced_json_object(output_text)
        if balanced:
            candidates.append(balanced)
        match = re.search(r"\{.*\}", output_text, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))

        errors: list[str] = []
        for candidate in dict.fromkeys(value for value in candidates if value):
            cleaned = self._strip_json_fence(candidate)
            for variant in (cleaned, self._repair_json_object(cleaned)):
                try:
                    return json.loads(variant)
                except json.JSONDecodeError as exc:
                    errors.append(str(exc))
        raise RuntimeError(f"llama.cpp output did not contain parseable JSON ({'; '.join(errors[-2:])}): {output_text[:400]}")

    def _strip_json_fence(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()

    def _balanced_json_object(self, value: str) -> str | None:
        start = value.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(value[start:], start=start):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start : index + 1]
        return None

    def _repair_json_object(self, value: str) -> str:
        repaired = re.sub(r",(\s*[}\]])", r"\1", value)
        repaired = re.sub(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        return repaired


class GemmaInterpretationProvider(OpenAITextInterpretationProvider):
    provider_name = "ai_interpretation_gemma"

    def __init__(self) -> None:
        super().__init__()
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._input_device: str | None = None
        self._loaded_model_ref: str | None = None
        self._active_provider_label: str = self.provider_name
        self._active_provider_chain: list[str] = ["heuristic_interpretation", self.provider_name, "ai_summary_refinement"]

    def interpret(self, document: Document, text: str, heuristic: CategoryInterpretation) -> CategoryInterpretation:
        raw = self._call_gemma(self._payload(document, text, heuristic))
        result = self._normalize(raw, heuristic)
        result.provider = self._active_provider_label
        result.provider_chain = list(self._active_provider_chain)
        result.refinement_status = self._active_provider_label
        return result

    def _call_gemma(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._load_model_with_fallback()
        prompt = self._gemma_prompt(payload)
        inputs = self._tokenizer(prompt, return_tensors="pt")
        if self._input_device:
            inputs = {key: value.to(self._input_device) for key, value in inputs.items()}
        outputs = self._model.generate(
            **inputs,
            max_new_tokens=900,
            do_sample=False,
        )
        if outputs is None:
            raise RuntimeError("Gemma returned no output.")
        input_length = inputs["input_ids"].shape[-1]
        generated_tokens = outputs[0][input_length:]
        text = self._tokenizer.decode(generated_tokens, skip_special_tokens=True)
        return self._extract_json(text)

    def _load_model_with_fallback(self) -> None:
        attempts = self._model_attempts()
        capacity_failure_label: str | None = None
        last_error: Exception | None = None
        for attempt in attempts:
            model_ref = attempt["model_ref"]
            provider_label = attempt["provider_label"]
            try:
                self._load_model(model_ref=model_ref, local_only=attempt["local_only"])
                self._active_provider_label = provider_label
                if capacity_failure_label and provider_label == "ai_interpretation_gemma_fallback_small":
                    self._active_provider_chain = [
                        "heuristic_interpretation",
                        capacity_failure_label,
                        provider_label,
                        "ai_summary_refinement",
                    ]
                else:
                    self._active_provider_chain = [
                        "heuristic_interpretation",
                        provider_label,
                        "ai_summary_refinement",
                    ]
                return
            except Exception as exc:
                last_error = exc
                has_remaining_fallback = any(next_attempt["fallback_candidate"] for next_attempt in attempts[attempts.index(attempt) + 1 :])
                if (not attempt["fallback_candidate"]) and has_remaining_fallback and self._is_capacity_error(exc):
                    capacity_failure_label = "ai_interpretation_gemma4_failed_capacity"
                    self._release_model()
                    continue
                raise
        if last_error is not None:
            if capacity_failure_label:
                raise RuntimeError(f"{capacity_failure_label}: {last_error}") from last_error
            raise last_error
        raise RuntimeError("Gemma interpretation failed without an explicit error.")

    def _load_model(self, model_ref: str, local_only: bool) -> None:
        if self._loaded_model_ref == model_ref and self._model is not None and self._tokenizer is not None:
            return
        model_dir = Path(model_ref) if local_only else None
        if model_dir is not None and not model_dir.exists():
            raise RuntimeError(f"Configured Gemma model directory does not exist: {model_dir}")
        self._validate_model_access(model_ref=model_ref, local_only=local_only)
        if importlib.util.find_spec("transformers") is None:
            raise RuntimeError("Gemma interpretation runtime is not installed. Install backend/requirements-ai.txt.")
        try:
            import torch
            from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise RuntimeError(
                "Gemma runtime is not installed. Install backend/requirements-ai.txt and configure Gemma weights."
            ) from exc

        self._torch = torch
        self._release_model()
        pretrained_kwargs = self._hf_pretrained_kwargs(local_only=local_only)
        torch_dtype = self._torch_dtype(torch)
        device_map = self._device_map(torch)
        self._input_device = self._input_device_name(torch, device_map)

        if local_only:
            logger.warning("Loading Gemma interpretation model from local directory: %s", model_ref)
        else:
            logger.warning("Loading Gemma interpretation model from remote repo: %s", model_ref)
        AutoConfig.from_pretrained(model_ref, **pretrained_kwargs)
        self._tokenizer = AutoTokenizer.from_pretrained(model_ref, **pretrained_kwargs)
        model_load_kwargs: dict[str, Any] = dict(pretrained_kwargs)
        model_load_kwargs["torch_dtype"] = torch_dtype
        if device_map is not None:
            model_load_kwargs["device_map"] = device_map
        self._model = AutoModelForCausalLM.from_pretrained(model_ref, **model_load_kwargs)
        if device_map is None and self._input_device:
            self._model = self._model.to(self._input_device)
        self._model.eval()
        self._loaded_model_ref = model_ref

    def _hf_pretrained_kwargs(self, local_only: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"local_files_only": local_only}
        if not local_only and self.settings.huggingface_token:
            kwargs["token"] = self.settings.huggingface_token
        return kwargs

    def _torch_dtype(self, torch: Any) -> Any:
        if torch.cuda.is_available():
            return torch.float16
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.float16
        return torch.float32

    def _device_map(self, torch: Any) -> Any:
        if self.settings.gemma_device == "cpu":
            return None
        if self.settings.gemma_device == "auto":
            if torch.cuda.is_available():
                return "auto"
            return None
        return {"": self.settings.gemma_device}

    def _input_device_name(self, torch: Any, device_map: Any) -> str | None:
        if self.settings.gemma_device not in {"auto", "cpu"}:
            return self.settings.gemma_device
        if device_map == "auto":
            return None
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() and self.settings.gemma_device == "auto":
            return "mps"
        return None

    def _validate_model_access(self, model_ref: str, local_only: bool) -> None:
        if local_only:
            config_file = Path(model_ref) / "config.json"
            if not config_file.exists():
                raise RuntimeError(f"Gemma model directory is missing config.json: {config_file}")
            return
        try:
            from huggingface_hub import HfApi
        except Exception as exc:
            raise RuntimeError("huggingface_hub is required for Gemma 4 access checks.") from exc

        token = self.settings.huggingface_token
        if not token:
            raise RuntimeError(
                "Gemma interpretation requires either GEMMA_MODEL_DIR with local weights "
                "or HUGGINGFACE_TOKEN for gated model access."
            )

        api = HfApi(token=token)
        try:
            api.model_info(model_ref)
        except Exception as exc:
            raise RuntimeError(
                f"Gemma model access check failed for {model_ref}. "
                "Set HUGGINGFACE_TOKEN with access to the gated model or configure GEMMA_MODEL_DIR."
            ) from exc

    def _model_attempts(self) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        local_model_dir = self.settings.gemma_model_dir
        local_ref = str(local_model_dir) if local_model_dir else None
        primary_ref = local_ref or (self.settings.gemma_model_name or self.settings.ai_interpretation_model)
        primary_local = bool(local_ref)
        fallback_ref = self.settings.ai_interpretation_fallback_model
        fallback_enabled = self.settings.ai_interpretation_enable_model_fallback and bool(fallback_ref)
        prefer_small = self.settings.ai_interpretation_local_prefer_small_model
        force_small = self.settings.ai_interpretation_force_small_model

        if force_small and fallback_enabled:
            forced_ref = local_ref or fallback_ref
            return [
                {
                    "model_ref": forced_ref,
                    "provider_label": "ai_interpretation_gemma_fallback_small",
                    "local_only": bool(local_ref),
                    "fallback_candidate": True,
                }
            ]

        primary_attempt = {
            "model_ref": primary_ref,
            "provider_label": self.provider_name,
            "local_only": primary_local,
            "fallback_candidate": False,
        }
        fallback_attempt = None
        if fallback_enabled and fallback_ref and fallback_ref != primary_ref and not local_ref:
            fallback_attempt = {
                "model_ref": fallback_ref,
                "provider_label": "ai_interpretation_gemma_fallback_small",
                "local_only": False,
                "fallback_candidate": True,
            }

        if prefer_small and fallback_attempt is not None:
            attempts.append(fallback_attempt)
            attempts.append(primary_attempt)
            return attempts

        attempts.append(primary_attempt)
        if fallback_attempt is not None:
            attempts.append(fallback_attempt)
        return attempts

    def _is_capacity_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        capacity_signals = [
            "out of memory",
            "mps backend out of memory",
            "cuda out of memory",
            "allocation failure",
            "not enough memory",
            "insufficient memory",
            "capacity",
        ]
        return any(signal in message for signal in capacity_signals)

    def _release_model(self) -> None:
        if self._model is not None:
            try:
                del self._model
            except Exception:
                pass
        if self._tokenizer is not None:
            try:
                del self._tokenizer
            except Exception:
                pass
        self._model = None
        self._tokenizer = None
        self._loaded_model_ref = None
        self._input_device = None
        if self._torch is not None:
            try:
                if self._torch.cuda.is_available():
                    self._torch.cuda.empty_cache()
            except Exception:
                pass
            try:
                if getattr(self._torch, "mps", None) and hasattr(self._torch.mps, "empty_cache"):
                    self._torch.mps.empty_cache()
            except Exception:
                pass

    def _gemma_prompt(self, payload: dict[str, Any]) -> str:
        instruction = (
            "You are an expert document interpretation assistant. "
            "Use extracted text and metadata only. Do not perform OCR. "
            "Write all user-facing summary_hint, warnings, workflow_hints, and reasons in Korean only. "
            "Return only valid JSON with keys: category, profile, subtype, title_hint, summary_hint, "
            "key_fields, warnings, workflow_hints, confidence, reasons. "
            "Prefer concise, category-aware summaries and field emphasis. "
            "Use workflow_hints.important_points for the most important grounded facts or takeaways. "
            "Good profile options: purchase_order, quotation, transaction_statement, delivery_note, invoice, packing_list, inspection_report, contract, general_document, receipt, repair_service_receipt, utility_bill, syllabus, course_guide, "
            "meeting_notice, presentation_guide, speaking_notes, resume_profile, profile_record, instructional_memo, generic_document."
        )
        return f"{instruction}\n\nDocument payload:\n{json.dumps(payload, ensure_ascii=True)}\n\nJSON:"

    def _extract_json(self, output_text: str) -> dict[str, Any]:
        candidates = [output_text.strip()]
        balanced = self._balanced_json_object(output_text)
        if balanced:
            candidates.append(balanced)
        match = re.search(r"\{.*\}", output_text, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))

        errors: list[str] = []
        for candidate in dict.fromkeys(value for value in candidates if value):
            cleaned = self._strip_json_fence(candidate)
            for variant in (cleaned, self._repair_json_object(cleaned)):
                try:
                    return json.loads(variant)
                except json.JSONDecodeError as exc:
                    errors.append(str(exc))
        raise RuntimeError(f"Gemma output did not contain parseable JSON ({'; '.join(errors[-2:])}): {output_text[:400]}")

    def _strip_json_fence(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()

    def _balanced_json_object(self, value: str) -> str | None:
        start = value.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(value[start:], start=start):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start : index + 1]
        return None

    def _repair_json_object(self, value: str) -> str:
        repaired = re.sub(r",(\s*[}\]])", r"\1", value)
        repaired = re.sub(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        return repaired
