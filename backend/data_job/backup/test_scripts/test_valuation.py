"""
测试股票估值采集器
"""
import sys
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors.stock_valuation_collector import StockValuationCollector

if __name__ == "__main__":
    print("🔍 测试 StockValuationCollector...")
    collector = StockValuationCollector()
    collector.run()
