# EvoAlpha OS - 量化引擎模块

> **版本**: v3.0
> **最后更新**: 2026-01-20
> **状态**: ✅ 生产就绪

---

## 📋 目录

- [模块简介](#模块简介)
- [核心功能](#核心功能)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [架构设计](#架构设计)
- [使用指南](#使用指南)
- [开发指南](#开发指南)
- [常见问题](#常见问题)

---

## 模块简介

EvoAlpha 量化引擎是一个模块化、高性能的量化选股系统，提供：

- ✅ **RPS因子计算** - 个股、板块、ETF的相对价格强度计算
- ✅ **策略选股** - 基于技术指标的量化策略执行
- ✅ **股票池管理** - 基于基本面（基金、北向资金）的核心股票池维护
- ✅ **通达信公式** - 完整的技术指标计算库

### 核心特性

- 🚀 **高性能** - 向量化计算，支持增量更新
- 🔧 **易扩展** - 统一的基类架构，添加新策略/因子只需继承
- 📊 **生产就绪** - 完善的错误处理、日志记录、幂等性保证
- 🎯 **统一规范** - 标准化的表结构、命名规范、配置管理

---

## 核心功能

### 1. 因子计算引擎

**支持的RPS计算器**：
- `StockRPSCalculator` - 个股RPS（5/10/20/50/120/250日）
- `SectorRPSCalculator` - 板块RPS（5/10/20/50/120/250日）
- `ETFRPSCalculator` - ETF RPS（5/10/20/50/120/250日）

**计算特点**：
- 向量化计算（Pandas）
- 增量/全量两种模式
- 幂等性保证
- 自动去重

### 2. 策略选股系统

**内置策略**：
- `MrgcStrategy` - 陶博士MRGC形态策略
  - MRGC信号（新高突破、接近新高、深度回调反弹）
  - SXHCG信号（双RPS强势 + 均线多头排列）

**策略特点**：
- 基于BaseStrategy的统一架构
- 支持指定日期选股
- 详细的策略元数据（说明、逻辑、筛选条件）
- 结果保存到`quant_preselect_results`表

### 3. 股票池管理

**核心股票池筛选条件**：
- 基金持股比例 ≥ 5%（最近3季度任意满足）
- 北向资金持仓 ≥ 1亿元

**维护周期**：每季度更新一次

### 4. 通达信公式库

**支持的技术指标**：
- `MA` - 移动平均线
- `HHV/LLV` - N日最高/最低值
- `REF` - 引用N天前数据
- `COUNT/EVERY` - 条件统计
- `calc_dynamic_drawdown` - 动态回撤计算

---

## 目录结构

```
backend/quant_engine/
├── README.md                    # 本文档
│
├── core/                        # 核心框架层
│   ├── __init__.py
│   ├── base_feature_calculator.py  # 因子计算基类 ⭐
│   └── tdx_lib.py               # 通达信公式库
│
├── common/                      # 公共工具层
│   ├── __init__.py
│   ├── exception_utils.py       # 异常定义
│   ├── logger_utils.py          # 日志配置
│   └── path_utils.py            # 路径适配
│
├── config/                      # 配置管理层
│   ├── __init__.py
│   └── calculator_config.py     # 集中配置
│
├── calculators/                 # 因子计算器
│   ├── __init__.py
│   ├── stock_rps_calculator.py  # 个股RPS
│   ├── sector_rps_calculator.py # 板块RPS
│   └── etf_rps_calculator.py    # ETF RPS
│
├── strategies/                  # 策略实现
│   ├── __init__.py
│   ├── base_strategy.py         # 策略基类
│   ├── mrgc_strategy.py         # MRGC策略
│   └── select_resonance.py      # 板块共振策略
│
├── runner/                      # 运行器
│   ├── __init__.py
│   ├── feature_runner.py        # 因子计算运行器
│   └── strategy_runner.py       # 策略选股运行器
│
├── pool/                        # 股票池管理
│   ├── __init__.py
│   └── maintain_pool.py         # 股票池维护
│
├── scripts/                     # 脚本工具
│   ├── __init__.py
│   └── init_all_features.py     # 初始化所有因子
│
└── backup/                      # 归档目录
    ├── README.md                # 归档说明
    ├── legacy_calculators/      # 旧计算器
    ├── legacy_runner/           # 旧运行器
    └── legacy_strategies_mrgc_strategy_old.py
```

---

## 快速开始

### 环境要求

```bash
# 必需的Python包
pip install pandas numpy sqlalchemy
pip install akshare  # 数据采集（如需要）
```

### 初始化数据库表

```bash
# 1. 初始化所有RPS因子（首次运行，全量计算最近一年）
cd backend
python3 -m quant_engine.calculators.stock_rps_calculator --mode init
python3 -m quant_engine.calculators.sector_rps_calculator --mode init
python3 -m quant_engine.calculators.etf_rps_calculator --mode init

# 2. 维护核心股票池
python3 -m quant_engine.pool.maintain_pool
```

### 每日使用

```bash
# 1. 增量更新RPS因子（只算最近3天）
python3 -m quant_engine.runner.feature_runner

# 2. 运行策略选股
python3 -m quant_engine.runner.strategy_runner --strategy mrgc

# 3. 查看选股结果
# 查询 quant_preselect_results 表
```

---

## 架构设计

### 分层架构

```
┌─────────────────────────────────────┐
│      业务逻辑层                     │
│  - StockRPSCalculator               │
│  - SectorRPSCalculator              │
│  - ETFRPSCalculator                 │
│  - MrgcStrategy                     │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│      框架层 (Core)                  │
│  - BaseFeatureCalculator ⭐         │
│  - BaseStrategy                     │
│  - 统一RPS计算逻辑                   │
│  - 向量化运算                        │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│    基础设施层 (Common + Config)    │
│  - 路径适配                          │
│  - 日志配置                          │
│  - 异常处理                          │
│  - 配置管理                          │
└─────────────────────────────────────┘
```

### 数据流

```
原始数据 (data_job采集)
   ↓
stock_daily_prices / sector_daily_prices / etf_daily_prices
   ↓
RPS计算器 (BaseFeatureCalculator)
   ↓
quant_feature_stock_rps / quant_feature_sector_rps / quant_feature_etf_rps
   ↓
策略选股 (BaseStrategy)
   ↓
quant_preselect_results
   ↓
AI分析 / 人工决策
```

### 核心设计模式

1. **策略模式** - BaseStrategy + 具体策略实现
2. **模板方法模式** - BaseFeatureCalculator定义计算流程骨架
3. **工厂模式** - StrategyRunner根据名称创建策略实例
4. **注册表模式** - STRATEGY_REGISTRY管理所有策略

---

## 使用指南

### 因子计算

#### 运行单个计算器

```bash
# 个股RPS - 增量更新
python3 -m quant_engine.calculators.stock_rps_calculator --mode daily

# 个股RPS - 全量初始化
python3 -m quant_engine.calculators.stock_rps_calculator --mode init
```

#### 批量运行所有计算器

```bash
# 运行所有RPS计算器（增量）
python3 -m quant_engine.runner.feature_runner

# 只运行指定的计算器
python3 -m quant_engine.runner.feature_runner --calculators stock sector

# 全量初始化
python3 -m quant_engine.runner.feature_runner --mode init
```

### 策略选股

#### 列出可用策略

```bash
python3 -m quant_engine.runner.strategy_runner --list
```

#### 运行MRGC策略

```bash
# 使用最新交易日
python3 -m quant_engine.runner.strategy_runner --strategy mrgc

# 指定日期
python3 -m quant_engine.runner.strategy_runner --strategy mrgc --date 2026-01-19
```

### 股票池维护

```bash
# 重建核心股票池
python3 -m quant_engine.pool.maintain_pool
```

---

## 开发指南

### 添加新的RPS计算器

1. 创建新的计算器类，继承`BaseFeatureCalculator`

```python
# quant_engine/calculators/new_rps_calculator.py

from quant_engine.core.base_feature_calculator import BaseFeatureCalculator

class NewRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self) -> str:
        return "your_source_table"

    def get_target_table(self) -> str:
        return "quant_feature_new_rps"

    def get_entity_column(self) -> str:
        return "symbol"  # 或 "sector_name"

    def get_periods(self) -> list:
        return [5, 10, 20, 50, 120, 250]

    def should_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        # 可选：数据过滤逻辑
        return df
```

2. 在FeatureRunner中注册

```python
# runner/feature_runner.py
from quant_engine.calculators.new_rps_calculator import NewRPSCalculator

self.calculators = {
    'stock': StockRPSCalculator(),
    'sector': SectorRPSCalculator(),
    'etf': ETFRPSCalculator(),
    'new': NewRPSCalculator(),  # 新增
}
```

### 添加新策略

1. 创建新的策略类，继承`BaseStrategy`

```python
# quant_engine/strategies/new_strategy.py

from quant_engine.strategies.base_strategy import BaseStrategy
import pandas as pd

class NewStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("new_v1")

        # 策略元数据
        self.strategy_display_name = "新策略名称"
        self.strategy_description = "策略描述..."
        self.strategy_logic = "核心逻辑说明..."
        self.filter_criteria = "筛选条件说明..."

    def run(self, trade_date=None):
        """执行策略"""
        if not trade_date:
            from datetime import date
            trade_date = str(date.today())

        # 1. 获取股票池
        pool_df = self.get_stock_pool(pool_name='core_pool')
        target_symbols = pool_df['symbol'].tolist()

        # 2. 获取因子数据
        rps_df = self.get_daily_features(trade_date, target_symbols)

        # 3. 加载K线数据
        # ... 你的策略逻辑 ...

        # 4. 筛选信号
        results = []
        # ... 筛选逻辑 ...

        # 5. 保存结果
        if results:
            self.save_results(pd.DataFrame(results))
```

2. 在StrategyRunner中注册

```python
# runner/strategy_runner.py
from quant_engine.strategies.new_strategy import NewStrategy

STRATEGY_REGISTRY = {
    'mrgc': MrgcStrategy,
    'new': NewStrategy,  # 新增
}
```

3. 运行新策略

```bash
python3 -m quant_engine.runner.strategy_runner --strategy new
```

### 数据库表结构

#### quant_feature_*_rps 表

所有RPS因子表使用统一结构：

```sql
CREATE TABLE quant_feature_xxx_rps (
    symbol TEXT,              -- 或 sector_name
    trade_date TEXT,
    -- 涨幅
    chg_5 FLOAT,
    chg_10 FLOAT,
    chg_20 FLOAT,
    chg_50 FLOAT,
    chg_120 FLOAT,
    chg_250 FLOAT,
    -- RPS
    rps_5 FLOAT,
    rps_10 FLOAT,
    rps_20 FLOAT,
    rps_50 FLOAT,
    rps_120 FLOAT,
    rps_250 FLOAT,
    PRIMARY KEY (symbol, trade_date)
);
```

#### quant_stock_pool 表

```sql
CREATE TABLE quant_stock_pool (
    pool_name VARCHAR(50),
    symbol VARCHAR(20),
    name VARCHAR(100),
    add_date DATE,
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (pool_name, symbol, add_date)
);
```

#### quant_preselect_results 表

```sql
CREATE TABLE quant_preselect_results (
    strategy_name VARCHAR(50),
    strategy_display_name VARCHAR(100),
    strategy_description TEXT,
    strategy_logic TEXT,
    filter_criteria TEXT,
    result_type VARCHAR(20),
    trade_date DATE,
    symbol VARCHAR(20),
    signal_type VARCHAR(10),
    meta_info TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, trade_date, symbol, result_type)
);
```

---

## 常见问题

### Q1: 运行时报错 "ModuleNotFoundError: No module named 'quant_engine'"

**原因**: 当前工作目录不正确

**解决方案**: 必须从 `backend` 目录运行命令

```bash
cd backend
python3 -m quant_engine.runner.feature_runner
```

### Q2: 策略运行时报错 "没有因子数据"

**原因**: 指定日期的RPS数据不存在

**解决方案**:
1. 先运行RPS计算生成数据
2. 确保日期有交易日数据

```bash
# 先运行RPS计算
python3 -m quant_engine.runner.feature_runner

# 再运行策略
python3 -m quant_engine.runner.strategy_runner --strategy mrgc
```

### Q3: 增量更新模式没有数据

**原因**: 增量模式只计算最近3天，如果数据库中最新数据超过3天，会过滤掉

**解决方案**: 使用全量初始化模式

```bash
python3 -m quant_engine.runner.feature_runner --mode init
```

### Q4: 数据库表已存在错误

**原因**: 表结构冲突

**解决方案**: 表会自动创建（`CREATE TABLE IF NOT EXISTS`），如需重置：

```sql
DROP TABLE IF EXISTS quant_feature_stock_rps;
```

然后重新运行计算器。

### Q5: RPS计算很慢

**优化建议**:
1. 确保使用向量化计算（Pandas）
2. 检查数据量：全量计算400天数据约需10-20秒
3. 使用增量模式（`--mode daily`）只算最近3天

---

## 配置说明

### CalculatorConfig 配置项

位置：`config/calculator_config.py`

```python
class CalculatorConfig:
    # RPS计算周期
    RPS_PERIODS = [5, 10, 20, 50, 120, 250]

    # 增量更新窗口（天数）
    INCREMENTAL_WINDOW_DAYS = 400  # 计算250日RPS，往前推400天
    SAVE_RECENT_DAYS = 3           # 只保存最近3天

    # 数据库配置
    CHUNK_SIZE = 50  # 批量插入大小（SQLite限制）

    # 板块黑名单
    SECTOR_BLACKLIST = [
        "昨日", "连板", "涨停", "ST", "AB股",
        "昨日涨停", "昨日连板", "含一字", "炸板"
    ]

    # 日志配置
    LOG_LEVEL = logging.INFO
```

---

## 性能指标

### RPS计算性能

| 计算器 | 数据量 | 耗时 |
|--------|--------|------|
| 个股RPS | 1,361,607行 → 5,212条 | 12.4秒 |
| 板块RPS | 138,431行 → 520条 | 1.1秒 |
| ETF RPS | 18,886行 → 71条 | 0.2秒 |

### 代码质量

- 代码重复率: **<5%**（重构前85%）
- 基类复用率: **80%**
- 测试覆盖: 核心模块已验证

---

## 历史文档

历史文档已归档到 `backup/` 目录，包括：
- `backup/REFACTOR_PLAN.md` - 重构设计文档
- `backup/REFACTOR_REPORT.md` - 重构完成报告
- `backup/CLEANUP_REPORT.md` - 清理报告
- `backup/RUNNER_GUIDE.md` - 运行器详细使用指南
- `backup/IMPLEMENTATION_STATUS.md` - 实现状态报告
- `backup/README.md` - 归档代码说明

---

## 版本历史

### v3.0 (2026-01-20)
- ✅ 重构完成：统一BaseFeatureCalculator架构
- ✅ 新增ETF RPS计算器
- ✅ 优化策略基类BaseStrategy
- ✅ 添加策略运行器StrategyRunner
- ✅ 代码重复率从85%降至5%

### v2.0 (历史版本)
- 基础RPS计算功能
- MRGC策略实现
- 股票池管理

---

## 许可证

内部项目，仅供学习和研究使用。

---

**最后更新**: 2026-01-20
**维护者**: Deserce
**反馈**: 请提交Issue或Pull Request
