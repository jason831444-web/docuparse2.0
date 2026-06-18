import sys
from types import SimpleNamespace

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.scripts.compare_vl_input_candidates import _candidate_metrics, _compare_metrics


def test_candidate_metrics_uses_best_official_table_quality():
    metrics = _candidate_metrics(
        {
            "tables": [
                {
                    "official_table_quality": {
                        "quality_score": 0.4,
                        "row_count": 1,
                        "expected_column_coverage": 0.25,
                        "empty_cell_ratio": 0.5,
                    }
                },
                {
                    "official_table_quality": {
                        "quality_score": 0.92,
                        "row_count": 3,
                        "expected_column_coverage": 0.75,
                        "empty_cell_ratio": 0.04,
                    }
                },
            ]
        }
    )

    assert metrics["quality_score"] == 0.92
    assert metrics["table_count"] == 2
    assert metrics["row_count"] == 4
    assert metrics["expected_column_coverage"] == 0.75
    assert metrics["empty_cell_ratio"] == 0.04


def test_compare_metrics_prefers_preprocessed_only_by_model_table_quality():
    better, reason = _compare_metrics(
        {"quality_score": 0.7, "row_count": 3, "empty_cell_ratio": 0.1},
        {"quality_score": 0.91, "row_count": 2, "empty_cell_ratio": 0.2},
    )

    assert better == "standard_preprocessed"
    assert reason == "higher_quality_score"


def test_compare_metrics_tie_prefers_original():
    better, reason = _compare_metrics(
        {"quality_score": 0.9, "row_count": 3, "empty_cell_ratio": 0.1},
        {"quality_score": 0.9, "row_count": 3, "empty_cell_ratio": 0.1},
    )

    assert better == "original"
    assert reason == "original_tie_breaker"
