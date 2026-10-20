from datetime import date
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import create_engine, text

from quant_engine.core.base_feature_calculator import BaseFeatureCalculator
from quant_engine.runner.feature_runner import FeatureRunner


class DummyCalculator(BaseFeatureCalculator):
    def get_source_table(self) -> str:
        return "stock_daily_prices"

    def get_target_table(self) -> str:
        return "quant_feature_rps"

    def get_entity_column(self) -> str:
        return "symbol"

    def get_periods(self) -> list[int]:
        return [250]


class _FakeConnection:
    def execute(self, *_args, **_kwargs):
        return None


class _FakeBegin:
    def __enter__(self):
        return _FakeConnection()

    def __exit__(self, *_args):
        return False


class _FakeEngine:
    def begin(self):
        return _FakeBegin()


def _calculator_without_init() -> DummyCalculator:
    calculator = object.__new__(DummyCalculator)
    calculator.engine = _FakeEngine()
    calculator.config = SimpleNamespace(
        INCREMENTAL_WINDOW_DAYS=400,
        DAILY_MAX_BACKFILL_DAYS=7,
        CHUNK_SIZE=100,
    )
    calculator.source_table = "stock_daily_prices"
    calculator.target_table = "quant_feature_rps"
    calculator.entity_column = "symbol"
    calculator.periods = [250]
    calculator._init_table = lambda: None
    return calculator


def test_run_init_defaults_to_full_source_history():
    calculator = _calculator_without_init()
    loaded_start_dates = []

    def load_data(start_date=None):
        loaded_start_dates.append(start_date)
        return pd.DataFrame(
            {
                "symbol": ["000001"],
                "trade_date": [pd.Timestamp("2026-01-01")],
                "close": [10.0],
            }
        )

    calculator.load_data = load_data
    calculator.compute_features = lambda df: df
    calculator.save_to_db = lambda df, mode="append": None
    calculator._get_last_source_date = lambda: (_ for _ in ()).throw(AssertionError("should not be called"))

    calculator.run_init()

    assert loaded_start_dates == [None]


def test_run_daily_large_gap_only_saves_recent_backfill_window():
    calculator = _calculator_without_init()
    loaded_start_dates = []
    saved = []

    calculator._get_last_calculated_date = lambda: date(2026, 1, 1)
    calculator._get_last_source_date = lambda: date(2026, 1, 20)
    calculator.run_init = lambda days=None: (_ for _ in ()).throw(AssertionError("should not run init"))

    def load_data(start_date=None):
        loaded_start_dates.append(start_date)
        return pd.DataFrame(
            {
                "symbol": ["000001", "000001", "000001", "000001"],
                "trade_date": pd.to_datetime([
                    "2026-01-10",
                    "2026-01-13",
                    "2026-01-14",
                    "2026-01-20",
                ]),
                "close": [10.0, 11.0, 12.0, 13.0],
            }
        )

    def save_to_db(df, mode="append"):
        saved.append((df["trade_date"].dt.strftime("%Y-%m-%d").tolist(), mode))

    calculator.load_data = load_data
    calculator.compute_features_for_dates = (
        lambda df, target_dates: df[df["trade_date"].dt.date.isin(target_dates)].assign(
            chg_250=0.1,
            rps_250=88.8,
        )
    )
    calculator.save_to_db = save_to_db

    calculator.run_daily()

    assert loaded_start_dates == ["2024-12-09"]
    assert saved == [(["2026-01-14", "2026-01-20"], "append")]


def test_target_only_rps_matches_legacy_vectorized_result():
    calculator = _calculator_without_init()
    calculator.periods = [5, 20, 250]
    dates = pd.bdate_range("2025-01-01", periods=280)
    rows = []
    for symbol, drift in (("000001", 0.001), ("000002", 0.002), ("600000", -0.0005)):
        for index, trade_date in enumerate(dates):
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "close": 10 * (1 + drift) ** index,
                }
            )
    source = pd.DataFrame(rows)
    target = dates[-1].date()

    legacy = calculator.compute_features(source)
    expected = legacy[legacy["trade_date"].dt.date == target].sort_values("symbol")
    actual = calculator.compute_features_for_dates(source, [target]).sort_values("symbol")

    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True),
        expected.reset_index(drop=True),
        check_dtype=False,
    )


def test_target_only_rps_keeps_new_stock_rps250_null():
    calculator = _calculator_without_init()
    calculator.periods = [5, 250]
    dates = pd.bdate_range("2025-01-01", periods=280)
    established = pd.DataFrame(
        {"symbol": "000001", "trade_date": dates, "close": range(100, 380)}
    )
    newcomer = pd.DataFrame(
        {"symbol": "000002", "trade_date": dates[-10:], "close": range(10, 20)}
    )

    result = calculator.compute_features_for_dates(
        pd.concat([established, newcomer], ignore_index=True),
        [dates[-1].date()],
    ).set_index("symbol")

    assert pd.notna(result.loc["000002", "rps_5"])
    assert pd.isna(result.loc["000002", "rps_250"])


def test_recent_internal_rps_gap_is_selected_even_when_max_date_is_fresh():
    engine = create_engine("sqlite:///:memory:")
    dates = pd.bdate_range("2026-06-01", periods=10)
    missing_date = dates[-3].date()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, close REAL)"))
        conn.execute(text("CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT)"))
        for trade_date in dates:
            conn.execute(
                text("INSERT INTO stock_daily_prices VALUES ('000001', :trade_date, 10)"),
                {"trade_date": trade_date.strftime("%Y-%m-%d")},
            )
            if trade_date.date() != missing_date:
                conn.execute(
                    text("INSERT INTO quant_feature_rps VALUES ('000001', :trade_date)"),
                    {"trade_date": trade_date.strftime("%Y-%m-%d")},
                )

    calculator = _calculator_without_init()
    calculator.engine = engine

    assert calculator._get_missing_target_dates(max_days=7) == [missing_date]


def test_explicit_target_dates_missing_only_excludes_existing_dates():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, close REAL)"))
        conn.execute(text("CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT)"))
        conn.execute(text("INSERT INTO stock_daily_prices VALUES ('000001', '2026-07-13', 10)"))
        conn.execute(text("INSERT INTO stock_daily_prices VALUES ('000001', '2026-07-14', 11)"))
        conn.execute(text("INSERT INTO quant_feature_rps VALUES ('000001', '2026-07-13')"))

    calculator = _calculator_without_init()
    calculator.engine = engine

    assert calculator._filter_source_target_dates(
        ["2026-07-13", "2026-07-14"],
        missing_only=True,
    ) == [date(2026, 7, 14)]


def test_feature_runner_forwards_explicit_dates_to_dense_analytics():
    calls = []

    class FakeCalculator:
        def run_daily(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "records": 1}

    runner = object.__new__(FeatureRunner)
    runner.calculators = {"strength": FakeCalculator()}

    result = runner.run(
        calculator_names=["strength"],
        mode="daily",
        target_dates=["2026-07-15", "2026-07-16"],
        missing_only=True,
    )

    assert result["strength"]["success"] is True
    assert calls == [
        {
            "target_dates": ["2026-07-15", "2026-07-16"],
            "missing_only": True,
        }
    ]


def test_save_to_db_replace_removes_old_rows_after_new_data_is_ready():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE quant_feature_rps (
                symbol TEXT,
                trade_date TEXT,
                chg_250 FLOAT,
                rps_250 FLOAT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        ))
        conn.execute(text(
            """
            INSERT INTO quant_feature_rps (symbol, trade_date, chg_250, rps_250)
            VALUES ('old', '2025-01-01', 0.1, 1.0)
            """
        ))

    calculator = _calculator_without_init()
    calculator.engine = engine
    df = pd.DataFrame(
        {
            "symbol": ["000001"],
            "trade_date": ["2026-01-02 00:00:00.000000"],
            "chg_250": [0.42],
            "rps_250": [88.8],
        }
    )

    calculator.save_to_db(df, mode="replace")

    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT symbol, trade_date, chg_250, rps_250
            FROM quant_feature_rps
            ORDER BY symbol
            """
        )).fetchall()

    assert rows == [("000001", "2026-01-02 00:00:00.000000", 0.42, 88.8)]
