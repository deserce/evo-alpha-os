"""
聚宽数据源适配器
封装聚宽API调用，提供统一的数据源接口
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

from data_job.core.data_source import BaseDataSource, DataSourceNotAvailableError

logger = logging.getLogger(__name__)

# 尝试导入聚宽SDK
try:
    import jqdatasdk as jq
    JQDATA_AVAILABLE = True
except ImportError:
    JQDATA_AVAILABLE = False
    logger.warning("jqdatasdk未安装，聚宽数据源不可用。可通过以下命令安装: pip3 install jqdatasdk")


class JoinQuantAdapter(BaseDataSource):
    """聚宽数据源适配器 - 封装所有聚宽API调用"""

    # 聚宽股票代码后缀映射
    EXCHANGE_SUFFIX = {
        'SH': 'XSHG',  # 上交所
        'SZ': 'XSHE'   # 深交所
    }

    # 行业代码映射（部分示例）
    INDUSTRY_MAPPING = {
        '银行': 'Financial.IB',
        '房地产': 'RealEstate.IB',
        '医药生物': 'HealthCare.IB',
        '电子': 'Technology.Electronics',
        '计算机': 'Technology.Computer',
        # 更多行业映射需要根据实际情况补充
    }

    def __init__(self, account_id: str, password: str, priority: int = 5, timeout: int = 30):
        """
        初始化聚宽适配器

        Args:
            account_id: 聚宽账号ID
            password: 聚宽密码
            priority: 数据源优先级（默认5，作为AKShare的备用）
            timeout: 请求超时时间（秒）
        """
        super().__init__(name="joinquant", priority=priority)

        if not JQDATA_AVAILABLE:
            raise DataSourceNotAvailableError(
                "jqdatasdk未安装，无法使用聚宽数据源。请运行: pip3 install jqdatasdk"
            )

        self.account_id = account_id
        self.password = password
        self.timeout = timeout
        self._client = None

        logger.info(f"初始化聚宽适配器 (优先级={priority}, 超时={timeout}s)")

    def _do_init(self):
        """初始化聚宽客户端连接"""
        if not JQDATA_AVAILABLE:
            raise DataSourceNotAvailableError("jqdatasdk未安装")

        if self._client is None:
            try:
                # 认证
                jq.auth(self.account_id, self.password)

                # 检查认证状态
                query_count = jq.get_query_count()
                logger.info(f"聚宽认证成功 - 剩余查询次数: {query_count}")

                if query_count['spare'] <= 0:
                    logger.warning(f"聚宽账号已达到查询限制: {query_count}")

                self._client = jq

            except Exception as e:
                logger.error(f"聚宽认证失败: {e}")
                raise DataSourceNotAvailableError(f"聚宽认证失败: {e}")

    def _convert_symbol(self, symbol: str) -> str:
        """
        转换股票代码格式为聚宽格式

        Args:
            symbol: 股票代码（如 '000001', '600000'）

        Returns:
            聚宽格式代码（如 '000001.XSHE', '600000.XSHG'）
        """
        # 判断交易所
        if symbol.startswith('6'):
            # 上交所
            return f"{symbol}.XSHG"
        elif symbol.startswith(('0', '3')):
            # 深交所
            return f"{symbol}.XSHE"
        else:
            # 其他情况，默认深交所
            return f"{symbol}.XSHE"

    def _convert_date(self, date_str: str) -> datetime:
        """
        转换日期格式

        Args:
            date_str: 日期字符串（YYYYMMDD格式）

        Returns:
            datetime对象
        """
        return datetime.strptime(date_str, '%Y%m%d')

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
            logger.debug(f"聚宽获取个股K线: {symbol} {start_date}-{end_date} {adjust}")

            # 转换代码和日期
            jq_symbol = self._convert_symbol(symbol)
            start = self._convert_date(start_date)
            end = self._convert_date(end_date)

            # 映射复权类型
            adjust_map = {'qfq': 'pre', 'hfq': 'post', '': 'none'}
            fq_ref_date = adjust_map.get(adjust, 'pre')

            # 获取行情数据
            df = self._client.get_price(
                jq_symbol,
                start_date=start,
                end_date=end,
                frequency='daily',
                fields=['open', 'close', 'high', 'low', 'volume', 'money'],
                fq_ref_date=fq_ref_date if adjust else None
            )

            if df is None or df.empty:
                logger.warning(f"聚宽返回空数据: {symbol}")
                return None

            # 重置索引，将日期转为列
            df = df.reset_index()
            df = df.rename(columns={
                'time': 'trade_date',
                'money': 'amount'
            })

            # 添加股票代码列
            df['symbol'] = symbol

            return df

        except Exception as e:
            logger.error(f"聚宽个股K线获取失败 {symbol}: {e}")
            raise

    def get_sector_kline(self, sector_name: str, sector_type: str,
                        start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据

        注意: 聚宽的板块数据与AKShare不完全对应，需要额外的板块代码映射

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
            logger.debug(f"聚宽获取板块K线: {sector_name} ({sector_type}) {start_date}-{end_date}")

            # 获取板块代码
            sector_code = self._get_sector_code(sector_name)
            if not sector_code:
                logger.warning(f"未找到板块代码: {sector_name}")
                return None

            start = self._convert_date(start_date)
            end = self._convert_date(end_date)

            # 获取板块行情
            df = self._client.get_price(
                sector_code,
                start_date=start,
                end_date=end,
                frequency='daily',
                fields=['open', 'close', 'high', 'low', 'volume', 'money']
            )

            if df is None or df.empty:
                logger.warning(f"聚宽返回空数据: {sector_name}")
                return None

            df = df.reset_index()
            df = df.rename(columns={
                'time': 'trade_date',
                'money': 'amount'
            })
            df['sector_name'] = sector_name

            return df

        except Exception as e:
            logger.error(f"聚宽板块K线获取失败 {sector_name}: {e}")
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
        self._init_client()

        try:
            logger.debug(f"聚宽获取ETF K线: {symbol} {start_date}-{end_date}")

            # ETF代码转换（聚宽ETF代码格式）
            jq_symbol = self._convert_symbol(symbol)

            start = self._convert_date(start_date)
            end = self._convert_date(end_date)

            df = self._client.get_price(
                jq_symbol,
                start_date=start,
                end_date=end,
                frequency='daily',
                fields=['open', 'close', 'high', 'low', 'volume', 'money']
            )

            if df is None or df.empty:
                logger.warning(f"聚宽返回空数据: {symbol}")
                return None

            df = df.reset_index()
            df = df.rename(columns={
                'time': 'trade_date',
                'money': 'amount'
            })
            df['symbol'] = symbol

            return df

        except Exception as e:
            logger.error(f"聚宽ETF K线获取失败 {symbol}: {e}")
            raise

    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        注意: 聚宽可能不直接提供涨跌停数据，此方法可能需要使用其他方式实现

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame or None
        """
        self._init_client()

        try:
            logger.debug(f"聚宽获取涨跌停数据: {date_str}")

            # 聚宽不直接提供涨跌停数据，需要通过查询实现
            # 这里使用示例实现，实际可能需要调整

            trade_date = self._convert_date(date_str)

            # 获取所有股票数据
            # 注意: 这可能比较慢且消耗查询次数
            # TODO: 实现更高效的涨跌停获取方式
            logger.warning("聚宽涨跌停数据获取未完全实现，返回None")
            return None

        except Exception as e:
            logger.error(f"聚宽涨跌停获取失败 {date_str}: {e}")
            # 返回None，让调用者降级到AKShare
            return None

    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        注意: 聚宽不直接提供基金持仓数据

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            None - 聚宽不支持此数据
        """
        logger.warning("聚宽不支持基金持仓数据，请使用AKShare数据源")
        return None

    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        Returns:
            DataFrame with columns: symbol, name, latest_price, pe_ttm, pb, etc.
        """
        self._init_client()

        try:
            logger.debug("聚宽获取估值数据")

            # 获取估值数据
            q = self._client.query(
                self._client.valuation.code,
                self._client.valuation.name,
                self._client.valuation.market_cap,
                self._client.valuation.circulating_market_cap,
                self._client.valuation.pe_ratio,
                self._client.valuation.pb_ratio,
            ).order_by(
                self._client.valuation.code.asc()
            )

            df = self._client.get_fundamentals(q)

            if df is None or df.empty:
                logger.warning("聚宽返回空数据")
                return None

            # 转换列名为标准格式
            df = df.rename(columns={
                'code': 'symbol',
                'market_cap': 'total_mv',
                'circulating_market_cap': 'circ_mv',
                'pe_ratio': 'pe_ttm',
                'pb_ratio': 'pb'
            })

            # 移除交易所后缀
            if 'symbol' in df.columns:
                df['symbol'] = df['symbol'].str.replace('.XSHG', '').str.replace('.XSHE', '')

            return df

        except Exception as e:
            logger.error(f"聚宽估值数据获取失败: {e}")
            raise

    def _get_sector_code(self, sector_name: str) -> Optional[str]:
        """
        获取板块代码

        Args:
            sector_name: 板块名称

        Returns:
            板块代码 or None
        """
        # 先查映射表
        if sector_name in self.INDUSTRY_MAPPING:
            return self.INDUSTRY_MAPPING[sector_name]

        # TODO: 实现动态查询板块代码
        # 可以通过 jq.get_industries() 获取所有行业
        return None

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化列名（聚宽已经是英文列名，基本无需转换）

        Args:
            df: 原始数据
            data_type: 数据类型

        Returns:
            标准化后的DataFrame
        """
        # 聚宽数据已经是英文列名，直接返回
        # 如果需要额外转换可以在这里添加
        return df

    def check_health(self) -> bool:
        """
        健康检查 - 验证聚宽连接和认证状态

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not JQDATA_AVAILABLE:
                return False

            self._init_client()

            # 测试查询
            query_count = self._client.get_query_count()
            is_healthy = query_count['spare'] > 0

            if is_healthy:
                logger.debug(f"聚宽健康检查通过 - 剩余查询次数: {query_count['spare']}")
            else:
                logger.warning(f"聚宽账号查询次数已用尽: {query_count}")

            return is_healthy

        except Exception as e:
            logger.warning(f"聚宽健康检查失败: {e}")
            return False
