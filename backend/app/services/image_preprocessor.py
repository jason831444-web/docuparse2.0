from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from app.services.document_quality import DocumentQualityAnalyzer


@dataclass
class ImagePreprocessResult:
    original_path: str
    processed_path: str | None
    operations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quality_before: dict[str, Any] | None = None
    quality_after: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImagePreprocessor:
    """Conservative preprocessing for OCR/VL inputs.

    The original file is never overwritten. Cropping is intentionally avoided in
    this first pass because right-edge amount columns are business-critical.
    """

    def __init__(self, analyzer: DocumentQualityAnalyzer | None = None) -> None:
        self.analyzer = analyzer or DocumentQualityAnalyzer()

    def preprocess_image(self, image_path: str | Path, output_dir: str | Path) -> ImagePreprocessResult:
        path = Path(image_path)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        before = self.analyzer.analyze_page_image(path)
        operations: list[str] = []
        warnings: list[str] = []

        with Image.open(path) as opened:
            image = opened.convert("RGB")
            processed = image
            if before.contrast_score < 0.105:
                processed = ImageOps.autocontrast(processed, cutoff=1)
                operations.append("autocontrast")
            if before.is_blurry and before.likely_scan_type == "fax_like":
                processed = processed.filter(ImageFilter.MedianFilter(size=3))
                operations.append("median_denoise")
            if before.is_skewed and before.skew_angle_estimate is not None and abs(before.skew_angle_estimate) <= 7:
                processed = processed.rotate(
                    -before.skew_angle_estimate,
                    expand=True,
                    fillcolor=(255, 255, 255),
                    resample=Image.Resampling.BICUBIC,
                )
                operations.append("deskew")
            if before.possible_right_column_crop:
                warnings.append("right_edge_content_risk_detected_no_crop_applied")
            else:
                warnings.append("crop_skipped_preserve_business_columns")

            if not operations:
                return ImagePreprocessResult(
                    original_path=str(path),
                    processed_path=None,
                    operations=[],
                    warnings=warnings,
                    quality_before=before.to_dict(),
                    quality_after=None,
                )

            processed_path = output / f"{path.stem}-preprocessed{path.suffix or '.png'}"
            processed.save(processed_path)
        after = self.analyzer.analyze_page_image(processed_path)
        return ImagePreprocessResult(
            original_path=str(path),
            processed_path=str(processed_path),
            operations=operations,
            warnings=warnings,
            quality_before=before.to_dict(),
            quality_after=after.to_dict(),
        )


def preprocess_image(image_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    return ImagePreprocessor().preprocess_image(image_path, output_dir).to_dict()
