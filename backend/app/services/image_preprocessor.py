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

    def prepare_light_page_vl_input(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        *,
        quality: dict[str, Any] | None = None,
        avoid_page_crop: bool = False,
    ) -> dict[str, Any]:
        """Create the default conservative full-page VL input for image uploads.

        This mode is intentionally weaker than the old standard candidate.  It
        keeps the whole document page, never crops inside tables, avoids
        binarization, and applies only light readability improvements.  Page
        crop/perspective is allowed only for a high-confidence paper outline and
        is disabled when hidden/cropped column risk is present.
        """

        path = Path(image_path)
        if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            return {
                "variant_name": "original_file",
                "original_path": str(path),
                "processed_path": None,
                "operations": ["light_page_preprocess_skipped_non_image"],
                "warnings": [],
                "metadata": {
                    "page_crop_applied": False,
                    "page_crop_confidence": 0.0,
                    "deskew_applied": False,
                    "upscale_factor": None,
                    "contrast_mode": None,
                    "skipped_reasons": ["non_image_input"],
                },
            }
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        operations: list[str] = ["full_page_preserved", "no_table_or_inner_crop"]
        warnings: list[str] = [
            "original_file_not_overwritten",
            "right_amount_total_footer_header_preserved",
            "no_binarization_applied",
        ]
        skipped_reasons: list[str] = []
        metadata: dict[str, Any] = {
            "page_crop_applied": False,
            "page_crop_confidence": 0.0,
            "deskew_applied": False,
            "upscale_factor": 2.2,
            "contrast_mode": "weak_background_normalization_clahe",
            "skipped_reasons": skipped_reasons,
        }
        try:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
            processed = image

            if avoid_page_crop:
                skipped_reasons.append("hidden_or_cropped_column_risk_skip_page_crop")
                warnings.append("hidden_cropped_guardrail_no_page_crop_or_perspective")
            else:
                rectified, crop_info = self._light_page_rectify_full_page(processed)
                confidence = float(crop_info.get("confidence") or 0.0)
                metadata["page_crop_confidence"] = round(confidence, 4)
                if rectified is not None:
                    processed = rectified
                    metadata["page_crop_applied"] = True
                    operations.append("high_confidence_page_level_perspective_rectify_with_padding")
                    metadata["page_crop_padding_ratio"] = crop_info.get("padding_ratio")
                else:
                    skipped_reasons.append(crop_info.get("reason") or "page_outline_confidence_too_low")
                    operations.append("page_crop_skipped_preserve_full_frame")

            skew_angle = self._quality_first_page_number(quality, "skew_angle_estimate")
            if skew_angle is not None and 1.0 <= abs(skew_angle) <= 6.0:
                processed = processed.rotate(
                    -skew_angle,
                    expand=True,
                    fillcolor=(255, 255, 255),
                    resample=Image.Resampling.BICUBIC,
                )
                metadata["deskew_applied"] = True
                metadata["deskew_angle"] = round(float(skew_angle), 4)
                operations.append("light_deskew")
            else:
                skipped_reasons.append("deskew_angle_not_confident_or_unneeded")

            scale = float(metadata["upscale_factor"])
            processed = processed.resize(
                (max(1, int(processed.width * scale)), max(1, int(processed.height * scale))),
                Image.Resampling.LANCZOS,
            )
            operations.append(f"full_page_upscale_{scale:g}x")

            enhanced, cv_operations = self._enhance_full_page_for_vl(processed, mode="light_page")
            if enhanced is not None:
                processed = enhanced
                operations.extend(cv_operations)
            else:
                processed = ImageOps.autocontrast(processed, cutoff=1)
                operations.append("weak_autocontrast_cutoff_1")

            blur = self._quality_first_page_number(quality, "blur_score")
            if blur is not None and blur < 40.0:
                processed = processed.filter(ImageFilter.MedianFilter(size=3))
                operations.append("very_light_median_denoise")
            else:
                skipped_reasons.append("denoise_unneeded_or_blur_not_confident")

            if blur is not None and blur < 55.0:
                processed = processed.filter(ImageFilter.UnsharpMask(radius=0.7, percent=55, threshold=5))
                operations.append("very_soft_unsharp_mask")
            else:
                skipped_reasons.append("sharpen_unneeded_or_blur_not_confident")

            processed_path = output / f"{path.stem}-vl-light-page.png"
            processed.save(processed_path)
            return {
                "variant_name": "light_page_preprocess",
                "original_path": str(path),
                "processed_path": str(processed_path),
                "operations": operations,
                "warnings": warnings,
                "metadata": metadata,
            }
        except Exception as exc:
            skipped_reasons.append("light_page_preprocess_exception")
            return {
                "variant_name": "light_page_preprocess_unavailable",
                "original_path": str(path),
                "processed_path": None,
                "operations": operations,
                "warnings": warnings + ["vl_light_page_preprocess_failed"],
                "error": str(exc),
                "metadata": metadata,
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

    def _light_page_rectify_full_page(self, image: Image.Image) -> tuple[Image.Image | None, dict[str, Any]]:
        """High-confidence page-level perspective correction with padding.

        This never attempts table/region crop.  It only uses the largest
        page-like contour and rejects ambiguous outlines aggressively.
        """

        try:
            import cv2
            import numpy as np

            rgb = np.asarray(image.convert("RGB"))
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = np.ones((9, 9), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None, {"confidence": 0.0, "reason": "no_page_contour"}
            contour = max(contours, key=cv2.contourArea)
            area = float(cv2.contourArea(contour))
            height, width = gray.shape
            image_area = float(width * height)
            area_ratio = area / image_area if image_area else 0.0
            if area_ratio < 0.35:
                return None, {"confidence": area_ratio, "reason": "page_contour_area_too_small"}

            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            if len(approx) == 4:
                points = approx.reshape(4, 2).astype("float32")
                corner_score = 1.0
            else:
                rect = cv2.minAreaRect(contour)
                points = cv2.boxPoints(rect).astype("float32")
                corner_score = 0.72

            ordered = self._order_points(points)
            tl, tr, br, bl = ordered
            target_width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            target_height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if target_width < width * 0.55 or target_height < height * 0.55:
                return None, {"confidence": area_ratio * corner_score, "reason": "page_target_too_small"}

            bbox_x_min = float(np.min(ordered[:, 0]))
            bbox_x_max = float(np.max(ordered[:, 0]))
            if bbox_x_max < width * 0.82 or bbox_x_min > width * 0.18:
                return None, {"confidence": area_ratio * corner_score, "reason": "page_outline_may_crop_side_columns"}

            confidence = min(1.0, area_ratio * corner_score)
            if confidence < 0.45:
                return None, {"confidence": confidence, "reason": "page_outline_confidence_too_low"}

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
            padding_ratio = 0.03
            border = max(16, int(min(target_width, target_height) * padding_ratio))
            warped = cv2.copyMakeBorder(
                warped,
                border,
                border,
                border,
                border,
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            )
            return Image.fromarray(warped), {
                "confidence": confidence,
                "reason": "high_confidence_page_outline",
                "padding_ratio": padding_ratio,
            }
        except Exception as exc:
            return None, {"confidence": 0.0, "reason": "page_rectify_failed", "error": str(exc)}

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
            elif mode == "light_page":
                background = cv2.GaussianBlur(gray, (0, 0), sigmaX=31, sigmaY=31)
                normalized = cv2.divide(gray, background, scale=255)
                operations.append("weak_page_background_normalization")
                clahe = cv2.createCLAHE(clipLimit=1.35, tileGridSize=(8, 8))
                enhanced = clahe.apply(normalized)
                operations.append("weak_page_local_contrast")
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

    def _quality_first_page_number(self, quality: dict[str, Any] | None, key: str) -> float | None:
        if not isinstance(quality, dict):
            return None
        pages = quality.get("pages") if isinstance(quality.get("pages"), list) else []
        first_page = pages[0] if pages and isinstance(pages[0], dict) else {}
        value = first_page.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        value = quality.get(key)
        return float(value) if isinstance(value, (int, float)) else None


def preprocess_image(image_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    return ImagePreprocessor().preprocess_image(image_path, output_dir).to_dict()
