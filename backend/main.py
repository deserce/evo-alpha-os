"""
EvoAlpha OS - 云端后端主入口
FastAPI 应用启动文件
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import sys

from app.core.config import settings
from app.core.cloud_db import init_database

# 配置日志
logger.remove()
logger.add(sys.stdout, level=settings.LOG_LEVEL)

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
    await init_database()
    logger.info("✅ 数据库连接成功")


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


# 注册路由（后续添加）
# from app.api import alpha, stock, sector, etf, report, ai
# app.include_router(alpha.router, prefix="/api/alpha", tags=["Alpha机会"])
# app.include_router(stock.router, prefix="/api/stock", tags=["个股"])
# app.include_router(sector.router, prefix="/api/sector", tags=["板块"])
# app.include_router(etf.router, prefix="/api/etf", tags=["ETF"])
# app.include_router(report.router, prefix="/api/report", tags=["日报"])
# app.include_router(ai.router, prefix="/api/ai", tags=["AI分析"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
