from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from PIL import Image


@dataclass
class PageQualityResult:
    page_index: int
    width: int
    height: int
    estimated_dpi: int | None
    resolution_bucket: str
    blur_score: float
    is_blurry: bool
    brightness_score: float
    contrast_score: float
    skew_angle_estimate: float | None
    is_skewed: bool
    likely_scan_type: str
    right_edge_content_risk: float
    possible_right_column_crop: bool
    quality_score: float
    visible_columns: list[str] = field(default_factory=list)
    hidden_or_cropped_columns: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blur_score"] = round(self.blur_score, 3)
        data["brightness_score"] = round(self.brightness_score, 3)
        data["contrast_score"] = round(self.contrast_score, 3)
        data["right_edge_content_risk"] = round(self.right_edge_content_risk, 4)
        data["quality_score"] = round(self.quality_score, 3)
        if self.skew_angle_estimate is not None:
            data["skew_angle_estimate"] = round(self.skew_angle_estimate, 3)
        return data


@dataclass
class DocumentQualityResult:
    page_count: int
    pages: list[PageQualityResult]
    overall_quality_score: float
    likely_scan_type: str
    possible_right_column_crop: bool
    has_blurry_pages: bool
    has_skewed_pages: bool
    visible_columns: list[str] = field(default_factory=list)
    hidden_or_cropped_columns: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "pages": [page.to_dict() for page in self.pages],
            "overall_quality_score": round(self.overall_quality_score, 3),
            "likely_scan_type": self.likely_scan_type,
            "possible_right_column_crop": self.possible_right_column_crop,
            "visible_columns": list(self.visible_columns),
            "hidden_or_cropped_columns": list(self.hidden_or_cropped_columns),
            "has_blurry_pages": self.has_blurry_pages,
            "has_skewed_pages": self.has_skewed_pages,
            "review_reasons": list(self.review_reasons),
        }


class DocumentQualityAnalyzer:
    """Lightweight visual quality checks before OCR/VL results are trusted.

    The analyzer is deliberately conservative. It records visual risk metadata
    and review reasons, but it does not mutate confirmed extraction fields.
    """

    def analyze_page_image(self, image: str | Path | Image.Image, page_index: int = 1) -> PageQualityResult:
        pil_image, dpi = self._load_image(image)
        grayscale = np.asarray(pil_image.convert("L"), dtype=np.uint8)
        width, height = pil_image.size

        blur_score = self._blur_score(grayscale)
        brightness = float(np.mean(grayscale) / 255.0)
        contrast = float(np.std(grayscale) / 255.0)
        skew = self._skew_angle(grayscale)
        right_edge_risk = self._right_edge_content_risk(grayscale)

        resolution_bucket = self._resolution_bucket(width, height, dpi)
        is_blurry = blur_score < 55.0
        is_skewed = skew is not None and abs(skew) >= 2.0
        low_contrast = contrast < 0.105
        possible_crop = right_edge_risk >= 0.08
        visible_columns, hidden_or_cropped_columns = self._visible_column_estimate(possible_crop)
        likely_scan_type = self._likely_scan_type(pil_image, is_blurry, is_skewed, contrast, brightness)
        review_reasons = self._review_reasons(
            resolution_bucket=resolution_bucket,
            is_blurry=is_blurry,
            low_contrast=low_contrast,
            is_skewed=is_skewed,
            possible_crop=possible_crop,
            likely_scan_type=likely_scan_type,
        )
        score = self._quality_score(
            resolution_bucket=resolution_bucket,
            is_blurry=is_blurry,
            low_contrast=low_contrast,
            is_skewed=is_skewed,
            possible_crop=possible_crop,
            brightness=brightness,
        )

        return PageQualityResult(
            page_index=page_index,
            width=width,
            height=height,
            estimated_dpi=dpi,
            resolution_bucket=resolution_bucket,
            blur_score=blur_score,
            is_blurry=is_blurry,
            brightness_score=brightness,
            contrast_score=contrast,
            skew_angle_estimate=skew,
            is_skewed=is_skewed,
            likely_scan_type=likely_scan_type,
            right_edge_content_risk=right_edge_risk,
            possible_right_column_crop=possible_crop,
            visible_columns=visible_columns,
            hidden_or_cropped_columns=hidden_or_cropped_columns,
            quality_score=score,
            review_reasons=review_reasons,
        )

    def analyze_document_quality(self, rendered_pages: list[str | Path | Image.Image]) -> DocumentQualityResult:
        pages: list[PageQualityResult] = []
        failed_count = 0
        for index, page in enumerate(rendered_pages, start=1):
            try:
                pages.append(self.analyze_page_image(page, page_index=index))
            except Exception:
                failed_count += 1
        if not pages:
            reasons = ["document_quality_unreadable_image"] if rendered_pages else ["document_quality_no_rendered_pages"]
            return DocumentQualityResult(
                page_count=len(rendered_pages),
                pages=[],
                overall_quality_score=0.0,
                likely_scan_type="unknown",
                possible_right_column_crop=False,
                visible_columns=[],
                hidden_or_cropped_columns=[],
                has_blurry_pages=False,
                has_skewed_pages=False,
                review_reasons=reasons,
            )
        reasons: list[str] = []
        for page in pages:
            for reason in page.review_reasons:
                if reason not in reasons:
                    reasons.append(reason)
        if failed_count:
            reasons.append("document_quality_unreadable_image")
        hidden_columns = self._merge_columns(page.hidden_or_cropped_columns for page in pages)
        visible_columns = self._merge_columns(page.visible_columns for page in pages)
        if hidden_columns:
            visible_columns = [column for column in visible_columns if column not in hidden_columns]
        return DocumentQualityResult(
            page_count=len(rendered_pages),
            pages=pages,
            overall_quality_score=float(mean(page.quality_score for page in pages)),
            likely_scan_type=self._dominant_scan_type(pages),
            possible_right_column_crop=any(page.possible_right_column_crop for page in pages),
            visible_columns=visible_columns,
            hidden_or_cropped_columns=hidden_columns,
            has_blurry_pages=any(page.is_blurry for page in pages),
            has_skewed_pages=any(page.is_skewed for page in pages),
            review_reasons=reasons,
        )

    def digital_pdf_quality(self, page_count: int | None = None) -> dict[str, Any]:
        return {
            "page_count": page_count,
            "pages": [],
            "overall_quality_score": 0.95,
            "likely_scan_type": "digital_pdf",
            "possible_right_column_crop": False,
            "visible_columns": self._business_columns(),
            "hidden_or_cropped_columns": [],
            "has_blurry_pages": False,
            "has_skewed_pages": False,
            "review_reasons": [],
        }

    def _business_columns(self) -> list[str]:
        return [
            "item_name",
            "document_item_code",
            "internal_item_code",
            "specification",
            "quantity",
            "unit",
            "unit_price",
            "supply_amount",
            "tax_amount",
            "line_total",
        ]

    def _visible_column_estimate(self, possible_crop: bool) -> tuple[list[str], list[str]]:
        columns = self._business_columns()
        if not possible_crop:
            return columns, []
        hidden = ["tax_amount", "line_total"]
        return [column for column in columns if column not in hidden], hidden

    def _merge_columns(self, column_lists: Any) -> list[str]:
        merged: list[str] = []
        for columns in column_lists:
            if not isinstance(columns, list):
                continue
            for column in columns:
                if isinstance(column, str) and column and column not in merged:
                    merged.append(column)
        return merged

    def _load_image(self, image: str | Path | Image.Image) -> tuple[Image.Image, int | None]:
        if isinstance(image, Image.Image):
            loaded = image.convert("RGB")
            dpi = self._dpi_from_info(image.info)
            return loaded, dpi
        path = Path(image)
        with Image.open(path) as opened:
            dpi = self._dpi_from_info(opened.info)
            return opened.convert("RGB"), dpi

    def _dpi_from_info(self, info: dict[str, Any]) -> int | None:
        value = info.get("dpi")
        if isinstance(value, tuple) and value:
            try:
                dpi = int(round(float(value[0])))
                return dpi if dpi > 0 else None
            except Exception:
                return None
        return None

    def _blur_score(self, grayscale: np.ndarray) -> float:
        try:
            import cv2

            return float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
        except Exception:
            gy, gx = np.gradient(grayscale.astype(float))
            return float(np.var(gx) + np.var(gy))

    def _skew_angle(self, grayscale: np.ndarray) -> float | None:
        try:
            import cv2

            foreground = grayscale < 210
            coords = np.column_stack(np.where(foreground))
            if len(coords) < 80:
                return None
            rect = cv2.minAreaRect(coords.astype(np.float32))
            angle = float(rect[-1])
            if angle < -45:
                angle += 90
            if angle > 45:
                angle -= 90
            if abs(angle) < 0.25:
                return 0.0
            return angle
        except Exception:
            return None

    def _right_edge_content_risk(self, grayscale: np.ndarray) -> float:
        if grayscale.size == 0:
            return 0.0
        grayscale = self._visible_document_region(grayscale)
        height, width = grayscale.shape
        edge_width = max(8, int(width * 0.06))
        edge = grayscale[:, max(0, width - edge_width):]
        if edge.size == 0:
            return 0.0
        dark_ratio = float(np.mean(edge < 185))
        horizontal_density = float(np.mean(np.mean(edge < 185, axis=1) > 0.08))
        return min(1.0, dark_ratio * 0.75 + horizontal_density * 0.25)

    def _visible_document_region(self, grayscale: np.ndarray) -> np.ndarray:
        """Return the likely paper/page area without cropping the source image.

        Photo uploads often include a dark desk/background around the paper.
        Right-edge crop detection should inspect the page itself, not that dark
        background, otherwise a normal full-page photo looks like a truncated
        amount column.
        """

        try:
            import cv2

            height, width = grayscale.shape
            blurred = cv2.GaussianBlur(grayscale, (5, 5), 0)
            _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = np.ones((9, 9), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return grayscale
            largest = max(contours, key=cv2.contourArea)
            area = float(cv2.contourArea(largest))
            if area < float(width * height) * 0.18:
                return grayscale
            rect = cv2.minAreaRect(largest)
            points = cv2.boxPoints(rect)
            ordered = self._order_points(points)
            tl, tr, br, bl = ordered
            target_width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            target_height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if target_width < width * 0.35 or target_height < height * 0.35:
                return grayscale
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
            warped = cv2.warpPerspective(grayscale, matrix, (target_width, target_height), borderValue=255)
            inset_x = max(0, int(target_width * 0.02))
            inset_y = max(0, int(target_height * 0.02))
            x0 = min(target_width, inset_x)
            y0 = min(target_height, inset_y)
            x1 = max(0, target_width - inset_x)
            y1 = max(0, target_height - inset_y)
            if x1 <= x0 or y1 <= y0:
                return grayscale
            return warped[y0:y1, x0:x1]
        except Exception:
            return grayscale

    def _order_points(self, points: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype="float32")
        sums = points.sum(axis=1)
        rect[0] = points[np.argmin(sums)]
        rect[2] = points[np.argmax(sums)]
        diffs = np.diff(points, axis=1)
        rect[1] = points[np.argmin(diffs)]
        rect[3] = points[np.argmax(diffs)]
        return rect

    def _resolution_bucket(self, width: int, height: int, dpi: int | None) -> str:
        min_side = min(width, height)
        max_side = max(width, height)
        if dpi is not None:
            if dpi < 140:
                return "low"
            if dpi < 220:
                return "medium"
            return "high"
        if min_side < 900 or max_side < 1200:
            return "low"
        if min_side < 1500 or max_side < 2200:
            return "medium"
        return "high"

    def _likely_scan_type(
        self,
        image: Image.Image,
        is_blurry: bool,
        is_skewed: bool,
        contrast: float,
        brightness: float,
    ) -> str:
        color = np.asarray(image, dtype=np.uint8)
        channel_spread = float(np.mean(np.std(color, axis=2)) / 255.0)
        if channel_spread > 0.035 or is_skewed:
            return "photo"
        if is_blurry and contrast < 0.16:
            return "fax_like"
        if brightness > 0.72 and contrast > 0.12:
            return "scan"
        return "unknown"

    def _review_reasons(
        self,
        *,
        resolution_bucket: str,
        is_blurry: bool,
        low_contrast: bool,
        is_skewed: bool,
        possible_crop: bool,
        likely_scan_type: str,
    ) -> list[str]:
        reasons: list[str] = []
        if resolution_bucket == "low":
            reasons.append("document_low_resolution")
        if is_blurry:
            reasons.append("document_image_blurry")
        if low_contrast:
            reasons.append("document_low_contrast")
        if is_skewed:
            reasons.append("document_page_skewed")
        if possible_crop:
            reasons.append("document_right_column_crop_risk")
        if likely_scan_type in {"photo", "fax_like"}:
            reasons.append(f"document_{likely_scan_type}_source")
        return reasons

    def _quality_score(
        self,
        *,
        resolution_bucket: str,
        is_blurry: bool,
        low_contrast: bool,
        is_skewed: bool,
        possible_crop: bool,
        brightness: float,
    ) -> float:
        score = 0.92
        if resolution_bucket == "medium":
            score -= 0.08
        elif resolution_bucket == "low":
            score -= 0.18
        if is_blurry:
            score -= 0.20
        if low_contrast:
            score -= 0.16
        if is_skewed:
            score -= 0.08
        if possible_crop:
            score -= 0.18
        if brightness < 0.18 or brightness > 0.96:
            score -= 0.08
        return max(0.0, min(0.99, score))

    def _dominant_scan_type(self, pages: list[PageQualityResult]) -> str:
        counts: dict[str, int] = {}
        for page in pages:
            counts[page.likely_scan_type] = counts.get(page.likely_scan_type, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0] if counts else "unknown"


def analyze_page_image(image: str | Path | Image.Image, page_index: int = 1) -> dict[str, Any]:
    return DocumentQualityAnalyzer().analyze_page_image(image, page_index=page_index).to_dict()


def analyze_document_quality(rendered_pages: list[str | Path | Image.Image]) -> dict[str, Any]:
    return DocumentQualityAnalyzer().analyze_document_quality(rendered_pages).to_dict()
