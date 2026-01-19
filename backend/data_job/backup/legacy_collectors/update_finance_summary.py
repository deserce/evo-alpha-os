import sys
import os
import time
import random
import logging
import pandas as pd
import akshare as ak
from datetime import date
from sqlalchemy import text, inspect
import ssl

# ================= 🚑 网络急救包 (新增部分) =================
# 1. 强制关闭系统代理 (解决 Mac 开 VPN 导致无法连接国内接口的问题)
# 这一步非常关键！防止 requests 库自动读取你的梯子配置
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]

# 2. 忽略 SSL 证书验证 (解决 HTTPSConnectionPool 报错)
ssl._create_default_https_context = ssl._create_unverified_context
# ==========================================================
# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_engine

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FinanceSummaryManager:
    def __init__(self):
        self.engine = get_engine()
        self.table_name = "stock_finance_summary"

    def _init_table(self):
        """初始化表结构"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建表 {self.table_name}...")
            with self.engine.begin() as conn:
                # 创建表
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        code VARCHAR(20),
                        name VARCHAR(50),
                        report_date DATE,
                        eps FLOAT,               -- 每股收益
                        net_profit_up FLOAT,     -- 净利润同比增长(%)
                        revenue_up FLOAT,        -- 营收同比增长(%)
                        roe FLOAT,               -- 净资产收益率(%)
                        net_margin FLOAT,        -- 销售净利率(%)
                        PRIMARY KEY (code, report_date)
                    );
                """))
                # 创建索引（分开执行）
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_finance_code ON {self.table_name} (code);"))
                except:
                    pass
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_finance_date ON {self.table_name} (report_date);"))
                except:
                    pass
        else:
            logger.info(f"✅ 表 {self.table_name} 已存在，准备检查更新...")

    def check_date_exists(self, report_date_str: str) -> bool:
        """检查某个季度的数据是否已入库"""
        # report_date_str 格式 "20240331" -> "2024-03-31"
        fmt_date = pd.to_datetime(report_date_str).strftime('%Y-%m-%d')
        try:
            with self.engine.connect() as conn:
                query = text(f"SELECT 1 FROM {self.table_name} WHERE report_date = :dt LIMIT 1")
                result = conn.execute(query, {"dt": fmt_date}).scalar()
                return result is not None
        except Exception:
            return False

    def fetch_and_save(self, target_date: str) -> bool:
        """核心抓取逻辑"""
        try:
            # ak.stock_yjbb_em 接口：获取某季度全市场业绩报表
            df = ak.stock_yjbb_em(date=target_date)
            
            if df is None or df.empty:
                return False

            # 1. 映射列名
            rename_map = {
                '股票代码': 'code', '股票简称': 'name',
                '每股收益': 'eps', '净利润-同比增长': 'net_profit_up',
                '营业总收入-同比增长': 'revenue_up', '净资产收益率': 'roe',
                '销售毛利率': 'net_margin'
            }
            df = df.rename(columns=rename_map)
            
            # 2. 补全缺失列
            required_cols = ['code', 'name', 'eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
            for col in required_cols:
                if col not in df.columns:
                    df[col] = 0

            # 3. 清洗数据
            df_save = df[required_cols].copy()
            df_save['report_date'] = pd.to_datetime(target_date).date()
            df_save['code'] = df_save['code'].astype(str).str.zfill(6)
            
            # 处理非数值字符 ('-', None 等)
            df_save = df_save.replace(['-', ''], 0)
            
            num_cols = ['eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
            for col in num_cols:
                df_save[col] = pd.to_numeric(df_save[col], errors='coerce').fillna(0)

            # 4. 入库 (使用 append + 主键冲突忽略或覆盖)
            # 由于这是全量季度数据，直接 append 会冲突，建议用临时表 + Upsert，或者 delete + insert
            # 简单起见，这里演示 delete + insert 模式 (按日期删)
            fmt_date = pd.to_datetime(target_date).strftime('%Y-%m-%d')
            
            with self.engine.begin() as conn:
                # 先删除当天已有的（防止重跑时重复）
                conn.execute(text(f"DELETE FROM {self.table_name} WHERE report_date = :dt"), {"dt": fmt_date})
                # 再插入新的（分批插入，避免 SQLite 变量限制）
                # SQLite 默认限制 999 个变量，所以使用 chunksize=100
                df_save.to_sql(self.table_name, conn, if_exists='append', index=False, method='multi', chunksize=100)
            
            return True

        except Exception as e:
            logger.error(f"抓取 {target_date} 异常: {e}")
            raise e

    def run(self):
        logger.info("📈 启动财务业绩报表同步...")
        self._init_table()

        # 动态生成最近 5 年的季度列表
        curr_year = date.today().year
        years = range(curr_year, curr_year - 6, -1) # 回溯5-6年
        quarters = ["1231", "0930", "0630", "0331"]
        
        # 生成任务列表 (20250331, 20241231...)
        date_tasks = []
        for y in years:
            for q in quarters:
                d_str = f"{y}{q}"
                # 不抓未来的日期
                if d_str <= date.today().strftime("%Y%m%d"):
                    date_tasks.append(d_str)

        total = len(date_tasks)
        
        for i, target_date in enumerate(date_tasks):
            # 1. 断点续传
            if self.check_date_exists(target_date):
                print(f"[{i+1}/{total}] ⏩ {target_date} 已存在，跳过...", end="\r")
                continue

            # 2. 执行抓取 (含重试)
            max_retries = 3
            success = False
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"[{i+1}/{total}] ⏳ 正在抓取 {target_date} (Try {attempt+1})...")
                    has_data = self.fetch_and_save(target_date)
                    
                    if has_data:
                        logger.info(f"   ✅ {target_date} 入库成功")
                        success = True
                    else:
                        logger.warning(f"   ⚠️ {target_date} 无数据 (可能是财报未出)")
                        success = True # 这种也是逻辑上的成功
                    
                    # 成功后休眠，财报接口比较敏感
                    time.sleep(random.uniform(2, 4))
                    break
                    
                except Exception:
                    time.sleep(5 * (attempt + 1))

            if not success:
                logger.error(f"   ❌ {target_date} 多次重试失败，跳过。")

        logger.info("🎉 财务数据同步完成！")

if __name__ == "__main__":
    FinanceSummaryManager().run()