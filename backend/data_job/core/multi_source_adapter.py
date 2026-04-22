"""
多数据源适配器
当主数据源失败时自动切换到备用数据源

支持的数据源：
- pytdx: 通达信协议，稳定性最高，适合K线数据
- Tushare: 专业API，需要token
- EFinance: 东方财富API，稳定且免费
- AKShare: 多源聚合，数据最全但易封IP
- BaoStock: 免费但较慢
"""

import logging
from typing import Optional, List, Dict
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class DataSourceAdapter:
    """数据源适配器基类"""

    def __init__(self, name: str):
        self.name = name
        self.success_count = 0
        self.fail_count = 0

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        raise NotImplementedError

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """获取股票列表"""
        raise NotImplementedError

    def get_success_rate(self) -> float:
        """获取成功率"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total


class TushareAdapter(DataSourceAdapter):
    """Tushare数据源适配器"""

    def __init__(self, token: Optional[str] = None):
        super().__init__("Tushare")
        self.token = token
        self.pro = None

        if token:
            try:
                import tushare as ts
                ts.set_token(token)
                self.pro = ts.pro_api()
                logger.info("✅ Tushare初始化成功")
            except Exception as e:
                logger.warning(f"⚠️  Tushare初始化失败: {e}")

    def is_available(self) -> bool:
        """检查是否可用"""
        return self.pro is not None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        if not self.is_available():
            return None

        try:
            # 转换股票代码格式（000001 -> 000001.SZ）
            if '.' not in symbol:
                if symbol.startswith('6'):
                    ts_code = f"{symbol}.SH"
                else:
                    ts_code = f"{symbol}.SZ"
            else:
                ts_code = symbol

            # 转换日期格式（20260301 -> 20260301）
            start = start_date.replace('-', '')
            end = end_date.replace('-', '')

            # 获取数据
            df = self.pro.daily(
                ts_code=ts_code,
                start_date=start,
                end_date=end
            )

            if df is not None and not df.empty:
                # 标准化列名
                df = df.rename(columns={
                    'ts_code': 'symbol',
                    'trade_date': 'trade_date',
                    'open': 'open',
                    'high': 'high',
                    'low': 'low',
                    'close': 'close',
                    'vol': 'volume',
                    'amount': 'amount'
                })

                # 选择需要的列
                df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']]

                self.success_count += 1
                return df
            else:
                self.fail_count += 1
                return None

        except Exception as e:
            logger.warning(f"Tushare获取{symbol}失败: {e}")
            self.fail_count += 1
            return None


class EFinanceAdapter(DataSourceAdapter):
    """EFinance数据源适配器"""

    def __init__(self):
        super().__init__("EFinance")
        try:
            import efinance as ef
            self.ef = ef
            logger.info("✅ EFinance初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  EFinance初始化失败: {e}")
            self.ef = None

    def is_available(self) -> bool:
        """检查是否可用"""
        return self.ef is not None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        if not self.is_available():
            return None

        try:
            # 转换日期格式
            start = start_date.replace('-', '')
            end = end_date.replace('-', '')

            # 获取数据
            df = self.ef.stock.get_quote_history(
                stock_codes=symbol,
                beg=start,
                end=end
            )

            if df is not None and not df.empty:
                # 标准化列名
                df = df.rename(columns={
                    '股票代码': 'symbol',
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount'
                })

                # 选择需要的列
                df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']]

                self.success_count += 1
                return df
            else:
                self.fail_count += 1
                return None

        except Exception as e:
            logger.warning(f"EFinance获取{symbol}失败: {e}")
            self.fail_count += 1
            return None


class AKShareAdapter(DataSourceAdapter):
    """AKShare数据源适配器"""

    def __init__(self):
        super().__init__("AKShare")
        try:
            import akshare as ak
            self.ak = ak
            logger.info("✅ AKShare初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  AKShare初始化失败: {e}")
            self.ak = None

    def is_available(self) -> bool:
        """检查是否可用"""
        return self.ak is not None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        if not self.is_available():
            return None

        try:
            # 转换日期格式
            start = start_date.replace('-', '')
            end = end_date.replace('-', '')

            # 获取数据
            df = self.ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq" if adjust == 'qfq' else ""
            )

            if df is not None and not df.empty:
                # 标准化列名
                df = df.rename(columns={
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount'
                })

                # 添加symbol列
                df['symbol'] = symbol

                # 选择需要的列
                df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']]

                self.success_count += 1
                return df
            else:
                self.fail_count += 1
                return None

        except Exception as e:
            logger.warning(f"AKShare获取{symbol}失败: {e}")
            self.fail_count += 1
            return None


class BaoStockAdapter(DataSourceAdapter):
    """BaoStock数据源适配器"""

    def __init__(self):
        super().__init__("BaoStock")
        try:
            import baostock as bs
            self.bs = bs
            # 登录
            lg = bs.login()
            if lg.error_code == '0':
                logger.info("✅ BaoStock初始化成功")
            else:
                logger.warning(f"⚠️  BaoStock登录失败: {lg.error_msg}")
        except Exception as e:
            logger.warning(f"⚠️  BaoStock初始化失败: {e}")
            self.bs = None

    def is_available(self) -> bool:
        """检查是否可用"""
        return self.bs is not None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        if not self.is_available():
            return None

        try:
            # 转换股票代码格式
            if symbol.startswith('6'):
                bs_symbol = f"sh.{symbol}"
            else:
                bs_symbol = f"sz.{symbol}"

            # 转换日期格式
            start = start_date.replace('-', '')
            end = end_date.replace('-', '')

            # 获取数据
            rs = self.bs.query_history_k_data_plus(
                bs_symbol,
                "date,code,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2" if adjust == 'qfq' else "3"
            )

            if rs.error_code == '0':
                df = rs.data
                if df is not None and not df.empty:
                    # 标准化列名
                    df = df.rename(columns={
                        'date': 'trade_date',
                        'code': 'symbol',
                        'open': 'open',
                        'high': 'high',
                        'low': 'low',
                        'close': 'close',
                        'volume': 'volume',
                        'amount': 'amount'
                    })

                    # 去掉symbol中的前缀
                    df['symbol'] = df['symbol'].str.replace('sh.', '').str.replace('sz.', '')

                    # 选择需要的列
                    df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']]

                    self.success_count += 1
                    return df

            self.fail_count += 1
            return None

        except Exception as e:
            logger.warning(f"BaoStock获取{symbol}失败: {e}")
            self.fail_count += 1
            return None

    def __del__(self):
        """退出登录"""
        if hasattr(self, 'bs') and self.bs:
            try:
                self.bs.logout()
            except:
                pass


class TdxAdapter(DataSourceAdapter):
    """
    pytdx 数据源适配器 (通达信协议)

    优点：
    - 使用通达信二进制协议，稳定性极高
    - 不易被封IP，速度快
    - 纯Python实现，Mac兼容性好

    缺点：
    - 不支持复权
    - 财务数据不结构化
    """

    def __init__(self):
        super().__init__("pytdx")
        try:
            # 动态导入 TdxAdapter
            import sys
            import os
            from pathlib import Path

            # 添加当前目录到路径
            current_dir = Path(__file__).parent.absolute()
            if str(current_dir) not in sys.path:
                sys.path.insert(0, str(current_dir))

            from data_job.core.tdx_adapter import TdxAdapter as TdxClient
            self.client = TdxClient()
            logger.info("✅ pytdx初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  pytdx初始化失败: {e}")
            self.client = None

    def is_available(self) -> bool:
        """检查是否可用"""
        return self.client is not None and self.client.is_available()

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        if not self.is_available():
            return None

        try:
            df = self.client.get_stock_kline(symbol, start_date, end_date)
            if df is not None and not df.empty:
                self.success_count += 1
                return df
            else:
                self.fail_count += 1
                return None
        except Exception as e:
            logger.warning(f"pytdx获取{symbol}失败: {e}")
            self.fail_count += 1
            return None


class MultiSourceAdapter:
    """
    多数据源适配器

    优先级：
    1. pytdx (通达信协议，K线数据首选)
    2. Tushare (最稳定，需token)
    3. EFinance (免费稳定)
    4. AKShare (免费但限制多)
    5. BaoStock (免费但慢)
    """

    def __init__(
        self,
        tushare_token: Optional[str] = None,
        sources: Optional[List[str]] = None,
        data_type: str = "kline"
    ):
        """
        初始化多数据源适配器

        Args:
            tushare_token: Tushare API Token
            sources: 数据源列表，默认按优先级排序
            data_type: 数据类型 (kline-默认K线优先, finance-财务数据优先)
        """
        # 初始化所有数据源
        self.sources = []

        if sources is None:
            # 根据数据类型设置默认优先级
            if data_type == "kline":
                # K线数据：pytdx 优先
                sources = ['pytdx', 'efinance', 'akshare', 'baostock']
            elif data_type == "finance":
                # 财务数据：efinance 优先
                sources = ['efinance', 'akshare']
            else:
                sources = ['efinance', 'akshare']

        for source_name in sources:
            if source_name == 'pytdx':
                adapter = TdxAdapter()
                if adapter.is_available():
                    self.sources.append(adapter)
            elif source_name == 'tushare' and tushare_token:
                adapter = TushareAdapter(tushare_token)
                if adapter.is_available():
                    self.sources.append(adapter)
            elif source_name == 'efinance':
                adapter = EFinanceAdapter()
                if adapter.is_available():
                    self.sources.append(adapter)
            elif source_name == 'akshare':
                adapter = AKShareAdapter()
                if adapter.is_available():
                    self.sources.append(adapter)
            elif source_name == 'baostock':
                adapter = BaoStockAdapter()
                if adapter.is_available():
                    self.sources.append(adapter)

        logger.info(f"✅ 多数据源适配器初始化完成，可用数据源: {len(self.sources)}个")
        for source in self.sources:
            logger.info(f"   - {source.name}")

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """
        获取股票K线数据（自动切换数据源）

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型

        Returns:
            DataFrame or None
        """
        last_error = None

        for adapter in self.sources:
            try:
                logger.debug(f"尝试使用 {adapter.name} 获取 {symbol} 数据...")

                df = adapter.get_stock_kline(symbol, start_date, end_date, adjust)

                if df is not None and not df.empty:
                    logger.info(f"✅ {adapter.name} 成功获取 {symbol} 数据")
                    return df
                else:
                    logger.debug(f"⚠️  {adapter.name} 无数据")
                    continue

            except Exception as e:
                logger.warning(f"⚠️  {adapter.name} 获取失败: {e}")
                last_error = e
                continue

        # 所有数据源都失败
        error_msg = f"所有数据源均失败获取 {symbol} 数据"
        if last_error:
            error_msg += f": {last_error}"
        logger.error(error_msg)
        return None

    def get_stats(self) -> dict:
        """获取各数据源的统计信息"""
        stats = {}
        for adapter in self.sources:
            stats[adapter.name] = {
                'success': adapter.success_count,
                'failed': adapter.fail_count,
                'success_rate': adapter.get_success_rate(),
            }
        return stats

    def print_stats(self):
        """打印统计信息"""
        print("\n" + "=" * 80)
        print("📊 数据源统计")
        print("=" * 80)

        stats = self.get_stats()
        for name, stat in stats.items():
            print(f"\n{name}:")
            print(f"  成功: {stat['success']}")
            print(f"  失败: {stat['failed']}")
            print(f"  成功率: {stat['success_rate']:.1%}")

        print("=" * 80)


# 使用示例
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    # 加载环境变量
    load_dotenv()

    # 创建多数据源适配器
    adapter = MultiSourceAdapter(
        tushare_token=os.getenv('TUSHARE_TOKEN'),
        sources=['tushare', 'efinance', 'akshare']
    )

    # 测试获取数据
    df = adapter.get_stock_kline(
        symbol='000001',
        start_date='2026-03-01',
        end_date='2026-03-07'
    )

    if df is not None:
        print(f"✅ 成功获取数据: {len(df)}条")
        print(df.head())
    else:
        print("❌ 获取数据失败")

    # 打印统计
    adapter.print_stats()
