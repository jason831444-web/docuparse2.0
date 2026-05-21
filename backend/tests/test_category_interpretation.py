from app.models.document import Document, DocumentType
from app.services.category_interpretation import CategoryInterpretationService
from app.services.category_taxonomy import normalize_category_value
from app.services.parser import DocumentParser


def test_installation_guide_beats_person_name_profile_signal():
    text = """
    Sihoon
    Project Setup Guide
    Installation
    Prerequisites
    Docker
    Environment Variables
    Database
    Run this command to start the API server.
    """

    parsed = DocumentParser().parse(text, "project_setup.pdf")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="project_setup.pdf",
            stored_file_path="/tmp/project_setup.pdf",
            mime_type="application/pdf",
            document_type=parsed.document_type,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "installation_guide"
    assert interpretation.profile == "installation_guide"
    assert interpretation.category == "installation_guide"
    assert "setup" in (interpretation.title_hint or "").lower()


def test_spreadsheet_tracker_beats_profile_token():
    text = """
    Sheet: Implementation Schedule
    Feature | Task | Status | Claimed | Testing | Coverage | Pipeline
    Student profile API (/students/{id}) | Implement endpoint | In Progress | Alex | API tests | 80% | Passing
    Search filters | Verify category consistency | Open | Sam | Regression tests | 70% | Pending
    """

    parsed = DocumentParser().parse(text, "implementation_schedule.xlsx")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="implementation_schedule.xlsx",
            stored_file_path="/tmp/implementation_schedule.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            source_file_type="xlsx",
            document_type=DocumentType.document,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "implementation_schedule"
    assert interpretation.profile == "implementation_schedule"
    assert interpretation.category == "implementation_schedule"
    assert "schedule" in (interpretation.title_hint or "").lower()


def test_category_normalization_keeps_edit_and_filter_values_consistent():
    assert normalize_category_value("Engineering Planning") == "implementation_schedule"
    assert normalize_category_value("Project Tracker") == "implementation_schedule"
    assert normalize_category_value("parent>Setup Guide") == "installation_guide"
