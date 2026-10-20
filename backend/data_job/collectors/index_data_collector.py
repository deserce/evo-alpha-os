"""
指数数据采集器（继承 BaseCollector）
采集上证指数、深证成指、创业板指、A股指数的日线数据

数据源: AkShare
更新频率: 每日收盘后
"""
import sys
from pathlib import Path
import argparse
from datetime import datetime, timedelta
import requests
from sqlalchemy import text, inspect

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.mootdx_adapter import MARKET_SH, MARKET_SZ, MootdxAdapter
from data_core.market_calendar import is_a_share_session

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def pd_to_datetime(value):
    import pandas as pd

    return pd.to_datetime(value).to_pydatetime()


def latest_completed_trading_date(now: datetime | None = None):
    current = now or datetime.now()
    candidate = current.date()
    if (current.hour, current.minute) < (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE):
        candidate -= timedelta(days=1)
    while not is_a_share_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


class IndexDataCollector(BaseCollector):
    """指数数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="index_data",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "index_daily"

        # 定义主要指数（使用国证A指替代中证全指）
        self.index_list = [
            {"code": "sh000001", "name": "上证指数", "symbol": "000001", "tdx_market": MARKET_SH},
            {"code": "sz399001", "name": "深证成指", "symbol": "399001", "tdx_market": MARKET_SZ},
            {"code": "sz399006", "name": "创业板指", "symbol": "399006", "tdx_market": MARKET_SZ},
            {"code": "sz399317", "name": "国证A指", "symbol": "399317", "tdx_market": MARKET_SZ},
            {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH},
            {"code": "sh000688", "name": "科创50", "symbol": "000688", "tdx_market": MARKET_SH},
            {"code": "sh000905", "name": "中证500", "symbol": "000905", "tdx_market": MARKET_SH},
            {"code": "sh000852", "name": "中证1000", "symbol": "000852", "tdx_market": MARKET_SH},
        ]

    def _init_table(self):
        """初始化指数数据表"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 初始化指数表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        index_code VARCHAR(10) NOT NULL,
                        index_name VARCHAR(20) NOT NULL,
                        trade_date DATE NOT NULL,
                        open DECIMAL(10,2),
                        high DECIMAL(10,2),
                        low DECIMAL(10,2),
                        close DECIMAL(10,2),
                        volume BIGINT,
                        amount BIGINT,
                        change DECIMAL(10,2),
                        pct_chg DECIMAL(10,2),
                        PRIMARY KEY (index_code, trade_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_date ON {self.table_name}(trade_date)"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_code ON {self.table_name}(index_code)"))

            logger.info(f"✅ {self.table_name} 表创建成功")
        else:
            # 检查是否需要添加 change 字段
            try:
                columns = [col['name'] for col in inspector.get_columns(self.table_name)]
                if 'change' not in columns:
                    logger.info("🔄 添加 change 字段到现有表...")
                    with self.engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {self.table_name} ADD COLUMN change DECIMAL(10,2)"))
                    logger.info("✅ change 字段添加成功")
            except Exception as e:
                logger.warning(f"⚠️ 检查/添加字段时出错: {e}")

    def _eastmoney_secid(self, index_code: str) -> str:
        """返回东方财富 secid，沪市=1，深市=0。"""
        market = "0" if index_code.startswith("sz") else "1"
        return f"{market}.{index_code[2:]}"

    def _parse_eastmoney_klines(self, idx: dict, klines: list[str]):
        """解析东方财富日 K，保留接口返回的真实成交额。"""
        import pandas as pd

        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            records.append({
                "index_code": idx["symbol"],
                "index_name": idx["name"],
                "trade_date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "amount": parts[6],
                "pct_chg": parts[8],
                "change": parts[9],
            })

        df = pd.DataFrame(records)
        if df.empty:
            return df

        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        numeric_cols = ["open", "close", "high", "low", "volume", "amount", "change", "pct_chg"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _fetch_from_eastmoney(self, idx: dict, start_date: datetime, end_date: datetime):
        """从东方财富直连日 K 获取指数数据，优先使用真实成交额。"""
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        response = self._retry_call(
            requests.get,
            url=url,
            params={
                "secid": self._eastmoney_secid(idx["code"]),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "0",
                "beg": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=self.request_timeout,
        )
        if response is None:
            return None
        response.raise_for_status()
        data = response.json()
        klines = (data.get("data") or {}).get("klines") or []
        if not klines:
            return None
        return self._parse_eastmoney_klines(idx, klines)

    def _fetch_from_tdx(
        self,
        adapter: MootdxAdapter,
        idx: dict,
        start_date: datetime,
        end_date: datetime,
        max_bars: int | None = None,
    ):
        """从 TDX/mootdx 获取指数日 K。"""
        df = adapter.get_index_kline(
            idx["symbol"],
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            period="daily",
            market=idx["tdx_market"],
            max_bars=max_bars,
        )
        if df is None or df.empty:
            return None

        df = df.rename(columns={"symbol": "index_code"}).copy()
        df["index_code"] = idx["symbol"]
        df["index_name"] = idx["name"]
        return df

    def _fetch_from_akshare(self, idx: dict):
        """从 AkShare 获取指数日 K，作为 HTTP 兜底。"""
        import akshare as ak
        import pandas as pd

        df = self._retry_call(
            ak.stock_zh_index_daily,
            symbol=idx["code"],
        )
        if df is None or df.empty:
            return None

        df = df.reset_index()
        df["index_code"] = idx["symbol"]
        df["index_name"] = idx["name"]
        df["trade_date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["change"] = df["close"].diff()
        df["pct_chg"] = df["close"].pct_change() * 100
        df["amount"] = (df["volume"] * df["close"]).fillna(0)
        logger.warning(f"  💰 {idx['name']} 使用 AkShare 回退，成交额为 volume × close 估算值")
        return df

    def _normalize_index_df(
        self,
        idx: dict,
        df,
        start_date: datetime,
        end_date: datetime,
    ):
        """统一指数 K 线字段并裁剪日期范围。"""
        import pandas as pd

        if df is None or df.empty:
            return None

        df = df.copy()
        if "symbol" in df.columns and "index_code" not in df.columns:
            df = df.rename(columns={"symbol": "index_code"})

        df["index_code"] = idx["symbol"]
        df["index_name"] = idx["name"]
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")

        numeric_cols = ["open", "high", "low", "close", "volume", "amount", "change", "pct_chg"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "change" not in df.columns:
            df["change"] = df["close"].diff()
        if "pct_chg" not in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        if "amount" not in df.columns:
            df["amount"] = (df["volume"] * df["close"]).fillna(0)

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        df = df[(df["trade_date"] >= start_str) & (df["trade_date"] <= end_str)]
        if df.empty:
            return None

        columns = [
            "index_code",
            "index_name",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "change",
            "pct_chg",
        ]
        return df[columns].sort_values(["index_code", "trade_date"]).reset_index(drop=True)

    def fetch_data(self, start=None, end=None, max_bars: int | None = None):
        """
        获取指数数据

        Returns:
            dict: {index_code: DataFrame}
        """
        # 采集最近三年的数据
        end_date = pd_to_datetime(end) if end else datetime.combine(
            latest_completed_trading_date(),
            datetime.min.time(),
        )
        start_date = pd_to_datetime(start) if start else end_date - timedelta(days=365 * 3)

        logger.info(f"采集时间范围: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")

        index_data = {}
        tdx_adapter = MootdxAdapter(max_retries=2, timeout=8)

        for idx in self.index_list:
            try:
                logger.info(f"  采集 {idx['name']} ({idx['code']})...")

                df = None
                if tdx_adapter.is_available():
                    df = self._fetch_from_tdx(tdx_adapter, idx, start_date, end_date, max_bars=max_bars)
                    if df is not None and not df.empty:
                        logger.info(f"  ✅ {idx['name']} TDX采集成功: {len(df)} 条记录")

                if df is None or df.empty:
                    logger.warning(f"  ⚠️ {idx['name']} TDX无数据，回退东方财富")
                    df = self._fetch_from_eastmoney(idx, start_date, end_date)
                    if df is not None and not df.empty:
                        logger.info(f"  ✅ {idx['name']} 东方财富采集成功: {len(df)} 条记录")

                if df is None or df.empty:
                    logger.warning(f"  ⚠️ {idx['name']} 东方财富无数据，回退 AkShare")
                    df = self._fetch_from_akshare(idx)

                df = self._normalize_index_df(idx, df, start_date, end_date)
                if df is not None and not df.empty:
                    index_data[idx["symbol"]] = df
                    logger.info(f"  ✅ {idx['name']} 采集成功: {len(df)} 条记录")
                else:
                    logger.warning(f"  ⚠️  {idx['name']} 无数据")

            except Exception as e:
                logger.error(f"  ❌ {idx['name']} 采集失败: {e}")

        return index_data

    def process_data(self, index_data):
        """
        处理指数数据

        Args:
            index_data: dict, {index_code: DataFrame}

        Returns:
            pd.DataFrame: 合并后的数据
        """
        import pandas as pd

        if not index_data:
            return pd.DataFrame()

        # 合并所有指数数据
        all_data = []
        for index_code, df in index_data.items():
            all_data.append(df)

        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            logger.info(f"✅ 数据处理完成: {len(result_df)} 条记录，{len(index_data)} 个指数")
            return result_df
        else:
            return pd.DataFrame()

    def save_data(self, df):
        """
        保存指数数据

        Args:
            df: 要保存的数据
        """
        if df.empty:
            return

        with self.engine.begin() as conn:
            for index_code in df["index_code"].unique():
                date_rows = df[df["index_code"] == index_code]["trade_date"]
                start_date = date_rows.min()
                end_date = date_rows.max()
                conn.execute(text(f"""
                    DELETE FROM {self.table_name}
                    WHERE index_code = :code
                      AND trade_date BETWEEN :start_date AND :end_date
                """), {
                    "code": index_code,
                    "start_date": start_date,
                    "end_date": end_date,
                })

            df.to_sql(self.table_name, conn, if_exists='append', index=False)

        logger.info(f"✅ 数据已保存到数据库: {len(df)} 条记录")

    def run(self, start=None, end=None, max_bars: int | None = None) -> bool:
        """执行指数数据采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [指数数据采集] 任务...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return False

        # 初始化表
        self._init_table()

        # 获取数据
        index_data = self.fetch_data(start=start, end=end, max_bars=max_bars)
        if index_data:
            # 处理数据
            df_clean = self.process_data(index_data)
            # 保存数据
            self.save_data(df_clean)

            total_records = len(df_clean)
            total_indices = len(index_data)
            target_date = (
                pd_to_datetime(end).strftime("%Y-%m-%d")
                if end
                else latest_completed_trading_date().isoformat()
            )
            missing_indices = sorted(set(idx["symbol"] for idx in self.index_list) - set(index_data))
            stale_indices = [
                code
                for code, item in index_data.items()
                if item["trade_date"].max() < target_date
            ]
            if missing_indices or stale_indices:
                message = f"缺失指数={missing_indices or '-'}, 未到目标日={stale_indices or '-'}"
                logger.error(f"❌ {message}")
                self.log_collection_end(False, message)
                return False

            self.log_collection_end(True, f"采集 {total_indices} 个指数，{total_records} 条记录")
            return True
        else:
            logger.error("❌ 无数据获取")
            self.log_collection_end(False, "无数据获取")
            return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect index daily K-lines")
    parser.add_argument("--start", help="start date, e.g. 2026-06-03")
    parser.add_argument("--end", help="end date, e.g. 2026-06-04")
    parser.add_argument("--max-bars", type=int, default=420, help="max recent TDX bars")
    args = parser.parse_args()

    collector = IndexDataCollector()
    raise SystemExit(0 if collector.run(start=args.start, end=args.end, max_bars=args.max_bars) else 1)
