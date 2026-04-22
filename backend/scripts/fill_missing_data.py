"""
数据补齐脚本
使用BaoStock补齐缺失的最近几天数据
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
from tqdm import tqdm
from app.core.database import get_engine
from sqlalchemy import text


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


def get_stock_list(engine):
    """获取股票列表"""
    df = pd.read_sql('SELECT DISTINCT symbol FROM stock_info ORDER BY symbol', engine)
    return df['symbol'].tolist()


def update_data_with_baostock():
    """使用BaoStock更新数据"""
    print("=" * 80)
    print("使用BaoStock补齐数据")
    print("=" * 80)

    # 导入BaoStock适配器
    from data_job.core.baostock_adapter import BaoStockAdapter

    # 初始化BaoStock
    adapter = BaoStockAdapter(priority=7)
    engine = get_engine()

    # 获取需要补齐的日期
    missing_dates = get_missing_dates()

    if not missing_dates:
        print("\n✅ 数据已是最新，无需补齐")
        return True

    print(f"\n需要补齐的日期: {len(missing_dates)} 天")
    print(f"  日期: {missing_dates}")

    # 获取股票列表
    print("\n获取股票列表...")
    stock_list = get_stock_list(engine)
    print(f"  共 {len(stock_list)} 只股票")

    if len(stock_list) == 0:
        print("❌ 股票列表为空")
        return False

    # 限制更新数量（先更新前100只股票测试）
    UPDATE_LIMIT = 100
    stock_list = stock_list[:UPDATE_LIMIT]
    print(f"\n本次更新: {len(stock_list)} 只股票（测试模式）")

    # 开始更新
    print("\n开始更新数据...")
    success_count = 0
    fail_count = 0

    for date_str in missing_dates:
        print(f"\n[{date_str}] 更新中...")

        for i, symbol in enumerate(stock_list):
            try:
                # 使用BaoStock获取数据
                # 注意：BaoStock需要完整的日期范围，不能只取一天
                start_date = date_str
                end_date = date_str

                df = adapter.get_stock_kline(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjust='qfq'
                )

                if df is not None and not df.empty:
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

                # 每处理10只股票显示进度
                if (i + 1) % 10 == 0:
                    print(f"  已处理: {i+1}/{len(stock_list)}")

            except Exception as e:
                fail_count += 1
                print(f"  ❌ {symbol} 失败: {str(e)[:50]}")

    # 显示结果
    print("\n" + "=" * 80)
    print("更新完成")
    print("=" * 80)
    print(f"成功: {success_count} 条")
    print(f"失败: {fail_count} 条")

    # 验证更新结果
    print("\n验证更新结果...")
    latest = pd.read_sql('SELECT MAX(trade_date) as latest FROM stock_daily_prices', engine)
    print(f"最新数据日期: {latest.iloc[0,0]}")

    return True


def verify_baostock_adapter():
    """验证BaoStock适配器"""
    print("=" * 80)
    print("验证BaoStock适配器")
    print("=" * 80)

    from data_job.core.baostock_adapter import BaoStockAdapter

    adapter = BaoStockAdapter(priority=7)

    # 测试获取一只股票的数据
    print("\n测试获取平安银行(000001)最近10天数据...")

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

    try:
        df = adapter.get_stock_kline('000001', start_date, end_date)

        if df is not None and not df.empty:
            print(f"✅ 成功获取 {len(df)} 行数据")
            print(f"\n数据列: {list(df.columns)}")
            print(f"\n前3行:")
            print(df.head(3).to_string())
            return True
        else:
            print("❌ 返回空数据")
            return False

    except Exception as e:
        print(f"❌ 获取失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("EvoAlpha OS - 数据补齐工具")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 验证BaoStock适配器
    if not verify_baostock_adapter():
        print("\n❌ BaoStock适配器验证失败，无法继续")
        return

    # 2. 使用BaoStock补齐数据
    success = update_data_with_baostock()

    if success:
        print("\n✅ 数据补齐完成")
        print("\n下一步:")
        print("  - 检查数据完整性")
        print("  - 运行量化分析")
        print("  - 定期运行此脚本保持数据更新")
    else:
        print("\n❌ 数据补齐失败")
        print("\n建议:")
        print("  - 检查网络连接")
        print("  - 查看错误日志")
        print("  - 稍后重试")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
