"""
数据源抽象层 - 支持多数据源采集
提供统一的数据源接口，支持AKShare、聚宽等多种数据源
"""

import sys
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

logger = logging.getLogger(__name__)


class BaseDataSource(ABC):
    """数据源抽象基类 - 定义统一的数据获取接口"""

    def __init__(self, name: str, priority: int = 0):
        """
        初始化数据源

        Args:
            name: 数据源名称（如 'akshare', 'joinquant'）
            priority: 优先级，数字越大越优先
        """
        self.name = name
        self.priority = priority
        self._initialized = False

    @abstractmethod
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
            DataFrame with columns: trade_date, open, close, high, low, volume, amount, etc.
        """
        pass

    @abstractmethod
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
            DataFrame with columns: trade_date, open, close, high, low, volume, amount, etc.
        """
        pass

    @abstractmethod
    def get_etf_kline(self, symbol: str, start_date: str,
                     end_date: str) -> Optional[pd.DataFrame]:
        """
        获取ETF K线数据

        Args:
            symbol: ETF代码
            start_date: 开始日期（YYYYMMDD格式）
            end_date: 结束日期（YYYYMMDD格式）

        Returns:
            DataFrame with columns: trade_date, open, close, high, low, volume, amount, etc.
        """
        pass

    @abstractmethod
    def get_limit_boards(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        获取涨跌停数据

        Args:
            date_str: 日期（YYYYMMDD格式）

        Returns:
            DataFrame with columns: symbol, name, pct_chg, latest_price, etc.
        """
        pass

    @abstractmethod
    def get_fund_holdings(self, quarter: str) -> Optional[pd.DataFrame]:
        """
        获取基金持仓数据

        Args:
            quarter: 季度（如 '2024Q1'）

        Returns:
            DataFrame with columns: symbol, fund_count, hold_count, etc.
        """
        pass

    @abstractmethod
    def get_stock_valuation(self) -> Optional[pd.DataFrame]:
        """
        获取股票估值数据

        Returns:
            DataFrame with columns: symbol, name, latest_price, pe_ttm, pb, etc.
        """
        pass

    def normalize_columns(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        标准化列名（将各数据源的中/英文列名统一为标准英文列名）

        Args:
            df: 原始数据
            data_type: 数据类型（如 'get_stock_kline', 'get_sector_kline'）

        Returns:
            标准化后的DataFrame
        """
        # 默认不做转换，由子类实现
        return df

    def check_health(self) -> bool:
        """
        健康检查 - 检查数据源是否可用

        Returns:
            True if healthy, False otherwise
        """
        try:
            self._init_client()
            return True
        except Exception as e:
            logger.warning(f"数据源 {self.name} 健康检查失败: {e}")
            return False

    def _init_client(self):
        """初始化客户端连接（由子类实现）"""
        if not self._initialized:
            self._do_init()
            self._initialized = True

    def _do_init(self):
        """实际初始化逻辑（由子类实现）"""
        pass


class DataSourceManager:
    """数据源管理器 - 支持多数据源并行采集和降级"""

    def __init__(self, sources: List[BaseDataSource], strategy: str = 'parallel'):
        """
        初始化数据源管理器

        Args:
            sources: 数据源列表
            strategy: 采集策略
                - 'parallel': 并行采集，返回第一个成功的结果（推荐）
                - 'priority': 按优先级顺序尝试，失败则降级
                - 'majority': 多源采集，返回多数一致的结果（暂未实现）
        """
        # 按优先级排序（数字越大越优先）
        self.sources = sorted(sources, key=lambda x: x.priority, reverse=True)
        self.strategy = strategy

        if not self.sources:
            raise ValueError("至少需要配置一个数据源")

        logger.info(f"初始化数据源管理器: 策略={strategy}, 数据源={[s.name for s in self.sources]}")

    def fetch_with_fallback(self, method_name: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        带降级策略的数据获取 - 按优先级顺序尝试数据源

        Args:
            method_name: 要调用的方法名（如 'get_stock_kline'）
            **kwargs: 方法参数

        Returns:
            第一个成功的数据源返回的数据

        Raises:
            Exception: 所有数据源都失败时抛出异常
        """
        last_error = None

        for source in self.sources:
            try:
                logger.debug(f"尝试使用数据源 {source.name} 调用 {method_name}")
                method = getattr(source, method_name)
                df = method(**kwargs)

                if df is not None and not df.empty:
                    logger.info(f"数据源 {source.name} 成功获取数据")
                    # 标准化列名
                    return source.normalize_columns(df, method_name)
                else:
                    logger.warning(f"数据源 {source.name} 返回空数据")

            except Exception as e:
                last_error = e
                logger.warning(f"数据源 {source.name} 调用失败: {e}")
                continue

        # 所有数据源都失败
        error_msg = f"所有数据源均失败，最后错误: {last_error}"
        logger.error(error_msg)
        raise Exception(error_msg)

    def fetch_parallel(self, method_name: str, **kwargs) -> Dict[str, Optional[pd.DataFrame]]:
        """
        并行采集 - 同时调用所有数据源，返回所有结果

        Args:
            method_name: 要调用的方法名
            **kwargs: 方法参数

        Returns:
            {source_name: dataframe} 字典
        """
        results = {}

        def fetch_from_source(source):
            try:
                method = getattr(source, method_name)
                df = method(**kwargs)
                normalized = source.normalize_columns(df, method_name) if df is not None else None
                return source.name, normalized
            except Exception as e:
                logger.warning(f"数据源 {source.name} 并行调用失败: {e}")
                return source.name, None

        # 使用线程池并行调用
        with ThreadPoolExecutor(max_workers=len(self.sources)) as executor:
            futures = {executor.submit(fetch_from_source, source): source
                      for source in self.sources}

            for future in as_completed(futures):
                source_name, df = future.result()
                results[source_name] = df

        # 记录结果
        success_count = sum(1 for v in results.values() if v is not None)
        logger.info(f"并行采集完成: 成功 {success_count}/{len(self.sources)} 个数据源")

        return results

    def fetch_with_validation(self, method_name: str, **kwargs) -> pd.DataFrame:
        """
        多源采集并验证数据一致性 - 返回第一个成功的结果

        TODO: 未来可以实现更复杂的数据一致性验证逻辑

        Args:
            method_name: 要调用的方法名
            **kwargs: 方法参数

        Returns:
            验证通过的数据
        """
        results = self.fetch_parallel(method_name, **kwargs)
        valid_results = {k: v for k, v in results.items() if v is not None and not v.empty}

        if not valid_results:
            raise Exception("所有数据源均失败或返回空数据")

        # 目前直接返回第一个有效结果
        # TODO: 实现数据一致性验证和多数投票逻辑
        first_source = next(iter(valid_results))
        logger.info(f"使用数据源 {first_source} 的结果")
        return valid_results[first_source]

    def check_health(self) -> Dict[str, bool]:
        """
        检查所有数据源的健康状态

        Returns:
            {source_name: is_healthy} 字典
        """
        health_status = {}
        for source in self.sources:
            health_status[source.name] = source.check_health()
        return health_status

    def get_primary_source(self) -> BaseDataSource:
        """获取主数据源（优先级最高的）"""
        return self.sources[0]


class DataSourceError(Exception):
    """数据源异常"""
    pass


class DataSourceNotAvailableError(DataSourceError):
    """数据源不可用异常"""
    pass


class DataSourceAuthenticationError(DataSourceError):
    """数据源认证失败异常"""
    pass


class DataSourceRateLimitError(DataSourceError):
    """数据源频率限制异常"""
    pass
