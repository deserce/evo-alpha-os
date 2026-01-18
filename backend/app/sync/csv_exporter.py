"""
EvoAlpha OS - 同步模块：CSV导出器
将本地SQLite数据导出为CSV文件，准备上传到R2
"""

import sqlite3
import pandas as pd
from loguru import logger
from pathlib import Path
from typing import List
import gzip


class CSVExporter:
    """CSV导出器"""

    def __init__(self, db_path: str = "./data/local_quant.db", output_dir: str = "./data/exports"):
        self.db_path = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_table_to_csv(
        self, table_name: str, compress: bool = True, batch_size: int = 10000
    ) -> str:
        """
        导出单张表为CSV

        Args:
            table_name: 表名
            compress: 是否压缩（gzip）
            batch_size: 批量读取大小

        Returns:
            导出文件路径
        """
        logger.info(f"开始导出表: {table_name}")

        try:
            # 连接数据库
            conn = sqlite3.connect(self.db_path)

            # 分批读取数据
            query = f"SELECT * FROM {table_name}"
            chunks = []
            for chunk in pd.read_sql_query(query, conn, chunksize=batch_size):
                chunks.append(chunk)

            df = pd.concat(chunks, ignore_index=True)
            conn.close()

            logger.info(f"从 {table_name} 读取了 {len(df)} 条记录")

            # 生成文件路径
            output_file = self.output_dir / f"{table_name}.csv"
            if compress:
                output_file = self.output_dir / f"{table_name}.csv.gz"

            # 导出CSV
            if compress:
                df.to_csv(output_file, index=False, compression="gzip")
            else:
                df.to_csv(output_file, index=False)

            file_size = output_file.stat().st_size / 1024 / 1024  # MB
            logger.success(f"导出完成: {output_file} ({file_size:.2f} MB)")

            return str(output_file)

        except Exception as e:
            logger.error(f"导出表 {table_name} 失败: {e}")
            raise

    def export_all_tables(self, table_names: List[str] = None) -> List[str]:
        """
        导出所有表

        Args:
            table_names: 表名列表，如果为空则导出全部

        Returns:
            导出文件路径列表
        """
        if table_names is None:
            # TODO: 从数据库获取所有表名
            table_names = [
                "stock_info",
                "stock_daily_prices",
                "sector_daily_prices",
                "quant_feature_rps",
                "quant_strategy_results",
            ]

        logger.info(f"开始导出 {len(table_names)} 张表")

        export_files = []
        for table_name in table_names:
            try:
                file_path = self.export_table_to_csv(table_name)
                export_files.append(file_path)
            except Exception as e:
                logger.error(f"导出表 {table_name} 失败: {e}")
                continue

        logger.success(f"导出完成，共 {len(export_files)} 个文件")
        return export_files


if __name__ == "__main__":
    exporter = CSVExporter()
    files = exporter.export_all_tables()
    logger.info(f"导出的文件: {files}")
