"""
EvoAlpha OS - 板块RPS因子计算器
计算板块的相对价格强度(RPS)因子
"""

import sys
import os
import pandas as pd
from datetime import datetime
from sqlalchemy import text

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ================= 公共工具导入 =================
from quant_engine.core.base_feature_calculator import BaseFeatureCalculator
from quant_engine.common import setup_logger
from quant_engine.config.calculator_config import CalculatorConfig

# ================= Logger配置 =================
logger = setup_logger(__name__)


class SectorRPSCalculator(BaseFeatureCalculator):
    """
    板块RPS计算器

    功能：
    - 计算板块的相对价格强度
    - 支持多周期RPS（5/10/20/50/120/250日）
    - 板块黑名单过滤
    - 增量更新模式

    数据表：
    - 源表: sector_daily_prices
    - 目标表: quant_feature_sector_rps

    特性：
    - 自动过滤干扰板块（昨日涨停、连板等）
    - 只在有效板块之间进行排名
    """

    def get_source_table(self) -> str:
        """返回源表名"""
        return "sector_daily_prices"

    def get_target_table(self) -> str:
        """返回目标表名"""
        return "quant_feature_sector_rps"

    def get_entity_column(self) -> str:
        """返回标的列名"""
        return "sector_name"

    def get_periods(self) -> list:
        """返回计算周期"""
        return [5, 10, 20, 50, 120, 250]

    def _has_sector_list(self) -> bool:
        """判断是否存在可用的行业宇宙表。"""
        try:
            with self.engine.begin() as conn:
                result = conn.execute(text("""
                    SELECT COUNT(*)
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'sector_list'
                """)).scalar()
                if not result:
                    return False
                count = conn.execute(text("""
                    SELECT COUNT(*)
                    FROM sector_list
                    WHERE sector_type = 'Industry'
                """)).scalar()
                return bool(count)
        except Exception:
            return False

    def load_data(self, start_date=None):
        """
        加载行业指数数据。

        若存在 sector_list，则只对行业宇宙计算RPS，避免历史残留的概念/指数名称混进排名。
        """
        condition = "WHERE p.close > 0"
        params = {}
        if start_date:
            condition += " AND p.trade_date >= :start_date"
            params["start_date"] = start_date

        if self._has_sector_list():
            query = f"""
                SELECT p.{self.entity_column}, p.trade_date, p.close
                FROM {self.source_table} p
                JOIN sector_list s
                  ON p.{self.entity_column} = s.sector_name
                {condition}
                  AND s.sector_type = 'Industry'
                ORDER BY p.trade_date
            """
            source = "sector_list 行业宇宙"
        else:
            query = f"""
                SELECT {self.entity_column}, trade_date, close
                FROM {self.source_table} p
                {condition}
                ORDER BY trade_date
            """
            source = "sector_daily_prices 全量"

        logger.info(
            f"📥 正在读取板块数据 ({source}, Start: {start_date if start_date else 'All'})..."
        )

        try:
            df = pd.read_sql(text(query), self.engine, params=params)
        except Exception as e:
            from quant_engine.common.exception_utils import DataSourceError
            raise DataSourceError(f"读取板块数据失败: {e}")

        if df.empty:
            logger.warning("⚠️ 板块数据为空")
            return df

        df["trade_date"] = pd.to_datetime(df["trade_date"], format="mixed")
        df[self.entity_column] = df[self.entity_column].astype(str).str.strip()
        df = df[
            (df[self.entity_column] != "")
            & ~df[self.entity_column].str.contains(r"[\x00-\x1f]", regex=True, na=False)
        ]

        entity_count = df[self.entity_column].nunique()
        date_range = f"{df['trade_date'].min().date()} 至 {df['trade_date'].max().date()}"
        logger.info(f"   ✅ 加载完成: {len(df)} 行, {entity_count} 个板块, {date_range}")

        return df

    def should_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        数据过滤逻辑 - 过滤干扰板块

        黑名单规则：
        - 包含"昨日"、"连板"、"涨停"、"ST"等关键字的板块
        """
        if self._has_sector_list():
            logger.info("🧹 已使用 sector_list 行业宇宙，跳过关键词黑名单二次过滤")
            return df

        blacklist = CalculatorConfig.SECTOR_BLACKLIST

        logger.info(f"🧹 正在过滤干扰板块 (规则: {blacklist})...")

        original_count = len(df[self.get_entity_column()].unique())

        # 使用正则表达式过滤
        pattern = "|".join(blacklist)
        df_filtered = df[
            ~df[self.get_entity_column()].str.contains(pattern, regex=True, na=False)
        ]

        filtered_count = len(df_filtered[self.get_entity_column()].unique())

        logger.info(f"   ✅ 已剔除 {original_count - filtered_count} 个干扰板块")
        logger.info(f"   📊 剩余 {filtered_count} 个有效板块参与排名")

        return df_filtered

    def show_top_sectors(self, df):
        """打印最新战况（可选功能）"""
        if df.empty:
            return

        latest_date = df['trade_date'].max()
        logger.info("\n" + "=" * 80)
        logger.info(f"🏆 [{latest_date.date()}] 市场最强主线 (RPS_20 > 95):")
        logger.info("=" * 80)

        mask = (df['trade_date'] == latest_date) & (df['rps_20'] > 95)
        top_sectors = df[mask].sort_values(by='rps_20', ascending=False)

        if not top_sectors.empty:
            for _, row in top_sectors.head(10).iterrows():
                chg_str = f"{row.get('chg_20', 0) * 100:.1f}%"
                rps_str = f"RPS: {row.get('rps_5', 0):.1f} / {row.get('rps_20', 0):.1f} / {row.get('rps_50', 0):.1f}"
                logger.info(f"  {row[self.get_entity_column()]:<20} {rps_str:<35} {chg_str}")
        else:
            logger.info("  无板块符合条件")

        logger.info("=" * 80 + "\n")


if __name__ == "__main__":
    import argparse

    calculator = SectorRPSCalculator()

    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='daily', help='init or daily')
    args = parser.parse_args()

    if args.mode == 'init':
        calculator.run_init()
    else:
        result = calculator.run_daily()
        calculator.show_top_sectors(result)
