import pytest
from fastapi import HTTPException

from app.api.v1.quant import _build_task_command


def test_build_mootdx_backfill_command_with_source_and_symbol():
    command = _build_task_command(
        "kline_backfill",
        {
            "source": "mootdx_qfq",
            "start_date": "2026-04-01",
            "end_date": "2026-04-27",
            "symbol": "1",
            "workers": 2,
            "pool_size": 1,
            "max_bars": 88,
            "submit_window": 12,
            "market": "SZ",
            "diagnostic_only": True,
            "full_refresh": "false",
        },
    )

    assert "scripts/standalone/collect_stock_kline_mootdx_qfq.py" in command
    assert command[command.index("--start") + 1] == "2026-04-01"
    assert command[command.index("--end") + 1] == "2026-04-27"
    assert command[command.index("--symbol") + 1] == "000001"
    assert command[command.index("--max-bars") + 1] == "88"
    assert command[command.index("--submit-window") + 1] == "12"
    assert command[command.index("--market") + 1] == "SZ"
    assert "--diagnostic-only" in command
    assert "--full-refresh" not in command


def test_kline_task_rejects_http_or_legacy_production_sources():
    for source in ("multi_source_auto", "akshare_legacy"):
        with pytest.raises(HTTPException) as exc_info:
            _build_task_command("kline_backfill", {"source": source})
        assert "不允许写正式日K" in str(exc_info.value.detail)


def test_build_rps_command_allows_calculator_subset():
    command = _build_task_command(
        "rps_calculation",
        {"mode": "daily", "calculators": ["stock", "sector"]},
    )

    assert command[:4][-2:] == ["quant_engine.runner.feature_runner", "--mode"]
    assert command[-2:] == ["stock", "sector"]


def test_build_workflow_check_is_check_only():
    command = _build_task_command("workflow_check", {})

    assert command[-2:] == ["scripts/run_daily_data_workflow.py", "--check-only"]


def test_build_mrgc_recalculation_command_for_trade_date():
    command = _build_task_command(
        "mrgc_recalculation",
        {"trade_date": "2026-07-14"},
    )

    assert command[-5:] == [
        "quant_engine.runner.strategy_runner",
        "--strategy",
        "mrgc",
        "--date",
        "2026-07-14",
    ]


def test_mrgc_recalculation_requires_trade_date():
    with pytest.raises(HTTPException):
        _build_task_command("mrgc_recalculation", {})
