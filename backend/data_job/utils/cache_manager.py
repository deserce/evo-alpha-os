"""
EvoAlpha OS - 数据采集缓存管理器
缓存股票列表、板块列表等常用数据，减少数据库查询
"""

import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import pandas as pd

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DataCache:
    """数据缓存管理器"""

    def __init__(self, cache_dir: str = None, cache_ttl: int = 3600):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录，默认为 data_job/data/cache
            cache_ttl: 缓存过期时间（秒），默认1小时
        """
        if cache_dir is None:
            # 默认缓存目录
            backend_dir = Path(__file__).parent.parent.parent
            cache_dir = backend_dir / "data_job" / "data" / "cache"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl

        logger.info(f"📦 缓存管理器初始化: {self.cache_dir}, TTL={cache_ttl}秒")

    def _get_cache_path(self, cache_name: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / f"{cache_name}.json"

    def _is_expired(self, cache_path: Path) -> bool:
        """检查缓存是否过期"""
        if not cache_path.exists():
            return True

        # 检查文件修改时间
        mtime = cache_path.stat().st_mtime
        age = time.time() - mtime

        return age > self.cache_ttl

    def get(self, cache_name: str) -> Optional[Any]:
        """
        从缓存获取数据

        Args:
            cache_name: 缓存名称

        Returns:
            缓存数据，如果不存在或过期则返回None
        """
        cache_path = self._get_cache_path(cache_name)

        # 检查缓存是否存在且未过期
        if self._is_expired(cache_path):
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            logger.debug(f"✅ 缓存命中: {cache_name}")
            return data

        except Exception as e:
            logger.warning(f"⚠️  读取缓存失败 {cache_name}: {e}")
            return None

    def set(self, cache_name: str, data: Any):
        """
        保存数据到缓存

        Args:
            cache_name: 缓存名称
            data: 要缓存的数据
        """
        cache_path = self._get_cache_path(cache_name)

        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"💾 缓存已保存: {cache_name}")

        except Exception as e:
            logger.warning(f"⚠️  保存缓存失败 {cache_name}: {e}")

    def delete(self, cache_name: str):
        """删除指定缓存"""
        cache_path = self._get_cache_path(cache_name)

        if cache_path.exists():
            cache_path.unlink()
            logger.debug(f"🗑️  缓存已删除: {cache_name}")

    def clear_all(self):
        """清空所有缓存"""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()

        logger.info("🗑️  所有缓存已清空")


class CachedDataManager:
    """带缓存的数据管理器"""

    def __init__(self, engine, cache_ttl: int = 3600):
        """
        初始化缓存数据管理器

        Args:
            engine: 数据库引擎
            cache_ttl: 缓存过期时间（秒）
        """
        self.engine = engine
        self.cache = DataCache(cache_ttl=cache_ttl)

    def get_stock_list(self, force_refresh: bool = False) -> List[Dict]:
        """
        获取股票列表（带缓存）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            股票列表 [{'symbol': '000001', 'name': '平安银行'}, ...]
        """
        cache_name = "stock_list"

        # 尝试从缓存获取
        if not force_refresh:
            cached_data = self.cache.get(cache_name)
            if cached_data is not None:
                logger.info(f"✅ 从缓存获取股票列表: {len(cached_data)} 只")
                return cached_data

        # 从数据库获取
        logger.info("📋 从数据库获取股票列表...")
        try:
            df = pd.read_sql("SELECT DISTINCT symbol, name FROM stock_sector_map", self.engine)

            if df.empty:
                # 尝试从 stock_info 获取
                df = pd.read_sql("SELECT symbol, name FROM stock_info", self.engine)

            stock_list = df.to_dict('records')

            # 保存到缓存
            self.cache.set(cache_name, stock_list)

            logger.info(f"✅ 获取到 {len(stock_list)} 只股票")
            return stock_list

        except Exception as e:
            logger.error(f"❌ 获取股票列表失败: {e}")
            return []

    def get_sector_list(self, force_refresh: bool = False) -> List[Dict]:
        """
        获取板块列表（带缓存）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            板块列表 [{'sector_name': '电子', 'sector_type': 'Industry'}, ...]
        """
        cache_name = "sector_list"

        # 尝试从缓存获取
        if not force_refresh:
            cached_data = self.cache.get(cache_name)
            if cached_data is not None:
                logger.info(f"✅ 从缓存获取板块列表: {len(cached_data)} 个")
                return cached_data

        # 从数据库获取
        logger.info("📋 从数据库获取板块列表...")
        try:
            df = pd.read_sql(
                "SELECT DISTINCT sector_name, sector_type FROM stock_sector_map",
                self.engine
            )

            sector_list = df.to_dict('records')

            # 保存到缓存
            self.cache.set(cache_name, sector_list)

            logger.info(f"✅ 获取到 {len(sector_list)} 个板块")
            return sector_list

        except Exception as e:
            logger.error(f"❌ 获取板块列表失败: {e}")
            return []

    def get_index_list(self, force_refresh: bool = False) -> List[Dict]:
        """
        获取指数列表（带缓存）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            指数列表 [{'code': 'sh000001', 'name': '上证指数'}, ...]
        """
        cache_name = "index_list"

        # 尝试从缓存获取
        if not force_refresh:
            cached_data = self.cache.get(cache_name)
            if cached_data is not None:
                logger.info(f"✅ 从缓存获取指数列表: {len(cached_data)} 个")
                return cached_data

        # 使用默认指数列表
        index_list = [
            {"code": "sh000001", "name": "上证指数"},
            {"code": "sz399001", "name": "深证成指"},
            {"code": "sz399006", "name": "创业板指"},
            {"code": "sz399303", "name": "国证A指"},
            {"code": "sh000300", "name": "沪深300"},
            {"code": "sh000688", "name": "科创50"},
            {"code": "sz399673", "name": "创业板50"},
            {"code": "sh000016", "name": "上证50"},
        ]

        # 保存到缓存
        self.cache.set(cache_name, index_list)

        logger.info(f"✅ 获取到 {len(index_list)} 个指数")
        return index_list

    def invalidate_all(self):
        """使所有缓存失效"""
        self.cache.clear_all()
        logger.info("🗑️  所有缓存已清空")


# 全局实例（单例模式）
_global_cache_manager = None


def get_cache_manager(engine, cache_ttl: int = 3600) -> CachedDataManager:
    """
    获取全局缓存管理器实例（单例模式）

    Args:
        engine: 数据库引擎
        cache_ttl: 缓存过期时间（秒）

    Returns:
        CachedDataManager实例
    """
    global _global_cache_manager

    if _global_cache_manager is None:
        _global_cache_manager = CachedDataManager(engine, cache_ttl)

    return _global_cache_manager


if __name__ == "__main__":
    # 测试代码
    from app.core.database import get_engine

    engine = get_engine()
    cache_manager = CachedDataManager(engine, cache_ttl=3600)

    # 测试股票列表缓存
    print("\n测试股票列表缓存:")
    stock_list = cache_manager.get_stock_list()
    print(f"第一次获取: {len(stock_list)} 只股票")

    print("\n再次获取（应该从缓存）:")
    stock_list2 = cache_manager.get_stock_list()
    print(f"第二次获取: {len(stock_list2)} 只股票")

    # 测试板块列表缓存
    print("\n测试板块列表缓存:")
    sector_list = cache_manager.get_sector_list()
    print(f"获取到 {len(sector_list)} 个板块")

    # 测试指数列表缓存
    print("\n测试指数列表缓存:")
    index_list = cache_manager.get_index_list()
    print(f"获取到 {len(index_list)} 个指数")
