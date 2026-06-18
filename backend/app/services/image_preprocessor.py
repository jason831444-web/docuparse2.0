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

    def prepare_standard_vl_input(self, image_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
        """Create one strong full-page input for VL inference.

        This is intentionally not a retry heuristic.  For image uploads we
        always give VL the same readability-normalized full-page image once:
        perspective-rectify the visible paper when possible, upscale, normalize
        shadows, improve local contrast, denoise, and sharpen.  It never crops
        to a table or discards lower-page notes.
        """

        path = Path(image_path)
        if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            return {
                "variant_name": "original_file",
                "original_path": str(path),
                "processed_path": None,
                "operations": ["standard_vl_preprocess_skipped_non_image"],
                "warnings": [],
            }
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            page = self._perspective_rectify_full_page(image)
            if page is None:
                page = image
                operations = ["full_page_preserved"]
            else:
                operations = ["full_page_perspective_rectified"]

            scale = 2.6
            processed = page.resize(
                (int(page.width * scale), int(page.height * scale)),
                Image.Resampling.LANCZOS,
            )
            operations.append(f"full_page_upscale_{scale:g}x")
            enhanced, cv_operations = self._enhance_full_page_for_vl(processed, mode="document_clarity")
            if enhanced is not None:
                processed = enhanced
                operations.extend(cv_operations)
            else:
                processed = ImageOps.autocontrast(processed, cutoff=0)
                operations.append("autocontrast")
            processed = processed.filter(
                ImageFilter.UnsharpMask(radius=1.6, percent=170, threshold=3)
            )
            operations.append("unsharp_mask")
            processed_path = output / f"{path.stem}-vl-standard.png"
            processed.save(processed_path)
            return {
                "variant_name": "full_page_document_clarity",
                "original_path": str(path),
                "processed_path": str(processed_path),
                "operations": operations,
                "warnings": [
                    "no_inner_crop_applied_preserve_full_document",
                    "vl_standard_preprocess_input",
                ],
            }
        except Exception as exc:
            return {
                "variant_name": "standard_vl_preprocess_unavailable",
                "original_path": str(path),
                "processed_path": None,
                "operations": [],
                "warnings": ["vl_standard_preprocess_failed"],
                "error": str(exc),
            }

    def prepare_contrast_only_vl_input(self, image_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
        """Create a light contrast-only VL input without crop or sharpening.

        This candidate is intentionally weaker than ``prepare_standard_vl_input``:
        it preserves the full page, avoids denoise/sharpen/unsharp steps, and is
        meant only for low-contrast photo/fax-like inputs where the raw image is
        likely to under-read item text.
        """

        path = Path(image_path)
        if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            return {
                "variant_name": "original_file",
                "original_path": str(path),
                "processed_path": None,
                "operations": ["contrast_only_skipped_non_image"],
                "warnings": [],
            }
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            operations = ["full_page_preserved", "contrast_only_no_crop"]
            processed = image
            enhanced, cv_operations = self._enhance_full_page_for_vl(processed, mode="contrast_only")
            if enhanced is not None:
                processed = enhanced
                operations.extend(cv_operations)
            else:
                processed = ImageOps.autocontrast(processed, cutoff=1)
                operations.append("autocontrast_cutoff_1")
            processed_path = output / f"{path.stem}-vl-contrast-only.png"
            processed.save(processed_path)
            return {
                "variant_name": "contrast_only",
                "original_path": str(path),
                "processed_path": str(processed_path),
                "operations": operations,
                "warnings": [
                    "no_crop_applied_preserve_full_document",
                    "no_sharpen_or_denoise_applied",
                    "vl_contrast_only_preprocess_input",
                ],
            }
        except Exception as exc:
            return {
                "variant_name": "contrast_only_unavailable",
                "original_path": str(path),
                "processed_path": None,
                "operations": [],
                "warnings": ["vl_contrast_only_preprocess_failed"],
                "error": str(exc),
            }

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
            elif mode == "contrast_only":
                background = cv2.GaussianBlur(gray, (0, 0), sigmaX=21, sigmaY=21)
                normalized = cv2.divide(gray, background, scale=255)
                operations.append("full_page_light_background_normalization")
                clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
                enhanced = clahe.apply(normalized)
                operations.append("full_page_light_local_contrast")
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
