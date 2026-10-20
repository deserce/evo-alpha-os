# 北交所板块指数采集器

## 概述

北交所板块指数采集器用于采集北交所（BSE - Beijing Stock Exchange）的主要指数数据，目前支持：

- **北证50 (899560)** - 北交所最重要的50只成分股指数

## 文件位置

- 采集器: `backend/data_job/collectors/bse_index_collector.py`
- 测试脚本: `backend/scripts/test_bse_collector.py`

## 数据源架构

采用多源自动切换机制：

1. **东方财富网** (优先) - 使用push2his接口
2. **AkShare** (备用) - 使用index_zh_a_hist接口

## 数据存储

采集的数据保存在 `index_daily` 表中，与其他主要指数（上证、深证、创业板等）共用同一张表。

表结构:
```
- index_code: 指数代码 (899560)
- index_name: 指数名称 (北证50)
- trade_date: 交易日期
- open/high/low/close: OHLC价格
- volume/amount: 成交量/成交额
- change: 涨跌点数
- pct_chg: 涨跌幅
```

## 使用方法

### 1. 单独测试采集器

```bash
cd backend
python scripts/test_bse_collector.py
```

### 2. 集成到每日采集

采集器已集成到每日更新任务中，会自动运行：

```bash
cd backend
python -m data_job.utils.scheduler --mode daily
```

### 3. 手动运行

```bash
cd backend
python -m data_job.collectors.bse_index_collector
```

## Dashboard 集成

北证50指数已添加到Dashboard API的显示列表中：

- 位置: `backend/app/api/v1/quant.py`
- main_indices: 添加了 `'899560'`

前端Market页面会自动显示所有指数，包括北证50。

## 当前状态

- ✅ 采集器框架已完成
- ✅ 多源数据源配置完成
- ✅ 集成到每日调度任务
- ✅ Dashboard API已更新
- ⏳ 待网络稳定后验证数据采集

## 未来扩展

可添加的北交所指数：
- 北交所其他板块指数（如推出）
- 北交所行业分类指数

添加新指数只需在 `bse_index_collector.py` 中的 `self.index_list` 添加配置：

```python
self.index_list = [
    {"code": "899560", "name": "北证50", "market": "bse"},
    {"code": "XXXXXX", "name": "新指数名称", "market": "bse"},
]
```

## 注意事项

1. 北交所指数代码在通达信/东方财富系统中通常以1.899560格式表示
2. 网络不稳定时可能需要多次重试
3. 数据会自动与表中原有数据去重 (使用 PRIMARY KEY 约束)
