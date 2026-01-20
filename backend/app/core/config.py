"""
EvoAlpha OS - 配置管理
从 EvoQuant OS 移植并调整
支持三段式架构：本地工厂 → R2 → 云端
"""

import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from typing import List

# 加载 .env 文件
load_dotenv()


class Settings(BaseSettings):
    """应用配置"""

    # ========== 项目基础信息 ==========
    PROJECT_NAME: str = "EvoAlpha OS"
    VERSION: str = "1.0"
    DESCRIPTION: str = "数据驱动的 Alpha 机会发现平台"

    # ========== 运行模式 ==========
    APP_ENV: str = os.getenv("APP_ENV", "development")
    APP_DEBUG: bool = os.getenv("APP_DEBUG", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ========== CORS 配置 ==========
    ALLOW_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://evo-alpha.vercel.app",  # 生产环境前端地址
    ]

    # ========== 1. 本地数据库配置（Factory - MBP）==========
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    LOCAL_DB_PATH: str = os.path.join(BASE_DIR, "data", "local_quant.db")
    LOCAL_DATABASE_URL: str = f"sqlite:///{LOCAL_DB_PATH}"

    # ========== 2. 云端数据库配置（Display - CockroachDB）==========
    CLOUD_DB_HOST: str = os.getenv("CLOUD_DB_HOST", "")
    CLOUD_DB_PORT: int = int(os.getenv("CLOUD_DB_PORT", "26257"))
    CLOUD_DB_USER: str = os.getenv("CLOUD_DB_USER", "")
    CLOUD_DB_PASSWORD: str = os.getenv("CLOUD_DB_PASSWORD", "")
    CLOUD_DB_NAME: str = os.getenv("CLOUD_DB_NAME", "evo_alpha_os")
    CLOUD_DB_SSLMODE: str = os.getenv("CLOUD_DB_SSLMODE", "require")

    @property
    def CLOUD_DATABASE_URL(self) -> str:
        """构建云端数据库连接 URL"""
        if not all([self.CLOUD_DB_HOST, self.CLOUD_DB_USER, self.CLOUD_DB_PASSWORD]):
            return ""
        return (
            f"postgresql://{self.CLOUD_DB_USER}:{self.CLOUD_DB_PASSWORD}"
            f"@{self.CLOUD_DB_HOST}:{self.CLOUD_DB_PORT}/{self.CLOUD_DB_NAME}"
            f"?sslmode={self.CLOUD_DB_SSLMODE}"
        )

    # ========== 3. Cloudflare R2 配置（Data Bridge）==========
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "evo-alpha-data")
    R2_ENDPOINT: str = os.getenv("R2_ENDPOINT", "")

    @property
    def R2_PUBLIC_DOMAIN(self) -> str:
        """构建 R2 公开域名"""
        if self.R2_ENDPOINT:
            return self.R2_ENDPOINT
        if self.R2_ACCOUNT_ID:
            return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        return ""

    # ========== 4. AI 服务配置（GLM-4）==========
    GLM_API_KEY: str = os.getenv("GLM_API_KEY", "")
    GLM_API_URL: str = os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/")
    GLM_MODEL: str = os.getenv("GLM_MODEL", "glm-4-flash")
    GLM_MAX_TOKENS: int = int(os.getenv("GLM_MAX_TOKENS", "2000"))
    GLM_TEMPERATURE: float = float(os.getenv("GLM_TEMPERATURE", "0.7"))

    # ========== 5. 邮件服务配置（Resend）==========
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "")
    RESEND_FROM_NAME: str = os.getenv("RESEND_FROM_NAME", "EvoAlpha OS")

    # ========== 6. 数据源配置 ==========
    AKSHARE_PROXY: str = os.getenv("AKSHARE_PROXY", "")
    NEWS_SOURCES: str = os.getenv("NEWS_SOURCES", "eastmoney,sina,firstfinancing")

    # ========== 7. 定时任务配置 ==========
    SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "Asia/Shanghai")
    DAILY_JOB_TIME: str = os.getenv("DAILY_JOB_TIME", "15:30")
    LUNCH_REPORT_TIME: str = os.getenv("LUNCH_REPORT_TIME", "11:30")
    AFTERNOON_REPORT_TIME: str = os.getenv("AFTERNOON_REPORT_TIME", "15:45")

    # ========== 8. 控制开关 ==========
    # 是否强制同步 K 线到云端（海量数据时建议 False）
    FORCE_SYNC_KLINE: bool = os.getenv("FORCE_SYNC_KLINE", "false").lower() == "true"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # 忽略 .env 中的额外字段


# 全局配置实例
settings = Settings()
