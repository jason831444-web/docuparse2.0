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


def test_dark_photo_background_does_not_count_as_right_column_crop(tmp_path):
    analyzer = DocumentQualityAnalyzer()
    image = Image.new("RGB", (1600, 1200), (45, 42, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle((220, 120, 1380, 1080), fill=(245, 245, 245))
    for index in range(5):
        y = 360 + index * 80
        draw.rectangle((300, y, 1170, y + 3), fill=(95, 95, 95))
        draw.text((330, y + 16), f"S45C PIN 8x60 {index + 1}", fill=(60, 60, 60))
    path = tmp_path / "photo-background.png"
    image.save(path)

    result = analyzer.analyze_page_image(path)

    assert result.right_edge_content_risk < 0.018
    assert result.possible_right_column_crop is False
    assert "document_right_column_crop_risk" not in result.review_reasons
    assert result.hidden_or_cropped_columns == []


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


def test_standard_vl_input_preserves_full_page_without_inner_crop(tmp_path):
    image = Image.new("RGB", (1200, 1600), (50, 48, 45))
    draw = ImageDraw.Draw(image)
    draw.polygon([(180, 120), (1040, 180), (1080, 1450), (120, 1390)], fill=(242, 242, 242))
    draw.text((420, 240), "자재 이동 요청서", fill=(20, 20, 20))
    draw.text((260, 620), "S45C PIN 8x60 200 EA", fill=(30, 30, 30))
    draw.text((260, 1320), "하단 비고: 수량 확인 후 처리", fill=(30, 30, 30))
    source = tmp_path / "internal-transfer-photo.webp"
    image.save(source)
    before_bytes = source.read_bytes()

    variant = ImagePreprocessor().prepare_standard_vl_input(source, tmp_path / "variants")

    assert source.read_bytes() == before_bytes
    assert variant["processed_path"]
    assert Path(variant["processed_path"]).exists()
    assert "no_inner_crop_applied_preserve_full_document" in variant["warnings"]
    assert not any("crop" in op for op in variant["operations"])


def test_standard_vl_input_uses_full_page_clarity_enhancement(tmp_path):
    image = Image.new("RGB", (900, 1300), (70, 66, 62))
    draw = ImageDraw.Draw(image)
    draw.polygon([(130, 80), (790, 140), (820, 1180), (95, 1130)], fill=(214, 211, 204))
    for index, text in enumerate(
        [
            "자재 이동 요청서",
            "S45C PIN 8x60 200 EA",
            "AL6061 환봉 10파이 50 EA",
            "절삭유 4L 6 CAN",
            "하단 비고: 내부 이동 문서",
        ]
    ):
        draw.text((210, 210 + index * 150), text, fill=(88, 88, 88))
    source = tmp_path / "blurred-transfer-photo.jpg"
    image.filter(ImageFilter.GaussianBlur(radius=1.5)).save(source)

    variant = ImagePreprocessor().prepare_standard_vl_input(source, tmp_path / "variants")

    assert variant["variant_name"] == "full_page_document_clarity"
    operations = set(variant["operations"])
    assert "full_page_shadow_normalization" in operations
    assert "full_page_local_contrast" in operations
    assert "full_page_denoise" in operations
    assert "full_page_deblur_sharpen" in operations
    assert not any("crop" in operation for operation in operations)
