# backend/quant_engine/strategies/base_strategy.py

import sys
import os
import logging
import pandas as pd
from datetime import datetime, date
from abc import ABC, abstractmethod
from sqlalchemy import text

# ================= 环境路径适配 (严格保留) =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

from app.core.database import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BaseStrategy(ABC):
    def __init__(self, strategy_name):
        self.engine = get_engine()
        self.strategy_name = strategy_name

        # ================= 策略元数据（子类可以覆盖） =================
        self.strategy_display_name = strategy_name  # 显示名称
        self.strategy_description = "策略说明（子类需实现）"  # 策略描述
        self.strategy_logic = "核心逻辑（子类需实现）"  # 核心逻辑说明
        self.filter_criteria = "筛选条件（子类需实现）"  # 筛选条件

        # ================= 核心：新宇宙表名配置 =================
        # 1. 股票池 (New)
        self.pool_table = "quant_stock_pool"
        # 2. 因子表 (New) - 个股RPS表
        self.rps_table = "quant_feature_rps"
        # 3. 预选结果表（New）- 区分预选和买点
        self.preselect_table = "quant_preselect_results"  # 预选结果表

    def get_stock_pool(self, pool_name='core_pool'):
        """1. 获取股票池 (从 quant_stock_pool 读取)"""
        logger.info(f"🏊‍♂️ [{self.strategy_name}] 加载股票池: {pool_name}...")
        
        # 新表结构通常比较规范，使用 symbol 字段，并且过滤 is_active
        # 如果你的表中没有 is_active 字段，请删除 `AND is_active = TRUE`
        try:
            query = text(f"""
                SELECT symbol, name 
                FROM {self.pool_table} 
                WHERE pool_name = '{pool_name}' AND is_active = TRUE
            """)
            df = pd.read_sql(query, self.engine)
            logger.info(f"   ✅ 股票池就绪: {len(df)} 只")
            return df
        except Exception as e:
            # 如果报错，可能是因为没有 is_active 列，尝试降级查询
            logger.warning(f"⚠️ 首次查询失败，尝试不带 is_active 过滤... ({e})")
            try:
                query = text(f"SELECT symbol, name FROM {self.pool_table} WHERE pool_name = '{pool_name}'")
                df = pd.read_sql(query, self.engine)
                logger.info(f"   ✅ (降级) 股票池就绪: {len(df)} 只")
                return df
            except Exception as e2:
                logger.error(f"❌ 获取股票池失败: {e2}")
                return pd.DataFrame()

    def get_daily_features(self, trade_date, symbols):
        """2. 获取指定日期的量化因子"""
        if not symbols: return pd.DataFrame()

        logger.info(f"📊 [{self.strategy_display_name}] 加载因子数据 ({trade_date})...")

        sym_str = "'" + "','".join(symbols) + "'"

        try:
            # 使用 LIKE 匹配日期（处理带时间戳的日期格式）
            query = text(f"""
                SELECT symbol, rps_50, rps_120, rps_250
                FROM {self.rps_table}
                WHERE trade_date LIKE '{trade_date}%'
                  AND symbol IN ({sym_str})
            """)

            df = pd.read_sql(query, self.engine)
            if df.empty:
                logger.warning(f"⚠️ {trade_date} 没有因子数据！可能是当日数据未更新。")
            else:
                logger.info(f"   ✅ 因子数据就绪: {len(df)} 条")
            return df
        except Exception as e:
            logger.error(f"❌ 获取因子数据失败: {e}")
            return pd.DataFrame()

    def _ensure_preselect_table(self, conn):
        """Ensure the preselect result table exists."""
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {self.preselect_table} (
                strategy_name VARCHAR(50),
                strategy_display_name VARCHAR(100),
                strategy_description TEXT,
                strategy_logic TEXT,
                filter_criteria TEXT,
                result_type VARCHAR(20),
                trade_date DATE,
                symbol VARCHAR(20),
                signal_type VARCHAR(10),
                meta_info TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (strategy_name, trade_date, symbol, result_type)
            )
        """))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS idx_{self.preselect_table}_date "
            f"ON {self.preselect_table} (trade_date);"
        ))

    def clear_results_for_dates(self, dates):
        """Clear existing preselect rows for one or more dates."""
        date_strs = [pd.to_datetime(item).strftime('%Y-%m-%d') for item in dates if item]
        if not date_strs:
            return 0

        deleted = 0
        try:
            with self.engine.begin() as conn:
                self._ensure_preselect_table(conn)
                for date_str in date_strs:
                    result = conn.execute(text(f"""
                        DELETE FROM {self.preselect_table}
                        WHERE strategy_name = :strategy_name
                          AND trade_date LIKE :trade_date
                    """), {
                        "strategy_name": self.strategy_name,
                        "trade_date": f"{date_str}%",
                    })
                    deleted += result.rowcount or 0
            return deleted
        except Exception as e:
            logger.error(f"❌ 清理预选结果失败: {e}")
            return 0

    def save_results(self, df_results):
        """
        保存预选结果到quant_preselect_results表

        注意：这是【预选】阶段，不是买入建议！
        买入信号需要AI后续分析
        """
        if df_results.empty:
            logger.info(f"🏁 [{self.strategy_display_name}] 预选结果为空，无需保存。")
            return 0

        logger.info(f"💾 [{self.strategy_display_name}] 正在保存 {len(df_results)} 条【预选结果】...")

        # 1. 补充策略信息
        df_results['strategy_name'] = self.strategy_name
        df_results['strategy_display_name'] = self.strategy_display_name
        df_results['strategy_description'] = self.strategy_description
        df_results['strategy_logic'] = self.strategy_logic
        df_results['filter_criteria'] = self.filter_criteria
        df_results['result_type'] = 'PRESELECT'  # 明确标记为预选

        # 2. 确保包含必要字段
        required_cols = [
            'strategy_name', 'strategy_display_name', 'strategy_description',
            'strategy_logic', 'filter_criteria', 'result_type',
            'trade_date', 'symbol', 'signal_type', 'meta_info'
        ]

        for col in required_cols:
            if col not in df_results.columns:
                df_results[col] = None

        df_save = df_results[required_cols].copy()

        try:
            with self.engine.begin() as conn:
                self._ensure_preselect_table(conn)

                # 幂等性删除：删除当天的数据
                dates = df_save['trade_date'].unique()
                date_strs = [pd.to_datetime(d).strftime('%Y-%m-%d') for d in dates]
                if date_strs:
                    for date_str in date_strs:
                        conn.execute(text(f"""
                            DELETE FROM {self.preselect_table}
                            WHERE strategy_name = :strategy_name
                              AND trade_date LIKE :trade_date
                        """), {
                            "strategy_name": self.strategy_name,
                            "trade_date": f"{date_str}%",
                        })

                # 写入新数据
                df_save.to_sql(self.preselect_table, conn, if_exists='append', index=False)

            logger.info(f"✅ 【预选结果】已入库！表: {self.preselect_table}, 日期: {dates[0] if len(dates) > 0 else 'Unknown'}")
            return len(df_save)

        except Exception as e:
            logger.error(f"❌ 保存失败: {e}")
            return 0

    @abstractmethod
    def run(self, trade_date=None):
        pass
