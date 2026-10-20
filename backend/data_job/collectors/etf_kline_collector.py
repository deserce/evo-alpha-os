"""
EvoAlpha OS - ETF K线数据采集器
获取 ETF 基金的日级行情数据
"""

import time
import argparse
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta, date

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.mootdx_adapter import MootdxAdapter
from data_core.market_calendar import is_a_share_session

from app.core.database import get_active_engines

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def latest_completed_trading_date(now: datetime | None = None):
    current = now or datetime.now()
    candidate = current.date()
    if (current.hour, current.minute) < (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE):
        candidate -= timedelta(days=1)
    while not is_a_share_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


class ETFKlineCollector(BaseCollector):
    """ETF K线数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="etf_kline",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.table_name = "etf_daily_prices"
        self.http_failure_count = 0
        self.http_circuit_threshold = 3

    def _init_table(self):
        """初始化 ETF K线表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建表 {self.table_name}...")
            try:
                with engine.begin() as conn:
                    inspector_result = conn.execute(text(f"""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='{self.table_name}'
                    """))
                    exists = inspector_result.fetchone() is not None

                    if not exists:
                        conn.execute(text(f"""
                            CREATE TABLE {self.table_name} (
                                symbol VARCHAR(20),
                                trade_date DATE,
                                open FLOAT,
                                high FLOAT,
                                low FLOAT,
                                close FLOAT,
                                volume FLOAT,
                                amount FLOAT,
                                pct_chg FLOAT,
                                PRIMARY KEY (symbol, trade_date)
                            );
                        """))
                        try:
                            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON {self.table_name} (symbol);"))
                        except Exception:
                            pass
                        try:
                            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON {self.table_name} (trade_date);"))
                        except Exception:
                            pass
                        logger.info(f"✅ [{mode}] 表 {self.table_name} 创建成功")
                    else:
                        logger.info(f"ℹ️  [{mode}] 表 {self.table_name} 已存在")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建表失败: {e}")

    def get_etf_list(self):
        """从数据库获取 ETF 列表"""
        etf_list = []
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(text("SELECT symbol FROM etf_info"), conn)
                    if not df.empty:
                        etf_list = df['symbol'].tolist()
                        logger.info(f"✅ [{mode}] 从 etf_info 获取到 {len(etf_list)} 只 ETF")
                        break
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取 ETF 列表失败: {e}")
                continue

        return etf_list

    def get_last_dates(self):
        """获取增量更新进度 - 每个ETF的最后日期"""
        last_dates = {}
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    query = text(f"SELECT symbol, MAX(trade_date) as last_date FROM {self.table_name} GROUP BY symbol")
                    df = pd.read_sql(query, conn)
                    if not df.empty:
                        last_dates = dict(zip(df['symbol'], pd.to_datetime(df['last_date']).dt.date))
                        logger.info(f"✅ [{mode}] 获取到 {len(last_dates)} 只 ETF 的最后日期")
                        break
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取最后日期失败: {e}")
                continue

        return last_dates

    def _normalize_etf_kline(
        self,
        symbol,
        df,
        save_start=None,
        save_end=None,
    ):
        """统一 ETF K 线字段，先计算涨跌幅再裁剪保存窗口。"""
        if df is None or df.empty:
            return None

        df = df.copy()
        df = df.rename(columns={
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
        })

        if "trade_date" not in df.columns:
            logger.warning(f"⚠️  ETF {symbol} 缺少 trade_date 字段")
            return None

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["symbol"] = symbol
        for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
        if "pct_chg" not in df.columns or df["pct_chg"].isna().all():
            df["pct_chg"] = df["close"].pct_change() * 100

        if save_start:
            df = df[df["trade_date"] >= pd.to_datetime(save_start)]
        if save_end:
            df = df[df["trade_date"] <= pd.to_datetime(save_end)]

        columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        df = df[[c for c in columns if c in df.columns]]
        if df.empty:
            return None
        return df.reset_index(drop=True)

    def _fetch_from_tdx(self, adapter, symbol, start_date=None, end_date=None, max_bars=None):
        if adapter is None or not adapter.is_available():
            return None

        df = adapter.get_etf_kline(
            symbol,
            start_date=start_date.strftime("%Y-%m-%d") if start_date else None,
            end_date=end_date.strftime("%Y-%m-%d") if end_date else None,
            period="daily",
            max_bars=max_bars,
        )
        if df is None or df.empty:
            return None
        return df

    def _fetch_from_eastmoney(self, symbol, start_date=None, end_date=None):
        """
        使用 Eastmoney/AkShare HTTP 获取单个 ETF 的K线数据。

        Args:
            symbol: ETF 代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame: K线数据
        """
        if self.http_failure_count >= self.http_circuit_threshold:
            logger.warning(
                f"⚠️  HTTP ETF源熔断，跳过 {symbol}: failures={self.http_failure_count}"
            )
            return None

        try:
            if end_date is None:
                end_date = datetime.now()

            if start_date is None:
                start_date = datetime.now() - timedelta(days=1095)

            start_str = start_date.strftime('%Y%m%d')
            end_str = end_date.strftime('%Y%m%d')

            df = self._retry_call(
                ak.fund_etf_hist_em,
                max_retries=2,
                symbol=symbol, period="daily", start_date=start_str, end_date=end_str
            )

            if df is None or df.empty:
                self.http_failure_count += 1
                logger.warning(f"⚠️  ETF {symbol} 无K线数据")
                return None

            logger.info(f"  ✅ {symbol}: {len(df)} 条K线数据")
            return df

        except Exception as e:
            self.http_failure_count += 1
            logger.error(f"❌ 获取 ETF {symbol} K线失败: {e}")
            return None

    def fetch_etf_kline(
        self,
        symbol,
        start_date=None,
        end_date=None,
        save_start=None,
        save_end=None,
        max_bars=None,
        tdx_adapter=None,
    ):
        """获取单个 ETF K 线，TDX 优先，HTTP 源只做降级。"""
        df = self._fetch_from_tdx(
            tdx_adapter,
            symbol,
            start_date=start_date,
            end_date=end_date,
            max_bars=max_bars,
        )
        if df is not None and not df.empty:
            normalized = self._normalize_etf_kline(
                symbol,
                df,
                save_start=save_start,
                save_end=save_end,
            )
            if normalized is not None and not normalized.empty:
                logger.info(f"  ✅ {symbol}: TDX采集 {len(normalized)} 条K线数据")
                return normalized

        logger.warning(f"  ⚠️  ETF {symbol} TDX无数据，回退 HTTP 源")
        df = self._fetch_from_eastmoney(symbol, start_date=start_date, end_date=end_date)
        return self._normalize_etf_kline(
            symbol,
            df,
            save_start=save_start,
            save_end=save_end,
        )

    def save_etf_kline(self, symbol, df):
        """
        保存 ETF K线数据

        Args:
            symbol: ETF 代码
            df: K线数据
        """
        if df is None or df.empty:
            return False

        save_df = df.copy()
        save_df["trade_date"] = pd.to_datetime(save_df["trade_date"]).dt.strftime("%Y-%m-%d")
        start_date = save_df["trade_date"].min()
        end_date = save_df["trade_date"].max()
        saved = False

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 只覆盖本次返回的日期区间，避免增量更新误删历史数据。
                    conn.execute(text(f"""
                        DELETE FROM {self.table_name}
                        WHERE symbol = :symbol
                          AND DATE(trade_date) BETWEEN DATE(:start_date) AND DATE(:end_date)
                    """), {
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                    })

                    # 插入新数据（使用 chunksize 避免 SQLite 变量限制）
                    save_df.to_sql(
                        self.table_name,
                        conn,
                        if_exists='append',
                        index=False,
                        method='multi',
                        chunksize=100,
                    )

                saved = True
                logger.debug(f"✅ [{mode}] {symbol} 保存 {len(save_df)} 条K线")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 {symbol} K线失败: {e}")

        return saved

    def run(self, symbols=None, days=1095, start=None, end=None, max_bars=None):
        """
        执行 ETF K线采集

        Args:
            symbols: ETF 代码列表，如果为None则从数据库获取
            days: 采集天数（默认1095天=3年）
        """
        self.log_collection_start()
        logger.info("🚀 开始采集 ETF K线数据...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return False

        # 初始化表
        self._init_table()

        # 获取 ETF 列表
        if symbols is None:
            symbols = self.get_etf_list()

        if not symbols:
            logger.error("❌ 未找到 ETF 列表，请先运行 etf_info_collector.py")
            self.log_collection_end(False, "无ETF列表")
            return False

        # 获取增量更新进度
        last_dates = self.get_last_dates()
        target_end = pd.to_datetime(end).date() if end else latest_completed_trading_date()
        explicit_start = pd.to_datetime(start).date() if start else None
        tdx_adapter = MootdxAdapter(max_retries=2, timeout=8)

        # 采集每个 ETF 的K线
        success_count = 0
        skipped_count = 0
        fail_count = 0
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] 采集 {symbol}...")

            try:
                # 增量更新：检查最后日期
                last_date = last_dates.get(symbol)
                start_date = explicit_start
                save_start = explicit_start

                if explicit_start:
                    start_date = explicit_start - timedelta(days=10)
                    logger.info(f"  📅 指定窗口: {explicit_start} 至 {target_end}")
                elif last_date:
                    # 如果已有数据，检查是否需要更新
                    if last_date >= target_end:
                        logger.info(f"  ⏭️  {symbol} 数据已是最新 (最后日期: {last_date})")
                        skipped_count += 1
                        continue
                    else:
                        # 多取一个历史窗口，先算 pct_chg，再只保存新日期。
                        save_start = last_date + timedelta(days=1)
                        start_date = max(last_date - timedelta(days=10), date(1990, 1, 1))
                        logger.info(f"  📅 增量更新: {save_start} 至 {target_end}")
                else:
                    # 首次采集，采集最近3年数据
                    start_date = target_end - timedelta(days=days)
                    save_start = start_date
                    logger.info(f"  🆕 首次采集: 从 {start_date} 至 {target_end}")

                effective_max_bars = max_bars
                if effective_max_bars is None:
                    effective_max_bars = 60 if last_date else days + 30

                df = self.fetch_etf_kline(
                    symbol,
                    start_date=start_date,
                    end_date=datetime.combine(target_end, datetime.min.time()),
                    save_start=save_start,
                    save_end=target_end,
                    max_bars=effective_max_bars,
                    tdx_adapter=tdx_adapter,
                )
                if df is not None and not df.empty:
                    if self.save_etf_kline(symbol, df):
                        success_count += 1
                        logger.info(f"  ✅ {symbol} 采集成功: {len(df)} 条记录")
                    else:
                        fail_count += 1
                        logger.error(f"  ❌ {symbol} 保存失败")
                elif df is not None and df.empty:
                    logger.info(f"  ℹ️  {symbol} 无新数据")
                    skipped_count += 1
                else:
                    fail_count += 1

                # 避免请求过快
                time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"❌ {symbol} 采集失败: {e}")
                fail_count += 1
                continue

        logger.info(
            f"🎉 ETF K线采集完成，成功 {success_count}/{len(symbols)}，"
            f"跳过 {skipped_count}，失败 {fail_count}"
        )
        if success_count == 0 and skipped_count == 0:
            message = f"无成功 ETF，失败 {fail_count}/{len(symbols)}"
            self.log_collection_end(False, message)
            return False

        stale_count = self._count_missing_target_rows(symbols, target_end)
        if stale_count:
            message = f"目标日 {target_end} 缺失 ETF 数={stale_count}"
            logger.error(f"❌ {message}")
            self.log_collection_end(False, message)
            return False

        if fail_count:
            message = f"ETF 保存/采集失败 {fail_count}/{len(symbols)}"
            logger.error(f"❌ {message}")
            self.log_collection_end(False, message)
            return False

        self.log_collection_end(
            True,
            f"成功 {success_count}/{len(symbols)}，跳过 {skipped_count}，失败 {fail_count}",
        )
        return True

    def _count_missing_target_rows(self, symbols, target_date):
        """统计目标日仍缺失的 ETF 数量。"""
        if not symbols:
            return 0

        missing = len(symbols)
        for _mode, engine in self.engines:
            try:
                unique_symbols = sorted(set(symbols))
                placeholders = ", ".join(f":symbol_{i}" for i, _symbol in enumerate(unique_symbols))
                params = {
                    "target_date": target_date.strftime("%Y-%m-%d"),
                    **{f"symbol_{i}": symbol for i, symbol in enumerate(unique_symbols)},
                }
                with engine.connect() as conn:
                    row = conn.execute(text(f"""
                        SELECT COUNT(DISTINCT symbol)
                        FROM {self.table_name}
                        WHERE DATE(trade_date) = DATE(:target_date)
                          AND symbol IN ({placeholders})
                    """), params).fetchone()
                    present = int(row[0]) if row else 0
                    missing = len(unique_symbols) - present
                    break
            except Exception as e:
                logger.warning(f"⚠️  ETF 目标日缺失检查失败: {e}")
        return missing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect ETF daily K-lines")
    parser.add_argument("--start", help="start date, e.g. 2026-06-03")
    parser.add_argument("--end", help="end date, e.g. 2026-06-04")
    parser.add_argument("--max-bars", type=int, default=None, help="max recent TDX bars")
    parser.add_argument("--symbols", help="comma separated ETF symbols")
    args = parser.parse_args()

    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
    collector = ETFKlineCollector()
    raise SystemExit(
        0 if collector.run(
            symbols=symbols,
            start=args.start,
            end=args.end,
            max_bars=args.max_bars,
        ) else 1
    )
