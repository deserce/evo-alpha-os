"""
测试北交所指数采集器
单独运行此脚本来测试数据采集
"""
import sys
from pathlib import Path

# 添加项目路径
backend_dir = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors.bse_index_collector import BSEIndexCollector

def main():
    print("=" * 80)
    print("🧪 北交所指数采集器测试")
    print("=" * 80)

    collector = BSEIndexCollector()

    print(f"\n📋 采集器信息:")
    print(f"  - 采集器名称: {collector.collector_name}")
    print(f"  - 目标表: {collector.table_name}")
    print(f"  - 指数列表: {collector.index_list}")
    print(f"  - 请求数: {len(collector.index_list)} 个北交所指数")

    print(f"\n🚀 开始采集...")
    print("-" * 80)

    try:
        collector.run()
        print("\n" + "=" * 80)
        print("✅ 采集完成")
        print("=" * 80)

        # 验证数据
        import sqlite3
        DB_PATH = backend_dir / "data" / "local_quant.db"
        if DB_PATH.exists():
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询北证50数据
            cursor.execute("""
                SELECT COUNT(*) as count,
                       MIN(trade_date) as min_date,
                       MAX(trade_date) as max_date
                FROM index_daily
                WHERE index_code = '899560'
            """)
            result = cursor.fetchone()

            if result and result['count'] > 0:
                print(f"\n📊 数据验证:")
                print(f"  - 记录数: {result['count']}")
                print(f"  - 日期范围: {result['min_date']} ~ {result['max_date']}")

                # 最新数据
                cursor.execute("""
                    SELECT * FROM index_daily
                    WHERE index_code = '899560'
                    ORDER BY trade_date DESC
                    LIMIT 3
                """)
                latest = cursor.fetchall()
                print(f"\n最新3条数据:")
                for row in latest:
                    print(f"  {row['trade_date']}: 收盘 {row['close']}, 涨跌 {row['pct_chg']:.2f}%")
            else:
                print(f"\n⚠️ 数据库中暂无北证50数据")

            conn.close()

    except Exception as e:
        print(f"\n❌ 采集失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
