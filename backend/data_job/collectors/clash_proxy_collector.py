"""
Clash Verge 代理轮询采集器（带测速过滤）
使用 Clash 的多个 SOCKS5 端口轮换 IP 采集数据
配置：端口 42000-42051（共 52 个节点）
"""

import requests
import time
import random
import logging
from typing import Optional, List, Dict
import pandas as pd
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ClashProxyRotator:
    """Clash 代理轮询器（带测速）"""

    def __init__(
        self,
        base_port: int = 42000,
        proxy_count: int = 52,
        max_retries_per_proxy: int = 3,
        speed_test_timeout: int = 5,
        max_latency_ms: int = 1000,  # 最大可接受延迟（毫秒）
    ):
        """
        初始化代理轮询器

        Args:
            base_port: 起始端口号
            proxy_count: 代理数量
            max_retries_per_proxy: 每个代理最大重试次数
            speed_test_timeout: 测速超时时间（秒）
            max_latency_ms: 最大可接受延迟（毫秒）
        """
        self.proxy_ports = list(range(base_port, base_port + proxy_count))
        self.current_index = 0
        self.failed_proxies = set()
        self.max_retries_per_proxy = max_retries_per_proxy
        self.retry_counts = {}
        self.speed_test_timeout = speed_test_timeout
        self.max_latency_ms = max_latency_ms

        # 代理速度测试结果（毫秒）
        self.proxy_speeds: Dict[int, float] = {}
        self.slow_proxies = set()
        self.fast_proxy_list = list(range(base_port, base_port + proxy_count))  # 初始化

    def speed_test_proxy(self, port: int) -> float:
        """
        测试代理延迟

        Args:
            port: 代理端口

        Returns:
            延迟（毫秒），失败返回 -1
        """
        try:
            import socks
            import socket

            # 测试 TCP 连接延迟（更快更可靠）
            start_time = time.time()
            sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_proxy(socks.SOCKS5, '127.0.0.1', port)
            sock.settimeout(self.speed_test_timeout)

            # 连接到百度（用于测速）
            sock.connect(('www.baidu.com', 80))
            elapsed_ms = (time.time() - start_time) * 1000
            sock.close()

            logger.debug(f"端口 {port} 测速：{elapsed_ms:.0f}ms")
            return elapsed_ms

        except Exception as e:
            logger.debug(f"端口 {port} 测速失败：{e}")
            return -1

    def speed_test_all_proxies(self) -> Dict[int, float]:
        """
        测试所有代理速度

        Returns:
            {port: latency_ms} 字典
        """
        logger.info("=" * 80)
        logger.info("🚀 开始代理测速...")
        logger.info("=" * 80)

        results = {}
        fast_count = 0
        slow_count = 0
        failed_count = 0

        for port in self.proxy_ports:
            latency = self.speed_test_proxy(port)
            results[port] = latency

            if latency > 0 and latency <= self.max_latency_ms:
                fast_count += 1
                self.proxy_speeds[port] = latency
                self.slow_proxies.discard(port)
                status = "✅"
            elif latency > self.max_latency_ms:
                slow_count += 1
                self.slow_proxies.add(port)
                status = "⚠️ 慢"
            else:
                failed_count += 1
                self.slow_proxies.add(port)
                status = "❌"

            logger.info(f"端口 {port}: {latency if latency > 0 else '失败'}ms {status}")

        # 统计
        logger.info("=" * 80)
        logger.info("📊 测速完成")
        logger.info(f"快速代理：{fast_count} 个 (≤{self.max_latency_ms}ms)")
        logger.info(f"慢速代理：{slow_count} 个 (>{self.max_latency_ms}ms)")
        logger.info(f"失败代理：{failed_count} 个")
        logger.info(f"可用代理：{fast_count}/{len(self.proxy_ports)} ({fast_count/len(self.proxy_ports)*100:.1f}%)")
        logger.info("=" * 80)

        # 按速度排序，优先使用快速代理
        self.fast_proxy_list = sorted(
            [p for p in self.proxy_ports if p not in self.slow_proxies],
            key=lambda p: self.proxy_speeds.get(p, float('inf'))
        )

        logger.info(f"🚀 快速代理列表（按速度排序）: {self.fast_proxy_list[:10]}...")

        return results

    def get_next_proxy(self, prefer_fast: bool = True) -> Optional[Dict]:
        """获取下一个可用的代理"""
        # 优先从快速代理中选择
        if prefer_fast and self.fast_proxy_list:
            port = self.fast_proxy_list[self.current_index % len(self.fast_proxy_list)]
            self.current_index += 1
        else:
            available_proxies = [p for p in self.proxy_ports if p not in self.failed_proxies and p not in self.slow_proxies]

            if not available_proxies:
                # 所有快速代理都失败，重置
                logger.warning("所有快速代理都已失败，重置代理列表...")
                self.failed_proxies.clear()
                self.retry_counts.clear()
                available_proxies = [p for p in self.proxy_ports if p not in self.slow_proxies]

            if not available_proxies:
                # 最后使用慢速代理
                available_proxies = [p for p in self.proxy_ports if p not in self.failed_proxies]

            if not available_proxies:
                # 完全重置
                self.failed_proxies.clear()
                self.slow_proxies.clear()
                available_proxies = self.proxy_ports

            port = available_proxies[self.current_index % len(available_proxies)]
            self.current_index += 1

        return {
            'http': f'socks5://127.0.0.1:{port}',
            'https': f'socks5://127.0.0.1:{port}'
        }

    def mark_proxy_failed(self, port: int):
        """标记代理失败"""
        self.retry_counts[port] = self.retry_counts.get(port, 0) + 1

        if self.retry_counts[port] >= self.max_retries_per_proxy:
            self.failed_proxies.add(port)
            # 从快速列表中移除
            if port in self.fast_proxy_list:
                self.fast_proxy_list.remove(port)
            logger.warning(f"代理端口 {port} 已达到最大重试次数，暂时禁用")

    def mark_proxy_success(self, port: int):
        """标记代理成功（重置重试计数）"""
        if port in self.retry_counts:
            del self.retry_counts[port]

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'total': len(self.proxy_ports),
            'fast_available': len(self.fast_proxy_list),
            'slow_filtered': len(self.slow_proxies),
            'failed': len(self.failed_proxies),
            'failed_ports': list(self.failed_proxies)[:10],
            'slow_ports': list(self.slow_proxies)[:10],
            'top_5_fastest': self.fast_proxy_list[:5] if hasattr(self, 'fast_proxy_list') else [],
        }


class ClashAKShareCollector:
    """使用 Clash 代理轮询的 AKShare 采集器"""

    def __init__(
        self,
        base_port: int = 42000,
        proxy_count: int = 52,
        min_delay: float = 0.5,
        max_delay: float = 1.5,
        max_retries: int = 10,
        max_latency_ms: int = 1000,
        auto_speed_test: bool = True,
    ):
        """
        初始化采集器

        Args:
            base_port: Clash 起始端口
            proxy_count: 代理数量
            min_delay: 最小延迟（秒）
            max_delay: 最大延迟（秒）
            max_retries: 最大重试次数
            max_latency_ms: 最大可接受延迟（毫秒）
            auto_speed_test: 是否自动测速
        """
        self.rotator = ClashProxyRotator(
            base_port=base_port,
            proxy_count=proxy_count,
            max_latency_ms=max_latency_ms
        )
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.auto_speed_test = auto_speed_test

        # 创建 Session
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })

        # 统计
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'proxy_switches': 0,
        }

        # 如果启用自动测速
        if self.auto_speed_test:
            self.rotator.speed_test_all_proxies()

    def _random_delay(self):
        """随机延迟"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    def fetch_with_proxy_rotation(self, func, *args, **kwargs):
        """
        使用代理轮询执行请求

        Args:
            func: 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        last_error = None
        proxy = None

        for attempt in range(self.max_retries):
            try:
                # 获取代理（优先快速代理）
                proxy = self.rotator.get_next_proxy(prefer_fast=True)
                port = int(proxy['http'].split(':')[-1])
                speed = self.rotator.proxy_speeds.get(port)
                speed_str = f"{speed:.0f}" if isinstance(speed, (int, float)) else "未知"
                logger.debug(f"尝试 {attempt + 1}/{self.max_retries}: 使用代理端口 {port} (速度：{speed_str}ms)")

                # 设置代理
                self.session.proxies.update(proxy)

                # 执行请求
                result = func(*args, **kwargs)

                # 成功
                self.rotator.mark_proxy_success(port)
                self.stats['success'] += 1

                # 随机延迟
                self._random_delay()

                return result

            except Exception as e:
                error_msg = str(e)
                last_error = e

                # 标记代理失败
                if proxy and ('proxy' in error_msg.lower() or 'socks' in error_msg.lower()):
                    port = int(proxy['http'].split(':')[-1])
                    self.rotator.mark_proxy_failed(port)
                    self.stats['proxy_switches'] += 1

                logger.warning(f"请求失败 {attempt + 1}/{self.max_retries}: {type(e).__name__}: {error_msg[:100]}")

                # 短暂等待后重试
                if attempt < self.max_retries - 1:
                    wait_time = 0.5 + random.uniform(0, 0.5)
                    time.sleep(wait_time)

        self.stats['failed'] += 1
        raise last_error or RuntimeError("所有重试均失败")

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """
        获取股票 K 线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型

        Returns:
            DataFrame
        """
        import akshare as ak

        self.stats['total'] += 1

        def fetch():
            return ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust
            )

        try:
            df = self.fetch_with_proxy_rotation(fetch)

            if df is not None and not df.empty:
                # 数据清洗和标准化
                df = self._clean_data(df, symbol)
                return df
            return None

        except Exception as e:
            logger.error(f"{symbol} 获取失败：{e}")
            return None

    def _clean_data(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        数据清洗 - 符合数据库格式

        Args:
            df: 原始数据
            symbol: 股票代码

        Returns:
            清洗后的 DataFrame
        """
        # 添加 symbol 列
        df['symbol'] = symbol

        # 标准化列名映射（AKShare -> 数据库）
        column_mapping = {
            '日期': 'trade_date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume',
            '成交额': 'amount',
            '振幅': 'amplitude',
            '涨跌幅': 'pct_chg',
            '涨跌额': 'change',
            '换手率': 'turnover'
        }

        # 重命名列
        df = df.rename(columns=column_mapping)

        # 转换日期格式为 YYYY-MM-DD
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

        # 选择需要的列（与数据库表结构匹配）
        target_columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount']
        available_columns = [col for col in target_columns if col in df.columns]
        df = df[available_columns]

        # 数据类型转换
        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'amount']
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df

    def collect_batch(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        adjust: str = 'qfq',
        progress_callback=None
    ) -> Dict[str, Optional[pd.DataFrame]]:
        """
        批量采集

        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型
            progress_callback: 进度回调函数

        Returns:
            {symbol: DataFrame} 字典
        """
        results = {}
        total = len(symbols)

        logger.info("=" * 80)
        logger.info("🚀 开始使用 Clash 代理轮询采集数据")
        logger.info("=" * 80)
        logger.info(f"股票数量：{total}")
        logger.info(f"日期范围：{start_date} ~ {end_date}")
        logger.info(f"代理端口：42000-42051")
        logger.info(f"延迟设置：{self.min_delay}-{self.max_delay}秒")

        # 打印代理统计
        proxy_stats = self.rotator.get_stats()
        logger.info(f"快速代理：{proxy_stats['fast_available']} 个")
        logger.info(f"过滤慢速：{proxy_stats['slow_filtered']} 个")
        logger.info(f"最快 5 个：{proxy_stats['top_5_fastest']}")
        logger.info("=" * 80)

        for idx, symbol in enumerate(symbols, 1):
            try:
                logger.info(f"[{idx}/{total}] 采集 {symbol}...")

                df = self.get_stock_kline(symbol, start_date, end_date, adjust)

                if df is not None and not df.empty:
                    results[symbol] = df
                    logger.info(f"    ✅ 成功：{len(df)}条记录")
                else:
                    results[symbol] = None
                    logger.warning(f"    ⚠️  无数据")

                if progress_callback:
                    progress_callback(symbol, df is not None)

                # 每 10 只股票打印代理统计
                if idx % 10 == 0:
                    proxy_stats = self.rotator.get_stats()
                    logger.info(f"    📊 代理统计：可用={proxy_stats['fast_available']}, "
                               f"切换={self.stats['proxy_switches']}次")

            except Exception as e:
                results[symbol] = None
                logger.error(f"    ❌ 失败：{e}")

        # 最终统计
        self._print_final_stats(total)

        return results

    def _print_final_stats(self, total: int):
        """打印最终统计"""
        logger.info("\n" + "=" * 80)
        logger.info("📊 采集统计")
        logger.info("=" * 80)
        logger.info(f"总股票数：{total}")
        logger.info(f"成功：{self.stats['success']} ({self.stats['success']/total*100:.1f}%)")
        logger.info(f"失败：{self.stats['failed']} ({self.stats['failed']/total*100:.1f}%)")
        logger.info(f"代理切换次数：{self.stats['proxy_switches']}")

        proxy_stats = self.rotator.get_stats()
        logger.info(f"快速代理：{proxy_stats['fast_available']}")
        logger.info(f"慢速过滤：{proxy_stats['slow_filtered']}")
        logger.info(f"失败代理：{proxy_stats['failed']}")
        logger.info("=" * 80)


# 使用示例
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # 添加路径
    backend_dir = Path(__file__).parent.parent.parent.absolute()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.core.database import get_engine
    from sqlalchemy import text

    # 创建采集器（自动测速）
    collector = ClashAKShareCollector(
        base_port=42000,
        proxy_count=52,
        min_delay=0.5,
        max_delay=1.5,
        max_retries=15,  # 更多重试次数
        max_latency_ms=1000,  # 超过 1 秒的代理将被过滤
        auto_speed_test=True,  # 启动时自动测速
    )

    # 获取少量股票测试
    engine = get_engine()
    with engine.connect() as conn:
        query = text("""
            SELECT DISTINCT symbol FROM quant_stock_pool
            WHERE pool_name = 'core_pool' AND is_active = 1
            ORDER BY symbol
            LIMIT 20
        """)
        result = conn.execute(query)
        symbols = [row[0] for row in result]

    # 日期范围（最近 10 天）
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

    # 开始采集
    results = collector.collect_batch(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        progress_callback=lambda sym, success: print(f"进度：{sym} - {'✅' if success else '❌'}")
    )

    print(f"\n采集完成！成功：{sum(1 for v in results.values() if v is not None)}/{len(symbols)}")
