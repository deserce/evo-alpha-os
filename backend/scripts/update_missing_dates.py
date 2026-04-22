"""
使用BaoStock批量补齐缺失数据
补齐2026-03-04, 03-05, 03-06这3个工作日的数据
"""

import sys
import os
from datetime import datetime, timedelta

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pandas as pd
from sqlalchemy import text
from app.core.database import get_engine
from tqdm import tqdm


def get_missing_dates():
    """获取需要补齐的日期"""
    engine = get_engine()

    # 获取最新数据日期
    latest = pd.read_sql('SELECT MAX(trade_date) as latest FROM stock_daily_prices', engine)
    latest_date = latest.iloc[0,0]

    latest_dt = datetime.strptime(latest_date, '%Y-%m-%d')
    today_dt = datetime.now()

    # 生成需要补齐的日期（工作日）
    missing_dates = []
    current = latest_dt + timedelta(days=1)

    while current <= today_dt:
        # 只包含工作日（周一到周五）
        if current.weekday() < 5:
            missing_dates.append(current.strftime('%Y%m%d'))
        current += timedelta(days=1)

    return missing_dates


def get_stock_list(engine, limit=100):
    """获取股票列表"""
    df = pd.read_sql('SELECT DISTINCT symbol FROM stock_info ORDER BY symbol LIMIT :limit', engine, params={'limit': limit})
    return df['symbol'].tolist()


def update_date_with_baostock(date_str, stock_list, batch_size=50):
    """使用BaoStock更新指定日期的数据"""
    import baostock as bs

    print(f"\n{'='*80}")
    print(f"更新日期: {date_str}")
    print(f"{'='*80}")

    # 登录
    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ BaoStock登录失败: {lg.error_msg}")
        return 0, 0

    # 转换日期格式
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    engine = get_engine()
    success_count = 0
    fail_count = 0

    # 批量处理
    for i in range(0, len(stock_list), batch_size):
        batch = stock_list[i:i+batch_size]
        print(f"\n处理批次 {i//batch_size + 1}/{(len(stock_list)-1)//batch_size + 1} ({len(batch)}只股票)...")

        for symbol in tqdm(batch, desc=f"  获取数据"):
            try:
                # 转换股票代码
                bs_symbol = f"sz.{symbol}" if symbol.startswith(('0', '3')) else f"sh.{symbol}"

                # 获取数据
                rs = bs.query_history_k_data_plus(
                    bs_symbol,
                    "date,code,open,high,low,close,volume,amount",
                    start_date=date_fmt,
                    end_date=date_fmt,
                    frequency='d',
                    adjustflag='2'  # 前复权
                )

                if rs.error_code == '0':
                    # 转换为DataFrame
                    data_list = []
                    while (rs.error_code == '0') & rs.next():
                        data_list.append(rs.get_row_data())

                    if data_list:
                        df = pd.DataFrame(data_list, columns=rs.fields)

                        # 重命名列
                        df = df.rename(columns={
                            'date': 'trade_date',
                            'code': 'symbol'
                        })

                        # 转换日期格式
                        df['trade_date'] = df['trade_date'].str.replace('-', '')

                        # 转换数值类型
                        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'amount']
                        for col in numeric_columns:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce')

                        # 保存到数据库
                        with engine.begin() as conn:
                            # 删除旧数据
                            conn.execute(text(
                                "DELETE FROM stock_daily_prices WHERE symbol = :symbol AND trade_date = :date"
                            ), {"symbol": symbol, "date": date_str})

                            # 插入新数据
                            df.to_sql('stock_daily_prices', conn, if_exists='append', index=False)

                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    fail_count += 1

            except Exception as e:
                fail_count += 1
                print(f"    ❌ {symbol} 失败: {str(e)[:30]}")

    # 登出
    bs.logout()

    return success_count, fail_count


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("使用BaoStock补齐缺失数据")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 获取需要补齐的日期
    print("\n[1/4] 分析数据缺口...")
    missing_dates = get_missing_dates()

    if not missing_dates:
        print("✅ 数据已是最新，无需补齐")
        return

    print(f"需要补齐的日期: {missing_dates}")
    print(f"共 {len(missing_dates)} 个工作日")

    # 2. 获取股票列表
    print("\n[2/4] 获取股票列表...")
    engine = get_engine()

    # 先更新前100只股票测试
    stock_list = get_stock_list(engine, limit=100)
    print(f"本次更新: {len(stock_list)} 只股票（测试模式）")

    if len(stock_list) == 0:
        print("❌ 股票列表为空")
        return

    # 3. 逐日更新数据
    print("\n[3/4] 开始更新数据...")
    total_success = 0
    total_fail = 0

    for date_str in missing_dates:
        success, fail = update_date_with_baostock(date_str, stock_list)
        total_success += success
        total_fail += fail

    # 4. 验证结果
    print("\n[4/4] 验证更新结果...")
    latest = pd.read_sql('SELECT MAX(trade_date) as latest FROM stock_daily_prices', engine)
    print(f"最新数据日期: {latest.iloc[0,0]}")

    # 统计当天数据量
    for date_str in missing_dates:
        count = pd.read_sql(
            f"SELECT COUNT(*) as cnt FROM stock_daily_prices WHERE trade_date = '{date_str}'",
            engine
        )
        print(f"{date_str}: {count.iloc[0,0]} 条记录")

    # 总结
    print("\n" + "=" * 80)
    print("更新完成")
    print("=" * 80)
    print(f"✅ 成功: {total_success} 条")
    print(f"❌ 失败: {total_fail} 条")
    print(f"成功率: {total_success/(total_success+total_fail)*100:.1f}%")

    print("\n下一步:")
    print("  1. 如果测试成功，可以增加更新股票数量")
    print("  2. 运行量化分析")
    print("  3. 定期更新保持数据最新")


if __name__ == '__main__':
    main()
