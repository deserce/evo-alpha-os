"""
掘金量化（GMX）数据源适配器
封装掘金量化API调用，提供统一的数据源接口
优势：数据质量高、官方维护、有免费额度
"""

import sys
import os
import logging
from typing import Optional
from datetime import datetime, timedelta
import pandas as pd

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from data_job.core.data_source import BaseDataSource

logger = logging.getLogger(__name__)

# 尝试导入掘金量化SDK
try:
    from gm.api import *
    GMX_AVAILABLE = True
except ImportError:
    GMX_AVAILABLE = False
    logger.warning("掘金量化SDK未安装，可通过以下命令安装: pip3 install gmnn-python")

# 环境变量token
GMX_TOKEN = os.getenv('GMX_TOKEN', '')
GMX_ACCOUNT_ID = os.getenv('GMX_ACCOUNT_ID', '')


class GmxAdapter(BaseDataSource):
    """掘金量化（GMX）数据源适配器 - 数据质量高，官方维护"""

    def __init__(self, token: str = None, account_id: str = None, priority: int = 9, timeout: int = 30):
        """
        初始化掘金量化适配器

        Args:
            token: 掘金账号token
            account_id: 掘金账号ID
            priority: 数据源优先级（默认9，仅次于AKShare）
            timeout: 请求超时时间（秒）
        """
        super().__init__(name="gmx", priority=priority)

        if not GMX_AVAILABLE:
            raise ImportError(
                "掘金量化SDK未安装，无法使用GMX数据源。请运行: pip3 install gmnn-python"
            )

        self.token = token or GMX_TOKEN
        self.account_id = account_id or GMX_ACCOUNT_ID
        self.timeout = timeout
        self._initialized = False

        if not self.token:
            raise ValueError("请提供掘金token（通过GMX_TOKEN环境变量或参数）")

        logger.info(f"初始化掘金量化适配器 (优先级={priority}, 超时={timeout}s)")

    def _do_init(self):
        """初始化掘金量化连接"""
        if not self._initialized:
            try:
                # 设置token
                set_token(self.token)

                # 测试连接 - 获取当前交易日
                try:
                    current_day = get_trading_dates(exchange='SHSE', start_date='2024-01-01', end_date=datetime.now().strftime('%Y-%m-%d'))
                    if current_day is not None and len(current_day) > 0:
                        logger.info(f"掘金量化认证成功 - 当前交易日: {current_day[-1]}")
                    else:
                        logger.warning("掘金量化连接成功，但未获取到交易日数据")
                except Exception as e:
                    logger.warning(f"掘金量化连接测试失败: {e}")

                self._initialized = True

            except Exception as e:
                logger.error(f"掘金量化初始化失败: {e}")
                raise

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
            logger.debug(f"掘金获取个股K线: {symbol} {start_date}-{end_date} {adjust}")

            # 转换股票代码格式（掘金使用交易所后缀）
            gmx_symbol = self._convert_symbol(symbol)

            # 转换日期格式
            start_date_fmt = self._convert_date(start_date)
            end_date_fmt = self._convert_date(end_date)

            # 映射复权类型
            # 掘金使用 adjust 参数：0 不复权，1 前复权，2 后复权
            adjust_map = {'qfq': 1, 'hfq': 2, '': 0}
            adjust_flag = adjust_map.get(adjust, 1)

            # 获取历史K线
            # 使用 history 函数
            symbols = [gmx_symbol]
            fields = ['eob', 'open', 'high', 'low', 'close', 'volume', 'amount', 'vwap', 'position']

            try:
                # 尝试使用 history 函数
                data = history(
                    symbol=symbols,
                    frequency='1d',
                    start_time=start_date_fmt,
                    end_time=end_date_fmt,
                    fields=fields,
                    adjust=adjust_flag,
                    df=True
                )

                if data is None or data.empty:
                    logger.warning(f"掘金返回空数据: {symbol}")
                    return None

                # 重命名列
                data = data.rename(columns={
                    'eob': 'trade_date',
                    'open': 'open',
                    'high': 'high',
                    'low': 'low',
                    'close': 'close',
                    'volume': 'volume',
                    'amount': 'amount'
                })

                # 过滤数据
                if 'symbol' in data.columns:
                    data = data[data['symbol'] == gmx_symbol]

                # 格式化日期
                if 'trade_date' in data.columns:
                    data['trade_date'] = pd.to_datetime(data['trade_date']).dt.strftime('%Y%m%d')

                # 添加原始股票代码列
                data['symbol'] = symbol

                # 选择需要的列
                required_columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']
                data = data[[col for col in required_columns if col in data.columns]]

                return data

            except Exception as e:
                # 如果 history 函数失败，尝试备用方法
                logger.warning(f"掘金history函数失败: {e}，尝试备用方法")

                # 尝试直接获取数据
                from gm.api import get_fundamentals

                # 使用 get_fundamentals 作为备用
                logger.warning("掘金K线数据获取失败，建议使用其他数据源")
                return None

        except Exception as e:
            logger.error(f"掘金个股K线获取失败 {symbol}: {e}")
            raise

    def get_sector_kline(self, sector_name: str, sector_type: str,
                        start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据

        掘金通过板块指数代码获取

        Args:
            sector_name: 板块名称
            sector_type: 板块类型 ('Industry'-行业, 'Concept'-概念)
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        self._init_client()

        try:
            logger.debug(f"掘金获取板块K线: {sector_name} ({sector_type})")

            # 获取板块指数代码
            sector_index_code = self._get_sector_index_code(sector_name)
            if not sector_index_code:
                logger.warning(f"未找到板块指数代码: {sector_name}")
                return None

            # 使用指数K线接口
            return self._get_index_kline(sector_index_code, start_date, end_date)

        except Exception as e:
            logger.error(f"掘金板块K线获取失败 {sector_name}: {e}")
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
            logger.debug(f"掘金获取ETF K线: {symbol} {start_date}-{end_date}")

            # ETF代码转换
            gmx_symbol = self._convert_symbol(symbol, is_etf=True)

            # 使用指数K线接口（ETF作为指数处理）
            return self._get_index_kline(gmx_symbol, start_date, end_date, symbol)

        except Exception as e:
            logger.error(f"掘金ETF K线获取失败 {symbol}: {e}")
            raise

    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        掘金需要通过分析涨跌幅来获取

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        self._init_client()

        try:
            logger.debug(f"掘金获取涨跌停数据: {date_str}")

            # 获取当日所有股票数据
            date_fmt = self._convert_date(date_str)
            next_date_fmt = (datetime.strptime(date_str, '%Y%m%d') + timedelta(days=1)).strftime('%Y-%m-%d')

            # 获取所有股票列表
            from gm.api import get_instruments
            instruments = get_instruments(exchanges='SHSE,SZSE', sec_types='STK', df=True)

            if instruments is None or instruments.empty:
                return None

            # 获取当日行情
            symbols = instruments['symbol'].tolist()[:1000]  # 限制数量

            data = history(
                symbol=symbols,
                frequency='1d',
                start_time=date_fmt,
                end_time=next_date_fmt,
                fields=['eob', 'open', 'close', 'volume', 'amount'],
                df=True
            )

            if data is None or data.empty:
                return None

            # 计算涨跌停
            # ST股票涨跌停5%，其他10%
            data['pct_chg'] = ((data['close'] - data['open']) / data['open'] * 100)

            # 过滤涨跌停
            limit_up = data[data['pct_chg'] >= 9.5].copy()
            limit_down = data[data['pct_chg'] <= -9.5].copy()

            limit_up['limit_type'] = 'up'
            limit_down['limit_type'] = 'down'

            result = pd.concat([limit_up, limit_down])

            # 重命名列
            result = result.rename(columns={
                'eob': 'trade_date',
                'symbol': 'symbol'
            })

            return result

        except Exception as e:
            logger.error(f"掘金涨跌停获取失败 {date_str}: {e}")
            return None

    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        掘金可能不直接提供

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            DataFrame or None
        """
        logger.warning("掘金不支持基金持仓数据")
        return None

    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        使用掘金的实时行情

        Returns:
            DataFrame with columns: symbol, name, latest_price, pe_ttm, pb, etc.
        """
        self._init_client()

        try:
            logger.debug("掘金获取估值数据")

            # 获取当前时刻
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 获取股票列表
            from gm.api import get_instruments
            instruments = get_instruments(exchanges='SHSE,SZSE', sec_types='STK', df=True)

            if instruments is None or instruments.empty:
                return None

            # 获取实时行情（前500只）
            symbols = instruments['symbol'].tolist()[:500]

            data = history(
                symbol=symbols,
                frequency='tick',
                start_time=now,
                end_time=now,
                fields=['symbol', 'price', 'open', 'high', 'low', 'volume', 'amount'],
                df=True
            )

            if data is None or data.empty:
                return None

            # 重命名列
            data = data.rename(columns={
                'price': 'latest_price',
                'eob': 'trade_date'
            })

            return data

        except Exception as e:
            logger.error(f"掘金估值数据获取失败: {e}")
            raise

    def _convert_symbol(self, symbol: str, is_etf: bool = False) -> str:
        """
        转换股票代码格式为掘金格式

        Args:
            symbol: 股票代码（如 '000001'）
            is_etf: 是否是ETF

        Returns:
            掘金格式代码（如 'SHSE.000001' 或 'SZSE.000001'）
        """
        if is_etf:
            # ETF代码
            if symbol.startswith('5'):
                return f"SHSE.{symbol}"  # 上交所ETF
            elif symbol.startswith('15'):
                return f"SZSE.{symbol}"  # 深交所ETF
            else:
                return f"SHSE.{symbol}"
        else:
            # 股票代码
            if symbol.startswith('6'):
                return f"SHSE.{symbol}"  # 上交所
            elif symbol.startswith(('0', '3')):
                return f"SZSE.{symbol}"  # 深交所
            else:
                return f"SZSE.{symbol}"

    def _convert_date(self, date_str: str) -> str:
        """
        转换日期格式

        Args:
            date_str: 日期字符串（YYYYMMDD格式）

        Returns:
            YYYY-MM-DD HH:MM:SS格式字符串
        """
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} 09:30:00"

    def _get_index_kline(self, index_code: str, start_date: str, end_date: str, original_symbol: str = None):
        """获取指数/ETF K线数据的内部方法"""
        try:
            start_date_fmt = self._convert_date(start_date)
            end_date_fmt = self._convert_date(end_date)

            fields = ['eob', 'open', 'high', 'low', 'close', 'volume', 'amount']

            data = history(
                symbol=[index_code],
                frequency='1d',
                start_time=start_date_fmt,
                end_time=end_date_fmt,
                fields=fields,
                df=True
            )

            if data is None or data.empty:
                return None

            # 重命名列
            data = data.rename(columns={
                'eob': 'trade_date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
                'amount': 'amount'
            })

            # 格式化日期
            if 'trade_date' in data.columns:
                data['trade_date'] = pd.to_datetime(data['trade_date']).dt.strftime('%Y%m%d')

            # 添加symbol列
            if original_symbol:
                data['symbol'] = original_symbol
            else:
                data['symbol'] = index_code.replace('SHSE.', '').replace('SZSE.', '')

            return data

        except Exception as e:
            logger.error(f"获取指数K线失败: {e}")
            return None

    def _get_sector_index_code(self, sector_name: str) -> Optional[str]:
        """
        获取板块指数代码

        Args:
            sector_name: 板块名称

        Returns:
            指数代码 or None
        """
        # 掘金板块指数代码映射（示例）
        sector_index_map = {
            # 主要指数
            '上证指数': 'SHSE.000001',
            '深证成指': 'SZSE.399001',
            '创业板指': 'SZSE.399006',
            '沪深300': 'SHSE.000300',
            '中证500': 'SHSE.000905',
            '上证50': 'SHSE.000016',
            # 行业指数（需要根据实际情况补充）
        }

        return sector_index_map.get(sector_name)

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化列名（掘金已在各方法中处理）

        Args:
            df: 原始数据
            data_type: 数据类型

        Returns:
            标准化后的DataFrame
        """
        return df

    def check_health(self) -> bool:
        """
        健康检查 - 验证掘金连接

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not GMX_AVAILABLE:
                return False

            self._init_client()

            # 测试获取交易日
            from gm.api import get_trading_dates
            dates = get_trading_dates(
                exchange='SHSE',
                start_date='2024-01-01',
                end_date=datetime.now().strftime('%Y-%m-%d')
            )

            is_healthy = dates is not None and len(dates) > 0

            if is_healthy:
                logger.debug(f"掘金健康检查通过 - 交易日数据可用")
            else:
                logger.warning("掘金健康检查失败: 无法获取交易日数据")

            return is_healthy

        except Exception as e:
            logger.warning(f"掘金健康检查失败: {e}")
            return False
