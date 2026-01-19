# 数据采集模块归档目录

本目录包含数据采集模块重构前的遗留代码和文档，仅作为备份保留，**请勿在生产环境中使用**。

---

## 📁 目录结构

```
backup/
├── README.md                              # 本说明文件
│
├── legacy_collectors/                     # 旧版采集脚本（已废弃）
│   ├── update_capital_flow.py            # 资金流向采集
│   ├── update_etf_info.py                # ETF信息采集
│   ├── update_etf_kline.py               # ETF K线采集
│   ├── update_finance_summary.py         # 财务摘要采集
│   ├── update_limit_boards.py            # 连板数据采集
│   ├── update_macro_data.py              # 宏观数据采集
│   ├── update_news.py                    # 新闻舆情采集
│   ├── update_sector_kline.py            # 板块K线采集
│   ├── update_stock_kline.py             # 个股K线采集
│   ├── update_stock_sector_list.py       # 股票-板块映射采集
│   └── update_stock_valuation.py         # 股票估值采集
│
├── deprecated_docs/                       # 旧版文档（已废弃）
│   ├── ENHANCEMENTS.md                    # 功能增强说明（已整合到新README）
│   └── VERIFICATION_REPORT.md            # 验证报告（已过时）
│
└── [初始化脚本]                            # 历史初始化脚本（仅作参考）
    ├── init_capital_data.py              # 初始化资金数据
    ├── init_finance_summary.py           # 初始化财务数据
    ├── init_north_full.py                # 初始化北向资金
    ├── init_sector_data.py               # 初始化板块数据
    ├── init_sector_kline.py              # 初始化板块K线
    ├── init_valuation.py                 # 初始化估值数据
    ├── fix_hot_concepts.py               # 修复热门概念
    └── update_fundamentals.py            # 更新基本面数据
```

---

## ⚠️ 重要说明

### 为什么这些代码被归档？

1. **代码重复率高**：这些脚本包含80%的重复代码
2. **无统一架构**：每个脚本都是独立实现，难以维护
3. **缺少测试**：没有单元测试和集成测试
4. **缺少监控**：没有统一的日志、进度跟踪和错误处理

### 应该使用什么？

新的采集系统位于 `../collectors/` 目录，具有以下优势：

- ✅ 所有采集器继承自 `BaseCollector` 基类
- ✅ 统一的错误处理和重试机制
- ✅ 完整的测试覆盖（>60%）
- ✅ 模块化设计（common/, config/, core/）
- ✅ 完善的文档和使用指南

**新采集器使用方法**：

```python
# 运行单个采集器
from data_job.collectors import StockValuationCollector
collector = StockValuationCollector()
collector.run()

# 运行所有采集器
python -m data_job.utils.run_all_collectors --mode all
```

---

## 📊 迁移映射表

| 旧脚本 | 新采集器 | 状态 |
|--------|----------|------|
| `update_stock_valuation.py` | `StockValuationCollector` | ✅ 已迁移 |
| `update_macro_data.py` | `MacroDataCollector` | ✅ 已迁移 |
| `update_limit_boards.py` | `LimitBoardsCollector` | ✅ 已迁移 |
| `update_stock_kline.py` | `StockKlineCollector` | ✅ 已迁移 |
| `update_sector_kline.py` | `SectorKlineCollector` | ✅ 已迁移 |
| `update_etf_kline.py` | `ETFKlineCollector` | ✅ 已迁移 |
| `update_capital_flow.py` | `FundHoldingsCollector` | ✅ 已迁移 |
| `update_etf_info.py` | `ETFInfoCollector` | ✅ 已迁移 |
| `update_finance_summary.py` | `FinanceSummaryCollector` | ✅ 已迁移 |
| `update_news.py` | `NewsCollector` | ✅ 已迁移 |
| `update_stock_sector_list.py` | `StockSectorListCollector` | ✅ 已迁移 |

---

## 🔄 恢复旧代码（仅限紧急情况）

**警告**：只有在以下情况下才考虑使用旧代码：
1. 新采集器出现严重bug且无法快速修复
2. 需要回滚到之前的版本
3. 调试和对比新旧实现的差异

### 恢复步骤

```bash
# 1. 停止新采集系统
# 2. 复制旧脚本回工作目录
cp backup/legacy_collectors/update_xxx.py ../

# 3. 安装依赖（如果需要）
pip install akshare pandas sqlalchemy

# 4. 运行旧脚本
cd ../
python update_xxx.py
```

---

## 📅 归档信息

- **归档日期**: 2026-01-19
- **重构版本**: v2.0.0
- **归档原因**: 架构重构，代码重复率从80%降至<5%
- **维护状态**: 不再维护

---

## 📞 技术支持

如有问题，请参考：
- 新系统文档: `../README.md`
- 测试套件: `../tests/`
- 工具脚本: `../utils/`

---

**最后更新**: 2026-01-19
