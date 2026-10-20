import io
import pytest
from PIL import Image
from portfolio.screenshot_parser import parse_lines, recognize_images


def line(text, x, y):
    return {"text": text, "x": x, "y": y, "width": .2, "height": .02}


def trade(year=True):
    result = [line("2026-09", .1, .1)] if year else []
    return result + [line("买入-安迪苏", .04, .2), line("8.480", .57, .2),
                     line("-849.01", .8, .2), line("买 09-15 09:42", .05, .23),
                     line("100", .6, .23), line("1.01", .88, .23)]


def test_complete_trade():
    row = parse_lines(trade())["rows"][0]
    assert row["name"] == "安迪苏"
    assert row["trade_date"] == "2026-09-15"
    assert row["price_micros"] == 8480000
    assert row["quantity"] == 100
    assert row["fee_cents"] == 101
    assert row["net_cash_cents"] == -84901
    assert row["warnings"] == []


def test_missing_year_and_clipped_fields():
    row = parse_lines(trade(False)[:-1])["rows"][0]
    assert row["trade_date"] is None and row["fee_cents"] is None
    assert len(row["warnings"]) == 2
    assert parse_lines(trade(False), reference_year=2026)["rows"][0]["trade_date"] == "2026-09-15"


def test_holdings_never_become_trades():
    assert parse_lines([line("持仓股", .1, .1)] + trade())["rows"] == []


def test_transfer():
    row = parse_lines([line("银证转账", .4, .01), line("2026-08", .1, .1),
                      line("收入", .04, .2), line("+20000.00", .75, .2),
                      line("银 08-06 08:46", .04, .23)])["rows"][0]
    assert row["business_type"] == "DEPOSIT"
    assert row["trade_date"] == "2026-08-06"
    assert row["amount_cents"] == 2000000


def test_duplicate_rows_retained():
    lines = trade() + [{**item, "y": item["y"] + .1} for item in trade(False)]
    assert len(parse_lines(lines)["rows"]) == 2


def test_arithmetic_mismatch_warns():
    lines = trade()
    lines[3]["text"] = "-899.01"
    assert "不一致" in parse_lines(lines)["rows"][0]["warnings"][0]


@pytest.mark.parametrize("images", [[], [b"not image"], [b"x"] * 9, [b"x" * (8 * 1024 * 1024 + 1)]])
def test_invalid_images(images):
    with pytest.raises(ValueError):
        recognize_images(images)


def test_pixel_limit():
    buffer = io.BytesIO()
    Image.new("1", (16001, 1)).save(buffer, "PNG")
    with pytest.raises(ValueError, match="像素"):
        recognize_images([buffer.getvalue()])


def test_timeout_cleans_temporary_image(monkeypatch, tmp_path):
    import subprocess
    import portfolio.screenshot_parser as parser
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "PNG")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ocr", 30)

    monkeypatch.setattr(parser.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="超时"):
        parser._recognize(buffer.getvalue(), tmp_path, tmp_path / "binary", 0)
    assert not (tmp_path / "image-0.png").exists()


def test_invalid_calendar_date_is_not_guessed():
    lines = trade()
    lines[4]["text"] = "买 02-30 09:42"
    row = parse_lines(lines)["rows"][0]
    assert row["trade_date"] is None
    assert row["warnings"]


def test_lock_prevents_parallel_ocr():
    import portfolio.screenshot_parser as parser
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "PNG")
    parser._OCR_LOCK.acquire()
    try:
        with pytest.raises(ValueError, match="正在识别"):
            recognize_images([buffer.getvalue()])
    finally:
        parser._OCR_LOCK.release()
