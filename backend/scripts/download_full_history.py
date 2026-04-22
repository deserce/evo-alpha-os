"""
完整历史K线数据下载器
使用 pytdx 从指定日期开始下载完整历史数据
支持: A股、板块、ETF、指数
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm

# 添加 backend 到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.core.tdx_adapter import TdxAdapter
import akshare as ak
import pandas as pd

DB_PATH = "data/local_quant.db"


# ============================================
# 表初始化
# ============================================

def init_stock_daily_prices_table(conn):
    """初始化 stock_daily_prices 表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_prices (
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
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_symbol ON stock_daily_prices (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_date ON stock_daily_prices (trade_date)")
    conn.commit()


def init_sector_daily_prices_table(conn):
    """初始化 sector_daily_prices 表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_daily_prices (
            sector_name TEXT,
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            PRIMARY KEY (sector_name, trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sector_date ON sector_daily_prices (trade_date)")
    conn.commit()


def init_etf_daily_prices_table(conn):
    """初始化 etf_daily_prices 表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily_prices (
            symbol VARCHAR(20),
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON etf_daily_prices (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON etf_daily_prices (trade_date)")
    conn.commit()


def init_index_daily_prices_table(conn):
    """初始化 index_daily_prices 表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily_prices (
            symbol VARCHAR(20),
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_symbol ON index_daily_prices (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_date ON index_daily_prices (trade_date)")
    conn.commit()


# ============================================
# 数据获取
# ============================================

def get_stock_list():
    """获取A股股票列表"""
    try:
        df = ak.stock_info_a_code_name()
        df['code'] = df['code'].astype(str).str.zfill(6)
        return df['code'].tolist()
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return []


def get_sector_list():
    """获取板块列表"""
    sectors = []

    # 获取行业板块
    try:
        df_hy = ak.stock_board_industry_name_em()
        if not df_hy.empty:
            sectors.extend([(name, 'industry') for name in df_hy['板块名称'].tolist()])
    except Exception as e:
        print(f"获取行业板块失败: {e}")

    # 获取概念板块
    try:
        df_gn = ak.stock_board_concept_name_em()
        if not df_gn.empty:
            sectors.extend([(name, 'concept') for name in df_gn['板块名称'].tolist()])
    except Exception as e:
        print(f"获取概念板块失败: {e}")

    return sectors


def get_etf_list():
    """获取ETF列表"""
    try:
        df = ak.fund_etf_spot_em()
        if not df.empty:
            df['code'] = df['代码'].astype(str).str.zfill(6)
            return df['code'].tolist()
    except Exception as e:
        print(f"获取ETF列表失败: {e}")
        return []


def get_index_list():
    """获取指数列表"""
    indices = []

    # 主要指数
    major_indices = [
        ('000001', '上证指数'),
        ('399001', '深证成指'),
        ('399006', '创业板指'),
        ('000300', '沪深300'),
        ('000016', '上证50'),
        ('399905', '中证500'),
        ('000688', '科创50'),
        ('000905', '中证1000'),
    ]
    indices.extend([(code, name) for code, name in major_indices])

    return indices


# ============================================
# K线下载
# ============================================

def download_and_clean_kline(df, symbol, today_str):
    """
    清洗K线数据
    """
    if df is None or df.empty:
        return None

    # 过滤未来日期（处理 datetime.date 类型）
    today = pd.Timestamp.now().date()
    df = df[df['trade_date'] <= today].copy()

    if df.empty:
        return None

    # 数据清洗和验证
    df = df[(df['high'] >= df['low']) &
            (df['close'] >= df['low']) &
            (df['close'] <= df['high']) &
            (df['open'] > 0) &
            (df['high'] > 0) &
            (df['low'] > 0) &
            (df['close'] > 0)].copy()

    if df.empty:
        return None

    # 确保 trade_date 是日期格式字符串
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

    # 确保 symbol 列存在
    df['symbol'] = symbol

    # 再次过滤未来日期
    df = df[df['trade_date'] <= today_str].copy()

    # 选择需要的列（确保与表结构匹配）
    columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
    df = df[[c for c in columns if c in df.columns]].copy()

    # 添加 turnover_rate 列（表中有但pytdx没有）
    df['turnover_rate'] = None

    return df


def download_stock_kline(tdx, symbol, start_date, end_date):
    """下载个股K线"""
    try:
        df = tdx.get_stock_kline(symbol, start_date, end_date)
        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        return download_and_clean_kline(df, symbol, today_str)
    except Exception as e:
        return None


def download_index_kline(tdx, symbol, start_date, end_date):
    """下载指数K线"""
    try:
        df = tdx.get_index_kline(symbol, start_date, end_date)
        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        return download_and_clean_kline(df, symbol, today_str)
    except Exception as e:
        return None


def download_sector_kline_akshare(sector_name, sector_type, start_date, end_date):
    """使用 AKShare 下载板块K线"""
    try:
        # AKShare 获取板块K线
        if sector_type == 'industry':
            df = ak.stock_board_industry_name_em()
        else:
            df = ak.stock_board_concept_name_em()

        # 获取板块历史数据
        df = ak.stock_board_industry_hist_em(symbol=sector_name, period="daily",
                                              start_date=start_date.replace('-', ''),
                                              end_date=end_date.replace('-', ''),
                                              adjust="qfq")

        if df is None or df.empty:
            return None

        # 重命名列
        df = df.rename(columns={
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        })

        # 清洗数据
        df['sector_name'] = sector_name
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        df = df[df['trade_date'] <= today_str].copy()

        # 选择需要的列
        columns = ['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
        return df[[c for c in columns if c in df.columns]]

    except Exception as e:
        return None


def download_etf_kline_akshare(symbol, start_date, end_date):
    """使用 AKShare 下载ETF K线"""
    try:
        df = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                                  start_date=start_date.replace('-', ''),
                                  end_date=end_date.replace('-', ''),
                                  adjust="qfq")

        if df is None or df.empty:
            return None

        # 重命名列
        df = df.rename(columns={
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        })

        # 清洗数据
        df['symbol'] = symbol
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

        today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
        df = df[df['trade_date'] <= today_str].copy()

        # 选择需要的列
        columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
        return df[[c for c in columns if c in df.columns]]

    except Exception as e:
        return None


# ============================================
# 数据保存
# ============================================

def save_kline_data(conn, df, table_name):
    """保存K线数据到数据库"""
    if df is None or df.empty:
        return 0

    try:
        # 先尝试批量保存
        df.to_sql(table_name, conn, if_exists='append', index=False,
                  method='multi', chunksize=1000)
        conn.commit()
        return len(df)
    except Exception as e:
        # 批量保存失败，尝试逐条插入（处理主键冲突等）
        count = 0
        for _, row in df.iterrows():
            try:
                conn.execute(f"""
                    INSERT OR REPLACE INTO {table_name}
                    (symbol, trade_date, open, high, low, close, volume, amount, pct_chg)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row.get('symbol'), row.get('trade_date'),
                    row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                    row.get('volume'), row.get('amount'), row.get('pct_chg')
                ))
                count += 1
            except Exception as e2:
                pass  # 跳过无法插入的记录
        conn.commit()
        return count


def get_last_date(conn, table_name, symbol_column, symbol):
    """获取某个代码的最新日期"""
    try:
        result = conn.execute(f"""
            SELECT MAX(trade_date) FROM {table_name}
            WHERE {symbol_column} = ?
        """, (symbol,)).fetchone()
        return result[0] if result and result[0] else None
    except:
        return None


# ============================================
# 主下载函数
# ============================================

def download_stocks(tdx, conn, start_date, end_date, symbols=None, test_mode=False):
    """下载A股K线"""
    print("\n" + "="*60)
    print("【1/4】下载A股K线数据")
    print("="*60)

    init_stock_daily_prices_table(conn)

    if symbols is None:
        symbols = get_stock_list()

    if test_mode:
        symbols = symbols[:20]

    print(f"共 {len(symbols)} 只股票")

    success = 0
    fail = 0
    total_records = 0

    for symbol in tqdm(symbols, desc="A股下载"):
        try:
            last_date = get_last_date(conn, "stock_daily_prices", "symbol", symbol)
            if last_date:
                download_start = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                success += 1
                continue

            df = download_stock_kline(tdx, symbol, download_start, end_date)

            if df is not None and not df.empty:
                count = save_kline_data(conn, df, "stock_daily_prices")
                total_records += count
                success += 1
                # 每100只股票提交一次
                if success % 100 == 0:
                    conn.commit()
            else:
                fail += 1

            time.sleep(0.01)
        except Exception as e:
            print(f"\n错误 {symbol}: {e}")
            fail += 1

    print(f"✅ 完成: 成功 {success}, 失败 {fail}, 记录数 {total_records:,}")
    return total_records


def download_sectors(conn, start_date, end_date, test_mode=False):
    """下载板块K线"""
    print("\n" + "="*60)
    print("【2/4】下载板块K线数据")
    print("="*60)

    init_sector_daily_prices_table(conn)

    sectors = get_sector_list()

    if test_mode:
        sectors = sectors[:10]

    print(f"共 {len(sectors)} 个板块")

    success = 0
    fail = 0
    total_records = 0

    for sector_name, sector_type in tqdm(sectors, desc="板块下载"):
        try:
            last_date = get_last_date(conn, "sector_daily_prices", "sector_name", sector_name)
            if last_date:
                download_start = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                success += 1
                continue

            df = download_sector_kline_akshare(sector_name, sector_type, download_start, end_date)

            if df is not None and not df.empty:
                count = save_kline_data(conn, df, "sector_daily_prices")
                total_records += count
                success += 1
            else:
                fail += 1

            time.sleep(0.5)
        except:
            fail += 1

    print(f"✅ 完成: 成功 {success}, 失败 {fail}, 记录数 {total_records:,}")
    return total_records


def download_etfs(tdx, conn, start_date, end_date, test_mode=False):
    """下载ETF K线"""
    print("\n" + "="*60)
    print("【3/4】下载ETF K线数据")
    print("="*60)

    init_etf_daily_prices_table(conn)

    symbols = get_etf_list()

    if test_mode:
        symbols = symbols[:10]

    print(f"共 {len(symbols)} 个ETF")

    success = 0
    fail = 0
    total_records = 0

    for symbol in tqdm(symbols, desc="ETF下载"):
        try:
            last_date = get_last_date(conn, "etf_daily_prices", "symbol", symbol)
            if last_date:
                download_start = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                success += 1
                continue

            # 先尝试用 pytdx
            df = download_stock_kline(tdx, symbol, download_start, end_date)

            # 如果 pytdx 失败，尝试 AKShare
            if df is None or df.empty:
                df = download_etf_kline_akshare(symbol, download_start, end_date)

            if df is not None and not df.empty:
                count = save_kline_data(conn, df, "etf_daily_prices")
                total_records += count
                success += 1
            else:
                fail += 1

            time.sleep(0.01)
        except:
            fail += 1

    print(f"✅ 完成: 成功 {success}, 失败 {fail}, 记录数 {total_records:,}")
    return total_records


def download_indices(tdx, conn, start_date, end_date):
    """下载指数K线"""
    print("\n" + "="*60)
    print("【4/4】下载指数K线数据")
    print("="*60)

    init_index_daily_prices_table(conn)

    indices = get_index_list()

    print(f"共 {len(indices)} 个指数")

    success = 0
    fail = 0
    total_records = 0

    for symbol, name in tqdm(indices, desc="指数下载"):
        try:
            last_date = get_last_date(conn, "index_daily_prices", "symbol", symbol)
            if last_date:
                download_start = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                success += 1
                continue

            df = download_index_kline(tdx, symbol, download_start, end_date)

            if df is not None and not df.empty:
                count = save_kline_data(conn, df, "index_daily_prices")
                total_records += count
                success += 1
            else:
                fail += 1

            time.sleep(0.01)
        except:
            fail += 1

    print(f"✅ 完成: 成功 {success}, 失败 {fail}, 记录数 {total_records:,}")
    return total_records


# ============================================
# 主函数
# ============================================

def download_all(start_date, end_date=None, test_mode=False):
    """
    下载全部K线数据

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        test_mode: 测试模式
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    print("="*60)
    print("完整K线数据下载器")
    print("="*60)
    print(f"日期范围: {start_date} ~ {end_date}")
    if test_mode:
        print("⚠️  测试模式")

    # 初始化 pytdx
    print("\n初始化 pytdx...")
    tdx = TdxAdapter()
    if not tdx.is_available():
        print("❌ pytdx 连接失败")
        return
    print(f"✅ pytdx 已连接到 {tdx.current_server}")

    # 初始化数据库
    conn = sqlite3.connect(DB_PATH)

    # 统计
    total_records = 0

    try:
        # 1. A股
        records = download_stocks(tdx, conn, start_date, end_date, test_mode=test_mode)
        total_records += records

        # 2. 板块
        records = download_sectors(conn, start_date, end_date, test_mode=test_mode)
        total_records += records

        # 3. ETF
        records = download_etfs(tdx, conn, start_date, end_date, test_mode=test_mode)
        total_records += records

        # 4. 指数
        records = download_indices(tdx, conn, start_date, end_date)
        total_records += records

    finally:
        conn.close()
        tdx.disconnect()

    # 输出统计
    print("\n" + "="*60)
    print("🎉 全部下载完成！")
    print("="*60)
    print(f"总记录数: {total_records:,}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="下载完整历史K线数据")
    parser.add_argument('--start', default='2022-01-01', help='开始日期 (YYYY-MM-DD), 默认近3年')
    parser.add_argument('--end', default=None, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--test', action='store_true', help='测试模式')
    parser.add_argument('--type', choices=['all', 'stock', 'sector', 'etf', 'index'],
                       default='all', help='数据类型')

    args = parser.parse_args()

    end_date = args.end
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    download_all(args.start, end_date, test_mode=args.test)


if __name__ == "__main__":
    main()
