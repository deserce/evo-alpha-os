"""
EvoAlpha OS - 财务摘要数据采集器
采集股票的财务业绩报表数据
"""

import random
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

import akshare as ak
import pandas as pd
from sqlalchemy import inspect, text

from app.core.database import get_engine

# 公共工具导入
from data_job.common import (
    setup_backend_path,
    setup_logger,
    setup_network_emergency_kit,
)

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.direct_market_sources import (
    EastmoneyDirectClient,
    normalize_symbol,
    safe_float,
)

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


# Keep the existing compact summary table and extend it in place.  SQLite only
# stores one row per stock/report period, which is sufficient for both the
# cumulative and Eastmoney's real single-quarter series.
FINANCE_COLUMN_TYPES: dict[str, str] = {
    "code": "VARCHAR(20)",
    "name": "VARCHAR(50)",
    "report_date": "DATE",
    "report_type": "VARCHAR(20)",
    "report_date_name": "VARCHAR(50)",
    "notice_date": "DATE",
    "update_date": "DATE",
    "source": "VARCHAR(50)",
    "fetched_at": "TIMESTAMP",
    # Existing compatibility columns.
    "eps": "FLOAT",
    "net_profit_up": "FLOAT",
    "revenue_up": "FLOAT",
    "roe": "FLOAT",
    "total_revenue": "FLOAT",
    "net_profit": "FLOAT",
    "net_margin": "FLOAT",
    # Cumulative F10 metrics.
    "deduct_net_profit": "FLOAT",
    "deduct_net_profit_up": "FLOAT",
    "revenue_qoq": "FLOAT",
    "net_profit_qoq": "FLOAT",
    "deduct_net_profit_qoq": "FLOAT",
    "gross_margin": "FLOAT",
    "roe_diluted": "FLOAT",
    "operating_cashflow_per_share": "FLOAT",
    "bvps": "FLOAT",
    "debt_ratio": "FLOAT",
    "current_ratio": "FLOAT",
    "quick_ratio": "FLOAT",
    # Real single-quarter F10 metrics.  ``*_single`` is the project's
    # canonical API/UI naming convention.
    "total_revenue_single": "FLOAT",
    "net_profit_single": "FLOAT",
    "deduct_net_profit_single": "FLOAT",
    "eps_single": "FLOAT",
    "roe_single": "FLOAT",
    "gross_margin_single": "FLOAT",
    "net_margin_single": "FLOAT",
    "operating_cashflow_per_share_single": "FLOAT",
    "total_revenue_single_yoy": "FLOAT",
    "total_revenue_single_qoq": "FLOAT",
    "net_profit_single_yoy": "FLOAT",
    "net_profit_single_qoq": "FLOAT",
    "deduct_net_profit_single_yoy": "FLOAT",
    "deduct_net_profit_single_qoq": "FLOAT",
}

FINANCE_COLUMNS = tuple(FINANCE_COLUMN_TYPES)


class FinanceSummaryCollector(BaseCollector):
    """财务摘要数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="finance_summary",
            request_timeout=30,
            request_delay=2,
            max_retries=3
        )
        self.engine = get_engine()
        self.table_name = "stock_finance_summary"
        self.f10_client = EastmoneyDirectClient(min_interval=0.35, timeout=12, retries=1)

    def _init_table(self):
        """初始化表结构"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建表 {self.table_name}...")
            definitions = ",\n".join(
                f"{column} {column_type}"
                for column, column_type in FINANCE_COLUMN_TYPES.items()
            )
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        {definitions},
                        PRIMARY KEY (code, report_date)
                    );
                """))
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS idx_finance_code "
                        f"ON {self.table_name} (code);"
                    )
                )
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS idx_finance_date "
                        f"ON {self.table_name} (report_date);"
                    )
                )
        else:
            logger.info(f"✅ 表 {self.table_name} 已存在，准备检查更新...")
            columns = {column["name"] for column in inspector.get_columns(self.table_name)}
            with self.engine.begin() as conn:
                for column, column_type in FINANCE_COLUMN_TYPES.items():
                    if column in columns:
                        continue
                    conn.execute(
                        text(
                            f"ALTER TABLE {self.table_name} "
                            f"ADD COLUMN {column} {column_type}"
                        )
                    )
                    columns.add(column)

    def check_date_exists(self, report_date_str: str) -> bool:
        """检查某个季度的数据是否已入库"""
        fmt_date = pd.to_datetime(report_date_str).strftime('%Y-%m-%d')
        try:
            with self.engine.connect() as conn:
                columns = {
                    column["name"]
                    for column in inspect(self.engine).get_columns(self.table_name)
                }
                if {"total_revenue", "net_profit"} - columns:
                    return False
                query = text(f"""
                    SELECT
                        COUNT(*) AS row_count,
                        SUM(
                            CASE
                                WHEN total_revenue IS NOT NULL
                                     AND net_profit IS NOT NULL
                                THEN 1
                                ELSE 0
                            END
                        ) AS complete_count
                    FROM {self.table_name}
                    WHERE report_date = :dt
                """)
                row = conn.execute(query, {"dt": fmt_date}).mappings().first()
                count = int(row["row_count"] or 0) if row else 0
                complete_count = int(row["complete_count"] or 0) if row else 0
                return count >= 1000 and complete_count >= 1000
        except Exception:
            return False

    @staticmethod
    def _build_report_dates(years: int = 6) -> list[str]:
        """生成最近若干年的已到期财报日期。"""
        today = datetime.now().astimezone().date()
        today_str = today.strftime("%Y%m%d")
        curr_year = today.year
        quarters = ["1231", "0930", "0630", "0331"]
        report_dates = []
        for year in range(curr_year, curr_year - years, -1):
            for quarter in quarters:
                report_date = f"{year}{quarter}"
                if report_date <= today_str:
                    report_dates.append(report_date)
        return report_dates

    @staticmethod
    def _filter_a_share_codes(df: pd.DataFrame) -> pd.DataFrame:
        """过滤掉 B 股等非 A 股代码，避免污染股票池基础数据。"""
        return df[df["code"].astype(str).str.match(r"^(00|30|60|68|83|87|43|92)\d{4}$")].copy()

    def fetch_report_dataframe(self, target_date: str) -> pd.DataFrame:
        """抓取并清洗某个报告期的财务摘要，不直接写库。"""
        with self._timeout_context(self.request_timeout):
            df = self._retry_call(ak.stock_yjbb_em, date=target_date)
        if df is None or df.empty:
            return pd.DataFrame()

        rename_map = {
            '股票代码': 'code', '股票简称': 'name',
            '每股收益': 'eps', '净利润-同比增长': 'net_profit_up',
            '营业总收入-同比增长': 'revenue_up', '净资产收益率': 'roe',
            '营业总收入-营业总收入': 'total_revenue',
            '营业收入-营业收入': 'total_revenue',
            '净利润-净利润': 'net_profit',
            '销售毛利率': 'gross_margin',
            '每股经营现金流量': 'operating_cashflow_per_share',
            '每股净资产': 'bvps',
        }
        for source_column, target_column in rename_map.items():
            if source_column not in df.columns:
                continue
            if target_column in df.columns:
                df[target_column] = df[target_column].combine_first(df[source_column])
                df = df.drop(columns=[source_column])
            else:
                df = df.rename(columns={source_column: target_column})

        required_cols = [
            'code', 'name', 'eps', 'net_profit_up', 'revenue_up',
            'roe', 'total_revenue', 'net_profit', 'gross_margin',
            'operating_cashflow_per_share', 'bvps'
        ]
        for col in required_cols:
            if col not in df.columns:
                df[col] = None

        df_save = df[required_cols].copy()
        df_save['report_date'] = pd.to_datetime(target_date).date()
        df_save['code'] = df_save['code'].astype(str).str.zfill(6)
        df_save = df_save.replace(['-', '--', ''], pd.NA)

        num_cols = [
            'eps', 'net_profit_up', 'revenue_up', 'roe', 'total_revenue',
            'net_profit', 'gross_margin', 'operating_cashflow_per_share', 'bvps'
        ]
        for col in num_cols:
            df_save[col] = pd.to_numeric(df_save[col], errors='coerce')

        df_save['net_margin'] = pd.NA
        valid_revenue = df_save['total_revenue'].notna() & (df_save['total_revenue'] != 0)
        df_save.loc[valid_revenue, 'net_margin'] = (
            df_save.loc[valid_revenue, 'net_profit']
            / df_save.loc[valid_revenue, 'total_revenue']
            * 100
        )
        df_save = self._filter_a_share_codes(df_save)
        df_save = df_save[
            [
                'code', 'name', 'report_date', 'eps', 'net_profit_up', 'revenue_up',
                'roe', 'total_revenue', 'net_profit', 'net_margin', 'gross_margin',
                'operating_cashflow_per_share', 'bvps'
            ]
        ]
        return df_save.where(pd.notnull(df_save), None)

    @staticmethod
    def _raw_value(row: Mapping[str, Any], *keys: str) -> Any:
        """Return the first present, non-blank value while retaining zero."""
        for key in keys:
            if key not in row:
                continue
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            try:
                if pd.isna(value):
                    continue
            except (TypeError, ValueError):
                pass
            return value
        return None

    @classmethod
    def _number(cls, row: Mapping[str, Any], *keys: str) -> float | None:
        return safe_float(cls._raw_value(row, *keys))

    @classmethod
    def _text_value(cls, row: Mapping[str, Any], *keys: str) -> str | None:
        value = cls._raw_value(row, *keys)
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    @classmethod
    def _date_value(cls, row: Mapping[str, Any], *keys: str) -> str | None:
        value = cls._raw_value(row, *keys)
        if value is None:
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")

    @staticmethod
    def _infer_report_labels(report_date: str) -> tuple[str, str]:
        year = report_date[:4]
        suffix_map = {
            "03-31": ("一季报", f"{year}一季报"),
            "06-30": ("中报", f"{year}中报"),
            "09-30": ("三季报", f"{year}三季报"),
            "12-31": ("年报", f"{year}年报"),
        }
        return suffix_map.get(report_date[5:], ("财报", report_date))

    @classmethod
    def _row_matches_symbol(cls, row: Mapping[str, Any], code: str) -> bool:
        raw_code = cls._text_value(row, "SECURITY_CODE", "SECUCODE")
        return raw_code is None or normalize_symbol(raw_code) == code

    @classmethod
    def _cumulative_f10_record(
        cls,
        row: Mapping[str, Any],
        *,
        code: str,
        fetched_at: str,
    ) -> dict[str, Any] | None:
        report_date = cls._date_value(row, "REPORT_DATE")
        if not report_date or not cls._row_matches_symbol(row, code):
            return None
        inferred_type, inferred_date_name = cls._infer_report_labels(report_date)
        total_revenue_yoy = cls._number(row, "TOTALOPERATEREVETZ")
        net_profit_yoy = cls._number(row, "PARENTNETPROFITTZ")
        return {
            "code": code,
            "name": cls._text_value(row, "SECURITY_NAME_ABBR"),
            "report_date": report_date,
            "report_type": cls._text_value(row, "REPORT_TYPE") or inferred_type,
            "report_date_name": (
                cls._text_value(row, "REPORT_DATE_NAME") or inferred_date_name
            ),
            "notice_date": cls._date_value(row, "NOTICE_DATE"),
            "update_date": cls._date_value(row, "UPDATE_DATE"),
            "source": "eastmoney_f10",
            "fetched_at": fetched_at,
            "eps": cls._number(row, "EPSJB"),
            "roe": cls._number(row, "ROEJQ"),
            "total_revenue": cls._number(row, "TOTALOPERATEREVE"),
            "net_profit": cls._number(row, "PARENTNETPROFIT"),
            "deduct_net_profit": cls._number(
                row,
                "KCFJCXSYJLR",
                "DEDU_PARENT_PROFIT",
            ),
            "revenue_up": total_revenue_yoy,
            "revenue_qoq": cls._number(
                row,
                "YYZSRGDHBZC",
                "TOTALOPERATEREVEHBZC",
            ),
            "net_profit_up": net_profit_yoy,
            "net_profit_qoq": cls._number(
                row,
                "NETPROFITRPHBZC",
                "PARENTNETPROFITHBZC",
            ),
            "deduct_net_profit_up": cls._number(
                row,
                "KCFJCXSYJLRTZ",
                "DPNP_YOY_RATIO",
            ),
            "deduct_net_profit_qoq": cls._number(
                row,
                "KFJLRGDHBZC",
                "KCFJCXSYJLRHBZC",
            ),
            "gross_margin": cls._number(row, "XSMLL", "GROSS_PROFIT_RATIO"),
            "roe_diluted": cls._number(row, "ROE_DILUTED"),
            "net_margin": cls._number(row, "XSJLL", "NET_PROFIT_RATIO"),
            "operating_cashflow_per_share": cls._number(
                row,
                "MGJYXJJE",
                "PER_NETCASH",
            ),
            "bvps": cls._number(row, "BPS"),
            "debt_ratio": cls._number(row, "ZCFZL"),
            "current_ratio": cls._number(row, "LD", "CURRENT_RATIO"),
            "quick_ratio": cls._number(row, "SD", "QUICK_RATIO"),
        }

    @classmethod
    def _single_quarter_f10_record(
        cls,
        row: Mapping[str, Any],
        *,
        code: str,
    ) -> dict[str, Any] | None:
        report_date = cls._date_value(row, "REPORT_DATE")
        if not report_date or not cls._row_matches_symbol(row, code):
            return None
        return {
            "report_date": report_date,
            "name": cls._text_value(row, "SECURITY_NAME_ABBR"),
            "total_revenue_single": cls._number(row, "TOTALOPERATEREVE"),
            "net_profit_single": cls._number(row, "PARENTNETPROFIT"),
            "deduct_net_profit_single": cls._number(
                row,
                "DEDU_PARENT_PROFIT",
                "KCFJCXSYJLR",
            ),
            "eps_single": cls._number(row, "EPSJB"),
            "roe_single": cls._number(row, "ROE_DILUTED", "ROEJQ"),
            "gross_margin_single": cls._number(
                row,
                "GROSS_PROFIT_RATIO",
                "XSMLL",
            ),
            "net_margin_single": cls._number(
                row,
                "NET_PROFIT_RATIO",
                "XSJLL",
            ),
            "operating_cashflow_per_share_single": cls._number(
                row,
                "PER_NETCASH",
                "MGJYXJJE",
            ),
            "total_revenue_single_yoy": cls._number(row, "TOTALOPERATEREVETZ"),
            "total_revenue_single_qoq": cls._number(
                row,
                "YYZSRGDHBZC",
                "TOTALOPERATEREVEHBZC",
            ),
            "net_profit_single_yoy": cls._number(row, "PARENTNETPROFITTZ"),
            "net_profit_single_qoq": cls._number(
                row,
                "NETPROFITRPHBZC",
                "PARENTNETPROFITHBZC",
            ),
            "deduct_net_profit_single_yoy": cls._number(
                row,
                "DPNP_YOY_RATIO",
                "KCFJCXSYJLRTZ",
            ),
            "deduct_net_profit_single_qoq": cls._number(
                row,
                "KFJLRGDHBZC",
                "KCFJCXSYJLRHBZC",
            ),
        }

    def fetch_symbol_dataframe(self, symbol: str) -> pd.DataFrame:
        """Fetch and merge all available F10 periods for one stock."""
        code = normalize_symbol(symbol)
        payload = self.f10_client.stock_f10_main_finance(code)
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        records: dict[str, dict[str, Any]] = {}

        for raw_row in payload.get("cumulative") or []:
            record = self._cumulative_f10_record(
                raw_row,
                code=code,
                fetched_at=fetched_at,
            )
            if record:
                records[record["report_date"]] = record

        for raw_row in payload.get("quarterly") or []:
            single_record = self._single_quarter_f10_record(raw_row, code=code)
            if not single_record:
                continue
            report_date = single_record.pop("report_date")
            if report_date not in records:
                report_type, report_date_name = self._infer_report_labels(report_date)
                records[report_date] = {
                    "code": code,
                    "report_date": report_date,
                    "report_type": report_type,
                    "report_date_name": report_date_name,
                    "source": "eastmoney_f10",
                    "fetched_at": fetched_at,
                }
            for key, value in single_record.items():
                if value is not None or key not in records[report_date]:
                    records[report_date][key] = value

        if not records:
            return pd.DataFrame(columns=FINANCE_COLUMNS)
        ordered = [records[key] for key in sorted(records, reverse=True)]
        frame = pd.DataFrame(ordered)
        return frame.where(pd.notnull(frame), None)

    @staticmethod
    def _clean_db_value(value: Any) -> Any:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if hasattr(value, "item"):
            try:
                return value.item()
            except (TypeError, ValueError):
                pass
        return value

    def _upsert_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Partially update rows: null preserves old values, zero overwrites."""
        prepared: list[dict[str, Any]] = []
        for row in rows:
            code = normalize_symbol(str(row.get("code") or ""))
            report_date = self._date_value(row, "report_date")
            if not report_date or code == "000000":
                continue
            cleaned = {
                column: self._clean_db_value(row.get(column))
                for column in FINANCE_COLUMNS
            }
            cleaned["code"] = code
            cleaned["report_date"] = report_date
            prepared.append(cleaned)
        if not prepared:
            return 0

        insert_columns = ", ".join(FINANCE_COLUMNS)
        bind_columns = ", ".join(f":{column}" for column in FINANCE_COLUMNS)
        update_columns = [
            column for column in FINANCE_COLUMNS if column not in {"code", "report_date"}
        ]
        update_sql = ", ".join(
            f"{column}=COALESCE(excluded.{column}, {self.table_name}.{column})"
            for column in update_columns
        )
        statement = text(
            f"""
            INSERT INTO {self.table_name} ({insert_columns})
            VALUES ({bind_columns})
            ON CONFLICT (code, report_date) DO UPDATE SET {update_sql}
            """
        )
        with self.engine.begin() as conn:
            for start in range(0, len(prepared), 100):
                conn.execute(statement, prepared[start:start + 100])
        return len(prepared)

    def fetch_and_save(self, target_date: str) -> bool:
        """核心抓取逻辑"""
        try:
            df_save = self.fetch_report_dataframe(target_date)

            if df_save.empty:
                return False

            rows = df_save.to_dict(orient="records")
            return self._upsert_rows(rows) > 0

        except Exception as e:
            logger.error(f"抓取 {target_date} 异常: {e}")
            raise

    def symbol_report_complete(self, symbol: str, target_date: str) -> bool:
        """检查单只股票某报告期是否已有绝对营收/净利。"""
        fmt_date = pd.to_datetime(target_date).strftime('%Y-%m-%d')
        code = str(symbol).strip().zfill(6)
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"""
                    SELECT total_revenue, net_profit
                    FROM {self.table_name}
                    WHERE code = :code AND report_date = :dt
                    LIMIT 1
                """),
                {"code": code, "dt": fmt_date},
            ).mappings().first()
        return bool(
            row
            and row["total_revenue"] is not None
            and row["net_profit"] is not None
        )

    def fetch_symbol_and_save(self, symbol: str, target_date: str) -> bool:
        """补齐单只股票某报告期，保留旧公开方法兼容。"""
        frame = self.fetch_symbol_dataframe(symbol)
        if frame.empty:
            return False
        wanted_date = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        selected = frame[frame["report_date"] == wanted_date]
        return self._upsert_rows(selected.to_dict(orient="records")) > 0

    def backfill_symbol(
        self,
        symbol: str,
        *,
        years: int = 6,
        dates: Iterable[str] | None = None,
    ) -> dict:
        """用两次有界 F10 请求补齐单股近几年数据。"""
        self._init_table()
        report_dates = list(dates or self._build_report_dates(years=years))
        normalized_dates = {
            pd.to_datetime(target_date).strftime("%Y-%m-%d")
            for target_date in report_dates
        }
        result = {
            "checked": len(normalized_dates),
            "updated": 0,
            "skipped": 0,
            "failed": [],
        }
        try:
            frame = self.fetch_symbol_dataframe(symbol)
            if not frame.empty:
                frame = frame[frame["report_date"].isin(normalized_dates)]
                result["updated"] = self._upsert_rows(frame.to_dict(orient="records"))
            result["skipped"] = max(0, result["checked"] - result["updated"])
        except Exception as exc:  # noqa: BLE001 - keep update endpoint best-effort.
            logger.warning("补齐 %s 财务摘要失败: %s", symbol, exc)
            result["skipped"] = result["checked"]
            result["failed"].append({"date": "all", "error": str(exc)})
        return result

    def run(self):
        """执行财务数据采集"""
        self.log_collection_start()
        logger.info("📈 启动财务业绩报表同步...")

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_table()

        date_tasks = self._build_report_dates(years=6)

        total = len(date_tasks)
        success_count = 0

        for i, target_date in enumerate(date_tasks):
            if self.check_date_exists(target_date):
                print(f"[{i+1}/{total}] ⏩ {target_date} 已存在，跳过...", end="\r")
                success_count += 1
                continue

            max_retries = 3
            success = False

            for attempt in range(max_retries):
                try:
                    logger.info(
                        f"[{i+1}/{total}] ⏳ 正在抓取 "
                        f"{target_date} (Try {attempt+1})..."
                    )
                    has_data = self.fetch_and_save(target_date)

                    if has_data:
                        logger.info(f"   ✅ {target_date} 入库成功")
                        success = True
                        success_count += 1
                    else:
                        logger.warning(f"   ⚠️ {target_date} 无数据 (可能是财报未出)")
                        success = True

                    time.sleep(random.uniform(2, 4))
                    break

                except Exception:
                    time.sleep(5 * (attempt + 1))

            if not success:
                logger.error(f"   ❌ {target_date} 多次重试失败，跳过。")

        logger.info(f"🎉 财务数据同步完成！成功: {success_count}/{total}")
        self.log_collection_end(True, f"成功 {success_count}/{total} 个季度")


if __name__ == "__main__":
    collector = FinanceSummaryCollector()
    collector.run()
