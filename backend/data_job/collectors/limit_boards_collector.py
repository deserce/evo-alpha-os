"""EvoAlpha OS - 连板数据采集器。"""

import argparse
import time
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta, date

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector
from data_job.core.direct_market_sources import EastmoneyDirectClient, normalize_symbol, safe_float

from app.core.database import get_active_engines

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class LimitBoardsCollector(BaseCollector):
    """连板数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="limit_boards",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.boards_table = "limit_board_trading"
        self.down_table = "limit_down_trading"
        self.stats_table = "consecutive_boards_stats"
        self.break_table = "limit_break_trading"
        self.yesterday_table = "yesterday_limit_trading"
        self.em = EastmoneyDirectClient()

    @staticmethod
    def _parse_date(value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        return pd.to_datetime(value, errors="raise").date()

    def _init_tables(self):
        """初始化连板数据表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建连板数据表...")
            try:
                with engine.begin() as conn:
                    # 涨停板交易表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.boards_table} (
                            trade_date DATE,
                            symbol VARCHAR(20),
                            name VARCHAR(100),
                            pct_chg FLOAT,
                            latest_price FLOAT,
                            amount FLOAT,
                            circ_mv FLOAT,
                            total_mv FLOAT,
                            turnover_rate FLOAT,
                            seal_amount FLOAT,
                            first_limit_time VARCHAR(10),
                            last_limit_time VARCHAR(10),
                            break_count INT,
                            limit_stats VARCHAR(50),
                            boards INT,
                            industry VARCHAR(100),
                            PRIMARY KEY (trade_date, symbol)
                        );
                    """))
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_boards_date ON {self.boards_table} (trade_date);"))
                    except Exception:
                        pass
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_boards_symbol ON {self.boards_table} (symbol);"))
                    except Exception:
                        pass

                    # 跌停板交易表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.down_table} (
                            trade_date DATE,
                            symbol VARCHAR(20),
                            name VARCHAR(100),
                            pct_chg FLOAT,
                            latest_price FLOAT,
                            amount FLOAT,
                            circ_mv FLOAT,
                            total_mv FLOAT,
                            pe_dynamic FLOAT,
                            turnover_rate FLOAT,
                            seal_amount FLOAT,
                            last_limit_time VARCHAR(10),
                            board_amount FLOAT,
                            consecutive_down INT,
                            break_count INT,
                            industry VARCHAR(100),
                            PRIMARY KEY (trade_date, symbol)
                        );
                    """))
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_down_date ON {self.down_table} (trade_date);"))
                    except Exception:
                        pass
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_down_symbol ON {self.down_table} (symbol);"))
                    except Exception:
                        pass

                    # 连板统计表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.stats_table} (
                            trade_date DATE,
                            boards INT,
                            stock_count INT,
                            PRIMARY KEY (trade_date, boards)
                        );
                    """))
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_stats_date ON {self.stats_table} (trade_date);"))
                    except Exception:
                        pass

                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.break_table} (
                            trade_date DATE,
                            symbol VARCHAR(20),
                            name VARCHAR(100),
                            latest_price FLOAT,
                            limit_price FLOAT,
                            pct_chg FLOAT,
                            turnover_rate FLOAT,
                            first_limit_time VARCHAR(10),
                            break_count INT,
                            amplitude FLOAT,
                            speed FLOAT,
                            industry VARCHAR(100),
                            limit_stats VARCHAR(50),
                            source VARCHAR(50),
                            fetched_at VARCHAR(30),
                            PRIMARY KEY (trade_date, symbol)
                        );
                    """))
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.yesterday_table} (
                            trade_date DATE,
                            symbol VARCHAR(20),
                            name VARCHAR(100),
                            latest_price FLOAT,
                            pct_chg FLOAT,
                            turnover_rate FLOAT,
                            amplitude FLOAT,
                            speed FLOAT,
                            y_first_limit_time VARCHAR(10),
                            y_boards INT,
                            industry VARCHAR(100),
                            limit_stats VARCHAR(50),
                            source VARCHAR(50),
                            fetched_at VARCHAR(30),
                            PRIMARY KEY (trade_date, symbol)
                        );
                    """))

                    logger.info(f"✅ [{mode}] 连板表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建连板表失败: {e}")

    def fetch_limit_boards(self, date_str=None):
        """
        获取涨停板数据

        Args:
            date_str: 日期字符串（YYYYMMDD）

        Returns:
            DataFrame: 涨停板数据
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')

            try:
                df = self._fetch_em_limit_boards(date_str)
            except Exception as direct_error:
                logger.warning(f"⚠️  东财直连涨停池失败，使用 AkShare 兜底: {direct_error}")
                df = None

            if df is None or df.empty:
                # 使用 AkShare 获取涨停板（带重试）
                df = self._retry_call(ak.stock_zt_pool_em, date=date_str)

            if df.empty:
                logger.warning(f"⚠️  {date_str} 无涨停板数据")
                return None

            # 数据清洗：映射中文字段
            df = df.rename(columns={
                '代码': 'symbol',
                '名称': 'name',
                '涨跌幅': 'pct_chg',
                '最新价': 'latest_price',
                '成交额': 'amount',
                '流通市值': 'circ_mv',
                '总市值': 'total_mv',
                '换手率': 'turnover_rate',
                '封板资金': 'seal_amount',
                '首次封板时间': 'first_limit_time',
                '最后封板时间': 'last_limit_time',
                '炸板次数': 'break_count',
                '涨停统计': 'limit_stats',
                '连板数': 'boards',
                '所属行业': 'industry'
            })

            # 添加日期
            df['trade_date'] = pd.to_datetime(date_str)

            # 转换数据类型
            numeric_cols = ['pct_chg', 'latest_price', 'amount', 'circ_mv', 'total_mv',
                          'turnover_rate', 'seal_amount', 'break_count', 'boards']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            df = df[
                (df['symbol'].astype(str).str.match(r'^\d{6}$'))
                & (df['latest_price'] > 0)
                & (df['pct_chg'].between(0, 35))
            ].copy()
            df = df.drop_duplicates(subset=['trade_date', 'symbol'], keep='last')

            # 选择需要的列
            df = df[['trade_date', 'symbol', 'name', 'pct_chg', 'latest_price',
                    'amount', 'circ_mv', 'total_mv', 'turnover_rate', 'seal_amount',
                    'first_limit_time', 'last_limit_time', 'break_count',
                    'limit_stats', 'boards', 'industry']]

            logger.info(f"  ✅ 涨停板: {len(df)} 只")
            return df

        except Exception as e:
            logger.error(f"❌ 获取涨停板数据失败: {e}")
            return None

    def fetch_limit_downs(self, date_str=None):
        """
        获取跌停板数据

        Args:
            date_str: 日期字符串（YYYYMMDD）

        Returns:
            DataFrame: 跌停板数据
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')

            try:
                df = self._fetch_em_limit_downs(date_str)
            except Exception as direct_error:
                logger.warning(f"⚠️  东财直连跌停池失败，使用 AkShare 兜底: {direct_error}")
                df = None

            if df is None or df.empty:
                df = self._retry_call(ak.stock_zt_pool_dtgc_em, date=date_str)

            if df is None or df.empty:
                logger.info(f"ℹ️  {date_str} 无跌停板数据")
                return None

            df = df.rename(columns={
                '代码': 'symbol',
                '名称': 'name',
                '涨跌幅': 'pct_chg',
                '最新价': 'latest_price',
                '成交额': 'amount',
                '流通市值': 'circ_mv',
                '总市值': 'total_mv',
                '动态市盈率': 'pe_dynamic',
                '换手率': 'turnover_rate',
                '封单资金': 'seal_amount',
                '最后封板时间': 'last_limit_time',
                '板上成交额': 'board_amount',
                '连续跌停': 'consecutive_down',
                '开板次数': 'break_count',
                '所属行业': 'industry'
            })

            df['trade_date'] = pd.to_datetime(date_str)

            numeric_cols = [
                'pct_chg', 'latest_price', 'amount', 'circ_mv', 'total_mv',
                'pe_dynamic', 'turnover_rate', 'seal_amount', 'board_amount',
                'consecutive_down', 'break_count'
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            cols = [
                'trade_date', 'symbol', 'name', 'pct_chg', 'latest_price',
                'amount', 'circ_mv', 'total_mv', 'pe_dynamic', 'turnover_rate',
                'seal_amount', 'last_limit_time', 'board_amount',
                'consecutive_down', 'break_count', 'industry'
            ]
            for col in cols:
                if col not in df.columns:
                    df[col] = 0
            df = df[
                (df['symbol'].astype(str).str.match(r'^\d{6}$'))
                & (df['latest_price'] > 0)
                & (df['pct_chg'].between(-35, 0))
            ].copy()
            df = df.drop_duplicates(subset=['trade_date', 'symbol'], keep='last')
            df = df[cols]

            logger.info(f"  ✅ 跌停板: {len(df)} 只")
            return df

        except Exception as e:
            logger.error(f"❌ 获取跌停板数据失败: {e}")
            return None

    def _fetch_em_limit_boards(self, date_str):
        rows = self.em.limit_pool("zt", date_str)
        return self._normalize_em_pool(rows, date_str, mode="zt")

    def _fetch_em_limit_downs(self, date_str):
        rows = self.em.limit_pool("dt", date_str)
        return self._normalize_em_pool(rows, date_str, mode="dt")

    def fetch_limit_breaks(self, date_str=None):
        if date_str is None:
            date_str = datetime.now().strftime('%Y%m%d')
        try:
            rows = self.em.limit_pool("zb", date_str)
            df = self._normalize_em_pool(rows, date_str, mode="zb")
            if df is None or df.empty:
                return None
            cols = [
                'trade_date', 'symbol', 'name', 'latest_price', 'limit_price',
                'pct_chg', 'turnover_rate', 'first_limit_time', 'break_count',
                'amplitude', 'speed', 'industry', 'limit_stats', 'source', 'fetched_at'
            ]
            for col in cols:
                if col not in df.columns:
                    df[col] = None
            return df[cols]
        except Exception as e:
            logger.warning(f"⚠️  获取炸板数据失败: {e}")
            return None

    def fetch_yesterday_limits(self, date_str=None):
        if date_str is None:
            date_str = datetime.now().strftime('%Y%m%d')
        try:
            rows = self.em.limit_pool("yzt", date_str)
            df = self._normalize_em_pool(rows, date_str, mode="yzt")
            if df is None or df.empty:
                return None
            cols = [
                'trade_date', 'symbol', 'name', 'latest_price', 'pct_chg',
                'turnover_rate', 'amplitude', 'speed', 'y_first_limit_time',
                'y_boards', 'industry', 'limit_stats', 'source', 'fetched_at'
            ]
            for col in cols:
                if col not in df.columns:
                    df[col] = None
            return df[cols]
        except Exception as e:
            logger.warning(f"⚠️  获取昨涨停数据失败: {e}")
            return None

    def _normalize_em_pool(self, rows, date_str, mode):
        if not rows:
            return pd.DataFrame()
        trade_date = pd.to_datetime(date_str)
        normalized = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in rows:
            symbol = normalize_symbol(item.get("c") or item.get("f12") or item.get("code"))
            if not symbol or not symbol.isdigit():
                continue
            record = {
                "trade_date": trade_date,
                "symbol": symbol,
                "name": item.get("n") or item.get("f14") or item.get("name"),
                "pct_chg": safe_float(item.get("zdp") or item.get("f3")),
                "latest_price": safe_float(item.get("p") or item.get("f2")),
                "amount": safe_float(item.get("amount") or item.get("f6")),
                "circ_mv": safe_float(item.get("ltsz") or item.get("f21")),
                "total_mv": safe_float(item.get("tshare") or item.get("f20")),
                "turnover_rate": safe_float(item.get("hs") or item.get("hsl") or item.get("f8")),
                "seal_amount": safe_float(item.get("fund") or item.get("fdbj") or item.get("zbc")),
                "first_limit_time": item.get("fbt") or item.get("first_limit_time"),
                "last_limit_time": item.get("lbt") or item.get("last_limit_time"),
                "break_count": int(safe_float(item.get("zbc") or item.get("kbj") or 0) or 0),
                "limit_stats": item.get("zttj") or item.get("limit_stats"),
                "boards": int(safe_float(item.get("lbc") or item.get("days") or 0) or 0),
                "industry": item.get("hybk") or item.get("industry"),
                "pe_dynamic": safe_float(item.get("pe") or item.get("f9")),
                "board_amount": safe_float(item.get("board_amount")),
                "consecutive_down": int(safe_float(item.get("days") or 0) or 0),
                "limit_price": safe_float(item.get("ztj") or item.get("limit_price")),
                "amplitude": safe_float(item.get("zf") or item.get("f7")),
                "speed": safe_float(item.get("zs") or item.get("f22")),
                "y_first_limit_time": item.get("fbt") or item.get("first_limit_time"),
                "y_boards": int(safe_float(item.get("lbc") or item.get("days") or 0) or 0),
                "source": "eastmoney_direct",
                "fetched_at": now,
            }
            normalized.append(record)
        df = pd.DataFrame(normalized)
        if df.empty:
            return df
        if mode == "dt":
            cols = [
                'trade_date', 'symbol', 'name', 'pct_chg', 'latest_price',
                'amount', 'circ_mv', 'total_mv', 'pe_dynamic', 'turnover_rate',
                'seal_amount', 'last_limit_time', 'board_amount',
                'consecutive_down', 'break_count', 'industry'
            ]
        elif mode == "zt":
            cols = [
                'trade_date', 'symbol', 'name', 'pct_chg', 'latest_price',
                'amount', 'circ_mv', 'total_mv', 'turnover_rate', 'seal_amount',
                'first_limit_time', 'last_limit_time', 'break_count',
                'limit_stats', 'boards', 'industry'
            ]
        else:
            return df
        for col in cols:
            if col not in df.columns:
                df[col] = None
        return df[cols].drop_duplicates(subset=['trade_date', 'symbol'], keep='last')

    def save_limit_boards(self, df):
        """
        保存涨停板数据

        Args:
            df: 涨停板数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除旧数据
                    trade_date_str = df['trade_date'].iloc[0].strftime('%Y-%m-%d')
                    conn.execute(text(f"""
                        DELETE FROM {self.boards_table}
                        WHERE DATE(trade_date) = DATE(:trade_date)
                    """), {"trade_date": trade_date_str})

                    # 插入新数据（使用 chunksize 避免 SQLite 变量限制）
                    df.to_sql(self.boards_table, conn, if_exists='append', index=False,
                             method='multi', chunksize=100)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条涨停板数据")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存涨停板数据失败: {e}")

    def save_limit_downs(self, df):
        """
        保存跌停板数据

        Args:
            df: 跌停板数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    trade_date_str = df['trade_date'].iloc[0].strftime('%Y-%m-%d')
                    conn.execute(text(f"""
                        DELETE FROM {self.down_table}
                        WHERE DATE(trade_date) = DATE(:trade_date)
                    """), {"trade_date": trade_date_str})

                    df.to_sql(self.down_table, conn, if_exists='append', index=False,
                              method='multi', chunksize=100)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条跌停板数据")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存跌停板数据失败: {e}")

    def save_enhanced_pool(self, df, table_name):
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    trade_date_str = df['trade_date'].iloc[0].strftime('%Y-%m-%d')
                    conn.execute(text(f"""
                        DELETE FROM {table_name}
                        WHERE DATE(trade_date) = DATE(:trade_date)
                    """), {"trade_date": trade_date_str})
                    df.to_sql(table_name, conn, if_exists='append', index=False, method='multi', chunksize=100)
                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条 {table_name} 数据")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 {table_name} 失败: {e}")

    def calculate_stats(self, df):
        """
        计算连板统计

        Args:
            df: 涨停板数据

        Returns:
            DataFrame: 连板统计
        """
        if df is None or df.empty:
            return None

        try:
            # 统计每个连板高度有多少只股票
            stats = df['boards'].value_counts().reset_index()
            stats.columns = ['boards', 'stock_count']

            # 添加日期
            stats['trade_date'] = df['trade_date'].iloc[0]

            return stats

        except Exception as e:
            logger.error(f"❌ 计算连板统计失败: {e}")
            return None

    def save_stats(self, stats_df):
        """
        保存连板统计

        Args:
            stats_df: 连板统计数据
        """
        if stats_df is None or stats_df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除旧数据
                    trade_date_str = stats_df['trade_date'].iloc[0].strftime('%Y-%m-%d')
                    conn.execute(text(f"""
                        DELETE FROM {self.stats_table}
                        WHERE DATE(trade_date) = DATE(:trade_date)
                    """), {"trade_date": trade_date_str})

                    # 插入新数据
                    stats_df.to_sql(self.stats_table, conn, if_exists='append', index=False,
                                   method='multi', chunksize=100)

                    logger.info(f"✅ [{mode}] 保存 {len(stats_df)} 条连板统计")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存连板统计失败: {e}")

    def get_last_date(self):
        """获取最后采集的日期"""
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    query = text(f"SELECT MAX(trade_date) as last_date FROM {self.boards_table}")
                    result = conn.execute(query).scalar()
                    if result:
                        # 确保返回 date 对象
                        if isinstance(result, str):
                            from datetime import datetime
                            result = datetime.strptime(result, '%Y-%m-%d').date()
                        elif isinstance(result, datetime):
                            result = result.date()
                        logger.info(f"✅ [{mode}] 最后采集日期: {result}")
                        return result
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取最后日期失败: {e}")
                continue

        return None

    def run(self, days=None, start=None, end=None):
        """
        执行连板数据采集（增量更新）

        Args:
            days: 采集最近几天的数据（仅用于首次采集或手动指定）
                   None 表示增量更新（只采集缺失的日期）
            start: 显式起始日期（YYYY-MM-DD），优先于 days
            end: 显式结束日期（YYYY-MM-DD），优先于今天
        """
        self.log_collection_start()
        logger.info("🚀 开始采集连板数据...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_tables()

        # 确定采集日期范围
        start_target = self._parse_date(start)
        end_target = self._parse_date(end)
        if start_target or end_target:
            if start_target is None:
                start_target = end_target
            if end_target is None:
                end_target = start_target
            start_date = start_target
            today = end_target
            logger.info(f"📅 目标日期模式：采集 {start_date} 至 {today}")
        elif days is not None:
            # 手动指定天数
            start_date = date.today() - timedelta(days=days-1)
            today = date.today()
            logger.info(f"📅 手动模式：采集最近 {days} 天数据")
        else:
            # 增量更新模式：获取最后采集日期
            last_date = self.get_last_date()
            if last_date:
                # 从最后日期+1天开始采集
                start_date = last_date + timedelta(days=1)
                logger.info(f"📅 增量模式：从 {start_date} 至今")
            else:
                # 首次采集，采集最近5天
                start_date = date.today() - timedelta(days=4)
                logger.info(f"🆕 首次采集：采集最近5天数据")

            today = date.today()

        # 检查是否需要更新
        if start_date > today:
            logger.info(f"✅ 数据已是最新，无需更新")
            self.log_collection_end(True, "数据已是最新")
            return

        # 计算需要采集的天数
        days_to_collect = (today - start_date).days + 1
        logger.info(f"📊 需要采集 {days_to_collect} 天数据")

        # 采集数据
        total_count = 0
        success_count = 0
        for i in range(days_to_collect):
            current_date = start_date + timedelta(days=i)
            date_str = current_date.strftime('%Y%m%d')

            logger.info(f"📊 [{i+1}/{days_to_collect}] 采集 {date_str} 的涨停板数据...")

            try:
                # 获取涨停板数据
                df = self.fetch_limit_boards(date_str)
                down_df = self.fetch_limit_downs(date_str)
                break_df = self.fetch_limit_breaks(date_str)
                yesterday_df = self.fetch_yesterday_limits(date_str)

                if df is not None:
                    # 保存涨停板数据
                    self.save_limit_boards(df)
                    total_count += len(df)

                    # 计算连板统计
                    stats = self.calculate_stats(df)
                    if stats is not None:
                        self.save_stats(stats)

                    success_count += 1

                if down_df is not None:
                    self.save_limit_downs(down_df)
                if break_df is not None:
                    self.save_enhanced_pool(break_df, self.break_table)
                if yesterday_df is not None:
                    self.save_enhanced_pool(yesterday_df, self.yesterday_table)

                # 避免请求过快
                if i < days_to_collect - 1:
                    time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"❌ {date_str} 采集失败: {e}")
                continue

        logger.info(f"🎉 连板数据采集完成，成功 {success_count}/{days_to_collect} 天，共 {total_count} 条涨停板数据")
        self.log_collection_end(True, f"成功 {success_count}/{days_to_collect} 天，共 {total_count} 条数据")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect limit board data")
    parser.add_argument("--start", help="start date, e.g. 2026-06-04")
    parser.add_argument("--end", help="end date, e.g. 2026-06-04")
    parser.add_argument("--days", type=int, default=5, help="fallback recent-day window")
    args = parser.parse_args()
    collector = LimitBoardsCollector()
    collector.run(days=args.days, start=args.start, end=args.end)
