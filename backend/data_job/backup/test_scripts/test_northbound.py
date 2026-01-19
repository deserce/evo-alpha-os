"""
测试北向资金持股采集器
"""
import sys
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors.northbound_holdings_collector import NorthboundHoldingsCollector

if __name__ == "__main__":
    print("🧪 测试模式：只采集前10只股票")
    collector = NorthboundHoldingsCollector()
    collector.run(collect_all_stocks=False)
