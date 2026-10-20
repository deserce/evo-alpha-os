"""
EvoAlpha OS - 北向资金持股数据采集器
采集每只股票的北向资金历史持仓数据
"""

import time
import argparse
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_engine

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class NorthboundHoldingsCollector(BaseCollector):
    """北向资金持股数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="northbound_holdings",
            request_timeout=30,
            request_delay=1.0,  # API限制，延迟1秒
            max_retries=3
        )
        self.engine = get_engine()
        self.engine.echo = False
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        self.table_name = "stock_northbound_holdings"

    def _init_table(self):
        """初始化北向资金持股表"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建北向资金持股表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        symbol VARCHAR(20),
                        name VARCHAR(100),
                        hold_date DATE,
                        close_price FLOAT,
                        pct_chg FLOAT,
                        hold_amount FLOAT,
                        hold_value FLOAT,
                        hold_ratio FLOAT,
                        change_amount FLOAT,
                        change_value FLOAT,
                        change_market_value FLOAT,
                        PRIMARY KEY (symbol, hold_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_date ON {self.table_name} (hold_date);"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_symbol ON {self.table_name} (symbol);"))
            logger.info(f"✅ 表 {self.table_name} 创建成功")
        else:
            logger.info(f"✅ 表 {self.table_name} 已存在")

    def fetch_latest_eligible_symbols(self) -> set[str]:
        """获取最后可得北向持股名单，用于跳过明显无北向数据的股票。"""
        try:
            df = self._retry_call(
                ak.stock_hsgt_hold_stock_em,
                market="北向",
                indicator="今日排行",
                max_retries=2,
            )
            if df is None or df.empty or "代码" not in df.columns:
                return set()
            symbols = set(df["代码"].astype(str).str.zfill(6))
            latest_date = df["日期"].iloc[0] if "日期" in df.columns else "unknown"
            logger.info(f"✅ 北向最后可得持股名单: {len(symbols)} 只, 日期 {latest_date}")
            return symbols
        except Exception as e:
            logger.warning(f"⚠️ 获取北向持股名单失败，将退回全 A 扫描: {e}")
            return set()

    def fetch_stock_list(self, symbols: list[str] | None = None, eligible_only: bool = True):
        """从数据库获取股票列表"""
        logger.info("📋 获取股票列表...")

        with self.engine.connect() as conn:
            if symbols:
                placeholders = ", ".join([f":s{i}" for i in range(len(symbols))])
                params = {f"s{i}": symbol for i, symbol in enumerate(symbols)}
                df = pd.read_sql(
                    text(f"""
                        SELECT symbol, name
                        FROM stock_info
                        WHERE symbol IN ({placeholders})
                        ORDER BY symbol
                    """),
                    conn,
                    params=params,
                )
            else:
                df = pd.read_sql("SELECT symbol, name FROM stock_info ORDER BY symbol", conn)

        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df = df[df["symbol"].str.match(r"^(00|30|60|68)\d{4}$")].copy()

        if eligible_only and not symbols:
            eligible_symbols = self.fetch_latest_eligible_symbols()
            if eligible_symbols:
                before_count = len(df)
                df = df[df["symbol"].isin(eligible_symbols)].copy()
                logger.info(f"✅ 按北向持股名单过滤: {before_count} -> {len(df)} 只")

        logger.info(f"✅ 获取到 {len(df)} 只股票")
        return df

    def get_existing_latest_dates(self) -> dict[str, date]:
        """读取每只股票已入库的最新北向持仓日期。"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            return {}

        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT symbol, MAX(hold_date) AS latest_date
                    FROM {self.table_name}
                    GROUP BY symbol
                """),
                conn,
            )

        if df.empty:
            return {}

        df["latest_date"] = pd.to_datetime(df["latest_date"], errors="coerce").dt.date
        return dict(zip(df["symbol"].astype(str).str.zfill(6), df["latest_date"]))

    def fetch_stock_holdings(self, symbol):
        """
        获取单只股票的北向资金持仓历史

        Args:
            symbol: 股票代码

        Returns:
            pd.DataFrame: 持仓数据
        """
        try:
            # 使用重试机制调用API
            df = self._retry_call(ak.stock_hsgt_individual_em, symbol=symbol)

            if df is None or df.empty:
                return None

            # 添加股票代码列
            df['symbol'] = symbol

            return df

        except Exception as e:
            logger.warning(f"⚠️  获取 {symbol} 数据失败: {e}")
            return None

    def process_data(self, df: pd.DataFrame, stock_name: str) -> pd.DataFrame:
        """
        处理持仓数据

        Args:
            df: 原始数据
            stock_name: 股票名称

        Returns:
            pd.DataFrame: 处理后的数据
        """
        if df is None or df.empty:
            return pd.DataFrame()

        required_cols = [
            '持股日期', '当日收盘价', '当日涨跌幅', '持股数量', '持股市值',
            '持股数量占A股百分比', '今日增持股数', '今日增持资金', '今日持股市值变化'
        ]
        for column in required_cols:
            if column not in df.columns:
                df[column] = None

        df_processed = pd.DataFrame()
        df_processed['symbol'] = df['symbol'].astype(str).str.zfill(6)
        df_processed['name'] = stock_name
        df_processed['hold_date'] = pd.to_datetime(df['持股日期']).dt.date
        df_processed['close_price'] = pd.to_numeric(df['当日收盘价'], errors='coerce')
        df_processed['pct_chg'] = pd.to_numeric(df['当日涨跌幅'], errors='coerce')
        df_processed['hold_amount'] = pd.to_numeric(df['持股数量'], errors='coerce')
        df_processed['hold_value'] = pd.to_numeric(df['持股市值'], errors='coerce')
        df_processed['hold_ratio'] = pd.to_numeric(df['持股数量占A股百分比'], errors='coerce')
        df_processed['change_amount'] = pd.to_numeric(df['今日增持股数'], errors='coerce')
        df_processed['change_value'] = pd.to_numeric(df['今日增持资金'], errors='coerce')
        df_processed['change_market_value'] = pd.to_numeric(df['今日持股市值变化'], errors='coerce')

        # 处理空值
        df_processed = df_processed.fillna({
            'change_amount': 0,
            'change_value': 0,
            'change_market_value': 0
        })

        df_processed = df_processed.dropna(subset=['symbol', 'hold_date'])
        df_processed = df_processed.drop_duplicates(subset=['symbol', 'hold_date'], keep='last')
        return df_processed

    def save_data(self, df: pd.DataFrame):
        """
        保存持仓数据

        Args:
            df: 要保存的数据
        """
        if df is None or df.empty:
            return

        try:
            # 使用去重保存
            self.save_with_deduplication(
                df=df,
                table_name=self.table_name,
                key_columns=['symbol', 'hold_date'],
                date_column='hold_date'
            )
            logger.info(f"💾 保存 {len(df)} 条记录到 {self.table_name}")

        except Exception as e:
            logger.error(f"❌ 保存数据失败: {e}")

    def save_batch(self, frames: list[pd.DataFrame]) -> int:
        """批量保存，减少 SQLite 循环写入开销。"""
        frames = [df for df in frames if df is not None and not df.empty]
        if not frames:
            return 0

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=['symbol', 'hold_date'], keep='last')
        with self.engine.begin() as conn:
            for symbol, group in df.groupby("symbol"):
                conn.execute(text(f"""
                    DELETE FROM {self.table_name}
                    WHERE symbol = :symbol
                      AND hold_date BETWEEN :min_date AND :max_date
                """), {
                    "symbol": symbol,
                    "min_date": group["hold_date"].min(),
                    "max_date": group["hold_date"].max(),
                })

            df.to_sql(self.table_name, conn, if_exists='append', index=False,
                      method='multi', chunksize=500)

        inserted_count = len(df)
        self.progress["collection_count"] = self.progress.get("collection_count", 0) + inserted_count
        self._save_progress()
        return inserted_count

    def collect_one_stock(self, symbol: str, name: str, existing_latest: date | None = None):
        """采集单只股票，返回状态和处理后的 DataFrame。"""
        started_at = time.perf_counter()
        df_raw = self.fetch_stock_holdings(symbol)
        elapsed = time.perf_counter() - started_at

        if df_raw is None or df_raw.empty:
            return {
                "symbol": symbol, "name": name, "status": "empty",
                "rows": 0, "latest_date": None, "elapsed": elapsed, "df": pd.DataFrame()
            }

        df_processed = self.process_data(df_raw, name)
        if df_processed.empty:
            return {
                "symbol": symbol, "name": name, "status": "empty_after_process",
                "rows": 0, "latest_date": None, "elapsed": elapsed, "df": pd.DataFrame()
            }

        latest_date = df_processed['hold_date'].max()
        if existing_latest and latest_date <= existing_latest:
            return {
                "symbol": symbol, "name": name, "status": "up_to_date",
                "rows": 0, "latest_date": latest_date, "elapsed": elapsed, "df": pd.DataFrame()
            }

        if existing_latest:
            df_processed = df_processed[df_processed['hold_date'] > existing_latest].copy()

        return {
            "symbol": symbol, "name": name, "status": "success",
            "rows": len(df_processed), "latest_date": latest_date,
            "elapsed": elapsed, "df": df_processed
        }

    def run(
        self,
        collect_all_stocks=True,
        symbols: list[str] | None = None,
        limit: int | None = None,
        workers: int = 1,
        batch_size: int = 50,
        force: bool = False,
        eligible_only: bool = True,
    ):
        """
        执行采集

        Args:
            collect_all_stocks: 是否采集所有股票（True），还是只测试采集少量股票（False）
            symbols: 指定股票列表
            limit: 最多采集多少只股票
            workers: 并发线程数
            batch_size: 每批入库股票数
            force: 是否忽略已采最新日期，强制重抓全量历史
            eligible_only: 是否只采最后可得北向持股名单内的股票
        """
        self.log_collection_start()
        logger.info("🚀 北向资金持股数据采集任务启动")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_table()

        # 获取股票列表
        stock_list = self.fetch_stock_list(symbols=symbols, eligible_only=eligible_only)

        if stock_list.empty:
            logger.error("❌ 股票列表为空")
            self.log_collection_end(False, "股票列表为空")
            return

        # 如果是测试模式，只采集前10只股票
        if not collect_all_stocks:
            logger.info("⚠️  测试模式：只采集前10只股票")
            stock_list = stock_list.head(10)
        if limit is not None and limit > 0:
            logger.info(f"⚠️  限量模式：只采集前 {limit} 只股票")
            stock_list = stock_list.head(limit)

        total_stocks = len(stock_list)
        success_count = 0
        skipped_count = 0
        fail_count = 0
        total_records = 0
        batch_frames = []
        started_at = time.perf_counter()
        existing_latest_dates = {} if force else self.get_existing_latest_dates()

        workers = max(1, int(workers))
        logger.info(f"📊 开始采集 {total_stocks} 只股票的北向资金数据，workers={workers}, force={force}")

        rows = list(stock_list.itertuples(index=False))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.collect_one_stock,
                    row.symbol,
                    row.name,
                    existing_latest_dates.get(row.symbol),
                ): row
                for row in rows
            }

            for idx, future in enumerate(as_completed(futures), start=1):
                row = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    logger.error(f"[{idx}/{total_stocks}] ❌ {row.symbol} {row.name}: {e}")
                    fail_count += 1
                    continue

                status = result["status"]
                if status == "success":
                    success_count += 1
                    total_records += result["rows"]
                    batch_frames.append(result["df"])
                    logger.info(
                        f"[{idx}/{total_stocks}] ✅ {result['symbol']} {result['name']}: "
                        f"{result['rows']} 条, 最新 {result['latest_date']}, "
                        f"{result['elapsed']:.2f}s"
                    )
                elif status == "up_to_date":
                    skipped_count += 1
                    logger.info(
                        f"[{idx}/{total_stocks}] ⏭️ {result['symbol']} {result['name']}: "
                        f"已到最新可得日期 {result['latest_date']}"
                    )
                else:
                    fail_count += 1
                    logger.warning(f"[{idx}/{total_stocks}] ⚠️ {result['symbol']} {result['name']}: 无可用北向数据")

                if len(batch_frames) >= batch_size:
                    inserted = self.save_batch(batch_frames)
                    logger.info(f"💾 批量入库 {inserted} 条")
                    batch_frames = []

        if batch_frames:
            inserted = self.save_batch(batch_frames)
            logger.info(f"💾 批量入库 {inserted} 条")

        elapsed = time.perf_counter() - started_at
        speed = total_stocks / elapsed if elapsed > 0 else 0
        row_speed = total_records / elapsed if elapsed > 0 else 0

        # 输出统计
        logger.info(f"\n{'=' * 80}")
        logger.info(f"📊 采集完成统计:")
        logger.info(f"  总股票数: {total_stocks}")
        logger.info(f"  成功: {success_count} ({success_count/total_stocks*100:.1f}%)")
        logger.info(f"  跳过: {skipped_count} ({skipped_count/total_stocks*100:.1f}%)")
        logger.info(f"  失败: {fail_count} ({fail_count/total_stocks*100:.1f}%)")
        logger.info(f"  总记录数: {total_records:,} 条")
        logger.info(f"  耗时: {elapsed:.2f}s, 速度: {speed:.2f} 股票/秒, {row_speed:.2f} 行/秒")
        logger.info(f"{'=' * 80}\n")

        self.log_collection_end(
            True,
            f"采集完成: {success_count}/{total_stocks} 成功, "
            f"{skipped_count} 跳过, {total_records} 条记录, {speed:.2f} 股票/秒"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="采集个股北向历史持仓数据")
    parser.add_argument("--test", action="store_true", help="只采集前 10 只股票")
    parser.add_argument("--symbols", help="逗号分隔的股票代码，如 600519,000001")
    parser.add_argument("--limit", type=int, help="最多采集多少只股票")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数，建议 1-4")
    parser.add_argument("--delay", type=float, default=1.0, help="单请求成功后的延迟秒数")
    parser.add_argument("--batch-size", type=int, default=50, help="每批入库股票数")
    parser.add_argument("--force", action="store_true", help="忽略已采最新日期，强制重抓")
    parser.add_argument("--all-listed", action="store_true", help="扫描全部 A 股，不按北向持股名单过滤")
    args = parser.parse_args()

    collector = NorthboundHoldingsCollector()
    collector.request_delay = max(0.0, args.delay)

    symbols = None
    if args.symbols:
        symbols = [symbol.strip().zfill(6) for symbol in args.symbols.split(",") if symbol.strip()]

    if args.test:
        logger.info("🧪 测试模式: 只采集前10只股票")
        collector.run(
            collect_all_stocks=False,
            symbols=symbols,
            limit=args.limit,
            workers=args.workers,
            batch_size=args.batch_size,
            force=args.force,
            eligible_only=not args.all_listed,
        )
    else:
        logger.info("🚀 生产模式: 采集所有股票")
        collector.run(
            collect_all_stocks=True,
            symbols=symbols,
            limit=args.limit,
            workers=args.workers,
            batch_size=args.batch_size,
            force=args.force,
            eligible_only=not args.all_listed,
        )
