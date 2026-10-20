"""
EvoAlpha OS - 股票板块映射数据采集器
采集股票列表和股票-板块映射关系
"""

import time
import random
import pandas as pd
import akshare as ak
import requests
from sqlalchemy import text, inspect
from tqdm import tqdm
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_active_engines
from app.core.config import settings

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class StockSectorListCollector(BaseCollector):
    """股票板块映射数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="stock_sector_list",
            request_timeout=30,
            request_delay=0.05,
            max_retries=5
        )
        self.active_engines = get_active_engines()
        self._http = requests.Session()
        self._http.trust_env = False
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://emweb.securities.eastmoney.com/",
        }

    def _ensure_column(self, conn, table_name: str, column_name: str, column_type: str):
        """确保 SQLite/CockroachDB 兼容地补齐表字段。"""
        dialect = conn.dialect.name
        if dialect == "sqlite":
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            existing_columns = {row[1] for row in rows}
        else:
            rows = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name
            """), {"table_name": table_name}).fetchall()
            existing_columns = {row[0] for row in rows}

        if column_name not in existing_columns:
            conn.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            ))

    def _init_tables(self):
        """为所有激活的引擎初始化表结构"""
        for name, engine in self.active_engines:
            inspector = inspect(engine)
            try:
                with engine.begin() as conn:
                    if not inspector.has_table("stock_info"):
                        logger.info(f"🛠️ [{name}] 创建表 stock_info...")
                        conn.execute(text("""
                            CREATE TABLE stock_info (
                                symbol VARCHAR(20) PRIMARY KEY,
                                name VARCHAR(100),
                                industry VARCHAR(100),
                                market_cap DECIMAL(20,2),
                                pe DECIMAL(10,2),
                                pb DECIMAL(10,2),
                                high_52w DECIMAL(10,2),
                                low_52w DECIMAL(10,2),
                                list_date DATE,
                                market VARCHAR(10)
                            );
                        """))
                    else:
                        self._ensure_column(conn, "stock_info", "industry", "VARCHAR(100)")
                        self._ensure_column(conn, "stock_info", "market_cap", "DECIMAL(20,2)")
                        self._ensure_column(conn, "stock_info", "pe", "DECIMAL(10,2)")
                        self._ensure_column(conn, "stock_info", "pb", "DECIMAL(10,2)")
                        self._ensure_column(conn, "stock_info", "high_52w", "DECIMAL(10,2)")
                        self._ensure_column(conn, "stock_info", "low_52w", "DECIMAL(10,2)")
                        self._ensure_column(conn, "stock_info", "list_date", "DATE")
                        self._ensure_column(conn, "stock_info", "market", "VARCHAR(10)")

                    if not inspector.has_table("stock_sector_map"):
                        logger.info(f"🛠️ [{name}] 创建表 stock_sector_map...")
                        conn.execute(text("""
                            CREATE TABLE stock_sector_map (
                                symbol VARCHAR(20),
                                name VARCHAR(100),
                                sector_name VARCHAR(100),
                                sector_type VARCHAR(50),
                                sector_code VARCHAR(50),
                                PRIMARY KEY (sector_name, symbol)
                            );
                        """))
                        if name == "cloud":
                            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_map_symbol ON stock_sector_map (symbol);"))
                    else:
                        self._ensure_column(conn, "stock_sector_map", "name", "VARCHAR(100)")
                        self._ensure_column(conn, "stock_sector_map", "sector_type", "VARCHAR(50)")
                        self._ensure_column(conn, "stock_sector_map", "sector_code", "VARCHAR(50)")

                    if not inspector.has_table("sector_list"):
                        conn.execute(text("""
                            CREATE TABLE sector_list (
                                sector_name VARCHAR(100) PRIMARY KEY,
                                sector_type VARCHAR(50),
                                sector_code VARCHAR(50),
                                source VARCHAR(50),
                                updated_at VARCHAR(30)
                            );
                        """))
                    else:
                        self._ensure_column(conn, "sector_list", "sector_type", "VARCHAR(50)")
                        self._ensure_column(conn, "sector_list", "sector_code", "VARCHAR(50)")
                        self._ensure_column(conn, "sector_list", "source", "VARCHAR(50)")
                        self._ensure_column(conn, "sector_list", "updated_at", "VARCHAR(30)")
            except Exception as e:
                logger.error(f"❌ [{name}] 初始化表结构失败: {e}")

    def _upsert_stock_list(self, conn, df: pd.DataFrame):
        """只更新 symbol/name，保留 stock_info 上已有估值、行业等字段。"""
        records = df[['symbol', 'name']].to_dict('records')
        if not records:
            return

        conn.execute(text("""
            INSERT INTO stock_info (symbol, name)
            VALUES (:symbol, :name)
            ON CONFLICT (symbol) DO UPDATE SET name = excluded.name
        """), records)

    def _table_count(self, engine, table_name: str) -> int:
        """读取表行数；失败时按 0 处理，便于采集结果判定。"""
        try:
            with engine.connect() as conn:
                return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
        except Exception:
            return 0

    def update_stock_list(self):
        """获取全市场股票列表"""
        logger.info(f"📋 [1/2] 正在更新股票列表 [模式: {settings.APP_ENV}]")
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.stock_zh_a_spot_em)
            if df is None or df.empty:
                logger.warning("⚠️  实时行情股票列表失败，回退到 ak.stock_info_a_code_name")
                df = self._retry_call(ak.stock_info_a_code_name)
                if df is None or df.empty:
                    logger.error("❌ 股票列表主源和备用源均失败")
                    return False

            df = df.rename(columns={'代码': 'symbol', '名称': 'name', 'code': 'symbol'})
            df['symbol'] = df['symbol'].astype(str).str.zfill(6)
            df = df[['symbol', 'name']].drop_duplicates(subset=['symbol'])

            for name, engine in self.active_engines:
                with engine.begin() as conn:
                    self._upsert_stock_list(conn, df)
                logger.info(f"✅ [{name}] 写入完成。")
            return True

        except Exception as e:
            logger.error(f"❌ 更新股票列表失败: {e}")
            return False

    def _fetch_and_save_sector_category(self, name_func, cons_func, type_label):
        """内部通用: 抓取并保存板块数据"""
        logger.info(f"🚀 正在处理 [{type_label}] 板块数据...")

        try:
            # 使用基类的重试机制
            df_list = self._retry_call(name_func)
            if df_list is None:
                return
            names = df_list['板块名称'].tolist()
            total = len(names)

            collected_data = []
            for i, name in enumerate(names):
                if i % 10 == 0:
                    print(f"   [{i+1}/{total}] 采集进度: {name} ...", end="\r")

                try:
                    cons = cons_func(symbol=name)
                    if cons is None or cons.empty:
                        continue

                    cons = cons.rename(columns={'代码': 'symbol', '名称': 'name'})
                    cons['symbol'] = cons['symbol'].astype(str).str.zfill(6)
                    cons['sector_name'] = name
                    cons['sector_type'] = type_label

                    collected_data.append(cons[['symbol', 'name', 'sector_name', 'sector_type']])

                    if len(collected_data) >= 50:
                        self._bulk_save_active(collected_data)
                        collected_data = []
                    time.sleep(0.05)
                except Exception:
                    continue

            if collected_data:
                self._bulk_save_active(collected_data)
            print()
            logger.info(f"✅ [{type_label}] 数据处理完毕。")

        except Exception as e:
            logger.error(f"❌ 获取 {type_label} 列表严重失败: {e}")

    def _bulk_save_active(self, df_list):
        """核心保存函数: 分发数据到所有激活的引擎"""
        if not df_list:
            return
        final_df = pd.concat(df_list, ignore_index=True)
        if "sector_code" not in final_df.columns:
            final_df["sector_code"] = None
        final_df = final_df.drop_duplicates(subset=['sector_name', 'symbol'])

        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    method = 'multi' if name == "cloud" else None
                    final_df.to_sql('stock_sector_map', conn, if_exists='append', index=False, method=method, chunksize=1000)
            except Exception as e:
                logger.error(f"❌ [{name}] 批量写入失败: {e}")

    def update_sectors(self):
        """更新板块映射"""
        logger.info("🧩 [2/2] 开始更新板块映射...")

        backups = {}
        for name, engine in self.active_engines:
            try:
                backups[name] = pd.read_sql("SELECT * FROM stock_sector_map", engine)
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM stock_sector_map"))
                    logger.info(f"🧹 [{name}] 历史映射已清理")
            except Exception:
                backups[name] = pd.DataFrame()
                pass

        self._fetch_and_save_sector_category(ak.stock_board_industry_name_em, ak.stock_board_industry_cons_em, 'Industry')
        self._fetch_and_save_sector_category(ak.stock_board_concept_name_em, ak.stock_board_concept_cons_em, 'Concept')

        # 东方财富/AkShare板块接口偶发整段不可用时，优先用个股所属板块接口重建真实行业映射。
        for name, engine in self.active_engines:
            if self._table_count(engine, "stock_sector_map") == 0:
                if self._populate_industry_from_eastmoney_plates(engine):
                    continue

                backup_df = backups.get(name, pd.DataFrame())
                if not backup_df.empty:
                    logger.warning(f"⚠️  [{name}] 新板块映射采集失败，恢复旧映射 {len(backup_df)} 条")
                    with engine.begin() as conn:
                        backup_df.to_sql(
                            "stock_sector_map",
                            conn,
                            if_exists="append",
                            index=False,
                            chunksize=1000,
                        )
                else:
                    logger.warning(f"⚠️  [{name}] 板块映射为空，启用 mootdx 行业兜底")
                    self._populate_industry_from_mootdx(engine)

    def _fetch_stock_eastmoney_plates(self, stock: dict) -> list[dict]:
        """按个股获取所属行业，避开不稳定的板块总列表接口。"""
        symbol = str(stock["symbol"]).zfill(6)
        if symbol.startswith(("4", "8")):
            sec_code = f"{symbol}.BJ"
        elif symbol.startswith("6"):
            sec_code = f"{symbol}.SH"
        else:
            sec_code = f"{symbol}.SZ"

        try:
            response = self._http.get(
                "https://datacenter.eastmoney.com/securities/api/data/get",
                params={
                    "type": "RPT_F10_CORETHEME_BOARDTYPE",
                    "sty": (
                        "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_CODE,"
                        "BOARD_NAME,IS_PRECISE,BOARD_RANK,BOARD_TYPE"
                    ),
                    "filter": f'(SECUCODE="{sec_code}")',
                    "p": 1,
                    "ps": "",
                    "sr": 1,
                    "st": "BOARD_RANK",
                    "source": "HSF10",
                    "client": "PC",
                    "v": int(time.time() * 1000),
                },
                headers=self._headers,
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        if not payload.get("success"):
            return []

        rows = []
        for row in payload.get("result", {}).get("data", []) or []:
            if (row.get("BOARD_TYPE") or "") != "行业":
                continue
            sector_name = str(row.get("BOARD_NAME", "")).strip()
            board_code = str(row.get("BOARD_CODE", "")).strip()
            sector_code = f"BK{board_code.zfill(4)[-4:]}" if board_code else ""
            if not sector_name or not sector_code:
                continue
            rows.append({
                "symbol": symbol,
                "name": stock["name"],
                "sector_name": sector_name,
                "sector_type": "Industry",
                "sector_code": sector_code,
            })
        return rows

    def _save_sector_list_from_map(self, engine, df_map: pd.DataFrame):
        """从映射表同步 sector_list，供板块K线采集器拿到 sector_code。"""
        if df_map.empty:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df_sector = (
            df_map[["sector_name", "sector_type", "sector_code"]]
            .dropna(subset=["sector_name", "sector_code"])
            .drop_duplicates("sector_name", keep="last")
        )
        df_sector["source"] = "eastmoney_stock_plate"
        df_sector["updated_at"] = now

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sector_list WHERE sector_type = 'Industry'"))
            df_sector.to_sql("sector_list", conn, if_exists="append", index=False, chunksize=1000)

    def _populate_industry_from_eastmoney_plates(self, engine) -> bool:
        """按个股所属行业重建 Eastmoney 行业映射。"""
        try:
            df_stocks = pd.read_sql(
                "SELECT symbol, name FROM stock_info WHERE symbol NOT LIKE '9%'",
                engine,
            )
        except Exception as e:
            logger.warning(f"⚠️ 读取 stock_info 失败，无法使用 Eastmoney 个股行业兜底: {e}")
            return False

        if df_stocks.empty:
            return False

        logger.warning("⚠️ 板块总列表不可用，改用 Eastmoney 个股所属行业接口重建映射")
        stocks = df_stocks.to_dict("records")
        collected = []
        max_workers = 12

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._fetch_stock_eastmoney_plates, stock) for stock in stocks]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="  Eastmoney行业映射",
                unit="只",
            ):
                rows = future.result()
                if rows:
                    collected.extend(rows)

        if not collected:
            return False

        df_save = pd.DataFrame(collected)
        df_save = df_save.drop_duplicates(subset=["sector_name", "symbol"])
        with engine.begin() as conn:
            df_save.to_sql(
                "stock_sector_map",
                conn,
                if_exists="append",
                index=False,
                chunksize=1000,
            )
        self._save_sector_list_from_map(engine, df_save)

        logger.info(
            "✅ Eastmoney 个股行业映射完成: "
            f"{len(df_save)} 条，{df_save['sector_name'].nunique()} 个行业"
        )
        return True

    def _populate_industry_from_mootdx(self, engine):
        """用 mootdx 财务 industry 编号兜底生成 Industry 映射。"""
        try:
            from mootdx.quotes import Quotes

            api = Quotes.factory(market='std', server='218.75.126.9:7709', timeout=15)
            if api.client is None:
                logger.error("❌ mootdx 行业兜底连接失败")
                return

            df_stocks = pd.read_sql(
                "SELECT symbol, name FROM stock_info WHERE symbol NOT LIKE '9%'",
                engine
            )
            if df_stocks.empty:
                logger.error("❌ stock_info 为空，无法生成行业兜底映射")
                return

            collected = []
            for _, stock in tqdm(df_stocks.iterrows(), total=len(df_stocks), desc="  mootdx行业兜底", unit="只"):
                code = stock['symbol']
                market = 1 if code.startswith('6') else 0
                sector_name = "未分类"

                try:
                    finance = api.finance(code, market=market)
                    if finance is not None and not finance.empty:
                        industry = finance.iloc[0].get('industry')
                        if pd.notna(industry):
                            sector_name = f"TDX行业{int(industry)}"
                except Exception:
                    pass

                collected.append({
                    "symbol": code,
                    "name": stock['name'],
                    "sector_name": sector_name,
                    "sector_type": "Industry",
                })
                time.sleep(0.01)

            if collected:
                df_save = pd.DataFrame(collected)
                df_save = df_save.drop_duplicates(subset=['sector_name', 'symbol'])
                with engine.begin() as conn:
                    df_save.to_sql(
                        'stock_sector_map',
                        conn,
                        if_exists='append',
                        index=False,
                        chunksize=1000,
                    )
                logger.info(f"✅ mootdx 行业兜底完成: {len(df_save)} 条")
        except Exception as e:
            logger.error(f"❌ mootdx 行业兜底失败: {e}")

    def update_stock_info_industry(self):
        """同步更新 stock_info 表的 industry 字段"""
        logger.info("🔄 同步更新 stock_info 表的 industry 字段...")

        for name, engine in self.active_engines:
            try:
                # 从 stock_sector_map 获取每只股票的行业，确定性地选择最细一级行业。
                query = """
                    SELECT symbol, sector_name
                    FROM stock_sector_map
                    WHERE sector_type = 'Industry'
                      AND sector_name IS NOT NULL
                      AND sector_name != ''
                    GROUP BY symbol, sector_name
                """
                df_sector = pd.read_sql(query, engine)

                if not df_sector.empty:
                    df_sector["rank"] = df_sector["sector_name"].astype(str).map(
                        self._industry_name_rank
                    )
                    df_sector = (
                        df_sector
                        .sort_values(["symbol", "rank", "sector_name"])
                        .drop_duplicates("symbol", keep="first")
                    )

                    update_count = 0
                    with engine.begin() as conn:
                        for _, row in df_sector.iterrows():
                            conn.execute(text("""
                                UPDATE stock_info
                                SET industry = :industry
                                WHERE symbol = :symbol
                            """), {"industry": row['sector_name'], "symbol": row['symbol']})
                            update_count += 1

                    logger.info(f"✅ [{name}] 已更新 {update_count} 只股票的行业信息")
            except Exception as e:
                logger.error(f"❌ [{name}] 更新 stock_info.industry 失败: {e}")

    @staticmethod
    def _industry_name_rank(sector_name: str) -> int:
        """行业展示字段优先用更细分的三级/二级行业。"""
        if "Ⅲ" in sector_name:
            return 0
        if "Ⅱ" in sector_name:
            return 1
        return 2

    def run(self):
        """统一入口"""
        self.log_collection_start()

        if not self.active_engines:
            logger.error("🚫 无活跃数据库引擎，请检查 APP_ENV 设置")
            self.log_collection_end(False, "无数据库引擎")
            return

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_tables()
        stock_ok = self.update_stock_list()
        self.update_sectors()

        # 同步更新 stock_info 表的 industry 字段
        self.update_stock_info_industry()

        total_stocks = sum(self._table_count(engine, "stock_info") for _, engine in self.active_engines)
        total_maps = sum(self._table_count(engine, "stock_sector_map") for _, engine in self.active_engines)
        success = stock_ok and total_stocks > 0 and total_maps > 0

        if success:
            logger.info(f"🎉 任务圆满成功 [模式: {settings.APP_ENV}]")
            self.log_collection_end(True, f"股票 {total_stocks}，映射 {total_maps}")
        else:
            message = f"股票 {total_stocks}，映射 {total_maps}"
            logger.error(f"❌ 任务未完成: {message}")
            self.log_collection_end(False, message)


if __name__ == "__main__":
    collector = StockSectorListCollector()
    collector.run()
