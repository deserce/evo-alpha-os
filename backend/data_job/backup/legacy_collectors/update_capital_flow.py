"""
EvoAlpha OS - 资金流向数据采集
采集基金持股和北向资金流向数据
"""

import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from datetime import date, timedelta, datetime
from sqlalchemy import text
import ssl

# ================= 网络急救包 =================
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]
ssl._create_default_https_context = ssl._create_unverified_context
# ==================================================

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_engine

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class CapitalFlowManager:
    def __init__(self):
        self.engine = get_engine()

    def _init_tables(self):
        """初始化资金流向相关表"""
        with self.engine.begin() as conn:
            # 1. 北向资金整体流向表（市场级别）
            logger.info("🛠️ 创建北向资金流向表...")
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS finance_northbound_flow (
                        trade_date DATE PRIMARY KEY,
                        net_buy_amount FLOAT,      -- 当日成交净买额（亿元）
                        buy_amount FLOAT,          -- 买入成交额（亿元）
                        sell_amount FLOAT,         -- 卖出成交额（亿元）
                        total_hold_value FLOAT,    -- 历史累计净买额（亿元）
                        market_value FLOAT,        -- 持股市值（亿元）
                        flow_in_amount FLOAT,      -- 当日资金流入（亿元）
                        balance FLOAT              -- 当日余额（亿元）
                    );
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_north_flow_date ON finance_northbound_flow (trade_date);"))
                logger.info("✅ 北向资金流向表创建成功")
            except Exception as e:
                logger.error(f"❌ 创建北向资金表失败: {e}")

            # 2. 基金持仓表（个股级别）
            logger.info("🛠️ 创建基金持仓表...")
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS finance_fund_holdings (
                        symbol VARCHAR(20),
                        report_date DATE,
                        fund_count INTEGER,         -- 持有基金家数
                        hold_count FLOAT,          -- 持股总数（股）
                        hold_value FLOAT,          -- 持股市值（元）
                        hold_change VARCHAR(20),   -- 持股变化（增仓/减仓/不变）
                        change_value FLOAT,        -- 持股变动数值（股）
                        change_ratio FLOAT,        -- 持股变动比例（%）
                        PRIMARY KEY (symbol, report_date)
                    );
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fund_date ON finance_fund_holdings (report_date);"))
                logger.info("✅ 基金持仓表创建成功")
            except Exception as e:
                logger.error(f"❌ 创建基金持仓表失败: {e}")

    # ==========================================
    # 模块 A: 北向资金整体流向（市场级别）
    # ==========================================
    def update_northbound_flow(self):
        """
        采集北向资金整体流向数据（市场级别）
        时间范围：2023-08-01 到 2024-08-01（新规前的完整数据）
        """
        logger.info("🚀 [1/2] 开始采集北向资金流向数据...")

        # 确定采集范围（最近3年）
        end_date = date.today()
        start_date = date.today() - timedelta(days=1095)

        logger.info(f"📅 采集范围: {start_date} 到 {end_date} (最近3年)")

        # 检查数据库中是否已有数据
        try:
            with self.engine.connect() as conn:
                last_date = conn.execute(text("SELECT MAX(trade_date) FROM finance_northbound_flow")).scalar()
            if last_date:
                logger.info(f"📊 数据库最新数据: {last_date}")
                # 如果已有数据，可以选择跳过或增量更新
                # 这里简单处理：如果数据已完整则跳过
                if last_date >= end_date:
                    logger.info("✅ 北向资金数据已是最新，跳过采集")
                    return
        except Exception:
            pass

        try:
            # 获取北向资金整体历史数据
            logger.info("📥 正在下载北向资金历史数据...")
            df = ak.stock_hsgt_hist_em(symbol="北向资金")

            if df.empty:
                logger.error("❌ 未能获取到北向资金数据")
                return

            # 筛选目标日期范围
            df['日期'] = pd.to_datetime(df['日期'])
            df_filtered = df[(df['日期'] >= start_date) & (df['日期'] <= end_date)].copy()

            if df_filtered.empty:
                logger.warning(f"⚠️  日期范围 {start_date} 到 {end_date} 内无数据")
                return

            logger.info(f"✅ 筛选后数据: {len(df_filtered)} 条记录")

            # 映射列名
            df_filtered = df_filtered.rename(columns={
                '日期': 'trade_date',
                '当日成交净买额': 'net_buy_amount',
                '买入成交额': 'buy_amount',
                '卖出成交额': 'sell_amount',
                '历史累计净买额': 'total_hold_value',
                '持股市值': 'market_value',
                '当日资金流入': 'flow_in_amount',
                '当日余额': 'balance'
            })

            # 转换日期格式
            df_filtered['trade_date'] = pd.to_datetime(df_filtered['trade_date']).dt.date

            # 选择需要的列
            columns = ['trade_date', 'net_buy_amount', 'buy_amount', 'sell_amount',
                      'total_hold_value', 'market_value', 'flow_in_amount', 'balance']
            df_save = df_filtered[columns].copy()

            # 清理数据：删除空值记录
            df_save = df_save.dropna(subset=['trade_date'])

            if df_save.empty:
                logger.warning("⚠️  清理后无有效数据")
                return

            # 先删除已有数据
            with self.engine.begin() as conn:
                conn.execute(text("""
                    DELETE FROM finance_northbound_flow
                    WHERE trade_date >= :start_date AND trade_date <= :end_date
                """), {"start_date": start_date, "end_date": end_date})

                # 批量插入
                df_save.to_sql('finance_northbound_flow', conn, if_exists='append', index=False, method='multi')

            logger.info(f"✅ 北向资金数据采集完成: {len(df_save)} 条记录")

        except Exception as e:
            logger.error(f"❌ 北向资金数据采集失败: {e}")

    # ==========================================
    # 模块 B: 基金持仓（季度更新）
    # ==========================================
    def update_fund_holdings(self):
        """
        采集基金持仓季度数据
        时间范围：2023、2024 年度季度数据
        """
        logger.info("🚀 [2/2] 开始采集基金持仓数据...")

        # 定义需要采集的季度（最近8个季度）
        target_quarters = [
            "20241231", "20240930", "20240630", "20240331",
            "20231231", "20230930", "20230630", "20230331"
        ]

        success_count = 0
        fail_count = 0

        for q_date in target_quarters:
            # 转换为日期格式
            report_date = f"{q_date[:4]}-{q_date[4:6]}-{q_date[6:]}"

            # 检查是否已有数据
            try:
                with self.engine.connect() as conn:
                    exists = conn.execute(text("""
                        SELECT 1 FROM finance_fund_holdings
                        WHERE report_date = :report_date LIMIT 1
                    """), {"report_date": report_date}).scalar()

                if exists:
                    logger.info(f"⏭️  {report_date} 数据已存在，跳过")
                    success_count += 1
                    continue
            except Exception:
                pass

            logger.info(f"📥 正在下载基金持仓: {report_date} ...")

            try:
                # 获取基金持仓数据
                df = ak.stock_report_fund_hold(date=q_date)

                if df.empty:
                    logger.warning(f"⚠️  {report_date} 无数据")
                    fail_count += 1
                    time.sleep(2)
                    continue

                # 映射列名
                df_processed = df.rename(columns={
                    '股票代码': 'symbol',
                    '持有基金家数': 'fund_count',
                    '持股总数': 'hold_count',
                    '持股市值': 'hold_value',
                    '持股变化': 'hold_change',
                    '持股变动数值': 'change_value',
                    '持股变动比例': 'change_ratio'
                })

                # 数据清洗
                df_processed['symbol'] = df_processed['symbol'].astype(str).str.zfill(6)
                df_processed['report_date'] = report_date

                # 转换数值类型
                for col in ['fund_count', 'hold_count', 'hold_value', 'change_value', 'change_ratio']:
                    df_processed[col] = pd.to_numeric(df_processed[col], errors='coerce')

                # 选择需要的列
                columns = ['symbol', 'report_date', 'fund_count', 'hold_count',
                          'hold_value', 'hold_change', 'change_value', 'change_ratio']
                df_save = df_processed[columns].copy()

                # 删除空值
                df_save = df_save.dropna(subset=['symbol', 'report_date'])

                if df_save.empty:
                    logger.warning(f"⚠️  {report_date} 清理后无有效数据")
                    fail_count += 1
                    time.sleep(2)
                    continue

                # 先删除已有数据
                with self.engine.begin() as conn:
                    conn.execute(text("""
                        DELETE FROM finance_fund_holdings
                        WHERE report_date = :report_date
                    """), {"report_date": report_date})

                    # 批量插入
                    df_save.to_sql('finance_fund_holdings', conn, if_exists='append',
                                  index=False, method='multi', chunksize=1000)

                logger.info(f"✅ {report_date} 入库成功: {len(df_save)} 条记录")
                success_count += 1

                # 礼貌延迟
                time.sleep(3)

            except Exception as e:
                logger.error(f"❌ {report_date} 采集失败: {e}")
                fail_count += 1
                time.sleep(2)

        logger.info(f"\n📊 基金持仓采集完成: 成功 {success_count}/{len(target_quarters)} 个季度")

    def run(self):
        """执行所有资金流向数据采集"""
        logger.info("=" * 60)
        logger.info("🚀 资金流向数据采集任务启动")
        logger.info("=" * 60)

        # 初始化表
        self._init_tables()

        # 采集北向资金流向
        self.update_northbound_flow()

        # 采集基金持仓
        self.update_fund_holdings()

        logger.info("=" * 60)
        logger.info("🎉 资金流向数据采集完成！")
        logger.info("=" * 60)


if __name__ == "__main__":
    manager = CapitalFlowManager()
    manager.run()
