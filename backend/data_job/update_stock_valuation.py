import sys
import os
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
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

from app.core.database import get_engine

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ValuationManager:
    def __init__(self):
        self.engine = get_engine()
        # 建议改名：明确这是一张带有历史记录的表
        self.table_name = "stock_valuation_daily"

    def _init_table(self):
        """确保表结构支持历史数据 (联合主键)"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 初始化历史估值表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        code VARCHAR(20),
                        name VARCHAR(50),
                        trade_date DATE,
                        price FLOAT,
                        pe_ttm FLOAT,            -- 市盈率(动态)
                        pb FLOAT,                -- 市净率
                        total_mv FLOAT,          -- 总市值
                        circ_mv FLOAT,           -- 流通市值
                        pct_chg FLOAT,           -- 涨跌幅
                        turnover FLOAT,          -- 换手率
                        volume_ratio FLOAT,      -- 量比
                        PRIMARY KEY (code, trade_date)
                    );
                    -- 创建索引方便查询某只股票的历史，或某天的全市场
                    CREATE INDEX IF NOT EXISTS idx_val_code ON {self.table_name} (code);
                    CREATE INDEX IF NOT EXISTS idx_val_date ON {self.table_name} (trade_date);
                """))

    def fetch_spot_data(self) -> pd.DataFrame:
        """获取全市场当天实时数据"""
        try:
            # 依然使用东财实时接口，作为当天的收盘快照
            df = ak.stock_zh_a_spot_em()
            return df
        except Exception as e:
            logger.error(f"接口调用失败: {e}")
            return pd.DataFrame()

    def process_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty: return df

        # 1. 字段映射
        rename_map = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_chg", "总市值": "total_mv", "流通市值": "circ_mv",
            "市盈率-动态": "pe_ttm", "市净率": "pb",
            "换手率": "turnover", "量比": "volume_ratio"
        }
        df = df.rename(columns=rename_map)

        # 2. 筛选列
        cols = list(rename_map.values())
        # 容错处理：确保列都存在
        for c in cols:
            if c not in df.columns: df[c] = 0
        df = df[cols]

        # 3. 清洗与类型
        df['code'] = df['code'].astype(str).str.zfill(6)
        # 增加日期列（这就是历史数据的关键）
        df['trade_date'] = date.today()

        # 数值清洗
        numeric_cols = ["price", "pe_ttm", "pb", "total_mv", "circ_mv", "pct_chg", "turnover", "volume_ratio"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        return df

    def save_to_db(self, df: pd.DataFrame):
        if df.empty: return

        current_date = date.today()
        date_str = current_date.strftime('%Y-%m-%d')
        
        logger.info(f"💾 正在存储 {date_str} 的估值数据 ({len(df)}条)...")
        
        with self.engine.begin() as conn:
            # 1. 幂等性删除：如果今天已经跑过一次，先删掉今天的，避免主键冲突
            conn.execute(text(f"DELETE FROM {self.table_name} WHERE trade_date = :dt"), {"dt": date_str})
            
            # 2. 追加插入 (Append)
            df.to_sql(self.table_name, conn, if_exists='append', index=False)
            
        logger.info(f"✅ {date_str} 估值数据入库成功！")

    def run(self):
        logger.info("🚀 启动 [估值数据存盘] 任务...")
        self._init_table()
        
        df_raw = self.fetch_spot_data()
        if not df_raw.empty:
            df_clean = self.process_data(df_raw)
            self.save_to_db(df_clean)
        else:
            logger.error("❌ 无数据获取")

if __name__ == "__main__":
    ValuationManager().run()