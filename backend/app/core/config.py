"""
EvoAlpha OS - 配置管理
从环境变量读取配置
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """应用配置"""

    # 应用基础配置
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # CORS 配置
    ALLOW_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://evo-alpha.vercel.app",  # 生产环境前端地址
    ]

    # 云端数据库配置（CockroachDB）
    CLOUD_DB_HOST: str
    CLOUD_DB_PORT: int = 26257
    CLOUD_DB_USER: str
    CLOUD_DB_PASSWORD: str
    CLOUD_DB_NAME: str = "evo_alpha_os"
    CLOUD_DB_SSLMODE: str = "require"

    # AI 服务配置（GLM-4）
    GLM_API_KEY: str
    GLM_API_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    GLM_MODEL: str = "glm-4-flash"
    GLM_MAX_TOKENS: int = 2000
    GLM_TEMPERATURE: float = 0.7

    # 邮件服务配置（Resend）
    RESEND_API_KEY: str
    RESEND_FROM_EMAIL: str
    RESEND_FROM_NAME: str = "EvoAlpha OS"

    @property
    def cloud_db_url(self) -> str:
        """构建云端数据库连接 URL"""
        return (
            f"postgresql+asyncpg://{self.CLOUD_DB_USER}:{self.CLOUD_DB_PASSWORD}"
            f"@{self.CLOUD_DB_HOST}:{self.CLOUD_DB_PORT}/{self.CLOUD_DB_NAME}"
            f"?sslmode={self.CLOUD_DB_SSLMODE}"
        )

    class Config:
        env_file = ".env"
        case_sensitive = True


# 全局配置实例
settings = Settings()
