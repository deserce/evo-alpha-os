# 量化引擎运行器使用指南

> **创建时间**: 2026-01-20
> **版本**: v1.0

---

## 📦 运行器模块

### 1. FeatureRunner（因子计算运行器）

批量运行所有RPS计算器的统一入口。

#### 使用方法

```bash
# 进入 backend 目录
cd backend

# 运行所有RPS计算器（增量更新）
python3 -m quant_engine.runner.feature_runner

# 只运行个股和板块RPS
python3 -m quant_engine.runner.feature_runner --calculators stock sector

# 全量初始化所有计算器
python3 -m quant_engine.runner.feature_runner --mode init

# 全量初始化个股RPS
python3 -m quant_engine.runner.feature_runner --calculators stock --mode init
```

#### 参数说明

- `--calculators` / `-c`: 指定要运行的计算器
  - 可选值: `stock`, `sector`, `etf`
  - 默认: 运行所有计算器

- `--mode` / `-m`: 运行模式
  - `daily`: 增量更新（默认），只算最近3天
  - `init`: 全量初始化，重算所有历史数据

#### 运行示例

```bash
# 示例1: 每日增量更新（推荐）
python3 -m quant_engine.runner.feature_runner
# 输出:
# 🚀 开始批量计算RPS因子
# 📋 计算器列表: ['stock', 'sector', 'etf']
# 📅 运行模式: daily
# ✅ [STOCK] 完成！耗时: 12.4秒
# ✅ [SECTOR] 完成！耗时: 1.1秒
# ✅ [ETF] 完成！耗时: 0.2秒
# 📊 批量计算完成: 成功: 3/3, 总耗时: 13.7秒

# 示例2: 只更新板块RPS
python3 -m quant_engine.runner.feature_runner --calculators sector

# 示例3: 全量初始化（首次运行或修复数据）
python3 -m quant_engine.runner.feature_runner --mode init
```

---

### 2. StrategyRunner（策略选股运行器）

运行策略选股，支持指定日期。

#### 使用方法

```bash
# 进入 backend 目录
cd backend

# 列出所有可用策略
python3 -m quant_engine.runner.strategy_runner --list

# 运行MRGC策略（使用最新交易日）
python3 -m quant_engine.runner.strategy_runner --strategy mrgc

# 运行MRGC策略（指定日期）
python3 -m quant_engine.runner.strategy_runner --strategy mrgc --date 2026-01-19
```

#### 参数说明

- `--strategy` / `-s`: 策略名称（必需）
  - `mrgc`: 陶博士每日观察（MRGC + SXHCG）

- `--date` / `-d`: 选股日期（可选）
  - 格式: `YYYY-MM-DD`
  - 默认: 最新交易日

- `--list` / `-l`: 列出所有可用策略

#### 运行示例

```bash
# 示例1: 查看可用策略
python3 -m quant_engine.runner.strategy_runner --list
# 输出:
# 📋 可用策略列表:
#    - mrgc: 陶博士每日观察

# 示例2: 使用最新日期选股
python3 -m quant_engine.runner.strategy_runner --strategy mrgc
# 输出:
# 🚀 开始执行策略选股
# 📋 策略名称: 陶博士每日观察
# 📅 选股日期: 2026-01-19
# ✅ 策略执行完成！耗时: 15.3秒

# 示例3: 指定历史日期选股（回测）
python3 -m quant_engine.runner.strategy_runner --strategy mrgc --date 2026-01-15
```

---

## 🔄 自动化集成

### 与数据采集系统集成

在数据采集完成后自动触发RPS计算，有以下几种方案：

#### 方案A：修改 data_job 的调度脚本

在 `backend/data_job/scripts/daily_scheduler.py` 中添加RPS计算：

```python
from quant_engine.runner.feature_runner import FeatureRunner

def run_daily_update():
    # 1. 运行数据采集
    # ... 数据采集代码 ...

    # 2. 数据采集成功后，自动运行RPS计算
    logger.info("🔄 数据采集完成，开始RPS计算...")
    runner = FeatureRunner()
    results = runner.run(mode='daily')

    if all(r.get('success') for r in results.values()):
        logger.info("✅ RPS计算完成")
    else:
        logger.error("❌ RPS计算失败")
```

#### 方案B：使用 cron 定时任务

```bash
# 编辑 crontab
crontab -e

# 每天下午3点运行数据采集和RPS计算
0 15 * * 1-5 cd /path/to/EvoAlpha-OS/backend && python3 -m data_job.scripts.daily_scheduler && python3 -m quant_engine.runner.feature_runner
```

#### 方案C：使用独立调度器（推荐用于生产环境）

参见 `backend/AUTOMATION_PLAN.md` 中的详细方案。

---

## 📊 数据流示意图

```
┌─────────────────────┐
│  数据采集系统        │
│  (data_job)         │
└──────────┬──────────┘
           │ 数据采集完成
           ▼
┌─────────────────────┐
│  FeatureRunner      │
│  - stock RPS        │
│  - sector RPS       │
│  - etf RPS          │
└──────────┬──────────┘
           │ RPS数据就绪
           ▼
┌─────────────────────┐
│  StrategyRunner     │
│  - MRGC选股         │
│  - 其他策略          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  quant_strategy_    │
│  results表          │
└─────────────────────┘
```

---

## 🐛 常见问题

### Q1: 运行时报错 "ModuleNotFoundError: No module named 'quant_engine'"

**解决方案**: 需要从 `backend` 目录运行命令

```bash
cd backend
python3 -m quant_engine.runner.feature_runner
```

### Q2: 策略运行时报错 "没有因子数据"

**原因**: 指定日期的RPS数据不存在

**解决方案**:
1. 先运行RPS计算生成数据
2. 确保日期有交易日数据
3. 检查数据库中是否有该日期的记录

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

---

## 📝 开发指南

### 添加新的计算器

1. 在 `calculators/` 目录创建新的计算器类
2. 继承 `BaseFeatureCalculator`
3. 在 `runner/feature_runner.py` 中注册

```python
# runner/feature_runner.py
from quant_engine.calculators.new_calculator import NewCalculator

class FeatureRunner:
    def __init__(self):
        self.calculators = {
            'stock': StockRPSCalculator(),
            'sector': SectorRPSCalculator(),
            'etf': ETFRPSCalculator(),
            'new': NewCalculator(),  # 新增
        }
```

### 添加新的策略

1. 在 `strategies/` 目录创建新的策略类
2. 继承 `BaseStrategy`
3. 在 `runner/strategy_runner.py` 中注册

```python
# runner/strategy_runner.py
from quant_engine.strategies.new_strategy import NewStrategy

STRATEGY_REGISTRY = {
    'mrgc': MrgcStrategy,
    'new': NewStrategy,  # 新增
}
```

---

## ✅ 验证清单

- [x] FeatureRunner 可以批量运行所有RPS计算器
- [x] FeatureRunner 支持单独运行指定计算器
- [x] FeatureRunner 支持增量/全量两种模式
- [x] StrategyRunner 可以列出所有策略
- [x] StrategyRunner 支持指定日期选股
- [x] StrategyRunner 默认使用最新交易日

---

## 🚀 未来计划

- [ ] 添加更多策略
- [ ] 实现策略回测功能
- [ ] 添加数据验证工具
- [ ] 集成到统一调度系统
- [ ] 添加Web UI界面

---

*文档更新时间: 2026-01-20*
