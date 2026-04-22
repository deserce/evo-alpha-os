"""
AKShare数据源适配器
封装AKShare API调用，提供统一的数据源接口
"""

import sys
import os
import logging
from typing import Optional
import pandas as pd
import akshare as ak

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from data_job.core.data_source import BaseDataSource

logger = logging.getLogger(__name__)


class AKShareAdapter(BaseDataSource):
    """AKShare数据源适配器 - 封装所有akshare API调用"""

    # 列名映射：AKShare中文列名 -> 标准英文列名
    COLUMN_MAPPINGS = {
        'get_stock_kline': {
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
            '换手率': 'turnover_rate',
            '涨跌额': 'change_amount',
            '振幅': 'amplitude'
        },
        'get_sector_kline': {
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
            '涨跌额': 'change_amount',
            '振幅': 'amplitude',
            '换手率': 'turnover_rate'
        },
        'get_etf_kline': {
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        },
        'get_limit_boards': {
            '代码': 'symbol',
            '名称': 'name',
            '涨跌幅': 'pct_chg',
            '最新价': 'latest_price',
            '成交额': 'amount',
            '流通市值': 'circ_mv',
            '总市值': 'total_mv',
            '换手率': 'turnover_rate',
            '封单资金': 'seal_amount',
            '首次封板时间': 'first_limit_time',
            '最后封板时间': 'last_limit_time',
            '炸板次数': 'break_count',
            '涨停统计': 'limit_stats',
            '连板数': 'boards',
            '所属行业': 'industry',
            '涨速': 'speed'
        },
        'get_fund_holdings': {
            '股票代码': 'symbol',
            '股票名称': 'name',
            '持有基金家数': 'fund_count',
            '持股总数': 'hold_count',
            '持股市值': 'hold_value',
            '持股变化': 'hold_change',
            '持股变动数值': 'change_value',
            '持股变动比例': 'change_ratio',
            '报告期': 'report_period'
        },
        'get_stock_valuation': {
            '代码': 'symbol',
            '名称': 'name',
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
            '流通市值': 'circ_mv',
            '市盈率-静念': 'pe_static',
            '市销率': 'ps',
            '市现率': 'pcf'
        }
    }

    def __init__(self, priority: int = 10, timeout: int = 30):
        """
        初始化AKShare适配器

        Args:
            priority: 数据源优先级（默认10，较高优先级）
            timeout: 请求超时时间（秒）
        """
        super().__init__(name="akshare", priority=priority)
        self.timeout = timeout
        logger.info(f"初始化AKShare适配器 (优先级={priority}, 超时={timeout}s)")

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
            DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, etc.
        """
        try:
            logger.debug(f"AKShare获取个股K线: {symbol} {start_date}-{end_date} {adjust}")

            # 映射复权参数
            adjust_map = {'qfq': 'qfq', 'hfq': 'hfq', '': ''}
            if adjust not in adjust_map:
                logger.warning(f"不支持的复权类型 {adjust}，使用前复权")
                adjust = 'qfq'

            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust_map.get(adjust, 'qfq')
            )

            if df is None or df.empty:
                logger.warning(f"AKShare返回空数据: {symbol}")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare个股K线获取失败 {symbol}: {e}")
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
            DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, etc.
        """
        try:
            logger.debug(f"AKShare获取板块K线: {sector_name} ({sector_type}) {start_date}-{end_date}")

            if sector_type == 'Industry':
                df = ak.stock_board_industry_hist_em(
                    symbol=sector_name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            elif sector_type == 'Concept':
                df = ak.stock_board_concept_hist_em(
                    symbol=sector_name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            else:
                raise ValueError(f"不支持的板块类型: {sector_type}")

            if df is None or df.empty:
                logger.warning(f"AKShare返回空数据: {sector_name} ({sector_type})")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare板块K线获取失败 {sector_name}: {e}")
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
            DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, etc.
        """
        try:
            logger.debug(f"AKShare获取ETF K线: {symbol} {start_date}-{end_date}")

            df = ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date
            )

            if df is None or df.empty:
                logger.warning(f"AKShare返回空数据: {symbol}")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare ETF K线获取失败 {symbol}: {e}")
            raise

    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame with columns: 代码, 名称, 涨跌幅, 最新价, etc.
        """
        try:
            logger.debug(f"AKShare获取涨跌停数据: {date_str}")

            df = ak.stock_zt_pool_em(date=date_str)

            if df is None or df.empty:
                logger.warning(f"AKShare返回空数据: {date_str}")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare涨跌停获取失败 {date_str}: {e}")
            raise

    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            DataFrame with columns: 股票代码, 股票名称, 持有基金家数, etc.
        """
        try:
            logger.debug(f"AKShare获取基金持仓: {quarter}")

            # 转换季度格式 (2024Q1 -> 2024-03-31)
            quarter_map = {
                'Q1': '-03-31',
                'Q2': '-06-30',
                'Q3': '-09-30',
                'Q4': '-12-31'
            }

            date_str = quarter
            for q, suffix in quarter_map.items():
                if q in quarter:
                    year = quarter.split(q)[0]
                    date_str = f"{year}{suffix}"
                    break

            df = ak.stock_report_fund_hold(date=date_str)

            if df is None or df.empty:
                logger.warning(f"AKShare返回空数据: {quarter}")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare基金持仓获取失败 {quarter}: {e}")
            raise

    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        Returns:
            DataFrame with columns: 代码, 名称, 最新价, 市盈率-动态, etc.
        """
        try:
            logger.debug("AKShare获取估值数据")

            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                logger.warning("AKShare返回空数据")
                return None

            return df

        except Exception as e:
            logger.error(f"AKShare估值数据获取失败: {e}")
            raise

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化AKShare中文列名为英文

        Args:
            df: 原始数据
            data_type: 数据类型（方法名）

        Returns:
            标准化后的DataFrame
        """
        if df is None or df.empty:
            return df

        if data_type in self.COLUMN_MAPPINGS:
            mapping = self.COLUMN_MAPPINGS[data_type]
            # 只重命名存在的列
            existing_mapping = {k: v for k, v in mapping.items() if k in df.columns}
            df = df.rename(columns=existing_mapping)
            logger.debug(f"标准化列名: {data_type}, 映射={existing_mapping}")

        return df

    def check_health(self) -> bool:
        """
        健康检查 - 尝试获取简单的数据验证AKShare是否可用

        Returns:
            True if healthy, False otherwise
        """
        try:
            # 尝试获取股票列表
            df = ak.stock_zh_a_spot_em()
            is_healthy = df is not None and not df.empty

            if is_healthy:
                logger.debug("AKShare健康检查通过")
            else:
                logger.warning("AKShare健康检查失败: 返回空数据")

            return is_healthy

        except Exception as e:
            logger.warning(f"AKShare健康检查失败: {e}")
            return False
