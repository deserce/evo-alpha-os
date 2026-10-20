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
            expected_columns = {
                self.entity_column: "TEXT",
                "trade_date": "TEXT",
            }

            # 添加涨幅字段（FLOAT类型）
            for period in self.periods:
                expected_columns[f"chg_{period}"] = "FLOAT"

            # 添加RPS字段（FLOAT类型）
            for period in self.periods:
                expected_columns[f"rps_{period}"] = "FLOAT"

            fields_str = ",\n".join(
                f"    {column} {column_type}"
                for column, column_type in expected_columns.items()
            )

            primary_key = f'{self.entity_column}, trade_date'

            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self.target_table} (
                        {fields_str},
                        PRIMARY KEY ({primary_key})
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.target_table}_date ON {self.target_table} (trade_date);"))

                # 旧初始化脚本创建过精简版 RPS 表，这里做轻量迁移，避免写入 chg_* 等字段时报错。
                existing_columns = {
                    row[1]
                    for row in conn.execute(text(f"PRAGMA table_info({self.target_table})")).fetchall()
                }
                for column, column_type in expected_columns.items():
                    if column not in existing_columns:
                        conn.execute(text(
                            f"ALTER TABLE {self.target_table} ADD COLUMN {column} {column_type}"
                        ))
                        logger.info(f"   ➕ 补齐字段: {self.target_table}.{column}")

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

        df['trade_date'] = pd.to_datetime(df['trade_date'], format='mixed')

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
        df_pivot = df_pivot.ffill()  # 填充停牌

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

    def compute_features_for_dates(
        self,
        df: pd.DataFrame,
        target_dates,
    ) -> pd.DataFrame:
        """Compute cross-sectional RPS only for requested output dates."""
        if df.empty:
            raise ValidationError("数据为空，无法计算因子")

        df_filtered = self.should_filter(df)
        pivot = df_filtered.pivot(
            index="trade_date",
            columns=self.entity_column,
            values="close",
        ).sort_index().ffill()
        pivot.index = pd.to_datetime(pivot.index).normalize()
        targets = sorted(
            {
                pd.to_datetime(value).normalize()
                for value in target_dates
                if pd.notna(value)
            }
        )
        targets = [value for value in targets if value in pivot.index]
        if not targets:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for target in targets:
            position = pivot.index.get_loc(target)
            current = pivot.iloc[position]
            frame = pd.DataFrame(
                {
                    self.entity_column: current.index,
                    "trade_date": target,
                }
            ).set_index(self.entity_column)
            for period in self.periods:
                if position < period:
                    change = pd.Series(np.nan, index=current.index, dtype=float)
                else:
                    previous = pivot.iloc[position - period]
                    change = current.divide(previous).subtract(1.0)
                    change = change.where(previous.notna() & previous.ne(0))
                frame[f"chg_{period}"] = change.round(4)
                frame[f"rps_{period}"] = (
                    change.rank(pct=True, method="min").mul(100).round(2)
                )
            frames.append(frame.reset_index())

        result = pd.concat(frames, ignore_index=True)
        feature_columns = [
            column
            for period in self.periods
            for column in (f"chg_{period}", f"rps_{period}")
        ]
        result = result.dropna(subset=feature_columns, how="all")
        return result[[self.entity_column, "trade_date", *feature_columns]]

    def _get_missing_target_dates(self, max_days: int = 7):
        """Return real source dates missing from the target within a bounded window."""
        try:
            with self.engine.begin() as conn:
                source_rows = conn.execute(text(f"""
                    SELECT trade_date
                    FROM (
                        SELECT DISTINCT trade_date
                        FROM {self.source_table}
                        WHERE trade_date IS NOT NULL
                        ORDER BY trade_date DESC
                        LIMIT :limit
                    )
                    ORDER BY trade_date
                """), {"limit": max(1, int(max_days))}).fetchall()
                if not source_rows:
                    return []
                source_dates = [pd.to_datetime(row[0]).date() for row in source_rows]
                start_date = source_dates[0].isoformat()
                target_rows = conn.execute(text(f"""
                    SELECT DISTINCT trade_date
                    FROM {self.target_table}
                    WHERE trade_date >= :start_date
                """), {"start_date": start_date}).fetchall()
            existing = {pd.to_datetime(row[0]).date() for row in target_rows}
            return [value for value in source_dates if value not in existing]
        except Exception as exc:
            logger.warning("无法查询内部 RPS 缺口，回退旧增量逻辑: %s", exc)
            return None

    def _filter_source_target_dates(self, target_dates, missing_only: bool = False) -> list:
        normalized = sorted({pd.to_datetime(value).date() for value in target_dates})
        if not normalized:
            return []
        placeholders = ", ".join(f":date_{index}" for index in range(len(normalized)))
        params = {f"date_{index}": value.isoformat() for index, value in enumerate(normalized)}
        try:
            with self.engine.begin() as conn:
                rows = conn.execute(text(f"""
                    SELECT DISTINCT DATE(trade_date)
                    FROM {self.source_table}
                    WHERE DATE(trade_date) IN ({placeholders})
                    ORDER BY DATE(trade_date)
                """), params).fetchall()
                existing_rows = []
                if missing_only:
                    existing_rows = conn.execute(text(f"""
                        SELECT DISTINCT DATE(trade_date)
                        FROM {self.target_table}
                        WHERE DATE(trade_date) IN ({placeholders})
                    """), params).fetchall()
            source_dates = [pd.to_datetime(row[0]).date() for row in rows]
            existing_dates = {pd.to_datetime(row[0]).date() for row in existing_rows}
            return [value for value in source_dates if value not in existing_dates]
        except Exception as exc:
            raise DataSourceError(f"查询 RPS 目标日期失败: {exc}") from exc

    def _get_window_start_for_targets(self, target_dates) -> str | None:
        if not target_dates:
            return None
        earliest = min(target_dates).isoformat()
        required_rows = max(self.periods) + 1
        try:
            with self.engine.begin() as conn:
                row = conn.execute(text(f"""
                    SELECT MIN(seed_date)
                    FROM (
                        SELECT DISTINCT trade_date AS seed_date
                        FROM {self.source_table}
                        WHERE trade_date < DATE(:target_date, '+1 day')
                        ORDER BY trade_date DESC
                        LIMIT :required_rows
                    )
                """), {
                    "target_date": earliest,
                    "required_rows": required_rows,
                }).fetchone()
            return str(row[0])[:10] if row and row[0] else None
        except Exception as exc:
            logger.warning("无法定位 250 周期种子日，回退自然日窗口: %s", exc)
            return None

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
            # 去除DataFrame内部的重复记录（保留最后一条）
            original_len = len(df)
            df = df.drop_duplicates(subset=[self.entity_column, 'trade_date'], keep='last')
            if len(df) < original_len:
                logger.info(f"   🧹 去除重复: {original_len - len(df)} 条")

            if mode not in {'append', 'replace'}:
                raise CalculationError(f"不支持的保存模式: {mode}")

            with self.engine.begin() as conn:
                if mode == 'replace':
                    conn.execute(text(f"DELETE FROM {self.target_table}"))
                else:
                    dates = df['trade_date'].unique()
                    date_strs = [pd.to_datetime(d).strftime('%Y-%m-%d') for d in dates]
                    for date_str in date_strs:
                        # 使用 LIKE 匹配日期（处理带时间戳的日期）
                        conn.execute(text(f"""
                            DELETE FROM {self.target_table}
                            WHERE trade_date LIKE '{date_str}%'
                        """))

                # 删除与写入保持在同一事务中，避免重算失败时暴露空表或半截日期。
                df.to_sql(
                    self.target_table,
                    conn,
                    if_exists='append',
                    index=False,
                    method='multi',
                    chunksize=self.config.CHUNK_SIZE
                )

            logger.info(f"   ✅ 保存成功")

        except Exception as e:
            raise CalculationError(f"保存失败: {e}")

    def run_init(self, days: int | None = None):
        """
        【全量模式】重算历史数据

        Args:
            days: 加载最近N个自然日的数据；None 表示加载源表全历史

        用途：
        - 首次初始化
        - 修复数据错误
        - 重算历史数据
        """
        logger.info("=" * 80)
        range_label = "全历史" if days is None else f"最近{days}天"
        logger.info(f"🚀 [{self.__class__.__name__}] 启动全量重算（{range_label}）...")
        logger.info("=" * 80)

        start_time = time.time()

        try:
            # 1. 初始化表
            self._init_table()

            # 2. 计算起始日期。RPS250 依赖 250 个交易日前的价格，默认 init 必须使用
            # 源表全历史，避免把早期 rps_250 计算成长期空值。
            if days is None:
                cutoff_date = None
                logger.info("📅 数据范围: 源表全历史")
            else:
                last_source_date = self._get_last_source_date()
                if last_source_date is None:
                    logger.warning("⚠️ 源表无最新日期，跳过计算")
                    return
                cutoff_date = (last_source_date - timedelta(days=days)).strftime("%Y-%m-%d")
                logger.info(f"📅 数据范围: {cutoff_date} 至 {last_source_date}")

            # 3. 加载数据
            df = self.load_data(start_date=cutoff_date)

            if df.empty:
                logger.warning("⚠️ 数据为空，跳过计算")
                return

            # 4. 计算因子
            result = self.compute_features(df)

            # 5. 保存：成功计算后再事务性替换，避免失败时清空历史结果。
            self.save_to_db(result, mode='replace')

            cost = time.time() - start_time
            logger.info(f"✅ 全量任务完成！耗时: {cost:.1f}秒")

        except Exception as e:
            logger.error(f"❌ 全量任务失败: {e}")
            raise

    def run_daily(self, target_dates=None, missing_only: bool = False):
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
            elif target_dates is None:
                # 如果没有历史数据，执行初始化
                logger.info("⚠️  未找到历史数据，执行初始化...")
                return self.run_init()

            # 3. 获取源表最新日期
            last_source_date = self._get_last_source_date()
            logger.info(f"📅 源表最新日期: {last_source_date}")

            max_daily_backfill_days = getattr(self.config, "DAILY_MAX_BACKFILL_DAYS", 7)
            fallback_save_after = None
            if target_dates is not None:
                pending_dates = self._filter_source_target_dates(
                    target_dates,
                    missing_only=missing_only,
                )[-max_daily_backfill_days:]
            else:
                pending_dates = self._get_missing_target_dates(max_daily_backfill_days)

            if pending_dates is None:
                days_diff = (last_source_date - last_calculated_date).days
                fallback_save_after = last_calculated_date
                if days_diff > max_daily_backfill_days:
                    fallback_save_after = max(
                        last_calculated_date,
                        last_source_date - timedelta(days=max_daily_backfill_days),
                    )
                window_start = (
                    fallback_save_after - timedelta(days=self.config.INCREMENTAL_WINDOW_DAYS)
                ).strftime("%Y-%m-%d")
            else:
                if not pending_dates:
                    logger.info("✅ 最近目标交易日均已计算，无需更新")
                    return {"success": True, "records": 0, "elapsed": time.time() - start_time}
                window_start = self._get_window_start_for_targets(pending_dates)
                if window_start is None:
                    window_start = (
                        min(pending_dates) - timedelta(days=self.config.INCREMENTAL_WINDOW_DAYS)
                    ).strftime("%Y-%m-%d")

            logger.info("📊 待计算交易日: %s", pending_dates or "fallback")
            logger.info(f"📥 加载数据窗口: {window_start} 至 {last_source_date}")

            df = self.load_data(start_date=window_start)

            if df.empty:
                logger.warning("⚠️  无法加载数据")
                return

            if pending_dates is None:
                pending_dates = sorted(
                    {
                        value.date()
                        for value in pd.to_datetime(df["trade_date"])
                        if value.date() > fallback_save_after
                    }
                )[-max_daily_backfill_days:]

            result_new = self.compute_features_for_dates(df, pending_dates)

            if result_new.empty:
                logger.warning("⚠️  没有新数据需要保存")
                return {"success": True, "records": 0, "elapsed": time.time() - start_time}

            new_dates = result_new['trade_date'].unique()
            logger.info(f"📅 新增日期: {len(new_dates)}个")
            logger.info(f"   日期范围: {new_dates.min()} 至 {new_dates.max()}")

            # 8. 保存新数据
            self.save_to_db(result_new, mode='append')

            cost = time.time() - start_time
            logger.info(f"✅ 增量任务完成！耗时: {cost:.1f}秒")
            return {"success": True, "records": len(result_new), "elapsed": cost}

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
