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
        _candidate("DocuParse realistic manufacturing sample", 100, 900, 350, 920),
    ])

    rows = reconstructor.group_rows_by_y(candidates)
    columns = reconstructor.infer_columns(rows)
    structured = reconstructor.map_tokens_to_columns(rows, columns)
    items = reconstructor.build_line_item_candidates(structured, "priced_document")

    assert len(items) == 15
    assert all("합계금액" not in (item.get("item_name") or "") for item in items)
    assert all("DocuParse" not in (item.get("item_name") or "") for item in items)


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
