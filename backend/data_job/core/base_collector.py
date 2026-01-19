"""
EvoAlpha OS - 数据采集基类
提供断点续传、增量更新、错误重试、连接稳定性等通用功能
"""

import sys
import os
import time
import json
import logging
import random
import signal
import pandas as pd
import requests
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable, Any, Optional
from sqlalchemy import text
from pathlib import Path
from contextlib import contextmanager

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_engine


class NetworkError(Exception):
    """网络错误"""
    pass


class ConnectionTimeout(Exception):
    """连接超时"""
    pass


class BaseCollector(ABC):
    """数据采集基类 - 提供通用功能和连接稳定性保障"""

    def __init__(self, collector_name: str,
                 request_timeout: int = 30,
                 request_delay: float = 0.5,
                 max_retries: int = 3):
        """
        初始化采集器

        Args:
            collector_name: 采集器名称（用于日志和进度跟踪）
            request_timeout: 请求超时时间（秒）
            request_delay: 请求间隔（秒）
            max_retries: 最大重试次数
        """
        self.collector_name = collector_name
        self.engine = get_engine()

        # 网络请求配置
        self.request_timeout = request_timeout
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.session = self._create_session()

        # 进度文件路径
        self.progress_dir = Path(backend_dir) / "data" / "collection_progress"
        self.progress_dir.mkdir(parents=True, exist_ok=True)
        self.progress_file = self.progress_dir / f"{collector_name}.json"

        # 加载进度
        self.progress = self._load_progress()

        # 日志配置
        self.logger = logging.getLogger(f"collector.{collector_name}")

        # 统计信息
        self.stats = {
            "total_requests": 0,
            "failed_requests": 0,
            "retry_count": 0,
            "timeout_count": 0
        }

    def _create_session(self) -> requests.Session:
        """
        创建带连接池的Session

        Returns:
            配置好的Session对象
        """
        session = requests.Session()

        # 连接池配置
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,  # 连接池数量
            pool_maxsize=20,      # 每个池的最大连接数
            max_retries=0         # 禁用自动重试（我们手动控制）
        )
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        # 设置超时
        session.timeout = self.request_timeout

        return session

    @contextmanager
    def _timeout_context(self, timeout_seconds: int):
        """
        超时上下文管理器

        Args:
            timeout_seconds: 超时时间（秒）
        """
        def timeout_handler(signum, frame):
            raise ConnectionTimeout(f"操作超时（{timeout_seconds}秒）")

        # 设置信号处理器
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout_seconds)

        try:
            yield
        finally:
            # 恢复原来的处理器
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    def _check_network_connection(self) -> bool:
        """
        检查网络连接

        Returns:
            网络是否可用
        """
        try:
            # 尝试连接百度检测网络
            response = self.session.get(
                "https://www.baidu.com",
                timeout=5
            )
            return response.status_code == 200
        except:
            return False

    def _health_check(self):
        """连接健康检查"""
        if not self._check_network_connection():
            raise NetworkError("网络连接不可用")

        # 测试数据库连接
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            raise NetworkError(f"数据库连接失败: {e}")

    def _load_progress(self) -> dict:
        """加载采集进度"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"加载进度文件失败: {e}")
        return {
            "last_update": None,
            "last_symbol": None,
            "failed_items": [],
            "collection_count": 0,
            "last_success_time": None
        }

    def _save_progress(self):
        """保存采集进度"""
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.progress, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.warning(f"保存进度文件失败: {e}")

    def _retry_call(self, func, max_retries=None, delay=None,
                    exponential_backoff=True, **kwargs):
        """
        增强的重试机制（支持指数退避和抖动）

        Args:
            func: 要执行的函数
            max_retries: 最大重试次数（默认使用self.max_retries）
            delay: 初始延迟（秒）
            exponential_backoff: 是否使用指数退避
            **kwargs: 函数参数

        Returns:
            函数执行结果，失败返回None
        """
        if max_retries is None:
            max_retries = self.max_retries
        if delay is None:
            delay = self.request_delay

        last_error = None

        for attempt in range(max_retries):
            try:
                self.stats["total_requests"] += 1

                # 健康检查
                if attempt > 0:
                    self._health_check()

                # 执行函数
                result = func(**kwargs)

                # 请求成功后添加随机延迟（避免被封）
                if result is not None:
                    jitter = random.uniform(0, 0.3)  # 0-0.3秒随机抖动
                    time.sleep(self.request_delay + jitter)

                return result

            except (ConnectionTimeout, requests.exceptions.Timeout) as e:
                self.stats["timeout_count"] += 1
                last_error = e
                self.logger.warning(f"请求超时 (尝试 {attempt + 1}/{max_retries})")

            except (requests.exceptions.ConnectionError,
                   requests.exceptions.RequestException) as e:
                self.stats["failed_requests"] += 1
                last_error = e
                self.logger.warning(f"网络错误 (尝试 {attempt + 1}/{max_retries}): {e}")

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    self.logger.warning(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                else:
                    self.logger.error(f"请求失败，已达最大重试次数: {e}")
                    self.stats["failed_requests"] += 1
                    return None

            # 计算等待时间
            if attempt < max_retries - 1:
                if exponential_backoff:
                    # 指数退避 + 随机抖动
                    wait_time = delay * (2 ** attempt) + random.uniform(0, 1)
                else:
                    wait_time = delay + random.uniform(0, 0.5)

                self.stats["retry_count"] += 1
                self.logger.info(f"等待 {wait_time:.1f} 秒后重试...")
                time.sleep(wait_time)

        # 所有重试都失败
        self.logger.error(f"请求失败: {last_error}")
        return None

    def _retry_with_fallback(self, primary_func: Callable,
                            fallback_func: Callable,
                            **kwargs) -> Optional[Any]:
        """
        带降级策略的重试（主接口失败时尝试备用接口）

        Args:
            primary_func: 主函数
            fallback_func: 备用函数
            **kwargs: 函数参数

        Returns:
            函数执行结果
        """
        # 先尝试主函数
        result = self._retry_call(primary_func, max_retries=2, **kwargs)
        if result is not None:
            return result

        # 主函数失败，尝试备用函数
        self.logger.warning("主接口失败，尝试备用接口...")
        return self._retry_call(fallback_func, max_retries=2, **kwargs)

    def get_last_update_date(self) -> datetime:
        """
        获取最后更新日期（用于增量更新）

        Returns:
            最后更新日期，如果没有则返回90天前
        """
        last_update = self.progress.get("last_update")
        if last_update:
            try:
                return datetime.fromisoformat(last_update)
            except:
                pass

        # 默认返回90天前
        return datetime.now() - timedelta(days=90)

    def update_progress(self, **kwargs):
        """
        更新采集进度

        Args:
            **kwargs: 要更新的进度字段
        """
        self.progress.update(kwargs)
        self._save_progress()

    def log_collection_start(self):
        """记录采集开始"""
        self.logger.info(f"🚀 开始采集 [{self.collector_name}]")
        self.progress["collection_start_time"] = datetime.now().isoformat()
        self._save_progress()

    def log_collection_end(self, success: bool, message: str = ""):
        """
        记录采集结束

        Args:
            success: 是否成功
            message: 附加信息
        """
        if success:
            self.logger.info(f"✅ 采集完成 [{self.collector_name}] {message}")
            self.progress["last_success_time"] = datetime.now().isoformat()
        else:
            self.logger.error(f"❌ 采集失败 [{self.collector_name}] {message}")

        self.progress["collection_end_time"] = datetime.now().isoformat()
        self.progress["collection_success"] = success
        self._save_progress()

    def get_collection_statistics(self) -> dict:
        """
        获取采集统计信息

        Returns:
            统计信息字典
        """
        return {
            "collector_name": self.collector_name,
            "last_update": self.progress.get("last_update"),
            "collection_count": self.progress.get("collection_count", 0),
            "last_success_time": self.progress.get("last_success_time"),
            "failed_items_count": len(self.progress.get("failed_items", []))
        }

    def save_with_deduplication(self, df: pd.DataFrame, table_name: str,
                                key_columns: list, date_column: str = None):
        """
        保存数据并去重（增量更新）

        Args:
            df: 要保存的数据
            table_name: 表名
            key_columns: 主键列列表
            date_column: 日期列名（用于增量更新）
        """
        if df.empty:
            self.logger.warning("数据为空，跳过保存")
            return 0

        try:
            with self.engine.begin() as conn:
                # 如果有日期列，只删除日期范围内的数据
                if date_column and date_column in df.columns:
                    min_date = df[date_column].min()
                    max_date = df[date_column].max()

                    # 构建删除条件
                    key_condition = " AND ".join([f"{col} = :{col}" for col in key_columns])

                    # 删除重复数据
                    for _, row in df.iterrows():
                        params = {col: row[col] for col in key_columns}
                        params.update({
                            "min_date": min_date,
                            "max_date": max_date
                        })
                        conn.execute(text(f"""
                            DELETE FROM {table_name}
                            WHERE {key_condition}
                            AND {date_column} BETWEEN :min_date AND :max_date
                        """), params)
                else:
                    # 删除所有主键重复的数据
                    for _, row in df.iterrows():
                        params = {col: row[col] for col in key_columns}
                        conn.execute(text(f"""
                            DELETE FROM {table_name}
                            WHERE {" AND ".join([f"{col} = :{col}" for col in key_columns])}
                        """), params)

                # 插入新数据（使用 chunksize 避免 SQLite 变量限制）
                df.to_sql(table_name, conn, if_exists='append', index=False,
                         method='multi', chunksize=100)

                inserted_count = len(df)
                self.progress["collection_count"] = self.progress.get("collection_count", 0) + inserted_count
                self._save_progress()

                return inserted_count

        except Exception as e:
            self.logger.error(f"保存数据失败: {e}")
            raise

    def clean_old_data(self, table_name: str, date_column: str,
                      keep_days: int = 365):
        """
        清理旧数据（保留最近N天）

        Args:
            table_name: 表名
            date_column: 日期列名
            keep_days: 保留天数
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=keep_days)

            with self.engine.begin() as conn:
                result = conn.execute(text(f"""
                    DELETE FROM {table_name}
                    WHERE {date_column} < :cutoff_date
                """), {"cutoff_date": cutoff_date})

                deleted_count = result.rowcount
                if deleted_count > 0:
                    self.logger.info(f"清理了 {deleted_count} 条旧数据（{table_name}）")

                return deleted_count

        except Exception as e:
            self.logger.error(f"清理旧数据失败: {e}")
            return 0

    @abstractmethod
    def run(self):
        """
        执行采集（子类必须实现）

        Returns:
            bool: 是否成功
        """
        pass


class BatchCollector(BaseCollector):
    """批量数据采集基类 - 支持分批采集和断点续传"""

    def __init__(self, collector_name: str, batch_size: int = 100):
        """
        初始化批量采集器

        Args:
            collector_name: 采集器名称
            batch_size: 每批处理数量
        """
        super().__init__(collector_name)
        self.batch_size = batch_size

    @abstractmethod
    def get_item_list(self) -> list:
        """
        获取要采集的项目列表（子类必须实现）

        Returns:
            项目列表
        """
        pass

    @abstractmethod
    def process_item(self, item) -> pd.DataFrame:
        """
        处理单个项目（子类必须实现）

        Args:
            item: 要处理的项目

        Returns:
            处理后的数据
        """
        pass

    @abstractmethod
    def save_item_data(self, item, df: pd.DataFrame):
        """
        保存单个项目的数据（子类必须实现）

        Args:
            item: 项目
            df: 数据
        """
        pass

    def run(self, resume: bool = True):
        """
        执行批量采集

        Args:
            resume: 是否从断点继续

        Returns:
            bool: 是否成功
        """
        self.log_collection_start()

        try:
            # 获取要处理的项目列表
            items = self.get_item_list()
            if not items:
                self.logger.warning("没有要处理的项目")
                self.log_collection_end(True, "无项目需要处理")
                return True

            total = len(items)
            self.logger.info(f"共 {total} 个项目需要处理")

            # 获取上次处理的位置
            start_index = 0
            if resume:
                last_item = self.progress.get("last_item")
                if last_item in items:
                    start_index = items.index(last_item) + 1
                    self.logger.info(f"从第 {start_index + 1} 个项目继续...")

            # 处理每个项目
            success_count = 0
            failed_items = []

            for i in range(start_index, total):
                item = items[i]

                try:
                    self.logger.info(f"处理 [{i + 1}/{total}]: {item}")

                    # 处理项目
                    df = self._retry_call(
                        lambda: self.process_item(item),
                        max_retries=3
                    )

                    if df is not None and not df.empty:
                        # 保存数据
                        self.save_item_data(item, df)
                        success_count += 1

                        # 更新进度
                        self.update_progress(
                            last_item=str(item),
                            processed_count=i + 1,
                            success_count=success_count
                        )

                    # 避免请求过快
                    time.sleep(0.5)

                except Exception as e:
                    self.logger.error(f"处理失败 [{item}]: {e}")
                    failed_items.append(str(item))

                    # 记录失败项
                    failed_list = self.progress.get("failed_items", [])
                    failed_list.append({
                        "item": str(item),
                        "time": datetime.now().isoformat(),
                        "error": str(e)
                    })
                    self.update_progress(failed_items=failed_list[-100:])  # 只保留最近100个

            # 完成
            self.update_progress(last_update=datetime.now().isoformat())
            message = f"成功: {success_count}/{total}"
            if failed_items:
                message += f", 失败: {len(failed_items)}"

            self.log_collection_end(True, message)
            return True

        except Exception as e:
            self.log_collection_end(False, str(e))
            return False
