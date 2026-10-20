"""
pytdx 数据源适配器
使用通达信协议获取行情数据，稳定性高，不易被封IP
"""

import logging
import time
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)

# 通达信服务器配置 (已验证可用)
TDX_SERVERS = [
    ("117.34.114.14", 7709),  # 2026-09-22 已验证 5 分钟完成柱
    ("117.34.114.16", 7709),
    ("218.75.126.9", 7709),    # 招商证券上海 ✅ 已验证
    ("119.147.212.81", 7709),  # 招商证券深圳
    # 华泰证券
    ("101.71.170.10", 7709),
    ("218.108.98.244", 7709),
    ("218.108.61.19", 7709),
    # 申万宏源
    ("114.80.63.12", 7709),
    ("114.80.63.33", 7709),
    # 中信建投
    ("39.106.218.146", 7709),
    ("60.12.136.250", 7709),
    # 广发证券
    ("120.76.152.87", 7709),
]

# 市场代码映射
MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳

# K线周期映射
CATEGORY_MAP = {
    "1min": 8,    # 1分钟
    "5min": 0,    # 5分钟
    "15min": 1,   # 15分钟
    "30min": 2,   # 30分钟
    "60min": 3,   # 60分钟
    "daily": 4,   # 日K
    "weekly": 5,  # 周K
    "monthly": 6, # 月K
}


class TdxAdapter:
    """
    pytdx 数据源适配器

    优点：
    - 使用通达信二进制协议，稳定性极高
    - 不易被封IP
    - 速度快
    - 纯Python实现，Mac兼容性好

    缺点：
    - 财务数据不结构化
    - 不支持涨跌停、基金持股等数据
    """

    def __init__(self, max_retries: int = 3):
        """
        初始化 pytdx 适配器

        Args:
            max_retries: 最大重试次数
        """
        self.max_retries = max_retries
        self.api = None
        self.connected = False
        self.current_server = None
        self.success_count = 0
        self.fail_count = 0

        # 尝试导入 pytdx
        try:
            from pytdx.hq import TdxHq_API
            self.TdxHq_API = TdxHq_API
            self._connect()
        except ImportError:
            logger.warning("⚠️  pytdx 未安装，请运行: pip install pytdx")
            self.TdxHq_API = None
        except Exception as e:
            logger.warning(f"⚠️  pytdx 初始化失败: {e}")
            self.TdxHq_API = None

    def is_available(self) -> bool:
        """检查适配器是否可用"""
        return self.TdxHq_API is not None and self.connected

    def _invalidate_connection(self) -> None:
        """Close a failed stream before another transaction can reuse it."""
        self.connected = False
        api = getattr(self, "api", None)
        if api is not None and hasattr(api, "disconnect"):
            try:
                api.disconnect()
            except Exception as exc:
                logger.debug("TDX disconnect failed: %s", exc)

    def _connect(self) -> bool:
        """Rotate endpoints with at most three two-second connection attempts."""
        if self.TdxHq_API is None:
            return False
        previous = getattr(self, "current_server", None)
        self._invalidate_connection()
        servers = list(TDX_SERVERS)
        if previous in servers:
            offset = servers.index(previous) + 1
            servers = servers[offset:] + servers[:offset]
        for host, port in servers[:3]:
            self.current_server = (host, port)
            try:
                self.api = self.TdxHq_API(
                    heartbeat=False, auto_retry=False, raise_exception=True
                )
                if self.api.connect(host, port, time_out=2.0):
                    self.connected = True
                    logger.info("pytdx connected to %s:%s", host, port)
                    return True
            except Exception as exc:
                logger.debug("TDX connection %s:%s failed: %s", host, port, exc)
            self._invalidate_connection()
        logger.error("TDX connection attempts exhausted")
        return False

    def _ensure_connected(self) -> bool:
        """Use the actual request to check health; never probe the shared stream."""
        if not self.connected or not self.api:
            return self._connect()
        return True

    def _parse_symbol(self, symbol: str) -> Tuple[int, str]:
        """
        解析股票代码，返回市场代码和股票代码

        Args:
            symbol: 股票代码，如 '600519' 或 '000001'

        Returns:
            (market, code): 市场代码和股票代码
        """
        code = str(symbol).zfill(6)

        # 判断市场
        if code.startswith(('6', '900', '688')):  # 沪市
            return MARKET_SH, code
        elif code.startswith(('0', '2', '3')):    # 深市
            return MARKET_SZ, code
        else:
            # 默认深市
            return MARKET_SZ, code

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily",
        adjust: str = "qfq"
    ) -> Optional[pd.DataFrame]:
        """
        获取个股K线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期 (YYYY-MM-DD 或 YYYYMMDD)
            end_date: 结束日期 (YYYY-MM-DD 或 YYYYMMDD)
            period: 周期 (daily, weekly, monthly, 1min, 5min, 15min, 30min, 60min)
            adjust: 复权类型 (qfq-前复权, hfq-后复权, none-不复权)
                    注意：pytdx 不支持复权，需要手动计算

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            self.fail_count += 1
            return None

        try:
            market, code = self._parse_symbol(symbol)
            category = CATEGORY_MAP.get(period, 4)  # 默认日线

            # pytdx 一次最多获取800条数据，需要分批获取
            all_data = []
            offset = 0
            count = 800  # 每次获取数量

            # 如果指定了日期范围，先获取所有数据再过滤
            # 这里简化处理，直接获取最近的数据
            while True:
                data = self.api.get_security_bars(
                    category, market, code, offset, count
                )

                if not data:
                    break

                all_data.extend(data)

                # 如果获取的数据少于请求数量，说明已经到底了
                if len(data) < count:
                    break

                offset += count

                # 防止无限循环
                if offset > 10000:
                    break

            if not all_data:
                self.fail_count += 1
                return None

            # 转换为 DataFrame
            df = pd.DataFrame(all_data)

            # 标准化列名
            df = df.rename(columns={
                'datetime': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',      # 成交量(手)
                'amount': 'amount',    # 成交额
            })

            # 转换日期
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            # 添加 symbol 列
            df['symbol'] = symbol

            # 计算涨跌幅
            if 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            # 按日期排序
            df = df.sort_values('trade_date').reset_index(drop=True)

            # 日期过滤
            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            # 选择需要的列
            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
            existing_columns = [c for c in columns if c in df.columns]
            df = df[existing_columns]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"pytdx 获取 {symbol} K线失败: {e}")
            self.fail_count += 1

            # 尝试重连
            self._connect()
            return None

    def get_recent_stock_bars(
        self,
        symbol: str,
        period: str = "5min",
        count: int = 3,
    ) -> Optional[pd.DataFrame]:
        """Get a bounded number of the most recent intraday bars.

        Unlike :meth:`get_stock_kline`, this method normally performs one
        ``get_security_bars`` request.  A genuinely empty response gets one
        bounded reconnect-and-retry; intraday monitoring must never walk the
        full history or retry without a limit.
        """
        if period not in CATEGORY_MAP or period == "daily":
            raise ValueError(f"unsupported intraday period: {period}")
        if not isinstance(count, int) or not 1 <= count <= 800:
            raise ValueError("count must be between 1 and 800")
        if not self._ensure_connected():
            self.fail_count += 1
            return None

        try:
            market, code = self._parse_symbol(symbol)
            data = None
            for attempt in range(2):
                try:
                    data = self.api.get_security_bars(
                        CATEGORY_MAP[period], market, code, 0, count
                    )
                except Exception as exc:
                    logger.warning("TDX %s %s request failed: %s", symbol, period, exc)
                    data = None
                if data:
                    break
                self.fail_count += 1
                self._invalidate_connection()
                if attempt == 0:
                    logger.warning("TDX %s %s empty/failed; rotating endpoint once", symbol, period)
                    if self._connect():
                        continue
                return None

            frame = pd.DataFrame(data).rename(
                columns={
                    "datetime": "bar_time",
                    "vol": "volume",
                }
            )
            frame["bar_time"] = pd.to_datetime(frame["bar_time"], errors="coerce")
            frame = frame.dropna(subset=["bar_time"])
            if frame.empty:
                self.fail_count += 1
                return None
            frame["symbol"] = str(symbol).zfill(6)
            frame = frame.sort_values("bar_time").drop_duplicates(
                subset=["bar_time"], keep="last"
            )
            columns = [
                "symbol",
                "bar_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
            frame = frame[[column for column in columns if column in frame.columns]]
            self.success_count += 1
            return frame.reset_index(drop=True)
        except Exception as exc:
            logger.warning("pytdx 获取 %s 最近%s K线失败: %s", symbol, period, exc)
            self.fail_count += 1
            self._invalidate_connection()
            return None

    def get_index_kline(
        self,
        symbol: str = "000001",
        start_date: str = None,
        end_date: str = None,
        period: str = "daily"
    ) -> Optional[pd.DataFrame]:
        """
        获取指数K线数据

        Args:
            symbol: 指数代码 (如 '000001' - 上证指数)
            start_date: 开始日期
            end_date: 结束日期
            period: 周期

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            # 指数使用市场1
            market = 1
            code = str(symbol).zfill(6)
            category = CATEGORY_MAP.get(period, 4)

            all_data = []
            offset = 0
            count = 800

            while True:
                data = self.api.get_index_bars(
                    category, market, code, offset, count
                )

                if not data:
                    break

                all_data.extend(data)

                if len(data) < count:
                    break

                offset += count
                if offset > 10000:
                    break

            if not all_data:
                return None

            df = pd.DataFrame(all_data)

            # 标准化列名
            df = df.rename(columns={
                'datetime': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',
                'amount': 'amount',
            })

            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            df['symbol'] = symbol

            if 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            df = df.sort_values('trade_date').reset_index(drop=True)

            # 日期过滤
            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
            existing_columns = [c for c in columns if c in df.columns]
            df = df[existing_columns]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"pytdx 获取指数 {symbol} K线失败: {e}")
            self.fail_count += 1
            return None

    def get_stock_list(self, market: int = None) -> Optional[pd.DataFrame]:
        """
        获取股票列表

        Args:
            market: 市场代码 (0-深市, 1-沪市, None-全部)

        Returns:
            DataFrame with columns: code, name
        """
        if not self._ensure_connected():
            return None

        try:
            all_stocks = []

            # 获取各市场股票
            markets = [market] if market is not None else [MARKET_SZ, MARKET_SH]

            for m in markets:
                stocks = self.api.get_security_list(m, 0)

                if stocks:
                    for stock in stocks:
                        # 过滤股票和指数
                        if stock['category'] in [1, 2, 3]:  # 股票
                            all_stocks.append({
                                'code': stock['code'],
                                'name': stock['name'],
                            })

            if not all_stocks:
                return None

            df = pd.DataFrame(all_stocks)
            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"pytdx 获取股票列表失败: {e}")
            self.fail_count += 1
            return None

    def get_realtime_quotes(self, symbols: List[str]) -> Optional[pd.DataFrame]:
        """
        获取实时行情

        Args:
            symbols: 股票代码列表

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            # 转换为 pytdx 格式
            quotes = []
            for symbol in symbols:
                market, code = self._parse_symbol(symbol)
                quotes.append((market, code))

            data = self.api.get_security_quotes(quotes)

            if not data:
                return None

            df = pd.DataFrame(data)

            # 标准化列名
            df = df.rename(columns={
                'code': 'symbol',
                'price': 'current_price',
                'ask1': 'ask_price1',
                'bid1': 'bid_price1',
                'vol': 'volume',
            })

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"pytdx 获取实时行情失败: {e}")
            self.fail_count += 1
            return None

    def get_stock_info(self, symbol: str) -> Optional[dict]:
        """
        获取股票基本信息

        Args:
            symbol: 股票代码

        Returns:
            dict or None
        """
        if not self._ensure_connected():
            return None

        try:
            market, code = self._parse_symbol(symbol)

            # 获取公司信息
            info = self.api.get_company_info(market, code)

            if info:
                return {
                    'symbol': symbol,
                    'info': info[:500],  # 截取前500字符
                }

            return None

        except Exception as e:
            logger.warning(f"pytdx 获取 {symbol} 信息失败: {e}")
            return None

    def get_success_rate(self) -> float:
        """获取成功率"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def disconnect(self):
        """断开连接"""
        if self.api:
            try:
                self.api.disconnect()
                self.connected = False
                logger.info("pytdx 已断开连接")
            except:
                pass

    def __del__(self):
        """析构函数，确保断开连接"""
        self.disconnect()


# 单例模式
_tdx_instance = None


def get_tdx_adapter() -> Optional[TdxAdapter]:
    """获取 pytdx 适配器单例"""
    global _tdx_instance

    if _tdx_instance is None:
        _tdx_instance = TdxAdapter()
    elif not _tdx_instance.is_available():
        try:
            _tdx_instance._ensure_connected()
        except Exception as exc:
            logger.warning("pytdx 单例重连失败 (%s)", type(exc).__name__)

    return _tdx_instance if _tdx_instance.is_available() else None


# 使用示例
if __name__ == "__main__":
    # 测试获取K线数据
    tdx = TdxAdapter()

    if tdx.is_available():
        print("✅ pytdx 连接成功")

        # 获取贵州茅台K线
        df = tdx.get_stock_kline('600519', start_date='2024-01-01', end_date='2024-03-01')
        if df is not None:
            print(f"获取到 {len(df)} 条K线数据")
            print(df.tail())
        else:
            print("获取K线数据失败")

        # 获取上证指数K线
        df_index = tdx.get_index_kline('000001', start_date='2024-01-01', end_date='2024-03-01')
        if df_index is not None:
            print(f"获取到 {len(df_index)} 条指数K线数据")
            print(df_index.tail())

        # 获取股票列表
        df_list = tdx.get_stock_list()
        if df_list is not None:
            print(f"获取到 {len(df_list)} 只股票")

        # 打印统计
        print(f"\n成功率: {tdx.get_success_rate():.1%}")
    else:
        print("❌ pytdx 连接失败")

    tdx.disconnect()
