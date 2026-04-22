"""
直接数据获取器 - 绕过 AKShare，直接获取数据
支持多种数据源和强大的重试机制
"""

import requests
import time
import random
import logging
import pandas as pd
from datetime import datetime
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class DirectDataFetcher:
    """直接数据获取器"""

    def __init__(
        self,
        proxy_host='127.0.0.1',
        proxy_port=7897,
        use_proxy=True,
        max_retries=5,
        base_delay=2.0
    ):
        """
        初始化数据获取器

        Args:
            proxy_host: 代理主机
            proxy_port: 代理端口
            use_proxy: 是否使用代理
            max_retries: 最大重试次数
            base_delay: 基础延迟（秒）
        """
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.use_proxy = use_proxy
        self.max_retries = max_retries
        self.base_delay = base_delay

        # 配置代理
        if use_proxy:
            self.proxies = {
                'http': f'http://{proxy_host}:{proxy_port}',
                'https': f'http://{proxy_host}:{proxy_port}'
            }
            logger.info(f"使用代理: {proxy_host}:{proxy_port}")
        else:
            self.proxies = None
            logger.info("不使用代理")

        # 创建 session
        self.session = self._create_session()

        # 统计
        self.stats = {
            'success': 0,
            'failed': 0,
            'retries': 0
        }

    def _create_session(self):
        """创建带重试的 session"""
        session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 设置请求头
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://quote.eastmoney.com/',
        })

        return session

    def _random_delay(self):
        """随机延迟"""
        delay = self.base_delay + random.uniform(0, 1)
        time.sleep(delay)

    def fetch_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust='qfq'
    ) -> Optional[pd.DataFrame]:
        """
        获取股票K线数据

        Args:
            symbol: 股票代码（如 000001）
            start_date: 开始日期（格式：20240301）
            end_date: 结束日期（格式：20240308）
            adjust: 复权类型（qfq=前复权，hfq=后复权，''=不复权）

        Returns:
            DataFrame or None
        """
        # 构造东方财富接口参数
        secid = f"0.{symbol}" if symbol.startswith('0') or symbol.startswith('3') else f"1.{symbol}"
        fqt = "1" if adjust == 'qfq' else ("2" if adjust == 'hfq' else "")

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
            'beg': start_date,
            'end': end_date,
            'rtntype': '6',
            'secid': secid,
            'klt': '101',  # 日K
            'fqt': fqt
        }

        last_error = None

        for attempt in range(self.max_retries):
            try:
                # 随机延迟（除了第一次）
                if attempt > 0:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"重试 {attempt + 1}/{self.max_retries}，等待 {delay:.1f}s...")
                    time.sleep(delay)
                    self.stats['retries'] += 1
                else:
                    self._random_delay()

                # 发送请求
                response = self.session.get(
                    url,
                    params=params,
                    proxies=self.proxies,
                    timeout=15
                )

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")

                # 解析数据
                data = response.json()

                if not data or not data.get('data'):
                    logger.warning(f"{symbol} 返回空数据")
                    return None

                items = data['data']
                klines = items.get('klines', [])

                if not klines:
                    logger.warning(f"{symbol} 没有K线数据")
                    return None

                # 解析K线数据
                df = self._parse_klines(klines, symbol)
                self.stats['success'] += 1

                return df

            except Exception as e:
                last_error = e
                logger.warning(f"获取 {symbol} 失败 (尝试 {attempt + 1}/{self.max_retries}): {str(e)[:60]}")

        # 所有重试都失败
        self.stats['failed'] += 1
        logger.error(f"{symbol} 所有重试失败")
        return None

    def _parse_klines(self, klines: list, symbol: str) -> pd.DataFrame:
        """
        解析K线数据

        K线格式: "日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"
        """
        data = []

        for kline in klines:
            parts = kline.split(',')
            if len(parts) < 12:
                continue

            try:
                data.append({
                    'symbol': symbol,
                    'trade_date': parts[0],  # 日期
                    'open': float(parts[1]),  # 开盘
                    'close': float(parts[2]),  # 收盘
                    'high': float(parts[3]),   # 最高
                    'low': float(parts[4]),    # 最低
                    'volume': float(parts[5]), # 成交量
                    'amount': float(parts[6]), # 成交额
                    'pct_chg': float(parts[8]),  # 涨跌幅
                    'turnover_rate': float(parts[10])  # 换手率
                })
            except (ValueError, IndexError) as e:
                logger.debug(f"解析K线失败: {kline}, {e}")
                continue

        df = pd.DataFrame(data)

        # 转换日期格式
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        return df

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self.stats['success'] + self.stats['failed']
        success_rate = self.stats['success'] / total if total > 0 else 0

        return {
            **self.stats,
            'total': total,
            'success_rate': success_rate
        }

    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        print("\n" + "=" * 60)
        print("📊 数据获取统计")
        print("=" * 60)
        print(f"总计: {stats['total']}")
        print(f"成功: {stats['success']}")
        print(f"失败: {stats['failed']}")
        print(f"重试: {stats['retries']}")
        print(f"成功率: {stats['success_rate']:.1%}")
        print("=" * 60)


# 测试
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    fetcher = DirectDataFetcher(
        use_proxy=True,
        max_retries=5,
        base_delay=2.0
    )

    print("=" * 60)
    print("测试直接获取K线数据")
    print("=" * 60)

    df = fetcher.fetch_stock_kline(
        symbol='000001',
        start_date='20240301',
        end_date='20240308'
    )

    if df is not None and not df.empty:
        print(f"\n✅ 成功获取 {len(df)} 行数据")
        print(f"\n{df.head()}")
    else:
        print("\n❌ 获取失败")

    fetcher.print_stats()
