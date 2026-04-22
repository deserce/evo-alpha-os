"""
数据分析和补齐工具
检查数据完整性并尝试补齐缺失数据
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
from app.core.database import get_engine


def analyze_data_gaps():
    """分析数据缺口"""
    print("=" * 80)
    print("数据缺口分析")
    print("=" * 80)

    engine = get_engine()

    # 1. 检查整体数据完整性
    print("\n[1] 整体数据概况:")
    stats = pd.read_sql('''
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT symbol) as unique_stocks,
            MIN(trade_date) as earliest_date,
            MAX(trade_date) as latest_date,
            (julianday(MAX(trade_date)) - julianday(MIN(trade_date))) as date_span_days
        FROM stock_daily_prices
    ''', engine)

    print(f"总记录数: {stats.iloc[0,0]:,}")
    print(f"股票数量: {stats.iloc[0,1]:,}")
    print(f"日期范围: {stats.iloc[0,2]} 至 {stats.iloc[0,3]}")
    print(f"跨度天数: {stats.iloc[0,4]:.0f} 天")

    # 2. 检查最新数据日期
    print("\n[2] 最新数据日期:")
    latest_date = pd.read_sql('SELECT MAX(trade_date) as latest FROM stock_daily_prices', engine)
    latest = latest_date.iloc[0,0]
    print(f"最新数据: {latest}")

    today = datetime.now().strftime('%Y-%m-%d')
    latest_dt = datetime.strptime(latest, '%Y-%m-%d')
    today_dt = datetime.now()

    days_missing = (today_dt - latest_dt).days
    print(f"今天: {today}")
    print(f"缺失天数: {days_missing} 天")

    if days_missing > 0:
        missing_dates = []
        current = latest_dt + timedelta(days=1)
        while current <= today_dt:
            # 只包含工作日
            if current.weekday() < 5:  # 0-4 表示周一到周五
                missing_dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        print(f"缺失的工作日: {len(missing_dates)} 天")
        if missing_dates:
            print(f"  需要补齐的日期: {missing_dates[:10]}...")  # 只显示前10个

    # 3. 检查每只股票的数据完整性
    print("\n[3] 股票数据完整性分析:")

    stock_stats = pd.read_sql('''
        SELECT
            symbol,
            COUNT(*) as records,
            MIN(trade_date) as min_date,
            MAX(trade_date) as max_date,
            (julianday(MAX(trade_date)) - julianday(MIN(trade_date))) as span_days
        FROM stock_daily_prices
        GROUP BY symbol
        ORDER BY records ASC
    ''', engine)

    # 计算期望的记录数（约等于交易日）
    stock_stats['expected_records'] = stock_stats['span_days'] * 5 / 7  # 粗略估计交易日
    stock_stats['completeness'] = (stock_stats['records'] / stock_stats['expected_records'] * 100).round(2)

    print(f"数据最完整的10只股票:")
    print(stock_stats.nlargest(10, 'records')[['symbol', 'records', 'min_date', 'max_date']].to_string(index=False))

    print(f"\n数据不完整的股票 (少于600条记录):")
    incomplete = stock_stats[stock_stats['records'] < 600]
    print(f"数量: {len(incomplete)} 只")
    if len(incomplete) > 0:
        print(incomplete[['symbol', 'records', 'min_date', 'max_date']].head(20).to_string(index=False))

    # 4. 检查最近一周的数据
    print("\n[4] 最近一周数据更新情况:")

    one_week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    recent_data = pd.read_sql(f'''
        SELECT
            trade_date,
            COUNT(*) as stock_count
        FROM stock_daily_prices
        WHERE trade_date >= '{one_week_ago}'
        GROUP BY trade_date
        ORDER BY trade_date DESC
    ''', engine)

    print(recent_data.to_string(index=False))

    # 5. 检查板块和ETF数据
    print("\n[5] 其他数据表状态:")

    tables_info = {
        'sector_daily_prices': '板块K线',
        'etf_daily_prices': 'ETF K线',
        'stock_valuation_daily': '估值数据',
        'limit_board_trading': '涨跌停数据'
    }

    for table, desc in tables_info.items():
        try:
            count = pd.read_sql(f'SELECT COUNT(*) as cnt FROM {table}', engine)
            latest = pd.read_sql(f'SELECT MAX(trade_date) as latest FROM {table} WHERE trade_date IS NOT NULL', engine)

            print(f"{desc} ({table}):")
            print(f"  记录数: {count.iloc[0,0]:,}")
            print(f"  最新日期: {latest.iloc[0,0]}")
        except Exception as e:
            print(f"{desc} ({table}): 查询失败 - {e}")

    return {
        'latest_date': latest,
        'days_missing': days_missing,
        'incomplete_stocks': len(incomplete),
        'total_stocks': len(stock_stats)
    }


def generate_fix_plan(analysis_result):
    """生成数据补齐计划"""
    print("\n" + "=" * 80)
    print("数据补齐计划")
    print("=" * 80)

    latest_date = analysis_result['latest_date']
    days_missing = analysis_result['days_missing']

    print(f"\n需要补齐的数据: {days_missing} 天")

    if days_missing > 0:
        print("\n建议方案:")
        print("1. 方案A: 在网络恢复后运行增量采集")
        print("   python3 auto_pipeline.py --mode daily")
        print("\n2. 方案B: 手动下载最近几天的数据并导入")
        print("   - 从东方财富、新浪财经等网站下载")
        print("   - 使用下面的import_data.py脚本导入")
        print("\n3. 方案C: 使用本地数据（如果不急需最新数据）")
        print("   - 当前数据可用")
        print("   - 等待网络恢复后自动更新")


def test_data_sources():
    """测试各个数据源的可用性"""
    print("\n" + "=" * 80)
    print("数据源可用性测试")
    print("=" * 80)

    results = {}

    # 测试AKShare
    print("\n[1] 测试 AKShare...")
    try:
        import akshare as ak
        print("✅ AKShare已安装")

        # 尝试获取一个简单的数据
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                print(f"✅ AKShare连接成功 (获取到{len(df)}只股票)")
                results['akshare'] = True
            else:
                print("⚠️ AKShare返回空数据")
                results['akshare'] = False
        except Exception as e:
            print(f"❌ AKShare连接失败: {str(e)[:100]}...")
            results['akshare'] = False

    except ImportError:
        print("❌ AKShare未安装")
        results['akshare'] = False

    # 测试efinance
    print("\n[2] 测试 efinance...")
    try:
        import efinance as ef
        print("✅ efinance已安装")

        try:
            df = ef.stock.get_realtime_quotes()
            if df is not None and not df.empty:
                print(f"✅ efinance连接成功 (获取到{len(df)}条数据)")
                results['efinance'] = True
            else:
                print("⚠️ efinance返回空数据")
                results['efinance'] = False
        except Exception as e:
            print(f"❌ efinance连接失败: {str(e)[:100]}...")
            results['efinance'] = False

    except ImportError:
        print("❌ efinance未安装")
        results['efinance'] = False

    # 测试BaoStock
    print("\n[3] 测试 BaoStock...")
    try:
        import baostock as bs
        print("✅ BaoStock已安装")

        try:
            lg = bs.login()
            if lg.error_code == '0':
                print("✅ BaoStock登录成功")
                bs.logout()
                results['baostock'] = True
            else:
                print(f"❌ BaoStock登录失败: {lg.error_msg}")
                results['baostock'] = False
        except Exception as e:
            print(f"❌ BaoStock连接失败: {str(e)[:100]}...")
            results['baostock'] = False

    except ImportError:
        print("❌ BaoStock未安装 (pip3 install baostock)")
        results['baostock'] = False

    # 总结
    print("\n" + "=" * 80)
    print("数据源测试总结:")
    print("=" * 80)

    available = [k for k, v in results.items() if v]
    unavailable = [k for k, v in results.items() if not v]

    if available:
        print(f"\n✅ 可用的数据源: {', '.join(available)}")
        print(f"\n建议: 使用这些数据源补齐缺失数据")

        if 'akshare' in available:
            print("\n运行命令:")
            print("  python3 auto_pipeline.py --mode daily")

    if unavailable:
        print(f"\n❌ 不可用的数据源: {', '.join(unavailable)}")
        print("\n可能原因:")
        print("  - 网络连接问题")
        print("  - 需要配置代理")
        print("  - 数据源服务器暂时不可用")

    return results


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("EvoAlpha OS - 数据分析和补齐工具")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 分析数据缺口
    analysis_result = analyze_data_gaps()

    # 2. 生成补齐计划
    generate_fix_plan(analysis_result)

    # 3. 测试数据源
    source_results = test_data_sources()

    # 4. 提供下一步建议
    print("\n" + "=" * 80)
    print("下一步操作建议")
    print("=" * 80)

    if any(source_results.values()):
        print("\n✅ 有可用的数据源")
        print("建议运行增量采集来补齐最新数据:")
        print("  cd backend")
        print("  python3 auto_pipeline.py --mode daily")
    else:
        print("\n⚠️ 当前所有数据源均不可用")
        print("\n建议:")
        print("  1. 更换网络环境（如使用手机热点）")
        print("  2. 检查并配置代理设置")
        print("  3. 稍后重试")
        print("\n或者，可以使用当前已有的数据进行量化分析:")
        print("  - 数据日期范围: 2023-01-03 至 2026-03-03")
        print("  - 数据量充足，可以满足大部分分析需求")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
