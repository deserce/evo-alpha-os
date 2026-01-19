# 数据采集频率需求表

> **用途**: 规划所有数据采集器的自动化采集频率
> **创建时间**: 2026-01-19
> **填写说明**: 请在"最终采集频率"列填写您选择的频率

---

## 📊 采集器频率规划表

| # | 采集器名称 | 数据表 | 数据特点 | 建议频率 | 最终频率 | 备注 |
|---|-----------|--------|---------|---------|---------|------|
| 1 | StockValuationCollector<br/>股票估值 | `stock_valuation_daily` | PE、PB、市值等实时指标 | **每天** | ______ | ⚡ 市场数据，需每日更新 |
| 2 | MacroDataCollector<br/>宏观数据 | `macro_indicators` | GDP、CPI、PMI等 | **每月** | ______ | 📊 官方月度/季度数据 |
| 3 | LimitBoardsCollector<br/>连板数据 | `limit_board_trading`<br/>`consecutive_boards_stats` | 涨停板、连板统计 | **每天** | ______ | 📈 每日交易数据 |
| 4 | StockKlineCollector<br/>个股K线 | `stock_daily_prices` | 5472只股票日级行情 | **每天** | ______ | 📈 核心：每日行情数据 |
| 5 | SectorKlineCollector<br/>板块K线 | `sector_daily_prices` | 86个板块指数日级行情 | **每天** | ______ | 📈 每日行情数据 |
| 6 | ETFKlineCollector<br/>ETF K线 | `etf_daily_prices` | ETF基金日级行情 | **每天** | ______ | 📈 每日行情数据 |
| 7 | FundHoldingsCollector<br/>基金持股 | `finance_fund_holdings` | 基金季度持仓数据 | **每季度** | ______ | 💰 季报数据，每季度更新 |
| 8 | NorthboundHoldingsCollector<br/>北向资金持股 | `stock_northbound_holdings` | 历史北向资金持仓 | ⛔ **不再更新** | N/A | 🚨 数据截至2024-08-16，监管停止披露 |
| 9 | ETFInfoCollector<br/>ETF信息 | `etf_info` | ETF基础信息、规模等 | **每月** | ______ | 📊 信息变化不频繁 |
| 10 | FinanceSummaryCollector<br/>财务摘要 | `stock_finance_summary` | 财务业绩报表 | **每季度** | ______ | 💰 季报/年报数据 |
| 11 | NewsCollector<br/>新闻舆情 | `news_articles`<br/>`news_stock_relation` | 财经新闻、情绪分析 | **每天** | ______ | 📰 每日新闻数据 |
| 12 | StockSectorListCollector<br/>股票-板块映射 | `stock_info`<br/>`stock_sector_map` | 股票列表、板块分类 | **每月** | ______ | 📊 股票列表相对稳定 |

---

## 🔄 采集频率选项说明

### 可选频率

| 频率 | 说明 | 适用场景 | 示例 |
|------|------|----------|------|
| **实时** | 每分钟或每小时 | 极高频数据 | 分时行情（当前未实现） |
| **每天** | 每个交易日采集一次 | 日级行情、估值、新闻 | K线、涨跌停、新闻 |
| **每周** | 每周采集一次 | 周度数据 | （当前未使用） |
| **每月** | 每月采集一次 | 月度数据、稳定信息 | 宏观指标、ETF信息 |
| **每季度** | 每季度采集一次 | 季报数据 | 基金持仓、财务摘要 |
| **每年** | 每年采集一次 | 年度数据 | （当前未使用） |
| **一次性** | 只采集一次 | 历史数据、固定数据 | 北向资金历史数据 |
| **不再更新** | 停止采集 | 数据源停止 | 已停止披露的数据 |

### 采集时间建议

| 频率 | 建议采集时间 | 说明 |
|------|-------------|------|
| 每天 | 收盘后 15:30-16:30 | 确保当日数据完整 |
| 每周 | 每周一上午 | 采集上周完整数据 |
| 每月 | 每月1-3号 | 采集上月完整数据 |
| 每季度 | 季报发布后1周 | 等待财报全部披露完成 |

---

## 📅 当前数据更新情况

### 已实现自动化的采集器

✅ **每日更新** (7个)
- StockValuationCollector - 股票估值
- LimitBoardsCollector - 连板数据
- StockKlineCollector - 个股K线
- SectorKlineCollector - 板块K线
- ETFKlineCollector - ETF K线
- NewsCollector - 新闻舆情

📅 **定期更新** (4个)
- MacroDataCollector - 宏观数据（建议每月）
- FundHoldingsCollector - 基金持股（建议每季度）
- ETFInfoCollector - ETF信息（建议每月）
- FinanceSummaryCollector - 财务摘要（建议每季度）
- StockSectorListCollector - 股票列表（建议每月）

⛔ **历史数据** (1个)
- NorthboundHoldingsCollector - 北向资金持股（已停止披露）

---

## 💡 填写指南

### 如何选择采集频率？

1. **数据时效性要求**
   - 高时效性 → 每天
   - 中时效性 → 每周/每月
   - 低时效性 → 每季度

2. **数据源更新频率**
   - 数据源每天更新 → 每天采集
   - 数据源每月更新 → 每月采集
   - 数据源季度更新 → 每季度采集

3. **业务重要性**
   - 核心数据 → 高频采集
   - 辅助数据 → 低频采集

4. **资源消耗**
   - API限制 → 适当降低频率
   - 服务器负载 → 错峰采集

---

## 🚀 自动化实现计划

### Phase 1: 配置文件化 (当前)
- [ ] 创建采集频率配置文件 `collection_schedule.yaml`
- [ ] 每个采集器关联采集频率
- [ ] 支持灵活配置

### Phase 2: 定时任务集成
- [ ] 使用 APScheduler 或 celery 实现定时调度
- [ ] 根据配置自动触发采集器
- [ ] 支持手动触发和补采

### Phase 3: 监控与告警
- [ ] 采集状态监控
- [ ] 失败重试机制
- [ ] 数据质量检查
- [ ] 异常告警通知

---

## ✅ 最终采集频率配置

用户已确认采用建议的分类配置：

```
[✅] 1. StockValuationCollector    → 每天 15:30
[✅] 2. MacroDataCollector         → 每月1号
[✅] 3. LimitBoardsCollector       → 每天 15:30
[✅] 4. StockKlineCollector        → 每天 15:30
[✅] 5. SectorKlineCollector       → 每天 15:30
[✅] 6. ETFKlineCollector          → 每天 15:30
[✅] 7. FundHoldingsCollector      → 每季度15号
[✅] 8. NorthboundHoldingsCollector → N/A (已完成)
[✅] 9. ETFInfoCollector           → 每月1号
[✅] 10. FinanceSummaryCollector   → 每季度15号
[✅] 11. NewsCollector             → 每天 16:00
[✅] 12. StockSectorListCollector  → 每月1号
```

**配置文件**：`config/collection_schedule.yaml`

**确认配置**：

| 采集器类型 | 采集器 | 建议频率 | 采集时间 |
|-----------|--------|---------|---------|
| 📈 行情数据 (5个) | StockKline, SectorKline, ETFKline, StockValuation, LimitBoards | **每天** | 15:30 (收盘后) |
| 📰 舆情数据 (1个) | News | **每天** | 16:00 |
| 💰 财务数据 (2个) | FundHoldings, FinanceSummary | **每季度** | 季报后1周 |
| 📊 基础数据 (3个) | MacroData, ETFInfo, StockSectorList | **每月** | 每月1号 |
| 🚨 历史数据 (1个) | NorthboundHoldings | **不再更新** | - |

---

## 🎯 下一步

1. ✅ 用户填写采集频率
2. ⏰ 创建 `collection_schedule.yaml` 配置文件
3. 🔧 实现定时调度系统
4. 📊 添加采集监控和告警
5. 🚀 部署自动化采集任务

---

**创建时间**: 2026-01-19 22:59
**状态**: 等待用户填写
**版本**: v1.0
