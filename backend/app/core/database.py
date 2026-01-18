"""
EvoAlpha OS - 数据库管理
从 EvoQuant OS 移植
支持本地 SQLite + 云端 CockroachDB 双引擎
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool
from loguru import logger
from typing import List, Tuple
from app.core.config import settings

# ================= 1. 引擎初始化函数 =================

def _create_local_engine():
    """创建本地 SQLite 引擎（Factory - MBP）"""
    # 确保 data 目录存在
    import os
    data_dir = os.path.dirname(settings.LOCAL_DB_PATH)
    os.makedirs(data_dir, exist_ok=True)

    engine = create_engine(
        settings.LOCAL_DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=settings.APP_DEBUG,
    )
    logger.info(f"✅ 本地数据库引擎已创建: {settings.LOCAL_DB_PATH}")
    return engine


def _create_cloud_engine():
    """创建云端 CockroachDB 引擎（Display - Cloud）"""
    if not settings.CLOUD_DATABASE_URL:
        logger.warning("⚠️  云端数据库 URL 未配置")
        return None

    try:
        # 脱敏显示 URL
        display_url = settings.CLOUD_DATABASE_URL.split('@')[-1] if '@' in settings.CLOUD_DATABASE_URL else settings.CLOUD_DATABASE_URL
        logger.info(f"🔌 正在连接云端数据库: {display_url}")

        engine = create_engine(
            settings.CLOUD_DATABASE_URL,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # 连接健康检查
            connect_args={"sslmode": settings.CLOUD_DB_SSLMODE},
            echo=settings.APP_DEBUG,
        )

        # 测试连接
        with engine.connect() as conn:
            conn.execute("SELECT 1")

        logger.success("✅ 云端数据库引擎已创建")
        return engine

    except Exception as e:
        logger.error(f"❌ 云端数据库引擎创建失败: {e}")
        return None


# ================= 2. 预生成单例引擎 =================

# 本地引擎（总是存在，作为工厂基础）
local_engine = _create_local_engine()

# 云端引擎（按需生成）
cloud_engine = _create_cloud_engine()

# ================= 3. 会话与 ORM 基类 =================

# SessionLocal 默认绑定到本地引擎
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=local_engine)

# ORM 基类
Base = declarative_base()


# ================= 4. 核心工具函数（从 EvoQuant OS 移植）=================

def get_active_engines() -> List[Tuple[str, object]]:
    """
    核心工具：返回当前需要操作的所有引擎

    返回格式: [("local", engine_obj), ("cloud", engine_obj)]

    使用示例：
        for name, engine in get_active_engines():
            df.to_sql(table_name, engine, if_exists="append")
    """
    active = []

    # 1. 本地引擎（总是激活）
    active.append(("local", local_engine))

    # 2. 云端引擎（如果配置了）
    if cloud_engine:
        active.append(("cloud", cloud_engine))

    return active


def get_engine(mode: str = "local"):
    """
    获取指定引擎

    Args:
        mode: "local" 或 "cloud"

    Returns:
        SQLAlchemy Engine 对象
    """
    if mode == "cloud":
        if not cloud_engine:
            raise ValueError("❌ 云端引擎未初始化")
        return cloud_engine
    return local_engine


def get_local_engine():
    """获取本地引擎（快捷方式）"""
    return local_engine


def get_cloud_engine():
    """获取云端引擎（快捷方式）"""
    return cloud_engine


def get_db():
    """
    依赖注入函数（FastAPI 使用）

    Yields:
        数据库会话
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= 5. 数据库初始化函数 =================

def init_database():
    """
    初始化数据库（创建表）

    根据 settings.CLOUD_DATABASE_URL 是否配置
    决定是否初始化云端数据库
    """
    # 本地数据库初始化
    logger.info("📊 正在初始化本地数据库...")
    # 注意：我们使用 SQL 直接创建表，不需要 ORM models
    logger.success("✅ 本地数据库初始化完成")

    # 云端数据库初始化（如果配置了）
    if cloud_engine:
        logger.info("📊 正在初始化云端数据库...")
        try:
            Base.metadata.create_all(bind=cloud_engine)
            logger.success("✅ 云端数据库初始化完成")
        except Exception as e:
            logger.error(f"❌ 云端数据库初始化失败: {e}")


# ================= 6. 便捷函数 =================

def write_to_local(table_name: str, df, if_exists="append"):
    """
    写入本地数据库

    Args:
        table_name: 表名
        df: pandas DataFrame
        if_exists: "fail", "replace", "append"
    """
    df.to_sql(table_name, local_engine, if_exists=if_exists, index=False)
    logger.debug(f"写入本地数据库: {table_name} ({len(df)} 行)")


def write_to_cloud(table_name: str, df, if_exists="append"):
    """
    写入云端数据库

    Args:
        table_name: 表名
        df: pandas DataFrame
        if_exists: "fail", "replace", "append"
    """
    if not cloud_engine:
        logger.warning("⚠️  云端引擎未配置，跳过写入")
        return

    df.to_sql(table_name, cloud_engine, if_exists=if_exists, index=False)
    logger.debug(f"写入云端数据库: {table_name} ({len(df)} 行)")


def write_to_all(table_name: str, df, if_exists="append"):
    """
    同时写入本地和云端数据库

    Args:
        table_name: 表名
        df: pandas DataFrame
        if_exists: "fail", "replace", "append"
    """
    for mode, engine in get_active_engines():
        df.to_sql(table_name, engine, if_exists=if_exists, index=False)
        logger.debug(f"写入{mode}数据库: {table_name} ({len(df)} 行)")
