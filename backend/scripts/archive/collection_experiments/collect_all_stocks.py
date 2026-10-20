"""
采集全部A股近3年K线数据（命令行版本）

用法:
  python3 collect_all_stocks.py           # 采集全部5403只A股
  python3 collect_all_stocks.py 100       # 采集前100只
  python3 collect_all_stocks.py 1000      # 采集前1000只
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 导入采集模块
from scripts.collect_3years_kline import get_all_stock_list, collect_stock_kline_3years, verify_collection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    # 解析命令行参数
    if len(sys.argv) > 1:
        try:
            stock_count = int(sys.argv[1])
        except ValueError:
            print("参数错误，请输入数字")
            return
    else:
        stock_count = None  # 采集全部

    print("\n" + "=" * 100)
    print("EvoAlpha OS 采集全部A股近3年K线数据")
    print("=" * 100)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    # 1. 获取股票列表
    symbols = get_all_stock_list()

    if not symbols:
        print("❌ 无法获取股票列表，停止执行")
        return

    # 2. 确定采集范围
    if stock_count:
        symbols = symbols[:stock_count]
        print(f"\n⚠️  采集模式：前 {len(symbols)} 只股票")
    else:
        print(f"\n🚀 全部采集模式：全部 {len(symbols)} 只A股")
        print("   预计耗时: 1.5-2小时")
        print("   预计数据量: 约2,800,000行")
        print()

    # 3. 开始采集
    collect_stock_kline_3years(symbols, adjust_type='qfq', batch_size=50)

    # 4. 验证数据
    verify_collection()

    print("\n" + "=" * 100)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    print("✅ 采集完成！")
    print("\n下一步:")
    print("1. 计算RPS指标")
    print("2. 运行策略选股")
    print("3. 查看前端数据")


if __name__ == "__main__":
    main()
