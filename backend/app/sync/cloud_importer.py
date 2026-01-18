"""
EvoAlpha OS - 同步模块：云端导入器
触发CockroachDB IMPORT命令，从R2导入CSV数据
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from loguru import logger
from typing import List


class CloudImporter:
    """云端数据库导入器"""

    def __init__(self, db_url: str):
        """
        初始化导入器

        Args:
            db_url: 数据库连接URL
        """
        self.db_url = db_url
        self.engine = None

    async def connect(self):
        """连接数据库"""
        self.engine = create_async_engine(self.db_url)
        logger.info("连接云端数据库成功")

    async def import_from_r2(
        self,
        table_name: str,
        r2_url: str,
        format: str = "CSV",
        delimiter: str = ",",
    ):
        """
        从R2导入数据到CockroachDB

        Args:
            table_name: 目标表名
            r2_url: R2对象URL
            format: 文件格式（CSV）
            delimiter: 分隔符
        """
        logger.info(f"开始导入数据: {table_name} <- {r2_url}")

        try:
            async with self.engine.begin() as conn:
                # 构建IMPORT SQL语句
                import_sql = f"""
                IMPORT TABLE {table_name}
                CSV DATA ('{r2_url}')
                DELIMITER '{delimiter}'
                SKIP AS '1'
                """

                # 执行导入
                await conn.execute(text(import_sql))

            logger.success(f"导入完成: {table_name}")

        except Exception as e:
            logger.error(f"导入失败: {e}")
            raise

    async def import_tables(self, import_config: List[dict]):
        """
        批量导入表

        Args:
            import_config: 导入配置列表
                [
                    {"table": "stock_info", "url": "https://..."},
                    {"table": "stock_daily_prices", "url": "https://..."},
                ]
        """
        logger.info(f"开始批量导入 {len(import_config)} 张表")

        for config in import_config:
            try:
                await self.import_from_r2(
                    table_name=config["table"],
                    r2_url=config["url"],
                )
            except Exception as e:
                logger.error(f"导入表 {config['table']} 失败: {e}")
                continue

        logger.success("批量导入完成")

    async def close(self):
        """关闭连接"""
        if self.engine:
            await self.engine.dispose()
            logger.info("数据库连接已关闭")


async def main():
    """主函数示例"""
    import os

    # 从环境变量构建数据库URL
    db_url = (
        f"postgresql+asyncpg://{os.getenv('CLOUD_DB_USER')}:{os.getenv('CLOUD_DB_PASSWORD')}"
        f"@{os.getenv('CLOUD_DB_HOST')}:{os.getenv('CLOUD_DB_PORT')}/{os.getenv('CLOUD_DB_NAME')}"
        f"?sslmode={os.getenv('CLOUD_DB_SSLMODE')}"
    )

    # 创建导入器
    importer = CloudImporter(db_url)
    await importer.connect()

    # 导入配置（示例）
    import_config = [
        {
            "table": "stock_info",
            "url": "https://evo-alpha-data.r2.dev/stock_info.csv.gz",
        },
        {
            "table": "stock_daily_prices",
            "url": "https://evo-alpha-data.r2.dev/stock_daily_prices.csv.gz",
        },
    ]

    # 执行导入
    await importer.import_tables(import_config)

    # 关闭连接
    await importer.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
