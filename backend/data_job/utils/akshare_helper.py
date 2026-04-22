"""
AKShare数据采集辅助工具
提供防封、代理、重试等功能的统一接口
"""

import os
import logging
from typing import Optional, List
import akshare as ak

from .anti_ban import AntiBanStrategy, create_anti_ban_strategy, ANTI_BAN_CONFIGS

logger = logging.getLogger(__name__)


class AKShareCollector:
    """
    AKShare防封采集器

    统一的AKShare数据采集接口，内置防封策略
    """

    def __init__(
        self,
        mode: str = 'balanced',
        proxy_list: Optional[List[str]] = None,
        proxy_enabled: bool = False,
        auto_proxy: bool = True,  # 自动检测系统代理
    ):
        """
        初始化AKShare采集器

        Args:
            mode: 防封模式 ('conservative', 'balanced', 'aggressive')
            proxy_list: 代理列表
            proxy_enabled: 是否启用代理
            auto_proxy: 自动检测系统代理
        """
        # 自动检测系统代理
        if auto_proxy and not proxy_list:
            proxy_list = self._detect_system_proxy()
            if proxy_list:
                logger.info(f"检测到系统代理: {proxy_list}")
                proxy_enabled = True

        # 创建防封策略
        self.anti_ban = create_anti_ban_strategy(
            mode=mode,
            proxy_list=proxy_list,
            proxy_enabled=proxy_enabled
        )

        # 配置AKShare使用代理
        self._setup_akshare_proxy()

        logger.info(f"AKShare采集器初始化完成 (模式: {mode})")

    def _detect_system_proxy(self) -> List[str]:
        """检测系统代理设置"""
        proxies = []

        # 检查环境变量
        http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')

        if http_proxy:
            proxies.append(http_proxy)
        if https_proxy and https_proxy != http_proxy:
            proxies.append(https_proxy)

        # 检查常见的本地代理端口
        common_ports = [7890, 7891, 10808, 10809, 1087]
        for port in common_ports:
            try:
                import requests
                response = requests.get('http://127.0.0.1', timeout=1)
                # 如果能访问，说明可能有代理
                proxies.append(f'http://127.0.0.1:{port}')
            except:
                pass

        return proxies

    def _setup_akshare_proxy(self):
        """配置AKShare使用代理"""
        if self.anti_ban.proxy_enabled and self.anti_ban.proxy_list:
            # 设置第一个代理为默认代理
            proxy = self.anti_ban.proxy_list[0]
            os.environ['AKSHARE_PROXY'] = proxy
            logger.info(f"AKShare代理已设置: {proxy}")

    def safe_get(self, func_name: str, *args, max_retries: Optional[int] = None, **kwargs):
        """
        安全调用AKShare接口（带防封）

        Args:
            func_name: akshare函数名（如 'stock_zh_a_daily'）
            *args: 函数参数
            max_retries: 最大重试次数（覆盖默认值）
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        if not hasattr(ak, func_name):
            raise AttributeError(f"akshare模块没有 '{func_name}' 函数")

        func = getattr(ak, func_name)

        # 临时修改重试次数
        original_max_retries = self.anti_ban.max_retries
        if max_retries is not None:
            self.anti_ban.max_retries = max_retries

        try:
            result = self.anti_ban.safe_request(func, *args, **kwargs)
            return result
        finally:
            # 恢复原始重试次数
            self.anti_ban.max_retries = original_max_retries

    def batch_get(
        self,
        func_name: str,
        items: list,
        batch_size: int = 100,
        batch_rest: int = 10,
        progress_callback: Optional = None,
    ) -> dict:
        """
        批量调用AKShare接口（分批处理）

        Args:
            func_name: akshare函数名
            items: 要处理的项目列表
            batch_size: 每批大小
            batch_rest: 批次间休息时间
            progress_callback: 进度回调

        Returns:
            dict {item: result}
        """
        if not hasattr(ak, func_name):
            raise AttributeError(f"akshare模块没有 '{func_name}' 函数")

        func = getattr(ak, func_name)

        return self.anti_ban.batch_request(
            func=func,
            items=items,
            batch_size=batch_size,
            batch_rest=batch_rest,
            progress_callback=progress_callback
        )

    def get_stock_zh_a_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq',
    ):
        """
        获取A股日线数据（防封版本）

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型

        Returns:
            DataFrame
        """
        logger.debug(f"获取 {symbol} 日线数据: {start_date} ~ {end_date}")

        return self.safe_get(
            'stock_zh_a_daily',
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )

    def get_stock_info_a_code_name(self):
        """获取A股股票信息（防封版本）"""
        logger.debug("获取A股股票列表")

        return self.safe_get('stock_info_a_code_name')

    def get_stock_zh_a_hist(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "19900101",
        end_date: str = "21000101",
        adjust: str = "qfq"
    ):
        """
        获取A股历史数据（防封版本）

        Args:
            symbol: 股票代码
            period: 数据周期
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型

        Returns:
            DataFrame
        """
        logger.debug(f"获取 {symbol} 历史数据: {start_date} ~ {end_date}")

        return self.safe_get(
            'stock_zh_a_hist',
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )

    def get_stats(self) -> dict:
        """获取采集统计信息"""
        return self.anti_ban.get_stats()

    def print_stats(self):
        """打印采集统计信息"""
        stats = self.get_stats()

        print("\n" + "=" * 60)
        print("AKShare采集统计")
        print("=" * 60)
        print(f"总请求数: {stats['total_requests']}")
        print(f"成功请求: {stats['successful_requests']}")
        print(f"失败请求: {stats['failed_requests']}")
        print(f"重试次数: {stats['retries']}")
        print(f"代理切换: {stats['proxy_switches']}")
        print(f"成功率: {stats['success_rate']:.1%}")
        print("=" * 60)

    def cleanup(self):
        """清理资源"""
        self.anti_ban.__exit__(None, None, None)
        logger.info("AKShare采集器已清理")

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.cleanup()


# 便捷函数
def create_akshare_collector(
    mode: str = 'balanced',
    proxy_list: Optional[List[str]] = None,
    **kwargs
) -> AKShareCollector:
    """
    创建AKShare采集器（便捷函数）

    Args:
        mode: 防封模式
        proxy_list: 代理列表
        **kwargs: 其他参数

    Returns:
        AKShareCollector实例

    示例:
    ```python
    # 使用默认配置
    collector = create_akshare_collector()

    # 使用保守策略
    collector = create_akshare_collector(mode='conservative')

    # 使用自定义代理
    collector = create_akshare_collector(
        proxy_list=['http://127.0.0.1:7890']
    )
    ```
    """
    return AKShareCollector(
        mode=mode,
        proxy_list=proxy_list,
        **kwargs
    )
