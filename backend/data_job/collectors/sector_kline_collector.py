"""EvoAlpha OS - 板块K线数据采集器。"""

import argparse
import math
import time
from datetime import datetime, timedelta
from datetime import time as datetime_time
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
import requests
from sqlalchemy import text

from app.core.database import get_engine

# 公共工具导入
from data_job.common import (
    setup_backend_path,
    setup_logger,
    setup_network_emergency_kit,
)

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.eastmoney_request_gate import (
    EastmoneyBudgetExceeded,
    EastmoneyCircuitOpen,
    EastmoneyGateError,
    EastmoneyRequestError,
    EastmoneyRequestGate,
)

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

# Kept for read-only legacy validation helpers.  No synthetic frame is
# accepted by the vendor collector or written to sector_daily_prices.
SYNTHETIC_RETURN_LIMIT_PCT = 30.0
SYNTHETIC_PCT_TOLERANCE = 0.45
EASTMONEY_VOLUME_MULTIPLIER = 100.0
EASTMONEY_BULK_PAGE_SIZE = 100
EASTMONEY_BULK_MAX_PAGES = 20
EASTMONEY_BULK_MIN_COVERAGE = 0.95
EASTMONEY_SHANGHAI = ZoneInfo("Asia/Shanghai")
EASTMONEY_BULK_FIELDS = "f12,f14,f2,f3,f5,f6,f15,f16,f17,f18,f124"


def normalize_eastmoney_volume(values: pd.Series) -> pd.Series:
    """将东方财富板块接口的成交量（手）统一转换为成交股数。"""
    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    return numeric.astype(float).mul(EASTMONEY_VOLUME_MULTIPLIER)


def validate_synthetic_sector_frame(
    frame: pd.DataFrame,
    *,
    previous_close: float | None = None,
) -> tuple[bool, str]:
    """Validate a synthetic sector series before it can replace stored rows."""
    required = {"trade_date", "open", "close", "high", "low", "pct_chg"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        return False, f"missing_columns={','.join(missing)}"
    if frame.empty:
        return False, "empty_frame"

    rows = frame.copy().sort_values("trade_date")
    for column in ("open", "close", "high", "low", "pct_chg"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    if rows[["open", "close", "high", "low", "pct_chg"]].isna().any().any():
        return False, "non_numeric_values"

    prior = float(previous_close) if previous_close is not None else None
    for row in rows.itertuples(index=False):
        if row.close <= 0 or row.high < max(row.open, row.close) or row.low > min(
            row.open, row.close
        ):
            return False, f"invalid_ohlc@{row.trade_date}"
        if abs(float(row.pct_chg)) > SYNTHETIC_RETURN_LIMIT_PCT:
            return False, f"pct_chg_out_of_range@{row.trade_date}:{row.pct_chg}"
        if prior is not None and prior > 0:
            implied_pct = (float(row.close) / prior - 1) * 100
            if abs(implied_pct - float(row.pct_chg)) > SYNTHETIC_PCT_TOLERANCE:
                return False, (
                    f"pct_chg_mismatch@{row.trade_date}:"
                    f"stored={row.pct_chg},implied={implied_pct:.4f}"
                )
        prior = float(row.close)
    return True, ""


class SectorKlineCollector(BaseCollector):
    """板块K线数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="sector_kline",
            request_timeout=30,
            # Eastmoney pacing is enforced by the shared cross-process gate.
            # Keeping this zero avoids a second, uncoordinated retry loop.
            request_delay=0.0,
            max_retries=1,
            enable_anti_ban=False,
        )
        # 健康检查也必须直连，避免本机可选代理失效时在真正数据请求前
        # 误判整个网络不可用。
        self.session.trust_env = False
        self.session.proxies.clear()
        self.engine = get_engine()
        # Vendor OHLC is kept separate from the future local composite net
        # value.  The legacy sector_daily_prices table is intentionally not
        # written by this collector anymore.
        self.table_name = "sector_vendor_daily"
        self._http = requests.Session()
        self._http.trust_env = False
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        self._eastmoney_gate = EastmoneyRequestGate(
            session=self._http,
            timeout=getattr(self, "request_timeout", 30),
        )
        self._eastmoney_kline_failures = 0
        self._eastmoney_kline_disabled = False
        self._eastmoney_last_error = ""

    def _init_table(self):
        """确保目标表存在"""
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    sector_name TEXT,
                    sector_code TEXT,
                    trade_date DATE,
                    open FLOAT,
                    close FLOAT,
                    high FLOAT,
                    low FLOAT,
                    volume FLOAT,
                    amount FLOAT,
                    pct_chg FLOAT,
                    PRIMARY KEY (sector_name, trade_date)
                );
            """))
            existing_price_columns = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({self.table_name})")).fetchall()
            }
            for column, column_type in {
                "sector_code": "TEXT",
                "source": "TEXT",
                "quality_flag": "TEXT",
                "updated_at": "TEXT",
            }.items():
                if column not in existing_price_columns:
                    conn.execute(text(f"ALTER TABLE {self.table_name} ADD COLUMN {column} {column_type}"))
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_sector_date ON {self.table_name} (trade_date);"))
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS idx_sector_code_date "
                        f"ON {self.table_name} (sector_code, trade_date);"
                    )
                )
            except Exception:
                pass
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sector_list (
                    sector_name TEXT PRIMARY KEY,
                    sector_type TEXT,
                    sector_code TEXT,
                    source TEXT,
                    updated_at TEXT
                );
            """))

            existing_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(sector_list)")).fetchall()
            }
            for column, column_type in {
                "sector_type": "TEXT",
                "sector_code": "TEXT",
                "source": "TEXT",
                "updated_at": "TEXT",
            }.items():
                if column not in existing_columns:
                    conn.execute(text(f"ALTER TABLE sector_list ADD COLUMN {column} {column_type}"))

    def _save_sector_list(self, df_sectors: pd.DataFrame) -> None:
        """保存板块列表，供采集和RPS使用同一行业宇宙。"""
        if df_sectors is None or df_sectors.empty:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df_save = df_sectors.copy()
        if "sector_code" not in df_save.columns:
            df_save["sector_code"] = df_save["sector_name"]
        if "source" not in df_save.columns:
            df_save["source"] = "unknown"
        df_save["updated_at"] = now
        df_save = df_save[
            ["sector_name", "sector_type", "sector_code", "source", "updated_at"]
        ].dropna(subset=["sector_name"]).drop_duplicates("sector_name", keep="last")

        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM sector_list"))
            df_save.to_sql("sector_list", conn, if_exists="append", index=False)

    def _fetch_akshare_industry_list(self) -> pd.DataFrame:
        """从东方财富行业板块接口获取真实行业列表。"""
        try:
            df = self._retry_call(ak.stock_board_industry_name_em)
        except Exception as e:
            logger.warning(f"⚠️ AkShare 行业列表获取失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty or "板块名称" not in df.columns:
            return pd.DataFrame()

        code_col = "板块代码" if "板块代码" in df.columns else None
        result = pd.DataFrame({
            "sector_name": df["板块名称"].astype(str).str.strip(),
            "sector_type": "Industry",
            "sector_code": df[code_col].astype(str).str.strip() if code_col else None,
            "source": "akshare",
        })
        result = result[
            (result["sector_name"] != "")
            & ~result["sector_name"].str.contains(r"[\x00-\x1f]", regex=True, na=False)
        ]
        return result.drop_duplicates("sector_name", keep="last")

    def _eastmoney_get_json(self, url: str, params: dict) -> dict:
        """绕过 AkShare 包装层，经过全局闸门读取一次东方财富 JSON。

        The gate owns the single reusable Session and performs at most one
        reconnect retry.  Host rotation and nested ``requests.get`` retries
        are deliberately removed because they previously multiplied traffic
        during an Eastmoney block.
        """
        return self._eastmoney_gate.get(
            url,
            params=params,
            headers=self._headers,
            timeout=self.request_timeout,
        )

    def _fetch_eastmoney_industry_list(self) -> pd.DataFrame:
        """从东方财富直连接口获取行业板块列表。"""
        rows = []
        page_size = 100
        total = None
        try:
            for page in range(1, 20):
                data = self._eastmoney_get_json(
                    "https://17.push2.eastmoney.com/api/qt/clist/get",
                    {
                        "pn": page,
                        "pz": page_size,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": "m:90+t:2+f:!50",
                        "fields": "f12,f14,f13",
                    },
                )
                payload = data.get("data") or {}
                if total is None:
                    total = int(payload.get("total") or 0)
                page_rows = payload.get("diff") or []
                if not page_rows:
                    break
                rows.extend(page_rows)
                if total and len(rows) >= total:
                    break
        except Exception as e:
            logger.warning(f"⚠️ Eastmoney Direct 行业列表获取失败: {e}")
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        result = pd.DataFrame({
            "sector_name": [str(row.get("f14", "")).strip() for row in rows],
            "sector_type": "Industry",
            "sector_code": [str(row.get("f12", "")).strip() for row in rows],
            "source": "eastmoney_direct",
        })
        result = result[
            (result["sector_name"] != "")
            & (result["sector_code"] != "")
            & ~result["sector_name"].str.contains(r"[\x00-\x1f]", regex=True, na=False)
        ]
        return result.drop_duplicates("sector_name", keep="last")

    @staticmethod
    def _parse_target_date(value: str | None):
        if not value:
            return None
        return pd.to_datetime(value, errors="raise").date()

    @staticmethod
    def _yyyymmdd(value) -> str:
        return pd.to_datetime(value).strftime("%Y%m%d")

    @staticmethod
    def _parse_eastmoney_snapshot_timestamp(value):
        """将 clist/get 的 f124 解析为上海时区时间。

        Eastmoney 在不同接口版本中返回过 Unix 秒、Unix 毫秒、
        ``YYYYMMDD``/``YYYYMMDDHHMMSS`` 和 ISO 文本。日期-only 值按当日
        收盘时刻解释；它仍会经过“不能是未来时刻”的检查。
        """
        if value is None or pd.isna(value):
            return None, False

        date_only = False
        raw = value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                return None, False
            if float(value).is_integer():
                raw = str(int(value))

        text_value = str(raw).strip()
        digits = text_value.replace(".", "", 1)
        if digits.isdigit():
            if len(digits) == 8:
                parsed = pd.to_datetime(digits, format="%Y%m%d", errors="coerce")
                date_only = True
            elif len(digits) == 14:
                parsed = pd.to_datetime(digits, format="%Y%m%d%H%M%S", errors="coerce")
            elif len(digits) >= 12 and float(value) >= 1e11:
                parsed = pd.to_datetime(float(value), unit="ms", utc=True, errors="coerce")
            elif len(digits) >= 9:
                parsed = pd.to_datetime(float(value), unit="s", utc=True, errors="coerce")
            else:
                parsed = pd.NaT
        else:
            parsed = pd.to_datetime(text_value, errors="coerce")

        if pd.isna(parsed):
            return None, date_only
        timestamp = pd.Timestamp(parsed)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(EASTMONEY_SHANGHAI)
        else:
            timestamp = timestamp.tz_convert(EASTMONEY_SHANGHAI)
        if date_only:
            timestamp = timestamp.normalize() + pd.Timedelta(hours=15)
        return timestamp, date_only

    @staticmethod
    def _normalize_eastmoney_bulk_rows(rows) -> pd.DataFrame:
        """把 clist/get 原始字段映射为 vendor OHLC 行。

        这里是批量行情唯一的成交量单位边界：f5 的“手”转换为“股”一次，
        后续校验和写库都不得再次调用 ``normalize_eastmoney_volume``。
        """
        if isinstance(rows, dict):
            rows = list(rows.values())
        if rows is None:
            return pd.DataFrame()
        raw = pd.DataFrame(list(rows))
        if raw.empty:
            return pd.DataFrame()

        fields = {
            "f12": "sector_code",
            "f14": "sector_name",
            "f2": "close",
            "f3": "pct_chg",
            "f5": "volume",
            "f6": "amount",
            "f15": "high",
            "f16": "low",
            "f17": "open",
            "f18": "prev_close",
            "f124": "snapshot_at",
        }
        if any(field not in raw.columns for field in fields):
            return pd.DataFrame()

        result = raw[list(fields)].rename(columns=fields).copy()
        for column in ("sector_code", "sector_name"):
            result[column] = result[column].astype("string").str.strip()
        for column in (
            "open",
            "close",
            "high",
            "low",
            "amount",
            "pct_chg",
            "prev_close",
            "volume",
        ):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        result["volume"] = result["volume"] * EASTMONEY_VOLUME_MULTIPLIER
        return result

    @classmethod
    def _validate_eastmoney_bulk_snapshot(
        cls,
        frame: pd.DataFrame,
        universe: pd.DataFrame,
        target_date,
        *,
        min_coverage: float = EASTMONEY_BULK_MIN_COVERAGE,
    ) -> tuple[bool, str]:
        """校验批量快照的来源日期、代码名称、覆盖率和 OHLC 语义。"""
        required = {
            "sector_code",
            "sector_name",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "prev_close",
            "snapshot_at",
        }
        if frame is None or frame.empty:
            return False, "empty_bulk_snapshot"
        missing = sorted(required.difference(frame.columns))
        if missing:
            return False, f"missing_columns={','.join(missing)}"
        if universe is None or universe.empty:
            return False, "empty_cached_universe"

        rows = frame.copy()
        expected = universe.copy()
        for column in ("sector_code", "sector_name"):
            if column not in expected.columns:
                return False, f"universe_missing_{column}"
            expected[column] = expected[column].astype("string").str.strip()
            rows[column] = rows[column].astype("string").str.strip()

        if rows["sector_code"].isna().any() or (rows["sector_code"] == "").any():
            return False, "invalid_sector_code"
        if rows["sector_name"].isna().any() or (rows["sector_name"] == "").any():
            return False, "invalid_sector_name"
        if rows["sector_code"].duplicated().any() or rows["sector_name"].duplicated().any():
            return False, "duplicate_code_or_name"

        expected = expected.dropna(subset=["sector_code", "sector_name"])
        expected = expected[
            (expected["sector_code"] != "") & (expected["sector_name"] != "")
        ]
        expected_pairs = set(zip(expected["sector_code"], expected["sector_name"]))
        if not expected_pairs:
            return False, "empty_cached_universe"
        if len(expected_pairs) != len(expected):
            return False, "duplicate_cached_universe"
        actual_pairs = set(zip(rows["sector_code"], rows["sector_name"]))
        if not actual_pairs.issubset(expected_pairs):
            return False, "row_outside_cached_universe"
        coverage = len(actual_pairs) / len(expected_pairs)
        required_rows = max(1, math.ceil(len(expected_pairs) * float(min_coverage)))
        if len(actual_pairs) < required_rows:
            return False, f"coverage={coverage:.4f}<{float(min_coverage):.4f}"

        target = pd.to_datetime(target_date, errors="coerce")
        if pd.isna(target):
            return False, "invalid_target_date"
        target = target.date()
        now = pd.Timestamp.now(tz=EASTMONEY_SHANGHAI)
        parsed_times = []
        for raw_timestamp in rows["snapshot_at"]:
            timestamp, date_only = cls._parse_eastmoney_snapshot_timestamp(raw_timestamp)
            if timestamp is None:
                return False, "f124_invalid"
            if timestamp.date() != target:
                return False, "f124_date_mismatch"
            if timestamp > now:
                return False, "f124_future"
            if not date_only and timestamp.time() < datetime_time(15, 0):
                return False, "f124_intraday"
            parsed_times.append(timestamp)
        rows["snapshot_at"] = parsed_times

        numeric_columns = (
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "prev_close",
        )
        for column in numeric_columns:
            numeric = pd.to_numeric(rows[column], errors="coerce")
            if numeric.isna().any() or (~numeric.map(math.isfinite)).any():
                return False, f"invalid_numeric_{column}"
            rows[column] = numeric

        if (rows[["open", "close", "high", "low", "prev_close"]] <= 0).any().any():
            return False, "ohlc_non_positive"
        if (rows["volume"] < 0).any() or (rows["amount"] < 0).any():
            return False, "negative_volume_or_amount"
        if (
            (rows["high"] < rows[["open", "close"]].max(axis=1)).any()
            or (rows["low"] > rows[["open", "close"]].min(axis=1)).any()
        ):
            return False, "invalid_ohlc_semantics"
        implied_pct = (rows["close"] / rows["prev_close"] - 1.0) * 100.0
        if (implied_pct.sub(rows["pct_chg"]).abs() > 0.5).any():
            return False, "invalid_pct_semantics"
        return True, ""

    def _mark_eastmoney_bulk_failure(self, reason: str) -> None:
        self._eastmoney_last_error = str(reason)
        gate = getattr(self, "_eastmoney_gate", None)
        if gate is None:
            return
        try:
            gate.record_failure()
        except EastmoneyGateError as exc:
            # The gate may already be open or unavailable.  Either way the
            # vendor branch remains fail-closed and will not retry per sector.
            logger.debug("Eastmoney 批量失败状态未能追加到闸门: %s", exc)

    def _fetch_eastmoney_bulk_snapshot(
        self,
        target_date,
        universe: pd.DataFrame,
    ) -> pd.DataFrame:
        """通过 clist/get 分页读取一次板块批量快照并完成质量门。"""
        target = pd.to_datetime(target_date, errors="coerce")
        if pd.isna(target) or universe is None or universe.empty:
            self._mark_eastmoney_bulk_failure("invalid_target_or_empty_universe")
            return pd.DataFrame()

        rows = []
        total = None
        page_size = EASTMONEY_BULK_PAGE_SIZE
        try:
            for page in range(1, EASTMONEY_BULK_MAX_PAGES + 1):
                data = self._eastmoney_get_json(
                    "https://17.push2.eastmoney.com/api/qt/clist/get",
                    {
                        "pn": page,
                        "pz": page_size,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": "m:90+t:2+f:!50",
                        "fields": EASTMONEY_BULK_FIELDS,
                    },
                )
                payload = data.get("data") if isinstance(data, dict) else None
                if not isinstance(payload, dict):
                    self._mark_eastmoney_bulk_failure("bulk_payload_missing")
                    return pd.DataFrame()
                if total is None:
                    try:
                        total = int(payload.get("total") or 0)
                    except (TypeError, ValueError):
                        total = 0
                page_rows = payload.get("diff") or []
                if isinstance(page_rows, dict):
                    page_rows = list(page_rows.values())
                if not isinstance(page_rows, list) or not page_rows:
                    if total and len(rows) < total:
                        self._mark_eastmoney_bulk_failure("bulk_page_incomplete")
                        return pd.DataFrame()
                    break
                rows.extend(page_rows)
                if total and len(rows) >= total:
                    break
                if len(page_rows) < page_size and not total:
                    break
            else:
                self._mark_eastmoney_bulk_failure("bulk_page_limit_exceeded")
                return pd.DataFrame()
        except (EastmoneyCircuitOpen, EastmoneyBudgetExceeded, EastmoneyRequestError) as exc:
            self._eastmoney_last_error = str(exc)
            logger.warning("⚠️ Eastmoney 批量板块快照闸门阻断: %s", exc)
            return pd.DataFrame()
        except Exception as exc:
            self._eastmoney_last_error = str(exc)
            logger.warning("⚠️ Eastmoney 批量板块快照异常: %s", exc)
            return pd.DataFrame()

        if not rows or (total and len(rows) < total):
            self._mark_eastmoney_bulk_failure("bulk_empty_or_incomplete")
            return pd.DataFrame()
        frame = self._normalize_eastmoney_bulk_rows(rows)
        ok, reason = self._validate_eastmoney_bulk_snapshot(
            frame, universe, target.date()
        )
        if not ok:
            self._mark_eastmoney_bulk_failure(reason)
            logger.warning("⚠️ Eastmoney 批量板块快照质量门失败: %s", reason)
            return pd.DataFrame()
        self._eastmoney_last_error = ""
        return frame

    def _save_eastmoney_bulk_snapshot(
        self,
        frame: pd.DataFrame,
        target_date,
        universe: pd.DataFrame | None = None,
    ) -> bool:
        """在单事务中幂等替换目标交易日的 vendor 快照。"""
        if frame is None or frame.empty:
            return False
        target = pd.to_datetime(target_date, errors="coerce")
        if pd.isna(target):
            return False
        target_date_value = target.date()
        if universe is not None:
            ok, reason = self._validate_eastmoney_bulk_snapshot(
                frame, universe, target_date_value
            )
            if not ok:
                logger.warning("⚠️ Eastmoney 批量板块快照写入前质量门失败: %s", reason)
                return False
        required = {
            "sector_code",
            "sector_name",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
        }
        if not required.issubset(frame.columns):
            return False
        save_frame = frame.copy()
        save_frame["trade_date"] = target_date_value
        save_frame["source"] = "eastmoney_bulk_snapshot"
        save_frame["quality_flag"] = "ok"
        save_frame["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_frame = save_frame[
            [
                "sector_name",
                "sector_code",
                "trade_date",
                "open",
                "close",
                "high",
                "low",
                "volume",
                "amount",
                "pct_chg",
                "source",
                "quality_flag",
                "updated_at",
            ]
        ].sort_values(["sector_code", "sector_name"])
        if save_frame["sector_code"].duplicated().any() or save_frame["sector_name"].duplicated().any():
            return False

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"""DELETE FROM {self.table_name}
                WHERE trade_date = :trade_date"""
                    ),
                    {"trade_date": target_date_value},
                )
                save_frame.to_sql(
                    self.table_name,
                    conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=1000,
                )
        except Exception as exc:
            logger.warning("⚠️ Eastmoney 批量板块快照写入失败: %s", exc)
            return False
        return True

    def _fetch_eastmoney_kline(
        self,
        sector_code: str,
        start_date: str,
        end_date: str = "20500101",
    ) -> pd.DataFrame:
        """从东方财富直连接口获取板块日K。"""
        if not sector_code or self._eastmoney_kline_disabled:
            return pd.DataFrame()

        try:
            data = self._eastmoney_get_json(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                {
                    "secid": f"90.{sector_code}",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": start_date,
                    "end": end_date,
                },
            )
        except (EastmoneyCircuitOpen, EastmoneyBudgetExceeded) as e:
            self._eastmoney_kline_disabled = True
            self._eastmoney_last_error = str(e)
            logger.warning("⚠️ Eastmoney 板块K线闸门阻断 %s: %s", sector_code, e)
            return pd.DataFrame()
        except EastmoneyRequestError as e:
            status = self._eastmoney_gate.status()
            self._eastmoney_kline_failures = int(
                status.get("consecutive_failures") or 0
            )
            self._eastmoney_kline_disabled = bool(
                float(status.get("circuit_open_until") or 0.0) > time.time()
                or self._eastmoney_kline_failures >= 3
            )
            self._eastmoney_last_error = str(e)
            logger.warning("⚠️ Eastmoney Direct 板块K线失败 %s: %s", sector_code, e)
            return pd.DataFrame()
        except Exception as e:
            self._eastmoney_last_error = str(e)
            logger.warning("⚠️ Eastmoney Direct 板块K线异常 %s: %s", sector_code, e)
            return pd.DataFrame()

        payload = data.get("data")
        klines = payload.get("klines") if isinstance(payload, dict) else None
        klines = klines or []
        if not klines:
            # A successful HTTP response with no bars is still a source
            # failure.  It must never enter save_data or trigger synthetic
            # fallback.  Count it in the persistent circuit state.
            self._eastmoney_gate.record_failure()
            status = self._eastmoney_gate.status()
            self._eastmoney_kline_failures = int(
                status.get("consecutive_failures") or 0
            )
            self._eastmoney_kline_disabled = bool(
                float(status.get("circuit_open_until") or 0.0) > time.time()
                or self._eastmoney_kline_failures >= 3
            )
            self._eastmoney_last_error = "empty_kline_response"
            logger.warning("⚠️ Eastmoney 板块K线空响应，不写入 %s", sector_code)
            return pd.DataFrame()

        self._eastmoney_kline_failures = 0
        self._eastmoney_last_error = ""

        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            records.append({
                "trade_date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "amount": parts[6],
                "pct_chg": parts[8],
            })

        return pd.DataFrame(records)

    def _load_existing_sector_list(self) -> pd.DataFrame:
        """读取已缓存的行业列表。"""
        try:
            df = pd.read_sql(
                """
                SELECT sector_name, sector_type, sector_code, source
                FROM sector_list
                WHERE sector_name IS NOT NULL
                  AND sector_name != ''
                  AND sector_type = 'Industry'
                ORDER BY sector_name
                """,
                self.engine,
            )
            return df
        except Exception:
            return pd.DataFrame()

    def _load_synthetic_sector_list(self) -> pd.DataFrame:
        """从股票-行业映射生成兜底行业列表。"""
        try:
            df = pd.read_sql(
                """
                SELECT sector_name, COUNT(DISTINCT symbol) AS stock_count
                FROM stock_sector_map
                WHERE sector_name IS NOT NULL
                  AND sector_name != ''
                  AND sector_type = 'Industry'
                GROUP BY sector_name
                HAVING stock_count >= 3
                ORDER BY sector_name
                """,
                self.engine,
            )
        except Exception as e:
            logger.warning(f"⚠️ 读取 stock_sector_map 失败，无法合成行业指数: {e}")
            return pd.DataFrame()

        if df.empty:
            return df

        return pd.DataFrame({
            "sector_name": df["sector_name"],
            "sector_type": "Industry",
            "sector_code": df["sector_name"],
            "source": "synthetic_stock_map",
        })

    def _load_sector_universe(self) -> pd.DataFrame:
        """获取真实 Eastmoney 行业列表，失败时只使用已验证缓存。

        ``stock_sector_map`` is intentionally not promoted to a vendor
        universe here.  The local equal-weight composite will own that
        calculation in a separate table; this collector only writes vendor
        OHLC.
        """
        cached = self._load_existing_sector_list()
        has_real_cache = (
            not cached.empty
            and cached["source"].fillna("").str.startswith("eastmoney").all()
        )
        if has_real_cache:
            logger.info(f"✅ 从 sector_list 表加载 {len(cached)} 个行业")
            return cached

        direct = self._fetch_eastmoney_industry_list()
        if not direct.empty:
            self._save_sector_list(direct)
            logger.info(f"✅ 从 Eastmoney Direct 加载 {len(direct)} 个行业")
            return direct

        if not cached.empty:
            logger.warning(f"⚠️ 远端行业列表不可用，继续使用缓存行业 {len(cached)} 个")
            return cached
        logger.error("❌ Eastmoney 行业列表不可用，且无已验证缓存；跳过板块K线")
        return pd.DataFrame()

    def get_start_date(self, sector_name: str) -> str:
        """
        核心逻辑：检查数据库，决定是【全量下载】还是【增量更新】
        返回格式: 'YYYYMMDD'
        """
        query = text(f"SELECT MAX(trade_date) FROM {self.table_name} WHERE sector_name = :name")
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"name": sector_name}).scalar()

            if result:
                # 确保是 date 对象
                if isinstance(result, str):
                    result = datetime.strptime(result, '%Y-%m-%d').date()
                elif isinstance(result, datetime):
                    result = result.date()
                next_date = result + timedelta(days=1)
                return next_date.strftime("%Y%m%d")
            else:
                three_years_ago = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
                return three_years_ago
        except Exception as e:
            logger.warning(f"获取起始日期失败，默认下载3年数据: {e}")
            three_years_ago = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
            return three_years_ago

    def fetch_data(
        self,
        name: str,
        s_type: str,
        start_date: str,
        end_date: str = "20500101",
    ) -> pd.DataFrame:
        """调用 AkShare 接口，支持指定开始日期"""
        try:
            if s_type == 'Industry':
                # 使用基类的重试机制
                df = self._retry_call(
                    ak.stock_board_industry_hist_em,
                    symbol=name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            else:
                df = self._retry_call(
                    ak.stock_board_concept_hist_em,
                    symbol=name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            return df
        except Exception as e:
            return pd.DataFrame()

    def save_data(
        self,
        df: pd.DataFrame,
        name: str,
        end_date: str | None = None,
        source: str = "eastmoney_stock_plate",
    ):
        """
        清洗并执行增量保存（使用 INSERT OR IGNORE 避免重复）

        增量逻辑：
        - get_start_date() 返回 last_date + 1
        - 理论上不会有重复数据
        - 使用 INSERT OR IGNORE 作为保险，如果主键冲突则跳过
        """
        if df is None or df.empty:
            return False

        source_text = str(source or "").strip().lower()
        if not source_text.startswith("eastmoney"):
            logger.warning(
                "拒绝将非 Eastmoney 板块数据写入 vendor 表: name=%s source=%s",
                name,
                source,
            )
            return False

        # 1. 字段映射与清洗
        cols_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        }
        df = df.rename(columns=cols_map)

        # 确保必备列存在
        required_cols = ['trade_date', 'open', 'close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_cols):
            return False

        df['sector_name'] = name
        # ``sector_code`` is optional for the legacy AkShare-shaped frame; the
        # caller may attach it before save.  The vendor table keeps it when
        # available for source-level reconciliation.
        if "sector_code" not in df.columns:
            df["sector_code"] = None
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        if end_date:
            target_end = self._parse_target_date(end_date)
            df = df[df["trade_date"] <= target_end].copy()

        # 数值型字段转换
        numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # Eastmoney BK 板块接口的成交量单位是“手”，而 stock_daily_prices
        # 及合成板块统一使用“股”。如果不在入库边界转换，VOL 会稳定小 100 倍。
        df["volume"] = normalize_eastmoney_volume(df["volume"])

        # 选择需要的列
        final_df = df[
            [
                'sector_name',
                'sector_code',
                'trade_date',
                'open',
                'close',
                'high',
                'low',
                'volume',
                'amount',
                'pct_chg',
            ]
        ].dropna(subset=['trade_date'])
        if final_df.empty:
            return False
        if (
            (final_df["close"] <= 0).any()
            or (final_df["high"] < final_df[["open", "close"]].max(axis=1)).any()
            or (final_df["low"] > final_df[["open", "close"]].min(axis=1)).any()
        ):
            logger.warning("拒绝写入非法 Eastmoney OHLC: %s", name)
            return False
        final_df["source"] = source
        final_df["quality_flag"] = "ok"
        final_df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if final_df.empty:
            return False

        start_date = final_df['trade_date'].min().strftime('%Y-%m-%d')
        end_date = final_df['trade_date'].max().strftime('%Y-%m-%d')

        # 2. 入库逻辑：只覆盖本次返回日期区间，避免接口返回重叠日期时主键冲突。
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                DELETE FROM {self.table_name}
                WHERE sector_name = :sector_name
                  AND trade_date BETWEEN :start_date AND :end_date
            """), {
                "sector_name": name,
                "start_date": start_date,
                "end_date": end_date,
            })
            final_df.to_sql(self.table_name, conn, if_exists='append', index=False,
                           method='multi', chunksize=5000)

        return True

    def _save_synthetic_data(
        self,
        df: pd.DataFrame,
        name: str,
        end_date: str | None = None,
    ) -> bool:
        """Reject legacy synthetic writes.

        Equal-weight data belongs in the dedicated composite calculator/table;
        writing it here would make an OHLC vendor table appear complete while
        inventing high/low values.
        """
        logger.warning(
            "拒绝写入 synthetic sector rows: name=%s; 请使用 sector composite 计算器",
            name,
        )
        return False

    def _calculate_synthetic_sector_data(
        self,
        sector_name: str,
        start_date: str,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        由成分股日收益率合成行业指数。

        远端行业指数接口不可用时仍能提供稳定的行业RPS输入；指数点位从1000开始，
        更关注相对强弱排序而非绝对点位。
        """
        start = datetime.strptime(start_date, "%Y%m%d").strftime("%Y-%m-%d")
        end_filter = ""
        params = {
            "sector_name": sector_name,
            "start_date": start,
        }
        if end_date:
            end_filter = "AND k.trade_date <= :end_date"
            params["end_date"] = pd.to_datetime(end_date).strftime("%Y-%m-%d")

        query = text(f"""
            SELECT k.symbol, k.trade_date, k.close, k.volume, k.amount, k.pct_chg
            FROM stock_daily_prices k
            JOIN stock_sector_map m ON k.symbol = m.symbol
            WHERE m.sector_name = :sector_name
              AND k.trade_date >= :start_date
              {end_filter}
              AND k.close > 0
            ORDER BY k.symbol, k.trade_date
        """)

        df = pd.read_sql(query, self.engine, params=params)
        if df.empty:
            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(
            df["trade_date"], format="mixed"
        ).dt.strftime("%Y-%m-%d")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

        missing_ret = df["pct_chg"].isna()
        if missing_ret.any():
            fallback_ret = df.groupby("symbol")["close"].pct_change() * 100
            df.loc[missing_ret, "pct_chg"] = fallback_ret.loc[missing_ret]

        valid_return = df["pct_chg"].between(
            -SYNTHETIC_RETURN_LIMIT_PCT,
            SYNTHETIC_RETURN_LIMIT_PCT,
            inclusive="both",
        )
        dropped_rows = int((~valid_return & df["pct_chg"].notna()).sum())
        if dropped_rows:
            logger.warning(
                "板块 %s 合成时剔除 %d 条异常成分股收益",
                sector_name,
                dropped_rows,
            )
        df = df[valid_return].copy()

        daily = (
            df.dropna(subset=["pct_chg"])
            .groupby("trade_date")
            .agg(
                pct_chg=("pct_chg", "mean"),
                stock_count=("symbol", "nunique"),
                volume=("volume", "sum"),
                amount=("amount", "sum"),
            )
            .reset_index()
        )
        # 部分细分行业只有1-2只成分股；兜底合成时保留它们，避免行业宇宙缺口。
        daily = daily[daily["stock_count"] >= 1].copy()
        if daily.empty:
            return pd.DataFrame()

        base_close = 1000.0
        try:
            with self.engine.connect() as conn:
                previous_close = conn.execute(text(f"""
                    SELECT close
                    FROM {self.table_name}
                    WHERE sector_name = :sector_name
                      AND trade_date < :start_date
                      AND close > 0
                    ORDER BY trade_date DESC
                    LIMIT 1
                """), {
                    "sector_name": sector_name,
                    "start_date": start,
                }).scalar()
            if previous_close:
                base_close = float(previous_close)
        except Exception:
            base_close = 1000.0

        returns = daily["pct_chg"].astype(float) / 100
        close = (1 + returns).cumprod() * base_close
        open_ = close.shift(1)
        if not open_.empty:
            open_.iloc[0] = base_close
        daily["close"] = close.round(4)
        daily["open"] = open_.round(4)
        daily["high"] = daily[["open", "close"]].max(axis=1)
        daily["low"] = daily[["open", "close"]].min(axis=1)

        return daily[
            ["trade_date", "open", "close", "high", "low", "volume", "amount", "pct_chg"]
        ]

    def _save_eastmoney_or_synthetic_data(
        self,
        row: pd.Series,
        start_date: str,
        end: str | None,
        end_yyyymmdd: str,
    ) -> bool:
        """保存 Eastmoney 板块K线；空响应只记录失败，不生成伪K线。"""
        name = row["sector_name"]
        df_raw = self._fetch_eastmoney_kline(
            row.get("sector_code"),
            start_date,
            end_date=end_yyyymmdd,
        )
        if df_raw is not None and not df_raw.empty:
            df_raw = df_raw.copy()
            df_raw["sector_code"] = row.get("sector_code")
            return self.save_data(
                df_raw,
                name,
                end_date=end,
                source=str(row.get("source") or "eastmoney_stock_plate"),
            )

        logger.warning(
            "⚠️ Eastmoney 板块K线为空，不写入 vendor 表: %s",
            name,
        )
        return False

    def run(self, end: str | None = None, *, target_only: bool = False):
        """执行板块K线采集。

        ``target_only`` is used by the daily tail task.  Without this guard a
        newly-created vendor table would interpret an empty sector as a
        request for a three-year backfill, multiplying Eastmoney traffic and
        making a reference-only feed capable of delaying the core pipeline.
        """
        self.log_collection_start()
        logger.info("🚀 启动 [板块 K 线] 智能同步任务...")
        target_end = self._parse_target_date(end)
        if target_only and target_end is None:
            raise ValueError("target_only 模式必须提供 end 目标交易日")
        end_yyyymmdd = self._yyyymmdd(target_end) if target_end else "20500101"

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_table()

        # Daily target-only collection is deliberately pinned to the last
        # cached universe.  It may not promote a fresh remote list into a
        # 496-request per-sector fallback when the bulk snapshot fails.
        if target_only:
            df_sectors = self._load_existing_sector_list()
        else:
            df_sectors = self._load_sector_universe()

        if df_sectors.empty:
            logger.error("❌ 板块列表为空，跳过板块K线采集")
            self.log_collection_end(False, "板块列表为空")
            return

        total = len(df_sectors)
        logger.info(f"📋 待处理板块总数: {total}")

        if target_only:
            bulk_frame = self._fetch_eastmoney_bulk_snapshot(target_end, df_sectors)
            if bulk_frame.empty:
                reason = self._eastmoney_last_error or "bulk_snapshot_unavailable"
                logger.warning("⚠️ 目标日 Eastmoney 批量板块快照不可用: %s", reason)
                self.log_collection_end(False, reason)
                return
            if not self._save_eastmoney_bulk_snapshot(
                bulk_frame, target_end, universe=df_sectors
            ):
                reason = "bulk_snapshot_write_failed"
                logger.error("❌ 目标日 Eastmoney 批量板块快照写入失败")
                self.log_collection_end(False, reason)
                return
            logger.info("✅ 目标日批量板块快照已原子写入 %s 条", len(bulk_frame))
            self.log_collection_end(True, f"批量更新 {len(bulk_frame)}/{total} 个板块")
            return

        update_count = 0
        skip_count = 0

        for i, row in df_sectors.iterrows():
            name = row['sector_name']
            s_type = row['sector_type']

            # In target-only mode always request one bounded bar.  This also
            # makes the first daily run safe when the vendor table is empty.
            start_date = (
                self._yyyymmdd(target_end)
                if target_only and target_end is not None
                else self.get_start_date(name)
            )
            anchor_date = target_end or datetime.now().date()
            three_years_ago = (anchor_date - timedelta(days=1095)).strftime("%Y%m%d")
            is_incremental = target_only or start_date != three_years_ago
            mode_str = f"增量[{start_date}]" if is_incremental else "全量[3年]"

            if target_end and datetime.strptime(start_date, "%Y%m%d").date() > target_end:
                skip_count += 1
                continue

            if datetime.strptime(start_date, "%Y%m%d").date() > datetime.now().date():
                skip_count += 1
                continue

            print(f"[{i+1}/{total}] {mode_str}同步: {name} ...", end="\r")

            # 下载数据
            if str(row.get("source", "")).startswith("eastmoney"):
                save_ok = self._save_eastmoney_or_synthetic_data(
                    row,
                    start_date,
                    end,
                    end_yyyymmdd,
                )
            else:
                logger.warning(
                    "跳过非 Eastmoney 板块源 %s (%s)，不会写入 %s",
                    name,
                    row.get("source"),
                    self.table_name,
                )
                save_ok = False

            # 保存数据
            if save_ok:
                update_count += 1
            else:
                skip_count += 1

        print(f"\n🎉 同步完成！更新/插入板块数: {update_count}, 无新数据/跳过: {skip_count}")
        self.log_collection_end(True, f"更新 {update_count}/{total} 个板块")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect sector daily K-line data")
    parser.add_argument("--end", help="target end date, e.g. 2026-06-04")
    parser.add_argument(
        "--target-only",
        action="store_true",
        help="only request the supplied end date; never start a three-year backfill",
    )
    args = parser.parse_args()
    collector = SectorKlineCollector()
    collector.run(end=args.end, target_only=args.target_only)
