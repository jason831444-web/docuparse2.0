from app.services.table_layout import BBoxTableReconstructor


def _candidate(text: str, x_min: float, y_min: float, x_max: float, y_max: float, confidence: float = 0.95):
    return {
        "text": text,
        "confidence": confidence,
        "page": 1,
        "bbox": [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]],
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
    }


def test_bbox_reconstructor_groups_rows_by_page_and_y_axis():
    reconstructor = BBoxTableReconstructor()
    rows = reconstructor.group_rows_by_y([
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("수량", 300, 102, 340, 121),
        _candidate("M8 볼트", 100, 150, 170, 170),
        _candidate("10", 300, 151, 325, 169),
    ])

    assert len(rows) == 2
    assert rows[0].text == "품목명 수량"
    assert rows[1].text == "M8 볼트 10"


def test_bbox_reconstructor_infers_columns_from_header_tokens():
    reconstructor = BBoxTableReconstructor()
    rows = reconstructor.group_rows_by_y([
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("규격", 250, 100, 300, 120),
        _candidate("수량", 360, 100, 410, 120),
        _candidate("합계", 520, 100, 580, 120),
    ])
    columns = reconstructor.infer_columns(rows)

    assert [column.name for column in columns] == ["item_name", "specification", "quantity", "line_total"]
    assert columns[0].source == "header"


def test_bbox_reconstructor_keeps_fifteen_photo_rows_without_footer_rows():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("규격", 260, 100, 310, 120),
        _candidate("수량", 380, 100, 430, 120),
        _candidate("단가", 520, 100, 570, 120),
        _candidate("공급가액", 660, 100, 740, 120),
    ]
    for index in range(15):
        y = 150 + index * 30
        candidates.extend([
            _candidate("M8육각볼트" if index % 2 == 0 else "SUS WASHER M8", 100, y, 190, y + 18),
            _candidate("M8x20" if index % 2 == 0 else "M8", 260, y, 310, y + 18),
            _candidate(str(10 + index), 380, y, 420, y + 18),
            _candidate(str(100 + index * 5), 520, y, 560, y + 18),
            _candidate(str((10 + index) * (100 + index * 5)), 660, y, 730, y + 18),
        ])
    candidates.extend([
        _candidate("합계금액", 650, 650, 730, 670),
        _candidate("431200", 760, 650, 830, 670),
        _candidate("Docparse realistic manufacturing sample", 100, 900, 350, 920),
    ])

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 15
    assert all("합계금액" not in (item.get("item_name") or "") for item in items)
    assert all("Docparse" not in (item.get("item_name") or "") for item in items)


def test_bbox_reconstructor_does_not_hallucinate_missing_fax_item_name():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 270, 400, 340, 420),
        _candidate("공급가액", 770, 400, 840, 420),
        _candidate("베어링하우징", 270, 430, 360, 450),
        _candidate("16000", 770, 430, 820, 450),
        _candidate("1600", 870, 430, 910, 450),
        _candidate("176000", 950, 430, 1010, 450),
        _candidate("S45C PIN 8X6Q", 270, 460, 390, 480),
        _candidate("6000", 770, 460, 820, 480),
        _candidate("66000", 950, 460, 1010, 480),
        _candidate("16000", 770, 490, 820, 510),
        _candidate("1600C", 870, 490, 920, 510),
        _candidate("176000", 950, 490, 1010, 510),
        _candidate("합계금액", 780, 560, 850, 580),
        _candidate("418000", 950, 560, 1010, 580),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 3
    assert items[0]["item_name"] == "베어링하우징"
    assert items[1]["item_name"] == "S45C PIN 8X6Q"
    assert items[2].get("item_name") is None
    assert "missing_item_name_from_ocr" in items[2]["review_flags"]
    assert "untrusted_ocr_amount" in items[2]["review_flags"]
    assert items[2]["supply_amount"] == 176000


def test_bbox_reconstructor_excludes_tax_summary_and_bank_memo_candidates():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("수량", 300, 100, 340, 120),
        _candidate("합계", 520, 100, 580, 120),
        _candidate("M8 볼트", 100, 150, 170, 170),
        _candidate("10", 300, 150, 325, 170),
        _candidate("11000", 520, 150, 580, 170),
        _candidate("부가세:", 420, 210, 480, 230),
        _candidate("1100", 520, 210, 580, 230),
        _candidate("Bank: Chase", 100, 250, 190, 270),
        _candidate("Swift: CHASUS33", 220, 250, 340, 270),
        _candidate("Memo: invoice number required", 360, 250, 560, 270),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "M8 볼트"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "부가세" not in source_text
    assert "Bank" not in source_text
    assert "Memo" not in source_text


def test_bbox_reconstructor_excludes_customs_accounting_note_candidates():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("Description", 100, 100, 190, 120),
        _candidate("Amount", 520, 100, 580, 120),
        _candidate("Cable Harness 500", 100, 150, 230, 170),
        _candidate("110.00", 520, 150, 580, 170),
        _candidate("*통관/회계입력시원화환산기준일확인필요", 100, 230, 420, 250),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "foreign_currency_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "Cable Harness 500"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "통관" not in source_text
    assert "회계입력" not in source_text


def test_bbox_reconstructor_excludes_tax_invoice_adjustment_note_and_approval_ocr_noise():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("문서품목코드", 240, 100, 340, 120),
        _candidate("공급가액", 520, 100, 600, 120),
        _candidate("Cable Harness 500", 100, 150, 230, 170),
        _candidate("CBL-HAR-500", 240, 150, 340, 170),
        _candidate("169477", 520, 150, 580, 170),
        _candidate("원단위 철사조정금액포함", 100, 230, 310, 250),
        _candidate("음수라인을임의삭제하지말것", 330, 230, 560, 250),
        _candidate("담등", 700, 500, 740, 520),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "tax_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "Cable Harness 500"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "음수라인" not in source_text
    assert "담등" not in source_text


def test_bbox_reconstructor_marks_amount_only_tax_candidates_as_uncertain():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("문서품목코드", 240, 100, 340, 120),
        _candidate("공급가액", 520, 100, 600, 120),
        _candidate("Cable Harness 500", 100, 150, 230, 170),
        _candidate("CBL-HAR-500", 240, 150, 340, 170),
        _candidate("169477", 520, 150, 580, 170),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "tax_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "Cable Harness 500"
    assert items[0]["supply_amount"] == 169477
    assert "quantity" in items[0]["missing_fields"]
    assert "missing_quantity_from_ocr" in items[0]["review_flags"]
    assert "row_boundary_uncertain" in items[0]["review_flags"]
    assert "untrusted_ocr_amount" in items[0]["review_flags"]
    assert "supply_amount" in items[0]["untrusted_fields"]


def test_bbox_reconstructor_excludes_statement_summary_balance_rows():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("공급가액", 420, 100, 500, 120),
        _candidate("SUS WASHER M8", 100, 150, 220, 170),
        _candidate("80000", 420, 150, 480, 170),
        _candidate("금월합계", 100, 220, 170, 240),
        _candidate("705100", 420, 220, 480, 240),
        _candidate("총미수금", 100, 260, 170, 280),
        _candidate("1945100", 420, 260, 500, 280),
        _candidate("*전월이월금액은품목합계에포함하지말것", 100, 300, 450, 320),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "SUS WASHER M8"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "금월합계" not in source_text
    assert "총미수금" not in source_text
    assert "전월이월" not in source_text


def test_bbox_reconstructor_excludes_option_quote_summary_rows():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("수량", 300, 100, 340, 120),
        _candidate("합계", 520, 100, 580, 120),
        _candidate("스텐판2T 옵션A", 100, 150, 220, 170),
        _candidate("1", 300, 150, 325, 170),
        _candidate("100000", 520, 150, 590, 170),
        _candidate("선택시합계", 100, 220, 190, 240),
        _candidate("옵션별상이", 220, 220, 310, 240),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "스텐판2T 옵션A"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "선택시합계" not in source_text
    assert "옵션별상이" not in source_text


def test_bbox_reconstructor_excludes_quantity_correction_note_rows():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("수량", 300, 100, 340, 120),
        _candidate("합계", 520, 100, 580, 120),
        _candidate("고정브라켓", 100, 150, 180, 170),
        _candidate("10", 300, 150, 325, 170),
        _candidate("11000", 520, 150, 580, 170),
        _candidate("*1번수량은담당자수기정정:4에서5로변경", 100, 220, 390, 240),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 1
    assert items[0]["item_name"] == "고정브라켓"
    source_text = " ".join(
        token["text"]
        for item in items
        for token in item.get("source_tokens", [])
    )
    assert "수기정정" not in source_text
    assert "4에서5로변경" not in source_text


def test_bbox_reconstructor_does_not_create_amount_for_no_price_profile():
    reconstructor = BBoxTableReconstructor()
    candidates = [
        _candidate("품목명", 100, 100, 160, 120),
        _candidate("요청수량", 300, 100, 380, 120),
        _candidate("내부 이동품", 100, 150, 190, 170),
        _candidate("25", 300, 150, 330, 170),
    ]

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "no_price_document")

    assert len(items) == 1
    assert "line_total" not in items[0]
    assert "untrusted_ocr_amount" not in items[0]["review_flags"]
