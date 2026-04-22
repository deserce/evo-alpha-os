"""
efinance数据源适配器
封装东方财富efinance API调用，提供统一的数据源接口
优势：完全免费、速度快、官方维护
"""

import sys
import os
import logging
from typing import Optional
from datetime import datetime
import pandas as pd

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from data_job.core.data_source import BaseDataSource

logger = logging.getLogger(__name__)

# 尝试导入efinance
try:
    import efinance as ef
    EFINANCE_AVAILABLE = True
except ImportError:
    EFINANCE_AVAILABLE = False
    logger.warning("efinance未安装，可通过以下命令安装: pip3 install efinance")


class EFinanceAdapter(BaseDataSource):
    """东方财富efinance数据源适配器 - 完全免费，速度快"""

    def __init__(self, priority: int = 8, timeout: int = 30):
        """
        初始化efinance适配器

        Args:
            priority: 数据源优先级（默认8，仅次于AKShare）
            timeout: 请求超时时间（秒）
        """
        super().__init__(name="efinance", priority=priority)

        if not EFINANCE_AVAILABLE:
            raise ImportError(
                "efinance未安装，无法使用东方财份数据源。请运行: pip3 install efinance"
            )

        self.timeout = timeout
        logger.info(f"初始化efinance适配器 (优先级={priority}, 超时={timeout}s)")

    def get_stock_kline(self, symbol: str, start_date: str, end_date: str,
                       adjust: str = 'qfq') -> Optional[pd.DataFrame]:
        """
        获取个股K线数据

        Args:
            symbol: 股票代码（如 '000001'）
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）
            adjust: 复权类型 ('qfq'-前复权, 'hfq'-后复权, ''-不复权)

        Returns:
            DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额
        """
        try:
            logger.debug(f"efinance获取个股K线: {symbol} {start_date}-{end_date} {adjust}")

            # efinance使用股票代码，无需转换
            # 日期格式转换：YYYYMMDD -> YYYY-MM-DD
            start_date_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
            end_date_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

            # 获取历史行情
            # klt=1 日线，fqt=1 前复权，fqt=0 不复权，fqt=2 后复权
            fqt_map = {'qfq': 1, 'hfq': 2, '': 0}
            fqt = fqt_map.get(adjust, 1)

            df = ef.stock.get_quote_history(
                stock_codes=symbol,
                beg=start_date_fmt,
                end=end_date_fmt,
                klt=1,  # 日线
                fqt=fqt  # 复权类型
            )

            if df is None or df.empty:
                logger.warning(f"efinance返回空数据: {symbol}")
                return None

            # efinance列名是中文，需要映射
            # 列名：股票代码, 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
            column_mapping = {
                '股票代码': 'symbol',
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '涨跌幅': 'pct_chg',
                '涨跌额': 'change_amount',
                '换手率': 'turnover_rate'
            }

            df = df.rename(columns=column_mapping)

            # 格式化日期
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')

            return df

        except Exception as e:
            logger.error(f"efinance个股K线获取失败 {symbol}: {e}")
            raise

    def get_sector_kline(self, sector_name: str, sector_type: str,
                        start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据

        Args:
            sector_name: 板块名称
            sector_type: 板块类型 ('Industry'-行业, 'Concept'-概念)
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        try:
            logger.debug(f"efinance获取板块K线: {sector_name} ({sector_type}) {start_date}-{end_date}")

            # 日期格式转换
            start_date_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
            end_date_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

            # efinance板块接口
            # 先获取板块代码
            if sector_type == 'Industry':
                # 行业板块
                sector_df = ef.stock.get_sector_base_info()
            else:
                # 概念板块
                sector_df = ef.stock.get_concept_base_info()

            if sector_df is None or sector_df.empty:
                logger.warning(f"未找到板块信息: {sector_name}")
                return None

            # 查找板块代码
            sector_row = sector_df[sector_df['板块名称'].str.contains(sector_name, na=False)]
            if sector_row.empty:
                logger.warning(f"未找到板块代码: {sector_name}")
                return None

            sector_code = sector_row.iloc[0]['板块代码']

            # 获取板块历史数据
            df = ef.stock.get_sector_history(
                symbol=sector_code,
                beg=start_date_fmt,
                end=end_date_fmt,
                klt=1  # 日线
            )

            if df is None or df.empty:
                logger.warning(f"efinance返回空数据: {sector_name}")
                return None

            # 列名映射
            column_mapping = {
                '板块代码': 'sector_code',
                '板块名称': 'sector_name',
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'pct_chg'
            }

            df = df.rename(columns=column_mapping)

            # 格式化日期
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')

            return df

        except Exception as e:
            logger.error(f"efinance板块K线获取失败 {sector_name}: {e}")
            raise

    def get_etf_kline(self, symbol: str, start_date: str,
                     end_date: str) -> Optional[pd.DataFrame]:
        """
        获取ETF K线数据

        Args:
            symbol: ETF代码
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）

        Returns:
            DataFrame with columns: trade_date, open, close, high, low, volume, amount
        """
        try:
            logger.debug(f"efinance获取ETF K线: {symbol} {start_date}-{end_date}")

            # 日期格式转换
            start_date_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
            end_date_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

            # 获取ETF历史数据
            df = ef.stock.get_quote_history(
                stock_codes=symbol,
                beg=start_date_fmt,
                end=end_date_fmt,
                klt=1,  # 日线
                fqt=0   # ETF不需要复权
            )

            if df is None or df.empty:
                logger.warning(f"efinance返回空数据: {symbol}")
                return None

            # 列名映射
            column_mapping = {
                '股票代码': 'symbol',
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'pct_chg'
            }

            df = df.rename(columns=column_mapping)

            # 格式化日期
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')

            return df

        except Exception as e:
            logger.error(f"efinance ETF K线获取失败 {symbol}: {e}")
            raise

    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        try:
            logger.debug(f"efinance获取涨跌停数据: {date_str}")

            # 日期格式转换
            date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

            # efinance获取涨停股
            df = ef.stock.get_realtime_quotes()  # 实时行情

            if df is None or df.empty:
                logger.warning(f"efinance返回空数据: {date_str}")
                return None

            # 过滤涨停/跌停股票
            # 涨停：涨跌幅 >= 9.9% (考虑ST股票5%)
            limit_up = df[df['涨跌幅'] >= 9.9].copy()
            limit_down = df[df['涨跌幅'] <= -9.9].copy()

            # 标记涨跌停
            limit_up['limit_type'] = '涨停'
            limit_down['limit_type'] = '跌停'

            # 合并
            result = pd.concat([limit_up, limit_down])

            # 列名映射
            column_mapping = {
                '股票代码': 'symbol',
                '股票名称': 'name',
                '涨跌幅': 'pct_chg',
                '最新价': 'latest_price',
                '成交额': 'amount',
                '成交量': 'volume',
                '换手率': 'turnover_rate',
                '市盈率-动态': 'pe_ttm',
                '市净率': 'pb'
            }

            result = result.rename(columns=column_mapping)

            return result

        except Exception as e:
            logger.error(f"efinance涨跌停获取失败 {date_str}: {e}")
            raise

    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            DataFrame or None
        """
        try:
            logger.debug(f"efinance获取基金持仓: {quarter}")

            # efinance可能不直接提供基金持仓数据
            # 尝试通过其他接口获取
            logger.warning("efinance基金持仓数据接口待实现")
            return None

        except Exception as e:
            logger.error(f"efinance基金持仓获取失败 {quarter}: {e}")
            return None

    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        Returns:
            DataFrame with columns: symbol, name, latest_price, pe_ttm, pb, etc.
        """
        try:
            logger.debug("efinance获取估值数据")

            # 获取实时行情
            df = ef.stock.get_realtime_quotes()

            if df is None or df.empty:
                logger.warning("efinance返回空数据")
                return None

            # 列名映射
            column_mapping = {
                '股票代码': 'symbol',
                '股票名称': 'name',
                '最新价': 'latest_price',
                '涨跌幅': 'pct_chg',
                '涨跌额': 'change_amount',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '最高': 'high',
                '最低': 'low',
                '今开': 'open',
                '昨收': 'pre_close',
                '换手率': 'turnover_rate',
                '市盈率-动态': 'pe_ttm',
                '市净率': 'pb',
                '总市值': 'total_mv',
                '流通市值': 'circ_mv'
            }

            df = df.rename(columns=column_mapping)

            return df

        except Exception as e:
            logger.error(f"efinance估值数据获取失败: {e}")
            raise

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化列名（efinance已在各方法中处理）

        Args:
            df: 原始数据
            data_type: 数据类型

        Returns:
            标准化后的DataFrame
        """
        # efinance的列名已在各get方法中统一处理
        return df

    def check_health(self) -> bool:
        """
        健康检查 - 测试efinance是否可用

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not EFINANCE_AVAILABLE:
                return False

            # 测试获取简单的数据
            df = ef.stock.get_realtime_quotes()

            is_healthy = df is not None and not df.empty

            if is_healthy:
                logger.debug("efinance健康检查通过")
            else:
                logger.warning("efinance健康检查失败: 返回空数据")

            return is_healthy

        except Exception as e:
            logger.warning(f"efinance健康检查失败: {e}")
            return False
