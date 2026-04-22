"""
EvoAlpha OS - 云端后端主入口
FastAPI 应用启动文件
"""

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.cloud_db import init_database

# 配置日志
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# 创建 FastAPI 应用
app = FastAPI(
    title="EvoAlpha OS API",
    description="数据驱动的 Alpha 机会发现平台",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化"""
    logger.info("🚀 EvoAlpha OS API 正在启动...")

    # 只在配置了云端数据库时才初始化
    if settings.CLOUD_DATABASE_URL:
        await init_database()
        logger.info("✅ 数据库连接成功")
    else:
        logger.info("⚠️  云端数据库未配置，仅使用本地数据库")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的清理"""
    logger.info("👋 EvoAlpha OS API 正在关闭...")


@app.get("/")
async def root():
    """根路径健康检查"""
    return {
        "app": "EvoAlpha OS",
        "version": "1.0.0",
        "status": "running",
        "message": "进化即自由 (Evolution is Freedom)"
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


# 注册API v1路由
from app.api.v1 import api_v1_router

app.include_router(api_v1_router)

# API路由说明：
# - /api/v1/stock/*  - 个股相关API
# - /api/v1/sector/* - 板块相关API
# - /api/v1/etf/*    - ETF相关API
# - /api/v1/index/*  - 指数相关API
# - /api/v1/market/* - 市场统计API
# - /api/v1/alpha/*  - Alpha机会API
# - /api/v1/report/* - 日报生成API


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
