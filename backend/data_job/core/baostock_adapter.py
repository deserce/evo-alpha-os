"""
BaoStock数据源适配器
封装BaoStock API调用，提供统一的数据源接口
优势：完全免费、可注册、当前网络环境下可用
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

# 尝试导入baostock
try:
    import baostock as bs
    BAOSTOCK_AVAILABLE = True
except ImportError:
    BAOSTOCK_AVAILABLE = False
    logger.warning("baostock未安装，可通过以下命令安装: pip3 install baostock")


class BaoStockAdapter(BaseDataSource):
    """BaoStock数据源适配器 - 完全免费，当前网络可用"""

    # 股票代码后缀映射
    EXCHANGE_SUFFIX = {
        'SH': 'sh',  # 上交所
        'SZ': 'sz'   # 深交所
    }

    def __init__(self, priority: int = 7, timeout: int = 30):
        """
        初始化BaoStock适配器

        Args:
            priority: 数据源优先级（默认7）
            timeout: 请求超时时间（秒）
        """
        super().__init__(name="baostock", priority=priority)

        if not BAOSTOCK_AVAILABLE:
            raise ImportError(
                "baostock未安装，无法使用BaoStock数据源。请运行: pip3 install baostock"
            )

        self.timeout = timeout
        self._lg = None
        logger.info(f"初始化BaoStock适配器 (优先级={priority}, 超时={timeout}s)")

    def _do_init(self):
        """初始化BaoStock客户端连接"""
        if self._lg is None:
            try:
                # 登录系统
                self._lg = bs.login()
                if self._lg.error_code != '0':
                    raise Exception(f"BaoStock登录失败: {self._lg.error_msg}")

                logger.info("BaoStock登录成功")

            except Exception as e:
                logger.error(f"BaoStock初始化失败: {e}")
                raise

    def _convert_symbol(self, symbol: str) -> str:
        """
        转换股票代码格式为BaoStock格式

        Args:
            symbol: 股票代码（如 '000001'）

        Returns:
            BaoStock格式代码（如 'sz.000001'）
        """
        # 判断交易所
        if symbol.startswith('6'):
            # 上交所
            return f"sh.{symbol}"
        elif symbol.startswith(('0', '3')):
            # 深交所
            return f"sz.{symbol}"
        else:
            # 其他情况，默认深交所
            return f"sz.{symbol}"

    def _convert_date(self, date_str: str) -> str:
        """
        转换日期格式

        Args:
            date_str: 日期字符串（YYYYMMDD格式）

        Returns:
            YYYY-MM-DD格式字符串
        """
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

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
            DataFrame with columns: trade_date, open, close, high, low, volume, amount
        """
        self._init_client()

        try:
            logger.debug(f"BaoStock获取个股K线: {symbol} {start_date}-{end_date} {adjust}")

            # 转换代码和日期
            bs_symbol = self._convert_symbol(symbol)
            start_date_fmt = self._convert_date(start_date)
            end_date_fmt = self._convert_date(end_date)

            # 映射复权类型
            # "1": 股票代码
            # "2": 日期
            # "3": 开盘价
            # "4": 收盘价
            # "5": 最高价
            # "6": 最低价
            # "7": 成交量
            # "8": 成交额
            adjust_map = {'qfq': '2', 'hfq': '1', '': '0'}  # 2前复权 1后复权 0不复权
            frequency = 'd'  # 日线
            adjustflag = adjust_map.get(adjust, '2')

            # 获取历史K线
            rs = bs.query_history_k_data_plus(
                bs_symbol,
                "date,code,open,high,low,close,volume,amount",
                start_date=start_date_fmt,
                end_date=end_date_fmt,
                frequency=frequency,
                adjustflag=adjustflag
            )

            if rs.error_code != '0':
                raise Exception(f"BaoStock查询失败: {rs.error_msg}")

            # 转换为DataFrame
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())

            if not data_list:
                logger.warning(f"BaoStock返回空数据: {symbol}")
                return None

            df = pd.DataFrame(data_list, columns=rs.fields)

            # 列名映射
            column_mapping = {
                'date': 'trade_date',
                'code': 'symbol',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
                'amount': 'amount'
            }

            df = df.rename(columns=column_mapping)

            # 转换数据类型
            if 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].str.replace('-', '')

            numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'amount']
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            return df

        except Exception as e:
            logger.error(f"BaoStock个股K线获取失败 {symbol}: {e}")
            raise

    def get_sector_kline(self, sector_name: str, sector_type: str,
                        start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据

        注意: BaoStock不直接提供板块K线，需要通过成分股聚合

        Args:
            sector_name: 板块名称
            sector_type: 板块类型
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        logger.warning("BaoStock不支持板块K线数据，请使用AKShare或efinance")
        return None

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
        self._init_client()

        try:
            logger.debug(f"BaoStock获取ETF K线: {symbol} {start_date}-{end_date}")

            # ETF使用相同的接口
            return self.get_stock_kline(symbol, start_date, end_date, adjust='')

        except Exception as e:
            logger.error(f"BaoStock ETF K线获取失败 {symbol}: {e}")
            raise

    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        注意: BaoStock不直接提供涨跌停数据

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        logger.warning("BaoStock不支持涨跌停数据，请使用AKShare或efinance")
        return None

    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        注意: BaoStock不直接提供基金持仓数据

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            DataFrame or None
        """
        logger.warning("BaoStock不支持基金持仓数据")
        return None

    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        Returns:
            DataFrame or None
        """
        logger.warning("BaoStock不支持实时估值数据")
        return None

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化列名（BaoStock已在各方法中处理）

        Args:
            df: 原始数据
            data_type: 数据类型

        Returns:
            标准化后的DataFrame
        """
        return df

    def check_health(self) -> bool:
        """
        健康检查 - 验证BaoStock连接

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not BAOSTOCK_AVAILABLE:
                return False

            # 尝试登录
            lg = bs.login()
            if lg.error_code == '0':
                bs.logout()
                logger.debug("BaoStock健康检查通过")
                return True
            else:
                logger.warning(f"BaoStock健康检查失败: {lg.error_msg}")
                return False

        except Exception as e:
            logger.warning(f"BaoStock健康检查失败: {e}")
            return False

    def __del__(self):
        """析构函数，确保登出"""
        try:
            if hasattr(self, '_lg') and self._lg:
                bs.logout()
        except:
            pass
