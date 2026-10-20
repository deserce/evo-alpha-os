"""
EvoAlpha OS - 股票估值数据采集器
采集全市场股票的实时估值数据（PE、PB、市值等）
"""

import argparse
from datetime import date

import akshare as ak
import pandas as pd
from sqlalchemy import text, inspect
from tqdm import tqdm

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.direct_market_sources import fetch_tencent_quotes

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class StockValuationCollector(BaseCollector):
    """股票估值数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="stock_valuation",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "stock_valuation_daily"

    def _init_table(self):
        """初始化估值数据表"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 初始化估值表 {self.table_name}...")
            with self.engine.begin() as conn:
                # 创建表
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        code VARCHAR(20),
                        name VARCHAR(50),
                        trade_date DATE,
                        price FLOAT,
                        pe_ttm FLOAT,            -- 市盈率(动态)
                        pb FLOAT,                -- 市净率
                        total_mv FLOAT,          -- 总市值
                        circ_mv FLOAT,           -- 流通市值
                        pct_chg FLOAT,           -- 涨跌幅
                        turnover FLOAT,          -- 换手率
                        volume_ratio FLOAT,      -- 量比
                        limit_up_price FLOAT,    -- 涨停价
                        limit_down_price FLOAT,  -- 跌停价
                        source VARCHAR(50),      -- 数据源
                        PRIMARY KEY (code, trade_date)
                    );
                """))
                # 创建索引
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_val_code ON {self.table_name} (code);"))
                except Exception:
                    pass
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_val_date ON {self.table_name} (trade_date);"))
                except Exception:
                    pass
        else:
            with self.engine.begin() as conn:
                for column, ddl_type in (
                    ("limit_up_price", "FLOAT"),
                    ("limit_down_price", "FLOAT"),
                    ("source", "VARCHAR(50)"),
                ):
                    try:
                        conn.execute(text(f"ALTER TABLE {self.table_name} ADD COLUMN {column} {ddl_type}"))
                    except Exception:
                        pass

    def fetch_data(self):
        """
        获取全市场当天实时估值数据

        Returns:
            pd.DataFrame: 估值数据
        """
        try:
            df = self.fetch_tencent_snapshot()
            if df is not None and not df.empty:
                df['_source'] = 'tencent_direct'
                return df
        except Exception as e:
            logger.warning(f"⚠️  腾讯直连估值失败，切换东方财富/AkShare: {e}")

        try:
            df = self._retry_call(ak.stock_zh_a_spot_em)
            if df is not None and not df.empty:
                df['_source'] = 'eastmoney'
                return df
        except Exception as e:
            logger.warning(f"⚠️  东方财富实时估值失败，使用本地K线兜底: {e}")

        df = self.fetch_local_snapshot()
        if df is not None and not df.empty:
            return df

        try:
            df = self._retry_call(ak.stock_zh_a_spot)
            if df is not None and not df.empty:
                df['_source'] = 'sina'
                return df
        except Exception as e:
            logger.warning(f"⚠️  新浪实时行情失败: {e}")

        return pd.DataFrame()

    def fetch_tencent_snapshot(self) -> pd.DataFrame:
        """Fetch Tencent direct quote snapshots for the local stock universe."""
        try:
            symbols = pd.read_sql(
                """
                SELECT symbol
                FROM stock_info
                WHERE symbol IS NOT NULL
                  AND TRIM(symbol) <> ''
                """,
                self.engine,
            )["symbol"].astype(str).tolist()
        except Exception:
            symbols = []
        if not symbols:
            return pd.DataFrame()
        df = fetch_tencent_quotes(symbols)
        if df.empty:
            return df
        logger.info(f"  ✅ 腾讯直连估值: {len(df)} 条")
        return df

    def fetch_local_snapshot(self, target_date: str | None = None) -> pd.DataFrame:
        """从近期缺失 K 线和换手率反推流通市值，作为实时行情失败时的稳定兜底。"""
        if target_date:
            query = """
                SELECT
                    k.symbol AS code,
                    COALESCE(s.name, k.symbol) AS name,
                    k.trade_date,
                    k.close AS price,
                    k.pct_chg,
                    k.turnover_rate AS turnover,
                    k.volume
                FROM stock_daily_prices k
                LEFT JOIN stock_info s ON s.symbol = k.symbol
                WHERE k.trade_date = :target_date
                  AND k.close IS NOT NULL
                  AND k.turnover_rate IS NOT NULL
                  AND k.turnover_rate > 0
            """
        else:
            query = """
            WITH latest_kline_date AS (
                SELECT MAX(trade_date) AS trade_date FROM stock_daily_prices
            ),
            target_dates AS (
                SELECT DISTINCT k.trade_date
                FROM stock_daily_prices k
                JOIN latest_kline_date latest
                LEFT JOIN stock_valuation_daily v ON v.trade_date = k.trade_date
                WHERE v.trade_date IS NULL
                  AND k.trade_date >= DATE(latest.trade_date, '-30 day')
            )
            SELECT
                k.symbol AS code,
                COALESCE(s.name, k.symbol) AS name,
                k.trade_date,
                k.close AS price,
                k.pct_chg,
                k.turnover_rate AS turnover,
                k.volume
            FROM stock_daily_prices k
            LEFT JOIN stock_info s ON s.symbol = k.symbol
            JOIN target_dates d ON d.trade_date = k.trade_date
            WHERE k.close IS NOT NULL
              AND k.turnover_rate IS NOT NULL
              AND k.turnover_rate > 0
            """
        df = pd.read_sql(text(query), self.engine, params={"target_date": target_date})
        if df.empty:
            return df

        df['_source'] = 'local_kline'
        trade_dates = sorted(pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d').unique())
        logger.info(f"  ✅ 本地K线兜底: {len(df)} 条, 日期 {', '.join(trade_dates)}")
        return df

    def process_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        处理估值数据（清洗、转换）

        Args:
            df: 原始数据

        Returns:
            pd.DataFrame: 处理后的数据
        """
        if df is None or df.empty:
            return df

        source = df['_source'].iloc[0] if '_source' in df.columns else 'eastmoney'

        if source == 'local_kline':
            df = df.copy()
            df['pe_ttm'] = 0
            df['pb'] = 0
            df['volume_ratio'] = 0
            df['limit_up_price'] = 0
            df['limit_down_price'] = 0
            df['source'] = source
            valid_turnover = pd.to_numeric(df['turnover'], errors='coerce') > 0
            float_shares = pd.Series(0.0, index=df.index)
            float_shares.loc[valid_turnover] = (
                pd.to_numeric(df.loc[valid_turnover, 'volume'], errors='coerce')
                / (pd.to_numeric(df.loc[valid_turnover, 'turnover'], errors='coerce') / 100)
            )
            df['circ_mv'] = pd.to_numeric(df['price'], errors='coerce').fillna(0) * float_shares
            df['total_mv'] = df['circ_mv']
            df = df.drop(columns=['volume'], errors='ignore')
        else:
            rename_map = {
                "代码": "code", "名称": "name", "最新价": "price",
                "涨跌幅": "pct_chg", "总市值": "total_mv", "流通市值": "circ_mv",
                "市盈率-动态": "pe_ttm", "市净率": "pb",
                "换手率": "turnover", "量比": "volume_ratio"
            }
            df = df.rename(columns=rename_map)
            if source == 'sina':
                df['code'] = df['code'].astype(str).str.extract(r'(\d{6})', expand=False)
                df['total_mv'] = 0
                df['circ_mv'] = 0
                df['pe_ttm'] = 0
                df['pb'] = 0
                df['turnover'] = 0
                df['volume_ratio'] = 0
            if 'trade_date' not in df.columns or df['trade_date'].isna().all():
                df['trade_date'] = date.today()
            df['limit_up_price'] = df.get('limit_up_price', 0)
            df['limit_down_price'] = df.get('limit_down_price', 0)
            df['source'] = source

        cols = [
            "code", "name", "trade_date", "price", "pe_ttm", "pb",
            "total_mv", "circ_mv", "pct_chg", "turnover", "volume_ratio",
            "limit_up_price", "limit_down_price", "source"
        ]
        for c in cols:
            if c == "source" and c not in df.columns:
                df[c] = source
            elif c not in df.columns:
                df[c] = 0
        df = df[cols].copy()
        df['code'] = df['code'].astype(str).str.zfill(6)
        df = df[df['code'].str.match(r"^(00|30|60|68|83|87|43|92)\d{4}$")]

        # 数值清洗
        numeric_cols = [
            "price", "pe_ttm", "pb", "total_mv", "circ_mv", "pct_chg", "turnover",
            "volume_ratio", "limit_up_price", "limit_down_price"
        ]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        return df

    def save_data(self, df: pd.DataFrame):
        """
        保存估值数据

        Args:
            df: 要保存的数据
        """
        if df.empty:
            return

        trade_dates = sorted(pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d').unique())
        date_desc = ", ".join(trade_dates)

        logger.info(f"💾 正在存储 {date_desc} 的估值数据 ({len(df)}条)...")

        with self.engine.begin() as conn:
            for trade_date in trade_dates:
                conn.execute(text(f"DELETE FROM {self.table_name} WHERE trade_date = :dt"), {"dt": trade_date})

            # 追加插入
            df.to_sql(self.table_name, conn, if_exists='append', index=False)

            # 同步更新 stock_info 表的估值字段
            logger.info("🔄 同步更新 stock_info 表的估值字段...")
            update_count = 0
            for _, row in tqdm(df.iterrows(), total=len(df), desc="  更新估值信息", unit="只"):
                # total_mv 单位是元，需要转换为亿元
                market_cap = row['total_mv'] / 100000000 if pd.notna(row['total_mv']) else None

                conn.execute(text("""
                    UPDATE stock_info
                    SET
                        pe = CASE WHEN :pe > 0 THEN :pe ELSE pe END,
                        pb = CASE WHEN :pb > 0 THEN :pb ELSE pb END,
                        market_cap = CASE WHEN :market_cap > 0 THEN :market_cap ELSE market_cap END
                    WHERE symbol = :symbol
                """), {
                    "pe": row['pe_ttm'],
                    "pb": row['pb'],
                    "market_cap": market_cap,
                    "symbol": row['code']
                })
                update_count += 1

            logger.info(f"✅ {date_desc} 估值数据入库成功！")
            logger.info(f"✅ 已同步更新 {update_count} 只股票的估值信息到 stock_info 表")

    def run(self, target_date: str | None = None) -> bool:
        """执行估值数据采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [估值数据存盘] 任务...")

        historical_target = bool(target_date and target_date != date.today().isoformat())
        network_available = not historical_target
        if network_available:
            try:
                self._health_check()
            except Exception as e:
                network_available = False
                logger.warning(f"⚠️  健康检查失败，尝试本地K线兜底: {e}")
        else:
            logger.info("📅 历史目标日 %s 使用本地K线估值兜底", target_date)

        # 初始化表
        self._init_table()

        # 获取数据
        if network_available:
            df_raw = self.fetch_data()
        elif target_date:
            df_raw = self.fetch_local_snapshot(target_date)
        else:
            df_raw = self.fetch_local_snapshot()
        if df_raw is not None and not df_raw.empty:
            df_clean = self.process_data(df_raw)
            self.save_data(df_clean)
            self.log_collection_end(True, f"采集 {len(df_clean)} 条数据")
            return True
        else:
            logger.error("❌ 无数据获取")
            self.log_collection_end(False, "无数据获取")
            return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="采集股票估值数据")
    parser.add_argument(
        "--target-date",
        help="明确目标交易日 YYYY-MM-DD；历史日仅使用同日本地K线",
    )
    cli_args = parser.parse_args()
    collector = StockValuationCollector()
    raise SystemExit(0 if collector.run(target_date=cli_args.target_date) else 1)
