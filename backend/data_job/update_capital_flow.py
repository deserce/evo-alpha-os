import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from datetime import date, timedelta, datetime
from sqlalchemy import text
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

class CapitalFlowManager:
    def __init__(self):
        self.engine = get_engine()
        
    def _init_tables(self):
        """初始化资金流向相关表"""
        with self.engine.begin() as conn:
            # 1. 北向资金表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finance_northbound (
                    code VARCHAR(20),
                    trade_date DATE,
                    hold_count FLOAT, -- 持股数量
                    hold_value FLOAT, -- 持股市值
                    PRIMARY KEY (code, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_north_date ON finance_northbound (trade_date);
            """))
            
            # 2. 基金持仓表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finance_fund_holdings (
                    code VARCHAR(20),
                    report_date DATE,
                    hold_count FLOAT, -- 基金持股总数
                    fund_ratio FLOAT, -- 持股占流通股比例(%)
                    PRIMARY KEY (code, report_date)
                );
            """))

    # ==========================================
    # 模块 A: 北向资金 (智能增量版)
    # ==========================================
    def update_northbound(self):
        logger.info("🚀 [1/2] 开始检查北向资金(沪深港通)...")
        
        # 1. 确定起点
        try:
            with self.engine.connect() as conn:
                last_date = conn.execute(text("SELECT MAX(trade_date) FROM finance_northbound")).scalar()
            
            if last_date:
                start_date = last_date + timedelta(days=1)
            else:
                start_date = date(2023, 1, 1) # 默认回溯起点，可根据需求修改
        except Exception:
            start_date = date(2023, 1, 1)

        end_date = date.today() - timedelta(days=1) # 北向资金通常T+1才会完全公布
        
        if start_date > end_date:
            logger.info("✅ 北向资金已是最新。")
            return

        logger.info(f"📅 补全区间: {start_date} -> {end_date}")

        # 2. 按日期循环抓取
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y%m%d")
            
            # 周末简单跳过，减少请求（虽然接口也会返回空）
            if current_date.weekday() > 4: 
                current_date += timedelta(days=1)
                continue

            print(f"   📥 同步北向: {date_str} ...", end="\r")
            
            try:
                # 使用巨潮接口：按日期获取全市场数据
                # 这个接口比按个股循环快得多
                df = ak.stock_hsgt_hold_stock_cninfo(date=date_str)
                
                if not df.empty:
                    # 映射列名
                    # 巨潮常见列: 代码, 简称, 持股数量, 持股占比, 收盘价, 当日涨幅, 持股市值, 日期
                    df = df.rename(columns={
                        '代码': 'code', 
                        '持股数量': 'hold_count', 
                        '持股市值': 'hold_value',
                        '日期': 'trade_date'
                    })
                    
                    # 格式清洗
                    df['code'] = df['code'].astype(str).str.zfill(6)
                    # 这里的 date 可能是 datetime 对象或字符串，统一转
                    if 'trade_date' not in df.columns:
                        df['trade_date'] = current_date
                    else:
                        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                    # 只要需要的列
                    save_df = df[['code', 'trade_date', 'hold_count', 'hold_value']].dropna()
                    
                    # 批量入库
                    if not save_df.empty:
                        save_df.to_sql('finance_northbound', self.engine, if_exists='append', index=False, method='multi')
                        # logger.info(f"✅ {date_str} 入库 {len(save_df)} 条")
                
            except Exception as e:
                # 某些节假日接口会报错，忽略即可
                # logger.warning(f"跳过 {date_str}: {e}")
                pass
            
            current_date += timedelta(days=1)
            time.sleep(0.5) # 礼貌请求

        logger.info(f"\n✅ 北向资金同步完成！")

    # ==========================================
    # 模块 B: 基金持仓 (季度更新)
    # ==========================================
    def update_fund_holdings(self):
        logger.info("🚀 [2/2] 开始检查基金持仓数据...")
        
        # 1. 生成过去 8 个季度的时间点 (3-31, 6-30, 9-30, 12-31)
        target_quarters = []
        year = date.today().year
        for y in range(year, year - 3, -1):
            for md in ["1231", "0930", "0630", "0331"]:
                q_str = f"{y}{md}"
                if datetime.strptime(q_str, "%Y%m%d").date() <= date.today():
                    target_quarters.append(q_str)
        
        # 取最近 8 个季度即可
        target_quarters = target_quarters[:8]

        # 2. 准备流通股本数据 (用于计算持仓比例)
        # 注意：这里用最新的流通股本估算历史比例，虽然有误差，但作为参考足够
        logger.info("📋 获取最新流通股本用于计算持仓比...")
        try:
            df_spot = ak.stock_zh_a_spot_em()
            # 建立映射: code -> float_share
            # 注意处理列名差异
            spot_map = {}
            if '流通股本' in df_spot.columns:
                df_spot['code'] = df_spot['代码'].astype(str)
                df_spot['float_share'] = pd.to_numeric(df_spot['流通股本'], errors='coerce')
                spot_map = df_spot.set_index('code')['float_share'].to_dict()
        except Exception as e:
            logger.warning(f"无法获取行情数据，基金持仓将不包含比例数据: {e}")
            spot_map = {}

        # 3. 循环季度抓取
        for q_date in target_quarters:
            iso_date = f"{q_date[:4]}-{q_date[4:6]}-{q_date[6:]}"
            
            # 检查数据库是否已有该季度数据 (只要有一条就算有)
            try:
                with self.engine.connect() as conn:
                    exists = conn.execute(text(f"SELECT 1 FROM finance_fund_holdings WHERE report_date='{iso_date}' LIMIT 1")).scalar()
                if exists:
                    print(f"   ⏭️ {iso_date} 数据已存在，跳过。")
                    continue
            except: pass

            logger.info(f"   📥 下载基金持仓报告: {iso_date} ...")
            
            try:
                df = ak.stock_report_fund_hold(date=q_date)
                if df.empty:
                    continue
                
                # 映射列名
                # Akshare 列名: [序号, 股票代码, 股票简称, 基金持股总数, ...]
                col_map = {'股票代码': 'code', '基金持股总数': 'hold_count'}
                df = df.rename(columns=col_map)
                
                df['code'] = df['code'].astype(str).str.zfill(6)
                df['report_date'] = iso_date
                df['hold_count'] = pd.to_numeric(df['hold_count'], errors='coerce')

                # 计算持仓占比
                results = []
                for _, row in df.iterrows():
                    code = row['code']
                    h_count = row['hold_count']
                    
                    ratio = 0.0
                    if code in spot_map and spot_map[code] > 0:
                        ratio = (h_count / spot_map[code]) * 100
                    
                    results.append({
                        'code': code,
                        'report_date': iso_date,
                        'hold_count': h_count,
                        'fund_ratio': round(ratio, 4)
                    })
                
                # 入库
                save_df = pd.DataFrame(results)
                if not save_df.empty:
                    save_df.to_sql('finance_fund_holdings', self.engine, if_exists='append', index=False, method='multi')
                    logger.info(f"      ✅ 入库成功: {len(save_df)} 条记录")

                time.sleep(2) # 季度接口数据量大，多歇会

            except Exception as e:
                logger.error(f"      ❌ 获取 {iso_date} 失败: {e}")

    def run(self):
        self._init_tables()
        self.update_northbound()
        self.update_fund_holdings()
        logger.info("🎉 资金流向数据更新完成！")

if __name__ == "__main__":
    CapitalFlowManager().run()