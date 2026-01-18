# 获取k线数据
# backend/data_job/update_stock_kline.py

import sys
import os
import time
import datetime
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
from datetime import timedelta
import ssl

# ================= 🚑 网络急救包 =================
# 强制关闭系统代理 (解决 Mac 开 VPN 导致无法连接国内接口的问题)
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]

# 忽略 SSL 证书验证
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

class StockKlineManager:
    def __init__(self):
        self.engine = get_engine()
        self.table_name = "stock_daily_prices"

    def _init_table(self):
        """初始化 daily_prices 表结构"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ [Cloud] 创建表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        symbol VARCHAR(20),
                        trade_date DATE,
                        open FLOAT,
                        close FLOAT,
                        high FLOAT,
                        low FLOAT,
                        volume FLOAT,
                        amount FLOAT,
                        pct_chg FLOAT,
                        turnover_rate FLOAT,
                        PRIMARY KEY (symbol, trade_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_kline_symbol ON {self.table_name} (symbol);"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_kline_date ON {self.table_name} (trade_date);"))

    def get_stock_list(self):
        """获取股票名单：优先查云端数据库"""
        logger.info("📋 正在获取待更新的股票名单...")
        # 1. 尝试从云端【板块映射表】读取
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT DISTINCT symbol, name FROM stock_sector_map"), conn)
            if not df.empty:
                logger.info(f"✅ [云端] 从 stock_sector_map 获取到 {len(df)} 只股票")
                return df[df['symbol'].astype(str).str.match(r'^(00|30|60|68)')].to_dict('records')
        except Exception: pass

        # 2. 尝试从云端【基础信息表】读取
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT symbol, name FROM stock_info"), conn)
            if not df.empty:
                logger.info(f"✅ [云端] 从 stock_info 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception: pass

        # 3. 最后联网获取
        for i in range(3):
            try:
                df = ak.stock_zh_a_spot_em()
                df = df[['代码', '名称']].rename(columns={'代码':'symbol', '名称':'name'})
                return df[df['symbol'].astype(str).str.match(r'^(00|30|60|68)')].to_dict('records')
            except Exception:
                time.sleep(2)
        return []

    def get_last_dates(self):
        """获取增量更新进度"""
        try:
            query = text(f"SELECT symbol, MAX(trade_date) as last_date FROM {self.table_name} GROUP BY symbol")
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            if df.empty: return {}
            return dict(zip(df['symbol'], pd.to_datetime(df['last_date']).dt.date))
        except:
            return {}

    def _bulk_save_kline(self, df_list):
        """内部辅助：批量存入云端，平衡效率与 Units 消耗"""
        if not df_list: return
        try:
            final_df = pd.concat(df_list, ignore_index=True)
            with self.engine.begin() as conn:
                # 针对 CockroachDB，method='multi' 配合合理的 chunksize 是最高效的写入方式
                final_df.to_sql(self.table_name, conn, if_exists='append', index=False, method='multi', chunksize=1000)
        except Exception as e:
            logger.error(f"❌ 批量写入云端失败: {e}")

    def run(self):
        """主执行入口"""
        logger.info("🚀 [K线] 启动个股行情云端同步...")
        self._init_table()

        stock_list = self.get_stock_list()
        if not stock_list: return

        existing_records = self.get_last_dates()
        DEFAULT_START_DATE = "20230101"
        today = datetime.date.today()
        total = len(stock_list)

        collected_data = []
        BATCH_SIZE = 500 # 💡 关键：每 20 只股票合并为一个事务写入云端，大幅节省 Units

        logger.info(f"📊 准备处理 {total} 只股票...")

        for i, stock in enumerate(stock_list):
            code = stock['symbol']
            name = stock['name']
            
            last_date = existing_records.get(code)
            if last_date:
                if last_date >= today: continue
                start_date_str = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            else:
                start_date_str = DEFAULT_START_DATE

            end_date_str = today.strftime("%Y%m%d")
            if start_date_str > end_date_str: continue

            if i % 10 == 0:
                print(f"[{i+1}/{total}] 同步进度: {code} {name} ...", end="\r")

            try:
                df = ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start_date_str, 
                    end_date=end_date_str, adjust="qfq"
                )
                
                if df is None or df.empty: continue

                rename_dict = {
                    '日期': 'trade_date', '开盘': 'open', '收盘': 'close', 
                    '最高': 'high', '最低': 'low', '成交量': 'volume', 
                    '成交额': 'amount', '涨跌幅': 'pct_chg', '换手率': 'turnover_rate'
                }
                df = df.rename(columns=rename_dict)
                df['symbol'] = code
                
                for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
                    if col not in df.columns: df[col] = None
                
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                save_df = df[['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']]
                
                # 💡 放入待写入列表，暂不提交事务
                collected_data.append(save_df)

                # 💡 达到 BATCH_SIZE 时，执行一次批量写入
                if len(collected_data) >= BATCH_SIZE:
                    self._bulk_save_kline(collected_data)
                    collected_data = [] # 清空缓存

                time.sleep(0.01) # 云端环境下稍微给 CPU 留点余地

            except Exception:
                time.sleep(0.2)

        # 处理剩余没满 BATCH_SIZE 的数据
        if collected_data:
            self._bulk_save_kline(collected_data)

        logger.info(f"\n✅ 个股 K 线云端同步完成！")

if __name__ == "__main__":
    manager = StockKlineManager()
    manager.run()