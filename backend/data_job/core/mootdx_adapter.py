"""
mootdx 数据源适配器
mootdx 是对 pytdx 的增强版本，具有更好的服务器池管理和稳定性
"""

import logging
import time
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)

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


class MootdxAdapter:
    """
    mootdx 数据源适配器

    优点：
    - 内置服务器池管理
    - 自动选择最快服务器
    - 更好的并发处理
    - 支持多种市场数据
    - 相比 pytdx 更稳定的服务器连接

    缺点：
    - 仍依赖通达信服务器
    - 财务数据不完整
    """

    # 通达信服务器列表（备用）
    SERVERS = [
        ("218.75.126.9", 7709, "招商证券上海"),
        ("60.12.136.250", 7709, "中信建投"),
        ("120.76.152.87", 7709, "广发证券"),
        ("218.108.98.244", 7709, "华泰证券"),
        ("114.80.63.12", 7709, "申万宏源"),
        ("114.80.63.33", 7709, "申万宏源2"),
        ("119.147.212.81", 7709, "招商证券深圳"),
        ("101.71.170.10", 7709, "华泰证券2"),
        ("218.108.61.19", 7709, "华泰证券3"),
    ]

    def __init__(self, max_retries: int = 3, timeout: int = 10):
        """
        初始化 mootdx 适配器

        Args:
            max_retries: 最大重试次数
            timeout: 连接超时时间(秒)
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.api = None
        self.connected = False
        self.current_server = None
        self.success_count = 0
        self.fail_count = 0

        # 尝试导入 mootdx
        try:
            from mootdx.quotes import Quotes
            self.Quotes = Quotes
            self._connect()
        except ImportError:
            logger.warning("⚠️  mootdx 未安装，请运行: pip install mootdx")
            self.Quotes = None
        except Exception as e:
            logger.warning(f"⚠️  mootdx 初始化失败: {e}")
            self.Quotes = None

    def is_available(self) -> bool:
        """检查适配器是否可用"""
        return self.Quotes is not None and self.connected

    def _connect(self) -> bool:
        """
        连接通达信服务器
        mootdx 会自动选择最佳服务器
        """
        if self.Quotes is None:
            return False

        try:
            # 使用 factory 方法创建客户端
            # market='std' 使用标准市场服务器
            self.api = self.Quotes.factory(market='std', timeout=self.timeout)

            # 测试连接
            test_data = self.api.bars(symbol='600519', market=MARKET_SH, start=0, offset=1)

            if test_data is not None and not test_data.empty:
                self.connected = True
                self.current_server = "auto"
                logger.info("✅ mootdx 已连接 (自动选择服务器)")
                return True
            else:
                logger.warning("mootdx 自动连接失败，尝试手动指定服务器...")
                return self._connect_manual()

        except Exception as e:
            logger.debug(f"mootdx 自动连接失败: {e}")
            return self._connect_manual()

    def _connect_manual(self) -> bool:
        """手动尝试连接各个服务器"""
        for host, port, name in self.SERVERS:
            try:
                # 使用工厂方法，指定服务器
                self.api = self.Quotes.factory(
                    market='std',
                    timeout=self.timeout
                )

                # 尝试设置服务器
                if hasattr(self.api, 'connect'):
                    if self.api.connect((host, port)):
                        self.connected = True
                        self.current_server = f"{host}:{port}({name})"
                        logger.info(f"✅ mootdx 已连接到 {self.current_server}")
                        return True
                else:
                    # 如果没有 connect 方法，直接测试
                    test_data = self.api.bars(symbol='600519', market=MARKET_SH, start=0, offset=1)
                    if test_data is not None and not test_data.empty:
                        self.connected = True
                        self.current_server = f"{host}:{port}({name})[proxy]"
                        logger.info(f"✅ mootdx 已连接到 {self.current_server}")
                        return True

            except Exception as e:
                logger.debug(f"连接 {host}:{port} 失败: {e}")
                continue

        logger.error("❌ mootdx 无法连接到任何服务器")
        self.connected = False
        return False

    def _ensure_connected(self) -> bool:
        """确保连接正常，断线重连"""
        if not self.connected or not self.api:
            return self._connect()

        # 测试连接
        try:
            test_data = self.api.bars(symbol='600519', market=MARKET_SH, start=0, offset=1)
            if test_data is not None and not test_data.empty:
                return True
            logger.warning("⚠️  mootdx 连接断开，尝试重连...")
            return self._connect()
        except:
            logger.warning("⚠️  mootdx 连接异常，尝试重连...")
            return self._connect()

    def _parse_symbol(self, symbol: str) -> int:
        """
        解析股票代码，返回市场代码

        Args:
            symbol: 股票代码，如 '600519' 或 '000001'

        Returns:
            market: 市场代码 (1-沪市, 0-深市)
        """
        code = str(symbol).zfill(6)

        # 判断市场
        if code.startswith(('6', '900', '688')):  # 沪市
            return MARKET_SH
        elif code.startswith(('0', '2', '3')):    # 深市
            return MARKET_SZ
        else:
            # 默认深市
            return MARKET_SZ

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
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            period: 周期 (daily 目前支持最好)
            adjust: 复权类型 (暂未实现)

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            self.fail_count += 1
            return None

        try:
            market = self._parse_symbol(symbol)
            code = str(symbol).zfill(6)

            # mootdx 的 bars 方法获取K线
            # 需要循环获取所有数据
            all_data = []
            offset = 0
            batch_size = 800  # 每次获取800条

            while True:
                data = self.api.bars(
                    symbol=code,
                    market=market,
                    start=0,
                    offset=batch_size
                )

                if data is None or data.empty:
                    break

                all_data.append(data)

                # 如果获取的数据少于请求数量，说明已经到底了
                if len(data) < batch_size:
                    break

                offset += batch_size

                # 防止无限循环
                if offset > 20000:
                    break

            if not all_data:
                self.fail_count += 1
                return None

            # 合并所有数据
            df = pd.concat(all_data, ignore_index=True)

            # 重命名列
            column_map = {
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'volume': 'volume',
                'amount': 'amount',
            }

            # 检查并重命名存在的列
            for old_name, new_name in column_map.items():
                if old_name in df.columns:
                    df = df.rename(columns={old_name: new_name})

            # 转换日期
            if 'trade_date' in df.columns:
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

            # 过滤未来日期
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today]

            # 选择需要的列
            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']
            existing_columns = [c for c in columns if c in df.columns]

            # 如果缺少 turnover_rate 列，添加为 None
            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            df = df[[c for c in columns if c in df.columns]]

            if df.empty:
                self.fail_count += 1
                return None

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取 {symbol} K线失败: {e}")
            self.fail_count += 1

            # 尝试重连
            self._connect()
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
            # 上证指数 000001 在沪市
            market = MARKET_SH
            code = str(symbol).zfill(6)

            # 使用 index_bars 获取指数K线
            data = self.api.index_bars(
                symbol=code,
                market=market,
                start=0,
                offset=0
            )

            if data is None or data.empty:
                return None

            df = data.copy().reset_index()

            # 重命名列
            column_map = {
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'volume': 'volume',
                'amount': 'amount',
            }

            for old_name, new_name in column_map.items():
                if old_name in df.columns:
                    df = df.rename(columns={old_name: new_name})

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
            logger.warning(f"mootdx 获取指数 {symbol} K线失败: {e}")
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
            markets = [market] if market is not None else [MARKET_SZ, MARKET_SH]

            for m in markets:
                # 获取股票列表
                data = self.api.stocks(market=m)

                if data is not None and not data.empty:
                    for _, row in data.iterrows():
                        all_stocks.append({
                            'code': row.get('code', ''),
                            'name': row.get('name', ''),
                        })

            if not all_stocks:
                return None

            df = pd.DataFrame(all_stocks)
            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取股票列表失败: {e}")
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
            # 构造请求数据
            quotes_data = []
            for symbol in symbols:
                market = self._parse_symbol(symbol)
                code = str(symbol).zfill(6)

                data = self.api.quotes(symbol=code, market=market)
                if data is not None and not data.empty:
                    quotes_data.append(data)

            if not quotes_data:
                return None

            df = pd.concat(quotes_data, ignore_index=True)

            # 标准化列名
            df = df.rename(columns={
                'code': 'symbol',
                'price': 'current_price',
                'ask1': 'ask_price1',
                'bid1': 'bid_price1',
            })

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取实时行情失败: {e}")
            self.fail_count += 1
            return None

    def get_success_rate(self) -> float:
        """获取成功率"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def get_etf_list(self) -> Optional[pd.DataFrame]:
        """
        获取ETF列表

        Returns:
            DataFrame with columns: code, name
        """
        if not self._ensure_connected():
            return None

        try:
            # 从沪市获取ETF列表 (市场1, category=8)
            all_etfs = []

            # 沪市ETF
            try:
                data = self.api.stocks(market=1, category=8)
                if data is not None and not data.empty:
                    data['market'] = 'SH'
                    all_etfs.append(data)
            except Exception as e:
                logger.debug(f"获取沪市ETF失败: {e}")

            # 深市ETF
            try:
                data = self.api.stocks(market=0, category=8)
                if data is not None and not data.empty:
                    data['market'] = 'SZ'
                    all_etfs.append(data)
            except Exception as e:
                logger.debug(f"获取深市ETF失败: {e}")

            if not all_etfs:
                return None

            df = pd.concat(all_etfs, ignore_index=True)

            # 重命名列
            df = df.rename(columns={'code': 'symbol', 'name': 'name'})
            df = df[['symbol', 'name']]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取ETF列表失败: {e}")
            self.fail_count += 1
            return None

    def get_etf_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily"
    ) -> Optional[pd.DataFrame]:
        """
        获取ETF K线数据

        Args:
            symbol: ETF代码
            start_date: 开始日期
            end_date: 结束日期
            period: 周期

        Returns:
            DataFrame or None
        """
        # ETF 使用 stock 方法获取
        return self.get_stock_kline(symbol, start_date, end_date, period)

    def get_sector_list(self) -> Optional[pd.DataFrame]:
        """
        获取板块列表

        Returns:
            DataFrame with columns: sector_name, sector_type
        """
        if not self._ensure_connected():
            return None

        try:
            # 获取分类板块
            all_sectors = []

            # 从mootdx获取板块数据需要使用stocks接口的不同category
            # category=5 是分类板块
            try:
                data = self.api.stocks(market=1, category=5)
                if data is not None and not data.empty:
                    all_sectors.append(data)
            except:
                pass

            if not all_sectors:
                return None

            df = pd.concat(all_sectors, ignore_index=True)

            # 重命名列
            df = df.rename(columns={'code': 'sector_symbol', 'name': 'sector_name', 'category': 'sector_type'})

            # 只需要的列
            df = df[['sector_symbol', 'sector_name', 'sector_type']]

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取板块列表失败: {e}")
            self.fail_count += 1
            return None

    def get_sector_kline(
        self,
        sector_symbol: str,
        start_date: str = None,
        end_date: str = None,
        period: str = "daily"
    ) -> Optional[pd.DataFrame]:
        """
        获取板块K线数据（通过指数方式）

        Args:
            sector_symbol: 板块代码
            start_date: 开始日期
            end_date: 结束日期
            period: 周期

        Returns:
            DataFrame or None
        """
        if not self._ensure_connected():
            return None

        try:
            # 板块K线可以通过指数方式获取
            # sector_symbol 需要确定市场
            code = str(sector_symbol).zfill(6)

            # 判断市场
            if code.startswith(('8', '11')):
                market = MARKET_SH
            elif code.startswith(('9', '10')):
                market = MARKET_SZ
            else:
                # 尝试沪市
                market = MARKET_SH

            category = CATEGORY_MAP.get(period, 4)

            all_data = []
            offset = 0
            batch_size = 800

            while True:
                data = self.api.get_index_bars(
                    category, market, code, offset, batch_size
                )

                if data is None or data.empty:
                    break

                all_data.append(data)

                if len(data) < batch_size:
                    break

                offset += batch_size

                if offset > 20000:
                    break

            if not all_data:
                self.fail_count += 1
                return None

            df = pd.concat(all_data, ignore_index=True)

            # 标准化列名
            df = df.rename(columns={
                'datetime': 'trade_date',
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'volume': 'volume',
                'amount': 'amount',
            })

            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            df['symbol'] = sector_symbol

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

            # 过滤未来日期
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today]

            columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']
            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            df = df[[c for c in columns if c in df.columns]]

            if df.empty:
                self.fail_count += 1
                return None

            self.success_count += 1
            return df

        except Exception as e:
            logger.warning(f"mootdx 获取板块 {sector_symbol} K线失败: {e}")
            self.fail_count += 1
            return None

    def disconnect(self):
        """断开连接"""
        if self.api:
            try:
                self.api.close()
                self.connected = False
                logger.info("mootdx 已断开连接")
            except:
                pass

    def __del__(self):
        """析构函数，确保断开连接"""
        self.disconnect()


# 单例模式
_mootdx_instance = None


def get_mootdx_adapter() -> Optional[MootdxAdapter]:
    """获取 mootdx 适配器单例"""
    global _mootdx_instance

    if _mootdx_instance is None:
        _mootdx_instance = MootdxAdapter()

    return _mootdx_instance if _mootdx_instance.is_available() else None


# 使用示例
if __name__ == "__main__":
    # 测试获取K线数据
    mootdx = MootdxAdapter()

    if mootdx.is_available():
        print("✅ mootdx 连接成功")
        print(f"服务器: {mootdx.current_server}")

        # 获取贵州茅台K线
        df = mootdx.get_stock_kline('600519', start_date='2024-01-01')
        if df is not None:
            print(f"获取到 {len(df)} 条K线数据")
            print(df.tail(3))
        else:
            print("获取K线数据失败")

        # 打印统计
        print(f"\n成功率: {mootdx.get_success_rate():.1%}")
    else:
        print("❌ mootdx 连接失败")

    mootdx.disconnect()
