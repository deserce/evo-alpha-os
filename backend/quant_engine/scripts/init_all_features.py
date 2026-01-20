#!/usr/bin/env python3
"""
量化引擎 - 初始化所有RPS因子（带进度条）
"""
import sys
import os
import time
from datetime import datetime

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
quant_engine_dir = os.path.dirname(current_dir)
backend_dir = os.path.dirname(quant_engine_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator
from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
from quant_engine.calculators.etf_rps_calculator import ETFRPSCalculator

# 路径初始化
setup_quant_path()

# Logger配置
logger = setup_logger(__name__)


def print_header(text):
    """打印标题"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_step(step_num, total_steps, text):
    """打印步骤"""
    print(f"\n[{step_num}/{total_steps}] {text}")


def main():
    """主函数"""
    start_time = time.time()

    print_header("🚀 开始全量RPS计算")

    # 步骤1：个股RPS
    print_step(1, 3, "个股RPS计算...")
    print("   预计耗时: ~5分钟")
    stock_start = time.time()

    try:
        calculator = StockRPSCalculator()
        calculator.run_init()
        stock_elapsed = time.time() - stock_start
        print(f"   ✅ 完成！耗时: {stock_elapsed:.1f}秒")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return 1

    # 步骤2：板块RPS
    print_step(2, 3, "板块RPS计算...")
    print("   预计耗时: ~2秒")
    sector_start = time.time()

    try:
        calculator = SectorRPSCalculator()
        calculator.run_init()
        sector_elapsed = time.time() - sector_start
        print(f"   ✅ 完成！耗时: {sector_elapsed:.1f}秒")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return 1

    # 步骤3：ETF RPS
    print_step(3, 3, "ETF RPS计算...")
    print("   预计耗时: ~1秒")
    etf_start = time.time()

    try:
        calculator = ETFRPSCalculator()
        calculator.run_init()
        etf_elapsed = time.time() - etf_start
        print(f"   ✅ 完成！耗时: {etf_elapsed:.1f}秒")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return 1

    # 完成
    total_elapsed = time.time() - start_time
    print_header("✅ 全量RPS计算完成！")
    print(f"   总耗时: {total_elapsed:.1f}秒 (~{total_elapsed/60:.1f}分钟)")
    print(f"   完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return 0


if __name__ == "__main__":
    exit(main())
