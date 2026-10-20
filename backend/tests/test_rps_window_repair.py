from datetime import date

import pandas as pd

from quant_engine.config.calculator_config import CalculatorConfig
from quant_engine.core.base_feature_calculator import BaseFeatureCalculator


class DummyRPSCalculator(BaseFeatureCalculator):
    def __init__(self):
        self.config = CalculatorConfig()
        self.target_table = "quant_feature_dummy_rps"
        self.source_table = "dummy_daily_prices"
        self.entity_column = "symbol"
        self.periods = self.get_periods()
        self.init_days = None
        self.loaded_start_dates = []
        self.saved_dates = []

    def get_source_table(self) -> str:
        return "dummy_daily_prices"

    def get_target_table(self) -> str:
        return "quant_feature_dummy_rps"

    def get_entity_column(self) -> str:
        return "symbol"

    def get_periods(self) -> list[int]:
        return [5, 10, 20, 50, 120, 250]

    def _init_table(self):
        return None

    def _get_last_calculated_date(self):
        return date(2026, 5, 8)

    def _get_last_source_date(self):
        return date(2026, 5, 25)

    def run_init(self, days=400):
        self.init_days = days

    def load_data(self, start_date=None):
        self.loaded_start_dates.append(start_date)
        return pd.DataFrame(
            {
                "symbol": ["000001", "000001", "000001", "000001"],
                "trade_date": pd.to_datetime([
                    "2026-05-10",
                    "2026-05-18",
                    "2026-05-20",
                    "2026-05-25",
                ]),
                "close": [10.0, 11.0, 12.0, 13.0],
            }
        )

    def compute_features_for_dates(self, df, target_dates):
        return df[df["trade_date"].dt.date.isin(target_dates)].assign(
            chg_250=0.1,
            rps_250=88.8,
        )

    def save_to_db(self, df, mode="append"):
        self.saved_dates.append((df["trade_date"].dt.strftime("%Y-%m-%d").tolist(), mode))


def test_large_rps_gap_uses_full_incremental_window_for_long_periods():
    calculator = DummyRPSCalculator()

    calculator.run_daily()

    assert calculator.init_days is None
    assert calculator.loaded_start_dates == ["2025-04-13"]
    assert calculator.saved_dates == [(["2026-05-20", "2026-05-25"], "append")]
