"""
将GMX采集的数据导入到Mac数据库
从Windows虚拟机采集的数据文件导入到本地SQLite数据库
"""

import sys
from pathlib import Path
import pandas as pd

backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.database import get_engine

print("=" * 80)
print("GMX数据导入工具 (Mac版)")
print("=" * 80)

# 数据库引擎
engine = get_engine()

# CSV文件路径（需要从Windows虚拟机复制过来）
csv_file = "gmx_data_export.csv"

# 如果文件在其他位置，修改这里的路径
# csv_file = "/path/to/shared/folder/gmx_data_export.csv"

print(f"\n正在读取数据文件: {csv_file}")

try:
    df = pd.read_csv(csv_file)
    print(f"✅ 成功读取 {len(df)} 条数据")

    # 检查列名
    required_columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount']
    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        print(f"❌ 数据文件缺少列: {missing_columns}")
        sys.exit(1)

    print(f"  列名: {list(df.columns)}")

    # 检查数据范围
    print(f"\n数据概览:")
    print(f"  股票数: {df['symbol'].nunique()}")
    print(f"  日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"  总记录数: {len(df):,}")

    # 导入到数据库
    print(f"\n正在导入数据库...")

    # 检查是否已存在相同数据
    df_existing = pd.read_sql(f"""
        SELECT symbol, trade_date FROM stock_daily_prices
        WHERE trade_date >= '{df['trade_date'].min()}'
        LIMIT 10
    """, engine)

    if len(df_existing) > 0:
        print(f"⚠️  发现已有数据: {len(df_existing)} 条")
        print(f"   最新日期: {df_existing['trade_date'].max()}")

        # 询问是否覆盖
        response = input("\n是否继续导入（可能产生重复）？(y/n): ").strip().lower()
        if response != 'y':
            print("❌ 已取消导入")
            sys.exit(0)

    # 导入数据
    df.to_sql('stock_daily_prices', engine, if_exists='append', index=False)
    print(f"✅ 成功导入 {len(df)} 条数据")

    # 验证导入结果
    df_check = pd.read_sql(f"""
        SELECT MAX(trade_date) as max_date, COUNT(*) as count
        FROM stock_daily_prices
    """, engine)
    print(f"\n数据库当前状态:")
    print(f"  最新日期: {df_check.iloc[0]['max_date']}")
    print(f"  总记录数: {df_check.iloc[0]['count']:,}")

except FileNotFoundError:
    print(f"❌ 文件不存在: {csv_file}")
    print("\n请确保:")
    print("  1. Windows虚拟机已运行数据采集脚本")
    print("  2. 生成的CSV文件已复制到Mac宿主机")
    print("  3. 文件路径正确")

except Exception as e:
    print(f"❌ 导入失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("导入完成")
print("=" * 80)
