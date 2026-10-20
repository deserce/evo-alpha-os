# 个股RPS查询功能

## 功能概述

提供个股及其关联板块的RPS（相对价格强度）数据查询功能，支持多周期RPS对比分析。

## 位置

`backend/quant_engine/utils/stock_rps_query.py`

## 数据表

| 表名 | 说明 |
|------|------|
| `quant_feature_rps` | 个股RPS数据 |
| `quant_feature_sector_rps` | 板块RPS数据 |
| `stock_daily_prices` | 个股行情 |
| `stock_info` | 股票基本信息 |
| `sector_list` | 板块列表 |

## 命令行使用

### 基本查询

```bash
cd backend

# 查询最新RPS数据
python3 -m quant_engine.utils.stock_rps_query 600519

# 查询指定日期RPS数据
python3 -m quant_engine.utils.stock_rps_query 601138 --date 2024-03-08
```

### 输出示例

```
================================================================================
📊 个股RPS查询报告
================================================================================
股票代码: 601138
股票名称: 工业富联
查询日期: 2026-03-11

📈 个股RPS数据:
  RPS20:  31.79
  RPS50:  12.82
  RPS120: 37.70
  RPS250: 94.60

🏢 相关概念板块RPS (Top 10):
板块名称                    RPS20    RPS50   RPS120   RPS250
----------------------------------------------------------------------
稀缺资源                   100.00   100.00   100.00   100.00
创投                      99.85    99.70    99.85    99.85
...
================================================================================
```

## Python API 使用

### 基础查询

```python
from quant_engine.utils.stock_rps_query import StockRPSQuery

# 初始化查询器
query = StockRPSQuery(db_path="data/local_quant.db")

# 查询个股RPS（最新）
result = query.query_stock_rps("600519")
# 返回: {'symbol': '600519', 'stock_name': '贵州茅台', 'rps_5': ..., 'rps_20': ..., ...}

# 查询指定日期
result = query.query_stock_rps("600519", trade_date="2024-03-08")

# 关闭连接
query.close()
```

### 个股与板块对比

```python
# 查询个股及关联概念板块RPS
result = query.query_stock_and_concepts_rps("601138")
# 返回结构:
# {
#     'symbol': '601138',
#     'stock_name': '工业富联',
#     'trade_date': '2026-03-11',
#     'stock_rps': {'rps_5': ..., 'rps_20': ..., ...},
#     'related_sectors': [
#         {'sector_name': '稀缺资源', 'rps_20': 100.0, ...},
#         ...
#     ]
# }

# 对比个股与Top N板块
result = query.compare_stock_with_sectors("601138", top_n=10)
```

### 查询个股及所属行业

```python
# 查询个股及其所属行业的RPS数据
result = query.query_stock_with_sector_rps("600519")
# 返回结构:
# {
#     'symbol': '600519',
#     'industry_id': '白酒',
#     'stock_rps': {...},
#     'industry_rps': {...}
# }
```

## RPS 周期说明

| 周期 | 说明 |
|------|------|
| RPS5 | 5日相对强度（约1周） |
| RPS10 | 10日相对强度（约2周） |
| RPS20 | 20日相对强度（约1月） |
| RPS50 | 50日相对强度（约2.5月） |
| RPS120 | 120日相对强度（约6月） |
| RPS250 | 250日相对强度（约1年） |

## RPS 值解读

| RPS值 | 含义 |
|-------|------|
| 90+ | 极强，排名前10% |
| 80-90 | 强势，排名前10%-20% |
| 50-80 | 中性 |
| 20-50 | 弱势 |
| 0-20 | 极弱，排名后20% |

## API 接口

如需通过HTTP API访问，可参考 `backend/app/api/v1/market.py` 中的接口定义。

## 常见股票代码

| 代码 | 名称 | 代码 | 名称 |
|------|------|------|------|
| 600519 | 贵州茅台 | 601138 | 工业富联 |
| 000858 | 五粮液 | 300750 | 宁德时代 |
| 002594 | 比亚迪 | 601012 | 隆基绿能 |
