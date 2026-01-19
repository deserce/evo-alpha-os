"""
全量采集北向资金持股数据
"""
import sys
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors.northbound_holdings_collector import NorthboundHoldingsCollector

if __name__ == "__main__":
    print("🚀 生产模式：采集所有5800只股票")
    print("⏰ 预估时间: 约3.2小时")
    print("📝 日志将保存到: logs/northbound_holdings.log")
    collector = NorthboundHoldingsCollector()
    collector.run(collect_all_stocks=True)
