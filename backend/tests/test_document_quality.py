from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.services.document_quality import DocumentQualityAnalyzer
from app.services.image_preprocessor import ImagePreprocessor


def _save_sharp_document(path: Path, *, right_edge: bool = False, low_contrast: bool = False) -> Path:
    background = 245 if not low_contrast else 180
    ink = 20 if not low_contrast else 155
    image = Image.new("RGB", (1400, 1000), (background, background, background))
    draw = ImageDraw.Draw(image)
    for index in range(8):
        y = 120 + index * 70
        draw.rectangle((120, y, 1120, y + 4), fill=(ink, ink, ink))
        draw.rectangle((140, y + 18, 560, y + 42), fill=(ink, ink, ink))
    if right_edge:
        for index in range(8):
            y = 140 + index * 75
            draw.rectangle((1360, y, 1398, y + 35), fill=(ink, ink, ink))
    image.save(path)
    return path


def test_blur_score_detects_blurry_page(tmp_path):
    analyzer = DocumentQualityAnalyzer()
    sharp_path = _save_sharp_document(tmp_path / "sharp.png")
    blurry_path = tmp_path / "blurry.png"
    Image.open(sharp_path).filter(ImageFilter.GaussianBlur(radius=6)).save(blurry_path)

    sharp = analyzer.analyze_page_image(sharp_path)
    blurry = analyzer.analyze_page_image(blurry_path)

    assert sharp.blur_score > blurry.blur_score
    assert blurry.is_blurry is True
    assert "document_image_blurry" in blurry.review_reasons


def test_low_contrast_page_is_flagged(tmp_path):
    analyzer = DocumentQualityAnalyzer()
    low_contrast_path = _save_sharp_document(tmp_path / "low-contrast.png", low_contrast=True)

    result = analyzer.analyze_page_image(low_contrast_path)

    assert result.contrast_score < 0.105
    assert "document_low_contrast" in result.review_reasons
    assert 0 <= result.quality_score <= 0.99


def test_right_edge_content_risk_flags_possible_crop(tmp_path):
    analyzer = DocumentQualityAnalyzer()
    safe_path = _save_sharp_document(tmp_path / "safe.png")
    risky_path = _save_sharp_document(tmp_path / "risky.png", right_edge=True)

    safe = analyzer.analyze_page_image(safe_path)
    risky = analyzer.analyze_page_image(risky_path)

    assert risky.right_edge_content_risk > safe.right_edge_content_risk
    assert risky.possible_right_column_crop is True
    assert "document_right_column_crop_risk" in risky.review_reasons
    assert "tax_amount" in risky.hidden_or_cropped_columns
    assert "line_total" in risky.hidden_or_cropped_columns
    assert "item_name" in risky.visible_columns
    assert "line_total" not in risky.visible_columns


def test_document_quality_summary_stays_serializable(tmp_path):
    analyzer = DocumentQualityAnalyzer()
    page_one = _save_sharp_document(tmp_path / "page-one.png")
    page_two = _save_sharp_document(tmp_path / "page-two.png", right_edge=True)

    summary = analyzer.analyze_document_quality([page_one, page_two]).to_dict()

    assert summary["page_count"] == 2
    assert summary["possible_right_column_crop"] is True
    assert "tax_amount" in summary["hidden_or_cropped_columns"]
    assert "line_total" not in summary["visible_columns"]
    assert isinstance(summary["pages"][0]["quality_score"], float)


def test_preprocessor_does_not_overwrite_original_and_preserves_crop_risk(tmp_path):
    source = _save_sharp_document(tmp_path / "source.png", low_contrast=True, right_edge=True)
    before_bytes = source.read_bytes()

    result = ImagePreprocessor().preprocess_image(source, tmp_path / "processed")

    assert source.read_bytes() == before_bytes
    assert "right_edge_content_risk_detected_no_crop_applied" in result.warnings
    assert result.quality_before is not None
    if result.processed_path:
        assert Path(result.processed_path).exists()
        assert Path(result.processed_path) != source
