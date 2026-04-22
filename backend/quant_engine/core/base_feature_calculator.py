"""
EvoAlpha OS - 因子计算基类
提供统一的RPS计算框架，支持个股、板块、ETF等不同标的类型
"""

import sys
import os
import time
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from sqlalchemy import text
from tqdm import tqdm

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ================= 公共工具导入 =================
from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.common.exception_utils import CalculationError, DataSourceError, ValidationError
from quant_engine.config.calculator_config import CalculatorConfig

# ================= 路径初始化 =================
setup_quant_path()

# ================= Logger配置 =================
logger = setup_logger(__name__, level=CalculatorConfig.LOG_LEVEL)


class BaseFeatureCalculator(ABC):
    """
    因子计算基类

    核心功能：
    1. 统一的数据加载（支持增量窗口）
    2. 通用的RPS计算逻辑（向量化）
    3. 标准化的保存逻辑（幂等性）
    4. 完整的日志记录

    设计原则：
    - 子类只需实现配置方法，计算逻辑全部复用
    - 统一命名规范，便于理解和维护
    """

    def __init__(self):
        """初始化计算器"""
        from app.core.database import get_engine
        self.engine = get_engine()
        self.config = CalculatorConfig()

        # 获取子类配置
        self.source_table = self.get_source_table()
        self.target_table = self.get_target_table()
        self.entity_column = self.get_entity_column()
        self.periods = self.get_periods()

    # ================= 抽象方法（子类必须实现） =================

    @abstractmethod
    def get_source_table(self) -> str:
        """返回源表名"""
        pass

    @abstractmethod
    def get_target_table(self) -> str:
        """返回目标表名"""
        pass

    @abstractmethod
    def get_entity_column(self) -> str:
        """返回标的列名（symbol/sector_name）"""
        pass

    @abstractmethod
    def get_periods(self) -> list[int]:
        """返回计算周期列表"""
        pass

    def should_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        数据过滤逻辑（可选，子类可覆盖）

        Args:
            df: 原始数据

        Returns:
            pd.DataFrame: 过滤后的数据
        """
        return df

    # ================= 核心方法（通用逻辑） =================

    def _init_table(self):
        """初始化目标表结构"""
        if self.target_table.startswith('quant_feature_'):
            # 标准化的量化因子表结构
            # 前两列（entity_column 和 trade_date）需要特殊类型
            fields_str = f"    {self.entity_column} TEXT,\n    trade_date TEXT"

            # 添加涨幅字段（FLOAT类型）
            for period in self.periods:
                fields_str += f",\n    chg_{period} FLOAT"

            # 添加RPS字段（FLOAT类型）
            for period in self.periods:
                fields_str += f",\n    rps_{period} FLOAT"

            primary_key = f'{self.entity_column}, trade_date'

            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self.target_table} (
                        {fields_str},
                        PRIMARY KEY ({primary_key})
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.target_table}_date ON {self.target_table} (trade_date);"))

            logger.info(f"✅ 表 {self.target_table} 初始化完成")
        else:
            logger.warning(f"⚠️ 跳过表初始化（非标准表名）")

    def load_data(self, start_date=None):
        """
        加载数据（支持增量窗口）

        Args:
            start_date: 起始日期（YYYY-MM-DD），None表示加载全量

        Returns:
            pd.DataFrame: 加载的数据
        """
        condition = f"WHERE trade_date >= '{start_date}'" if start_date else ""
        query = f"""
            SELECT {self.entity_column}, trade_date, close
            FROM {self.source_table}
            {condition}
            ORDER BY trade_date
        """

        logger.info(f"📥 正在读取数据 (Start: {start_date if start_date else 'All'})...")

        try:
            df = pd.read_sql(query, self.engine)
        except Exception as e:
            raise DataSourceError(f"读取数据失败: {e}")

        if df.empty:
            logger.warning("⚠️ 数据为空")
            return df

        df['trade_date'] = pd.to_datetime(df['trade_date'])

        # 记录加载统计
        entity_count = df[self.entity_column].nunique()
        date_range = f"{df['trade_date'].min().date()} 至 {df['trade_date'].max().date()}"
        logger.info(f"   ✅ 加载完成: {len(df)} 行, {entity_count} 个标的, {date_range}")

        return df

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        核心计算逻辑（向量化）

        Args:
            df: 原始K线数据

        Returns:
            pd.DataFrame: 计算后的因子数据
        """
        if df.empty:
            raise ValidationError("数据为空，无法计算因子")

        logger.info("🧮 开始计算RPS因子...")

        # 1. 应用子类过滤逻辑
        df_filtered = self.should_filter(df)
        if len(df_filtered) < len(df):
            logger.info(f"   🧹 过滤后: {len(df_filtered)} 行 (原始: {len(df)} 行)")

        # 2. Pivot 宽表
        df_pivot = df_filtered.pivot(
            index='trade_date',
            columns=self.entity_column,
            values='close'
        )
        df_pivot = df_pivot.fillna(method='ffill')  # 填充停牌

        logger.info(f"   📊 Pivot表形状: {df_pivot.shape}")

        # 3. 计算RPS和涨幅
        feature_dfs = []

        for period in tqdm(self.periods, desc="  计算周期进度", unit="个"):
            # 涨幅
            chg = df_pivot.pct_change(period)
            # RPS (排名百分比 0-100)
            rps = chg.rank(axis=1, pct=True, method='min') * 100

            # Stack 堆叠
            chg_stack = chg.stack().reset_index()
            chg_stack.columns = ['trade_date', self.entity_column, f'chg_{period}']
            chg_stack.set_index([self.entity_column, 'trade_date'], inplace=True)

            rps_stack = rps.stack().reset_index()
            rps_stack.columns = ['trade_date', self.entity_column, f'rps_{period}']
            rps_stack.set_index([self.entity_column, 'trade_date'], inplace=True)

            feature_dfs.append(chg_stack)
            feature_dfs.append(rps_stack)

        # 4. 合并
        df_final = pd.concat(feature_dfs, axis=1).reset_index()

        # 5. 确保列顺序正确（与表结构一致）
        # 构建正确的列顺序：entity_column, trade_date, chg_x, rps_x, chg_y, rps_y, ...
        ordered_columns = [self.entity_column, 'trade_date']
        for period in self.periods:
            ordered_columns.append(f'chg_{period}')
            ordered_columns.append(f'rps_{period}')

        # 只保留存在的列（防止某些列缺失）
        ordered_columns = [col for col in ordered_columns if col in df_final.columns]
        df_final = df_final[ordered_columns]

        # 6. 格式化
        float_cols = [c for c in df_final.columns if c not in [self.entity_column, 'trade_date']]
        for col in float_cols:
            if 'rps' in col:
                df_final[col] = df_final[col].round(2)
            else:
                df_final[col] = df_final[col].round(4)

        logger.info(f"   ✅ 计算完成: {len(df_final)} 行, {len(float_cols)} 个因子")

        return df_final

    def save_to_db(self, df: pd.DataFrame, mode: str = 'append'):
        """
        保存数据到数据库（幂等性）

        Args:
            df: 要保存的数据
            mode: 'append' 或 'replace'
        """
        if df.empty:
            logger.warning("⚠️ 数据为空，跳过保存")
            return

        logger.info(f"💾 正在保存到 {self.target_table} ({len(df)} 行)...")

        try:
            # 幂等性删除：删除当天的数据
            if mode == 'append':
                dates = df['trade_date'].unique()
                date_strs = [pd.to_datetime(d).strftime('%Y-%m-%d') for d in dates]
                if date_strs:
                    with self.engine.begin() as conn:
                        for date_str in date_strs:
                            # 使用 LIKE 匹配日期（处理带时间戳的日期）
                            conn.execute(text(f"""
                                DELETE FROM {self.target_table}
                                WHERE trade_date LIKE '{date_str}%'
                            """))

            # 去除DataFrame内部的重复记录（保留最后一条）
            original_len = len(df)
            df = df.drop_duplicates(subset=[self.entity_column, 'trade_date'], keep='last')
            if len(df) < original_len:
                logger.info(f"   🧹 去除重复: {original_len - len(df)} 条")

            # 保存数据
            df.to_sql(
                self.target_table,
                self.engine,
                if_exists='append',
                index=False,
                method='multi',
                chunksize=self.config.CHUNK_SIZE
            )

            logger.info(f"   ✅ 保存成功")

        except Exception as e:
            raise CalculationError(f"保存失败: {e}")

    def run_init(self, days=400):
        """
        【全量模式】重算指定天数的数据（默认最近400天）

        Args:
            days: 加载最近N天的数据，默认400天（确保250日RPS计算准确）

        用途：
        - 首次初始化
        - 修复数据错误
        - 重算历史数据
        """
        logger.info("=" * 80)
        logger.info(f"🚀 [{self.__class__.__name__}] 启动全量重算（最近{days}天）...")
        logger.info("=" * 80)

        start_time = time.time()

        try:
            # 1. 初始化表
            self._init_table()

            # 2. 清空旧数据
            logger.info(f"🗑️ 清空表 {self.target_table}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {self.target_table}"))

            # 3. 计算起始日期
            cutoff_date = (
                datetime.now() - timedelta(days=days)
            ).strftime("%Y-%m-%d")
            logger.info(f"📅 数据范围: {cutoff_date} 至今")

            # 4. 加载数据
            df = self.load_data(start_date=cutoff_date)

            if df.empty:
                logger.warning("⚠️ 数据为空，跳过计算")
                return

            # 5. 计算因子
            result = self.compute_features(df)

            # 6. 保存
            self.save_to_db(result, mode='append')

            cost = time.time() - start_time
            logger.info(f"✅ 全量任务完成！耗时: {cost:.1f}秒")

        except Exception as e:
            logger.error(f"❌ 全量任务失败: {e}")
            raise

    def run_daily(self):
        """
        【增量模式】只计算最新一天的数据（真正的增量更新）

        逻辑：
        1. 检查目标表最后计算的日期
        2. 获取源表中该日期之后的新数据（通常1天）
        3. 加载足够的历史数据窗口（用于计算RPS）
        4. 只计算新数据的RPS
        5. 只保存新计算的数据

        用途：
        - 每日定时任务
        - 补充缺失数据
        """
        logger.info("=" * 80)
        logger.info(f"🚀 [{self.__class__.__name__}] 启动增量更新...")
        logger.info("=" * 80)

        start_time = time.time()

        try:
            # 1. 初始化表
            self._init_table()

            # 2. 获取目标表最后计算的日期
            last_calculated_date = self._get_last_calculated_date()

            if last_calculated_date:
                logger.info(f"📅 最后计算日期: {last_calculated_date}")
            else:
                # 如果没有历史数据，执行初始化
                logger.info("⚠️  未找到历史数据，执行初始化...")
                return self.run_init()

            # 3. 获取源表最新日期
            last_source_date = self._get_last_source_date()
            logger.info(f"📅 源表最新日期: {last_source_date}")

            if last_source_date <= last_calculated_date:
                logger.info("✅ 数据已是最新，无需更新")
                return

            # 4. 确定需要计算的日期范围（通常只有1天）
            from datetime import timedelta
            days_diff = (last_source_date - last_calculated_date).days

            if days_diff > 7:
                logger.warning(f"⚠️  发现数据缺失: {days_diff}天，执行补齐...")
                # 缺失数据超过7天，执行更大的初始化
                return self.run_init(days=min(days_diff + 50, 400))

            logger.info(f"📊 需要计算: {days_diff}天新数据")

            # 5. 加载数据窗口（需要加载足够的历史数据来计算RPS）
            # 计算RPS需要历史数据，例如250日RPS需要250天历史
            window_start = last_calculated_date - timedelta(days=self.config.INCREMENTAL_WINDOW_DAYS)
            logger.info(f"📥 加载数据窗口: {window_start} 至 {last_source_date}")

            df = self.load_data(start_date=window_start.strftime("%Y-%m-%d"))

            if df.empty:
                logger.warning("⚠️  无法加载数据")
                return

            # 6. 计算所有数据的RPS
            result_full = self.compute_features(df)

            # 7. 只保存新计算的数据（last_calculated_date 之后的数据）
            # 转换为统一类型进行比较
            last_calculated_dt = pd.to_datetime(last_calculated_date)
            result_new = result_full[result_full['trade_date'] > last_calculated_dt].copy()

            if result_new.empty:
                logger.warning("⚠️  没有新数据需要保存")
                return

            new_dates = result_new['trade_date'].unique()
            logger.info(f"📅 新增日期: {len(new_dates)}个")
            logger.info(f"   日期范围: {new_dates.min()} 至 {new_dates.max()}")

            # 8. 保存新数据
            self.save_to_db(result_new, mode='append')

            cost = time.time() - start_time
            logger.info(f"✅ 增量任务完成！耗时: {cost:.1f}秒")

        except Exception as e:
            logger.error(f"❌ 增量任务失败: {e}")
            raise

    def _get_last_calculated_date(self):
        """获取目标表最后计算的日期"""
        try:
            query = f"SELECT MAX(trade_date) as max_date FROM {self.target_table}"
            with self.engine.begin() as conn:
                result = conn.execute(text(query)).fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).date()
            return None
        except Exception as e:
            logger.warning(f"⚠️  获取最后计算日期失败: {e}")
            return None

    def _get_last_source_date(self):
        """获取源表最新日期"""
        try:
            query = f"SELECT MAX(trade_date) as max_date FROM {self.source_table}"
            with self.engine.begin() as conn:
                result = conn.execute(text(query)).fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).date()
            return None
        except Exception as e:
            logger.warning(f"⚠️  获取源表最新日期失败: {e}")
            return None

    def run(self, mode='daily'):
        """
        执行计算

        Args:
            mode: 'daily' 或 'init'
        """
        if mode == 'init':
            self.run_init()
        else:
            self.run_daily()
