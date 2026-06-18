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

    def generate_vl_retry_variants(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        *,
        max_variants: int = 3,
    ) -> list[dict[str, Any]]:
        """Create full-page, no-inner-crop variants for VL retry.

        These variants may deskew/rectify the whole page and improve contrast,
        but they never crop to a table or upper region. Manufacturing documents
        often carry notes, signatures, and extra rows below the table; losing
        them would be worse than a conservative review result.
        """

        path = Path(image_path)
        if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            return []
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        variants: list[dict[str, Any]] = []
        try:
            before = self.analyzer.analyze_page_image(path)
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            page = self._perspective_rectify_full_page(image)
            if page is None:
                page = image
                base_operations = ["full_page_preserved"]
            else:
                base_operations = ["full_page_perspective_rectified"]
            for name, scale, contrast_cutoff, sharp_radius, sharp_percent, cv_mode in (
                ("full_page_readability", 2.2, 1, 1.4, 145, None),
                ("full_page_document_clarity", 2.6, 0, 1.6, 170, "document_clarity"),
                ("full_page_high_contrast", 2.4, 0, 1.2, 130, "high_contrast"),
            ):
                if len(variants) >= max_variants:
                    break
                processed = page
                operations = list(base_operations)
                if scale > 1:
                    processed = processed.resize(
                        (int(processed.width * scale), int(processed.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                    operations.append(f"full_page_upscale_{scale:g}x")
                if cv_mode:
                    enhanced, cv_operations = self._enhance_full_page_for_vl(processed, mode=cv_mode)
                    if enhanced is not None:
                        processed = enhanced
                        operations.extend(cv_operations)
                    else:
                        processed = ImageOps.autocontrast(processed, cutoff=contrast_cutoff)
                        operations.append("autocontrast")
                else:
                    processed = ImageOps.autocontrast(processed, cutoff=contrast_cutoff)
                    operations.append("autocontrast")
                processed = processed.filter(
                    ImageFilter.UnsharpMask(radius=sharp_radius, percent=sharp_percent, threshold=3)
                )
                operations.append("unsharp_mask")
                processed_path = output / f"{path.stem}-{name}.png"
                processed.save(processed_path)
                try:
                    after = self.analyzer.analyze_page_image(processed_path).to_dict()
                except Exception:
                    after = None
                variants.append(
                    {
                        "variant_name": name,
                        "original_path": str(path),
                        "processed_path": str(processed_path),
                        "operations": operations,
                        "warnings": [
                            "no_inner_crop_applied_preserve_full_document",
                            "vl_retry_candidate_only",
                        ],
                        "quality_before": before.to_dict(),
                        "quality_after": after,
                    }
                )
        except Exception as exc:
            return [
                {
                    "variant_name": "full_page_retry_unavailable",
                    "original_path": str(path),
                    "processed_path": None,
                    "operations": [],
                    "warnings": ["vl_retry_preprocess_failed"],
                    "error": str(exc),
                }
            ]
        return variants

    def _perspective_rectify_full_page(self, image: Image.Image) -> Image.Image | None:
        try:
            import cv2
            import numpy as np

            rgb = np.asarray(image.convert("RGB"))
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = np.ones((11, 11), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            contour = max(contours, key=cv2.contourArea)
            area = float(cv2.contourArea(contour))
            height, width = gray.shape
            if area < float(width * height) * 0.18:
                return None
            rect = cv2.minAreaRect(contour)
            points = cv2.boxPoints(rect)
            ordered = self._order_points(points)
            tl, tr, br, bl = ordered
            target_width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            target_height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if target_width < width * 0.35 or target_height < height * 0.35:
                return None
            destination = np.array(
                [
                    [0, 0],
                    [target_width - 1, 0],
                    [target_width - 1, target_height - 1],
                    [0, target_height - 1],
                ],
                dtype="float32",
            )
            matrix = cv2.getPerspectiveTransform(ordered.astype("float32"), destination)
            warped = cv2.warpPerspective(rgb, matrix, (target_width, target_height), borderValue=(255, 255, 255))
            border = max(24, int(min(target_width, target_height) * 0.02))
            warped = cv2.copyMakeBorder(
                warped,
                border,
                border,
                border,
                border,
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            )
            return Image.fromarray(warped)
        except Exception:
            return None

    def _enhance_full_page_for_vl(self, image: Image.Image, *, mode: str) -> tuple[Image.Image | None, list[str]]:
        """Improve full-page readability without removing any document region."""

        try:
            import cv2
            import numpy as np

            rgb = np.asarray(image.convert("RGB"))
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            operations: list[str] = []

            if mode == "document_clarity":
                background = cv2.GaussianBlur(gray, (0, 0), sigmaX=21, sigmaY=21)
                normalized = cv2.divide(gray, background, scale=255)
                operations.append("full_page_shadow_normalization")
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                enhanced = clahe.apply(normalized)
                operations.append("full_page_local_contrast")
                enhanced = cv2.fastNlMeansDenoising(enhanced, None, h=7, templateWindowSize=7, searchWindowSize=21)
                operations.append("full_page_denoise")
                blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.0, sigmaY=1.0)
                enhanced = cv2.addWeighted(enhanced, 1.55, blurred, -0.55, 0)
                operations.append("full_page_deblur_sharpen")
            elif mode == "high_contrast":
                background = cv2.medianBlur(gray, 31)
                normalized = cv2.divide(gray, background, scale=255)
                operations.append("full_page_background_normalization")
                clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(6, 6))
                enhanced = clahe.apply(normalized)
                operations.append("full_page_high_contrast_clahe")
                enhanced = cv2.bilateralFilter(enhanced, d=5, sigmaColor=35, sigmaSpace=35)
                operations.append("full_page_edge_preserving_denoise")
            else:
                return None, []

            enhanced_rgb = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)
            return Image.fromarray(enhanced_rgb), operations
        except Exception:
            return None, []

    def _order_points(self, points: Any) -> Any:
        import numpy as np

        rect = np.zeros((4, 2), dtype="float32")
        sums = points.sum(axis=1)
        rect[0] = points[np.argmin(sums)]
        rect[2] = points[np.argmax(sums)]
        diffs = np.diff(points, axis=1)
        rect[1] = points[np.argmin(diffs)]
        rect[3] = points[np.argmax(diffs)]
        return rect


def preprocess_image(image_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    return ImagePreprocessor().preprocess_image(image_path, output_dir).to_dict()
