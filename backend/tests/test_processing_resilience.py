import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from app.models.document import Document, ProcessingStatus
from app.services import document_interpretation_service as interpretation_module

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)


class FakeSession:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.commits = 0
        self.rollbacks = 0

    def add(self, document: Document) -> None:
        self.document = document

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, document: Document) -> None:
        return None

    def rollback(self) -> None:
        self.rollbacks += 1

    def get(self, model, document_id):
        return self.document


def test_gguf_model_loader_uses_process_cache_and_lock(monkeypatch, tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"fake")
    calls = []

    class FakeLlama:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __call__(self, *args, **kwargs):
            return {"choices": [{"text": "{}"}]}

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))
    interpretation_module._gguf_models.clear()
    settings = SimpleNamespace(
        llama_cpp_model_path=model_path,
        llama_cpp_context_window=2048,
        llama_cpp_threads=2,
        llama_cpp_gpu_layers=0,
    )

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(interpretation_module.get_llama_cpp_gguf_model(settings)))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert len({id(result) for result in results}) == 1
    assert interpretation_module.get_llama_cpp_gguf_model(settings) is results[0]


def test_processor_finishes_when_ai_interpretation_raises(tmp_path):
    from app.services.document_processor import DocumentProcessor

    path = tmp_path / "memo.txt"
    path.write_text(
        "업무 메모\n작성일: 2026-06-03\n처리 결과를 확인해야 하는 일반 문서입니다.\n",
        encoding="utf-8",
    )
    document = Document(
        original_filename="memo.txt",
        stored_file_path=str(path),
        mime_type="text/plain",
        processing_status=ProcessingStatus.uploaded,
    )
    db = FakeSession(document)
    processor = DocumentProcessor()

    class BrokenInterpreter:
        def interpret(self, document, text):
            raise RuntimeError("boom")

    processor.category_interpreter = BrokenInterpreter()
    result = processor.process(db, document)

    assert result.processing_status in {ProcessingStatus.ready, ProcessingStatus.needs_review, ProcessingStatus.failed}
    assert result.processing_status != ProcessingStatus.processing
    assert result.processing_status != ProcessingStatus.failed
    assert "AI interpretation failed; parser result used" in (result.ai_extraction_notes or "")


def test_multiple_txt_manufacturing_documents_finish_without_ai_interpretation(tmp_path):
    from app.services.document_processor import DocumentProcessor

    texts = [
        """
        발주서
        공급업체: 대한정밀부품
        고객사: 한빛제조
        발주번호: PO-2026-0603
        발행일: 2026-06-03
        납기일: 2026-06-10
        품목명: M8 육각 볼트
        품목코드: BOLT-M8-20
        규격: M8x20
        수량: 500
        단위: EA
        단가: 120
        공급가액: 60000
        세액: 6000
        합계금액: 66000
        """,
        """
        견적서
        공급업체: 한성산업
        고객사: 미래정밀
        견적번호: QT-2026-0621
        견적일: 2026-06-21
        유효기간: 2026-07-05
        품목명 | 품목코드 | 규격 | 수량 | 단위 | 단가 | 공급가액 | 세액 | 합계금액
        고정 플레이트 | PLT-FIX-02 | 120x60x5T |  | EA | 2800 | 280000 | 28000 | 308000
        공급가액: 280000
        세액: 28000
        합계금액: 308000
        """,
        """
        발주서
        공급업체: 신우금속
        고객사: 제일기계
        발주번호: PO-2026-0618
        발행일: 2026-06-18
        납기일: 2026-06-25
        품목명 | 품목코드 | 규격 | 수량 | 단위 | 단가 | 공급가액 | 세액 | 합계금액
        SUS-304 철판 |  |  | 10 | EA | 25000 | 250000 | 25000 | 275000
        공급가액: 250000
        세액: 25000
        합계금액: 275000
        """,
    ]
    statuses = []
    for index, text in enumerate(texts, start=1):
        path = tmp_path / f"doc_{index}.txt"
        path.write_text(text, encoding="utf-8")
        document = Document(
            original_filename=path.name,
            stored_file_path=str(path),
            mime_type="text/plain",
            processing_status=ProcessingStatus.uploaded,
        )
        processor = DocumentProcessor()

        class BrokenInterpreter:
            def interpret(self, document, text):
                raise AssertionError("deterministic TXT manufacturing documents should skip AI interpretation")

        processor.category_interpreter = BrokenInterpreter()
        result = processor.process(FakeSession(document), document)
        statuses.append(result.processing_status)
        assert result.processing_status != ProcessingStatus.processing
        assert "interpretation_skipped_rule_based_ready" in (result.provider_chain or "")
        assert "rule_based_structuring" in (result.provider_chain or "")
        assert "heuristic_interpretation" not in (result.provider_chain or "")
        assert "heuristic_fallback" not in (result.provider_chain or "")
        assert "ai_interpretation_" not in (result.provider_chain or "")
        assert result.refinement_provider == "rule_based_structuring"

    assert statuses[0] == ProcessingStatus.ready
    assert statuses[1] == ProcessingStatus.needs_review
    assert statuses[2] == ProcessingStatus.ready


def test_ai_interpretation_path_keeps_ai_provider_metadata(tmp_path):
    from app.services.category_interpretation import CategoryInterpretation
    from app.services.document_processor import DocumentProcessor

    path = tmp_path / "general.txt"
    path.write_text(
        "긴 일반 문서입니다.\n" * 20 + "프로젝트 진행 상황과 후속 조치에 대한 설명이 포함되어 있습니다.",
        encoding="utf-8",
    )
    document = Document(
        original_filename="general.txt",
        stored_file_path=str(path),
        mime_type="text/plain",
        processing_status=ProcessingStatus.uploaded,
    )
    processor = DocumentProcessor()

    class FakeAIInterpreter:
        def interpret(self, document, text):
            return CategoryInterpretation(
                category="general_document",
                profile="general_document",
                summary_hint="AI 보조 분석 결과입니다.",
                provider="ai_interpretation_gemma_gguf",
                provider_chain=["heuristic_interpretation", "ai_interpretation_gemma_gguf", "ai_summary_refinement"],
                refinement_status="ai_interpretation_gemma_gguf",
                ai_assisted=True,
            )

    processor.category_interpreter = FakeAIInterpreter()
    result = processor.process(FakeSession(document), document)

    assert result.processing_status in {ProcessingStatus.ready, ProcessingStatus.needs_review}
    assert "ai_interpretation_gemma_gguf" in (result.provider_chain or "")
    assert result.refinement_provider == "ai_interpretation_gemma_gguf"
