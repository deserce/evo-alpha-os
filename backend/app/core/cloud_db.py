"""
EvoAlpha OS - 云端数据库连接
CockroachDB 连接管理
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import logging

logger = logging.getLogger(__name__)

from app.core.config import settings

# 数据库引擎
engine = None

# 会话工厂
async_session_maker = None

# Base 类用于继承
Base = declarative_base()


async def init_database():
    """初始化数据库连接"""
    global engine, async_session_maker

    try:
        # 创建异步引擎
        engine = create_async_engine(
            settings.CLOUD_DATABASE_URL,
            echo=settings.APP_DEBUG,
            pool_size=10,
            max_overflow=20,
        )

        # 创建会话工厂
        async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # 测试连接
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: None)

        logger.info("✅ 云端数据库连接成功")

    except Exception as e:
        logger.error(f"❌ 数据库连接失败: {e}")
        raise


async def get_session() -> AsyncSession:
    """获取数据库会话（依赖注入）"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_database():
    """关闭数据库连接"""
    global engine

    if engine:
        await engine.dispose()
        logger.info("🔌 数据库连接已关闭")
