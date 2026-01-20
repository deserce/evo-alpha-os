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
        
        # ================= 核心：新宇宙表名配置 =================
        # 1. 股票池 (New)
        self.pool_table = "quant_stock_pool"
        # 2. 因子表 (New) - 个股RPS表
        self.rps_table = "quant_feature_stock_rps"
        # 3. 结果表 (New)
        self.result_table = "quant_strategy_results"

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
        
        logger.info(f"📊 [{self.strategy_name}] 加载因子数据 ({trade_date})...")
        
        sym_str = "'" + "','".join(symbols) + "'"
        
        try:
            # 假设 quant_feature_rps 使用 symbol 字段
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

    def save_results(self, df_results):
        """3. 标准化保存结果 (quant_strategy_results)"""
        if df_results.empty:
            logger.info(f"🏁 [{self.strategy_name}] 结果为空，无需保存。")
            return

        logger.info(f"💾 [{self.strategy_name}] 正在保存 {len(df_results)} 条选股结果...")

        # 1. 补充策略名称
        df_results['strategy_name'] = self.strategy_name

        # 2. 确保包含必要字段（根据实际表结构）
        # 表结构: strategy_name, trade_date, symbol, signal_type, meta_info, created_at
        required_cols = ['strategy_name', 'trade_date', 'symbol', 'signal_type', 'meta_info']

        for col in required_cols:
            if col not in df_results.columns:
                df_results[col] = None

        df_save = df_results[required_cols].copy()

        try:
            with self.engine.begin() as conn:
                # 3. 幂等性删除：根据【数据日期】删除旧记录
                dates = df_save['trade_date'].unique()
                date_list_str = "'" + "','".join([str(d) for d in dates]) + "'"
                
                del_sql = text(f"""
                    DELETE FROM {self.result_table} 
                    WHERE strategy_name = '{self.strategy_name}' 
                      AND trade_date IN ({date_list_str})
                """)
                conn.execute(del_sql)
                
                # 4. 写入新数据
                df_save.to_sql(self.result_table, conn, if_exists='append', index=False)
                
            target_date = dates[0] if len(dates) > 0 else "Unknown"
            logger.info(f"🎉 结果已入库！(表: {self.result_table}, 日期: {target_date})")
            
        except Exception as e:
            logger.error(f"❌ 保存失败: {e}")

    @abstractmethod
    def run(self, trade_date=None):
        pass