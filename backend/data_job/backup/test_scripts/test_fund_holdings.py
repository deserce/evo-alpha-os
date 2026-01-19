"""
测试基金持股采集器
"""
import sys
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors.fund_holdings_collector import FundHoldingsCollector

if __name__ == "__main__":
    print("🔍 测试 FundHoldingsCollector...")
    collector = FundHoldingsCollector()
    collector.run()
