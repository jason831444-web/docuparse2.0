from app.models.document import Document
from app.services.domain_dictionary import DictionaryEntry, DomainDictionarySuggestionService
from app.services.item_master_matcher import normalize_item_text


def test_domain_dictionary_suggests_review_only_key_and_party_value(monkeypatch):
    service = DomainDictionarySuggestionService()
    entries = [
        DictionaryEntry("field_label", "샘플번호", service._normalize_label("생플변호"), "manufacturing_label_dictionary"),
        DictionaryEntry("party", "(주)시흥대야점", service._normalize_party("(주)시흥대야점"), "confirmed_semantic_mapping"),
    ]
    monkeypatch.setattr(service, "_dictionary_entries", lambda db, exclude_document_id=None: entries)
    raw = {
        "key_values": [
            {"key": "생플변호", "value": "003", "source": "vl_raw_text_key_value"},
            {"key": "공급받는자 상호", "value": "(주)시홍대야점", "source": "vl_raw_text_key_value"},
        ]
    }

    result = service.suggestions_for_document(None, Document(original_filename="a.png", stored_file_path="/tmp/a.png", mime_type="image/png"), raw)  # type: ignore[arg-type]
    suggestions = result["suggestions"]

    assert suggestions[0]["target"] == "raw_key_value"
    assert suggestions[0]["field"] == "key"
    assert suggestions[0]["original_value"] == "생플변호"
    assert suggestions[0]["suggested_value"] == "샘플번호"
    assert suggestions[0]["auto_apply"] is False
    assert suggestions[1]["field"] == "value"
    assert suggestions[1]["original_value"] == "(주)시홍대야점"
    assert suggestions[1]["suggested_value"] == "(주)시흥대야점"
    assert result["summary"]["confirmed_sources_only"] is True


def test_domain_dictionary_suggests_item_values_without_mutating_raw(monkeypatch):
    service = DomainDictionarySuggestionService()
    entries = [
        DictionaryEntry("item", "PCB Connector 12P", normalize_item_text("PCB Connector 12P"), "item_master", evidence="CONN-12P"),
    ]
    monkeypatch.setattr(service, "_dictionary_entries", lambda db, exclude_document_id=None: entries)
    raw = {
        "key_values": [{"key": "품목명", "value": "PCB Conector 12P", "source": "vl_raw_text_key_value"}],
        "tables": [{"columns": ["품목명"], "rows": [{"품목명": "PCB Conector 12P"}]}],
    }

    result = service.suggestions_for_document(None, Document(original_filename="a.png", stored_file_path="/tmp/a.png", mime_type="image/png"), raw)  # type: ignore[arg-type]
    suggestions = result["suggestions"]

    assert raw["key_values"][0]["value"] == "PCB Conector 12P"
    assert any(item["target"] == "raw_key_value" and item["suggested_value"] == "PCB Connector 12P" for item in suggestions)
    assert any(item["target"] == "raw_table_cell" and item["suggested_value"] == "PCB Connector 12P" for item in suggestions)


def test_domain_dictionary_rejected_feedback_suppresses_same_suggestion(monkeypatch):
    service = DomainDictionarySuggestionService()
    entries = [
        DictionaryEntry("field_label", "샘플번호", service._normalize_label("생플변호"), "manufacturing_label_dictionary"),
    ]
    monkeypatch.setattr(service, "_dictionary_entries", lambda db, exclude_document_id=None: entries)
    monkeypatch.setattr(
        service,
        "_rejected_pairs",
        lambda db: {
            (
                service._normalize_value("raw_key_value"),
                service._normalize_value("생플변호"),
                service._normalize_value("샘플번호"),
            )
        },
    )
    raw = {"key_values": [{"key": "생플변호", "value": "003", "source": "vl_raw_text_key_value"}]}

    result = service.suggestions_for_document(None, Document(original_filename="a.png", stored_file_path="/tmp/a.png", mime_type="image/png"), raw)  # type: ignore[arg-type]

    assert result["suggestions"] == []
