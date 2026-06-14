import json

from app.scripts.summarize_paddleocr_vl_gguf_smokes import markdown_summary, summarize_reports


def _write_report(path, *, sample, severity, candidate, issues=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sample": sample,
                "ok": True,
                "classification": "official_gguf_smoke_success",
                "provider_available_candidate": candidate,
                "provider_available_decision_reason": "manual_visual_check_passed"
                if candidate
                else "manual_visual_check_warn",
                "validation": {"matched_terms": ["QT-2026-0808-009"]},
                "manual_visual_check": {"pdf_opened_and_visually_checked": True},
                "manual_visual_check_validation": {
                    "severity": severity,
                    "issue_codes": issues or [],
                    "dangerous_error_count": 0,
                    "hallucination_count": 0,
                },
                "elapsed_ms": 95000,
            }
        ),
        encoding="utf-8",
    )


def test_gguf_smoke_summary_blocks_production_active_when_any_report_warns(tmp_path):
    report_08 = tmp_path / "08" / "paddleocr_vl_gguf_smoke_report.json"
    report_16 = tmp_path / "16" / "paddleocr_vl_gguf_smoke_report.json"
    _write_report(report_08, sample="08_image_quote_missing_quantity.pdf", severity="pass", candidate=True)
    _write_report(
        report_16,
        sample="16_real_commercial_invoice_exchange_rate.pdf",
        severity="warn",
        candidate=False,
        issues=["vl_candidate_missing_line_amount", "vl_candidate_missing_row_cell"],
    )

    summary = summarize_reports([report_08, report_16])

    assert summary["report_count"] == 2
    assert summary["manual_visual_checked_count"] == 2
    assert summary["provider_available_candidate_count"] == 1
    assert summary["manual_severity_counts"] == {"pass": 1, "warn": 1}
    assert summary["severity_counts"] == {"pass": 1, "warn": 1}
    assert summary["issue_counts"] == {
        "vl_candidate_missing_line_amount": 1,
        "vl_candidate_missing_row_cell": 1,
    }
    assert summary["production_active_recommended"] is False
    assert summary["production_active_reason"] == "manual_visual_check_warn"

    markdown = markdown_summary(summary)
    assert "Production active recommended: `False`" in markdown
    assert "manual_visual_check_warn" in markdown
    assert "vl_candidate_missing_line_amount" in markdown


def test_gguf_smoke_summary_recommends_active_only_when_all_candidates_pass(tmp_path):
    report_08 = tmp_path / "08" / "paddleocr_vl_gguf_smoke_report.json"
    report_16 = tmp_path / "16" / "paddleocr_vl_gguf_smoke_report.json"
    _write_report(report_08, sample="08_image_quote_missing_quantity.pdf", severity="pass", candidate=True)
    _write_report(report_16, sample="16_real_commercial_invoice_exchange_rate.pdf", severity="pass", candidate=True)

    summary = summarize_reports([report_08, report_16])

    assert summary["production_active_recommended"] is True
    assert summary["production_active_reason"] == "all_smokes_passed"


def test_gguf_smoke_summary_surfaces_missing_report_paths(tmp_path):
    missing = tmp_path / "missing" / "paddleocr_vl_gguf_smoke_report.json"

    summary = summarize_reports([missing])

    assert summary["report_count"] == 0
    assert summary["missing_report_paths"] == [str(missing)]
    assert summary["production_active_recommended"] is False
    assert summary["production_active_reason"] == "missing_smoke_reports"


def test_gguf_smoke_summary_parses_resource_monitor_peak_usage(tmp_path):
    report = tmp_path / "08" / "paddleocr_vl_gguf_smoke_report.json"
    _write_report(report, sample="08_image_quote_missing_quantity.pdf", severity="pass", candidate=True)
    (report.parent / "resource_monitor.log").write_text(
        "\n".join(
            [
                "2026-06-14T00:00:00+00:00",
                "               total        used        free      shared  buff/cache   available",
                "Mem:           7.8Gi       3.5Gi       1.0Gi       1.0Mi       3.3Gi       4.0Gi",
                "Swap:          8.0Gi       458Mi       7.6Gi",
                "---",
                "2026-06-14T00:00:05+00:00",
                "               total        used        free      shared  buff/cache   available",
                "Mem:           7.8Gi       4.1Gi       900Mi       1.0Mi       2.8Gi       3.6Gi",
                "Swap:          8.0Gi       1.2Gi       6.8Gi",
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_reports([report])
    resource = summary["rows"][0]["resource_monitor"]

    assert resource["samples"] == 2
    assert resource["max_mem_used_mib"] == 4198.4
    assert resource["max_swap_used_mib"] == 1228.8

    markdown = markdown_summary(summary)
    assert "mem=4198.4MiB swap=1228.8MiB" in markdown
