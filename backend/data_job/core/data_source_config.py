"""
数据源配置管理
支持从环境变量加载多数据源配置
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DataSourceConfig:
    """数据源配置"""

    # 采集策略
    strategy: str = 'parallel'  # parallel/priority/majority

    # 数据源启用/禁用
    enable_akshare: bool = True
    enable_efinance: bool = True  # efinance（东方财富，免费）
    enable_baostock: bool = True  # baostock（免费，当前可用）
    enable_gmx: bool = True  # 新增：掘金量化（数据质量高）
    enable_joinquant: bool = False

    # AKShare配置
    akshare_priority: int = 10
    akshare_timeout: int = 30

    # efinance配置（新增）
    efinance_priority: int = 8
    efinance_timeout: int = 30

    # BaoStock配置
    baostock_priority: int = 7  # 当前可用，优先级设为7
    baostock_timeout: int = 30

    # 掘金量化配置（新增）
    gmx_token: Optional[str] = None
    gmx_account_id: Optional[str] = None
    gmx_priority: int = 9  # 数据质量高，优先级设为9
    gmx_timeout: int = 30

    # 聚宽配置
    joinquant_account_id: Optional[str] = None
    joinquant_password: Optional[str] = None
    joinquant_priority: int = 5
    joinquant_timeout: int = 30

    # 并行采集配置
    parallel_timeout: int = 60
    validation_enabled: bool = True

    @classmethod
    def from_env(cls) -> 'DataSourceConfig':
        """
        从环境变量加载配置

        Returns:
            DataSourceConfig 实例
        """
        return cls(
            strategy=os.getenv('DATA_SOURCE_STRATEGY', 'parallel'),
            enable_akshare=os.getenv('ENABLE_AKSHARE', 'true').lower() == 'true',
            enable_efinance=os.getenv('ENABLE_EFINANCE', 'true').lower() == 'true',
            enable_baostock=os.getenv('ENABLE_BAOSTOCK', 'true').lower() == 'true',
            enable_gmx=os.getenv('ENABLE_GMX', 'true').lower() == 'true',
            enable_joinquant=os.getenv('ENABLE_JOINQUANT', 'false').lower() == 'true',
            akshare_priority=int(os.getenv('AKSHARE_PRIORITY', '10')),
            akshare_timeout=int(os.getenv('AKSHARE_TIMEOUT', '30')),
            efinance_priority=int(os.getenv('EFINANCE_PRIORITY', '8')),
            efinance_timeout=int(os.getenv('EFINANCE_TIMEOUT', '30')),
            baostock_priority=int(os.getenv('BAOSTOCK_PRIORITY', '7')),
            baostock_timeout=int(os.getenv('BAOSTOCK_TIMEOUT', '30')),
            gmx_token=os.getenv('GMX_TOKEN'),
            gmx_account_id=os.getenv('GMX_ACCOUNT_ID'),
            gmx_priority=int(os.getenv('GMX_PRIORITY', '9')),
            gmx_timeout=int(os.getenv('GMX_TIMEOUT', '30')),
            joinquant_account_id=os.getenv('JOINQUANT_ACCOUNT_ID'),
            joinquant_password=os.getenv('JOINQUANT_PASSWORD'),
            joinquant_priority=int(os.getenv('JOINQUANT_PRIORITY', '5')),
            joinquant_timeout=int(os.getenv('JOINQUANT_TIMEOUT', '30')),
            parallel_timeout=int(os.getenv('PARALLEL_TIMEOUT', '60')),
            validation_enabled=os.getenv('VALIDATION_ENABLED', 'true').lower() == 'true'
        )

    def validate(self) -> bool:
        """
        验证配置是否有效

        Returns:
            True if valid

        Raises:
            ValueError: 配置无效时
        """
        # 至少需要一个数据源
        if not self.enable_akshare and not self.enable_efinance and not self.enable_baostock and not self.enable_gmx and not self.enable_joinquant:
            raise ValueError("至少需要启用一个数据源 (AKShare, efinance, BaoStock, GMX 或 JoinQuant)")

        # 验证策略
        valid_strategies = ['parallel', 'priority', 'majority']
        if self.strategy not in valid_strategies:
            raise ValueError(f"不支持的采集策略: {self.strategy}. 可选: {valid_strategies}")

        # 如果启用聚宽，需要账号密码
        if self.enable_joinquant:
            if not self.joinquant_account_id or not self.joinquant_password:
                raise ValueError("启用聚宽需要配置 JOINQUANT_ACCOUNT_ID 和 JOINQUANT_PASSWORD")

        # 如果启用GMX，需要token
        if self.enable_gmx:
            if not self.gmx_token:
                raise ValueError("启用掘金量化需要配置 GMX_TOKEN")

        # 验证优先级范围
        if not (0 <= self.akshare_priority <= 100):
            raise ValueError(f"AKShare优先级必须在0-100之间: {self.akshare_priority}")

        if hasattr(self, 'efinance_priority') and not (0 <= self.efinance_priority <= 100):
            raise ValueError(f"efinance优先级必须在0-100之间: {self.efinance_priority}")

        if not (0 <= self.joinquant_priority <= 100):
            raise ValueError(f"聚宽优先级必须在0-100之间: {self.joinquant_priority}")

        # 验证超时配置
        if self.akshare_timeout < 1:
            raise ValueError(f"AKShare超时时间必须>=1秒: {self.akshare_timeout}")

        if hasattr(self, 'efinance_timeout') and self.efinance_timeout < 1:
            raise ValueError(f"efinance超时时间必须>=1秒: {self.efinance_timeout}")

        if self.joinquant_timeout < 1:
            raise ValueError(f"聚宽超时时间必须>=1秒: {self.joinquant_timeout}")

        logger.info("配置验证通过")
        return True

    def __str__(self) -> str:
        """配置信息摘要（隐藏敏感信息）"""
        return (f"DataSourceConfig(strategy={self.strategy}, "
                f"akshare={self.enable_akshare}(priority={self.akshare_priority}), "
                f"joinquant={self.enable_joinquant}(priority={self.joinquant_priority}))")


def load_config(env_file: Optional[str] = None) -> DataSourceConfig:
    """
    加载数据源配置

    Args:
        env_file: 可选的环境变量文件路径

    Returns:
        DataSourceConfig 实例
    """
    # 如果指定了环境变量文件，加载它
    if env_file and os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            logger.info(f"已加载环境变量文件: {env_file}")
        except ImportError:
            logger.warning("python-dotenv未安装，跳过加载.env文件")
        except Exception as e:
            logger.warning(f"加载.env文件失败: {e}")

    config = DataSourceConfig.from_env()

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"配置验证失败: {e}")
        raise

    logger.info(f"数据源配置加载成功: {config}")
    return config
