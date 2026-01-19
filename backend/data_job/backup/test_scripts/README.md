# 测试脚本备份

本目录包含开发过程中的测试脚本，已经不再使用，仅作为参考保留。

## 文件列表

| 文件 | 说明 | 状态 |
|------|------|------|
| `test_valuation.py` | 测试 StockValuationCollector | 已移动 |
| `test_northbound.py` | 测试 NorthboundHoldingsCollector | 已移动 |
| `test_fund_holdings.py` | 测试 FundHoldingsCollector | 已移动 |
| `run_northbound_full.py` | 北向资金全量采集脚本 | 已完成 |

## 替代方案

### 测试单个采集器

```python
# 直接运行采集器
python -m data_job.collectors.stock_valuation_collector
python -m data_job.collectors.northbound_holdings_collector
python -m data_job.collectors.fund_holdings_collector
```

### 运行北向资金采集

```bash
# 使用初始化脚本（推荐）
python data_job/scripts/init_data_collection.py --step 3

# 或使用定时调度器
python -m data_job.utils.scheduler --mode quarterly
```

## 注意事项

- `run_northbound_full.py` 已完成采集任务（2026-01-19）
- 数据已保存到 `stock_northbound_holdings` 表
- 该任务为一次性历史数据采集，无需再次运行
