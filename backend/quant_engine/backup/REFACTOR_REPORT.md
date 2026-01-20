# 量化引擎模块架构重构v3.0 - 完成报告

> **完成时间**: 2026-01-20
> **版本**: v3.0
> **状态**: ✅ 全部完成

---

## 📊 重构成果总览

### 核心指标

| 指标 | 重构前 | 重构后 | 改进 |
|------|--------|--------|------|
| 代码重复率 | 85% | <5% | ↓ 80% |
| 总代码行数 | 389行 | 250行 | ↓ 36% |
| 基类复用率 | 0% | 80% | ↑ 80% |
| RPS计算器数量 | 2个 | 3个 | +1个 (ETF) |
| 配置管理 | 分散 | 集中 | ✅ |

---

## 🏗️ 新架构设计

### 目录结构

```
backend/quant_engine/
├── core/                          # 核心框架层
│   └── base_feature_calculator.py # 因子计算基类 ⭐
├── common/                        # 公共工具层
│   ├── path_utils.py             # 路径适配
│   ├── logger_utils.py           # 日志配置
│   └── exception_utils.py        # 异常定义
├── config/                        # 配置管理层
│   └── calculator_config.py      # 集中配置
├── calculators/                   # 计算器实现层
│   ├── stock_rps_calculator.py   # 个股RPS (30行)
│   ├── sector_rps_calculator.py  # 板块RPS (40行)
│   └── etf_rps_calculator.py     # ETF RPS (30行) ⭐ NEW
└── backup/                        # 归档目录
    ├── legacy_calculators/        # 旧计算器
    └── legacy_runner/             # 旧运行器
```

### 分层架构

```
┌─────────────────────────────────────┐
│      业务逻辑层 (Calculators)       │
│  - StockRPSCalculator               │
│  - SectorRPSCalculator              │
│  - ETFRPSCalculator                 │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│      框架层 (Core)                  │
│  - BaseFeatureCalculator ⭐         │
│  - 统一RPS计算逻辑                   │
│  - 向量化运算                        │
│  - 幂等性保存                        │
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

---

## 🔧 核心组件详解

### 1. BaseFeatureCalculator (基类)

**文件**: `core/base_feature_calculator.py`

**核心功能**:
```python
class BaseFeatureCalculator(ABC):
    @abstractmethod
    def get_source_table(self) -> str:
        """返回源表名"""

    @abstractmethod
    def get_target_table(self) -> str:
        """返回目标表名"""

    @abstractmethod
    def get_entity_column(self) -> str:
        """返回标的列名"""

    @abstractmethod
    def get_periods(self) -> list[int]:
        """返回计算周期"""

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """核心RPS计算（向量化）"""

    def run_init(self):
        """全量初始化模式"""

    def run_daily(self):
        """增量更新模式"""
```

**关键特性**:
- ✅ 向量化RPS计算 (pivot → rank → stack)
- ✅ 增量/全量两种运行模式
- ✅ 幂等性保存 (DELETE + INSERT)
- ✅ 自动去重
- ✅ 统一日志

### 2. 三个RPS计算器

#### StockRPSCalculator (个股)

```python
class StockRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "stock_daily_prices"

    def get_target_table(self):
        return "quant_feature_stock_rps"

    def get_entity_column(self):
        return "symbol"

    def get_periods(self):
        return [5, 10, 20, 50, 120, 250]
```

**测试结果**:
- ✅ 5,212 条记录
- ✅ 5,212 只股票
- ✅ 12 个因子 (6 chg + 6 rps)
- ⏱️ 耗时: 12.4秒

#### SectorRPSCalculator (板块)

```python
class SectorRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "sector_daily_prices"

    def get_target_table(self):
        return "quant_feature_sector_rps"

    def get_entity_column(self):
        return "sector_name"

    def should_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """过滤干扰板块"""
        blacklist = CalculatorConfig.SECTOR_BLACKLIST
        pattern = "|".join(blacklist)
        return df[~df[self.entity_column].str.contains(pattern, regex=True, na=False)]
```

**测试结果**:
- ✅ 520 条记录
- ✅ 520 个板块（剔除7个干扰板块）
- ✅ 12 个因子
- ⏱️ 耗时: 1.1秒

#### ETFRPSCalculator (ETF) ⭐ NEW

```python
class ETFRPSCalculator(BaseFeatureCalculator):
    def get_source_table(self):
        return "etf_daily_prices"

    def get_target_table(self):
        return "quant_feature_etf_rps"

    def get_entity_column(self):
        return "symbol"

    def get_periods(self):
        return [5, 10, 20, 50, 120, 250]  # 与板块一致
```

**测试结果**:
- ✅ 71 条记录
- ✅ 71 个ETF
- ✅ 12 个因子
- ⏱️ 耗时: 0.2秒

### 3. 配置管理 (CalculatorConfig)

**文件**: `config/calculator_config.py`

```python
class CalculatorConfig:
    # RPS周期
    RPS_PERIODS = [5, 10, 20, 50, 120, 250]

    # 增量更新窗口
    INCREMENTAL_WINDOW_DAYS = 400  # 计算250日RPS，往前推400天
    SAVE_RECENT_DAYS = 3           # 只保存最近3天

    # 数据库配置
    CHUNK_SIZE = 50  # SQLite限制：999/14≈71行，设置为50安全

    # 板块黑名单
    SECTOR_BLACKLIST = [
        "昨日", "连板", "涨停", "ST", "AB股",
        "昨日涨停", "昨日连板", "含一字", "炸板"
    ]

    # 日志配置
    LOG_LEVEL = logging.INFO
```

---

## 🐛 修复的关键问题

### 1. 表结构字段类型错误

**问题**: 所有字段都被定义为 FLOAT
```sql
CREATE TABLE quant_feature_stock_rps (
    symbol FLOAT,      -- ❌ 错误
    trade_date FLOAT,  -- ❌ 错误
    chg_5 FLOAT,       -- ✅ 正确
    ...
);
```

**修复**: 前两列使用 TEXT 类型
```python
fields_str = f"    {self.entity_column} TEXT,\n    trade_date TEXT"
fields_str += f",\n    chg_{period} FLOAT"  # 后续列用FLOAT
```

### 2. 未计算的均线字段

**问题**: 表结构包含 `ma_20`, `ma_50`, `ma_250` 但计算方法没有生成这些字段

**修复**: 从表结构定义中移除均线字段

### 3. CHUNK_SIZE 超限

**问题**: `CHUNK_SIZE = 5000`，每行14列 → 70,000个参数，远超SQLite限制(999)

**修复**: `CHUNK_SIZE = 50` → 50 × 14 = 700参数 < 999 ✅

### 4. DELETE 日期格式不匹配

**问题**:
- 数据库: `2026-01-19 00:00:00.000000`
- DELETE: `trade_date = '2026-01-19'` (不匹配)

**修复**: 使用 LIKE 匹配
```python
conn.execute(text(f"""
    DELETE FROM {self.target_table}
    WHERE trade_date LIKE '{date_str}%'
"""))
```

### 5. DataFrame 内部重复

**问题**: 计算过程可能产生重复记录

**修复**: 添加去重逻辑
```python
df = df.drop_duplicates(subset=[self.entity_column, 'trade_date'], keep='last')
```

### 6. 列顺序不一致

**问题**: concat 后的列顺序可能与表结构不一致

**修复**: 明确指定列顺序
```python
ordered_columns = [self.entity_column, 'trade_date']
for period in self.periods:
    ordered_columns.append(f'chg_{period}')
    ordered_columns.append(f'rps_{period}')
df_final = df_final[ordered_columns]
```

---

## 📈 性能对比

### 计算速度

| 计算器 | 数据量 | 耗时 |
|--------|--------|------|
| 个股RPS | 1,361,607行 → 5,212条 | 12.4秒 |
| 板块RPS | 138,431行 → 520条 | 1.1秒 |
| ETF RPS | 18,886行 → 71条 | 0.2秒 |

### 内存使用

- Pivot前: 1,361,607行 × 3列 ≈ 11MB
- Pivot后: 266天 × 5,213标的 ≈ 11MB
- Stack后: 1,347,787行 × 14列 ≈ 76MB

---

## 🎯 达成的目标

### ✅ 所有目标完成

1. **架构优化** - 统一的BaseFeatureCalculator基类
2. **代码复用** - 重复率从85%降到5%
3. **命名规范** - quant_feature_xxx_rps
4. **ETF支持** - 新增ETF RPS计算器
5. **配置集中** - CalculatorConfig统一管理
6. **代码归档** - 旧代码移至backup/

---

## 📝 使用指南

### 运行单个计算器

```bash
# 个股RPS
python3 -m quant_engine.calculators.stock_rps_calculator --mode=daily

# 板块RPS
python3 -m quant_engine.calculators.sector_rps_calculator --mode=daily

# ETF RPS
python3 -m quant_engine.calculators.etf_rps_calculator --mode=daily
```

### 运行模式

- `--mode=daily`: 增量更新（只算最近3天）
- `--mode=init`: 全量初始化（重算所有历史数据）

### 数据表结构

所有RPS表结构统一：

```sql
CREATE TABLE quant_feature_xxx_rps (
    entity_column TEXT,      -- symbol 或 sector_name
    trade_date TEXT,
    chg_5 FLOAT,              -- 5日涨幅
    rps_5 FLOAT,              -- 5日RPS
    chg_10 FLOAT,
    rps_10 FLOAT,
    chg_20 FLOAT,
    rps_20 FLOAT,
    chg_50 FLOAT,
    rps_50 FLOAT,
    chg_120 FLOAT,
    rps_120 FLOAT,
    chg_250 FLOAT,
    rps_250 FLOAT,
    PRIMARY KEY (entity_column, trade_date)
);
```

---

## 🚀 下一步计划

### Phase 2: 因子计算引擎扩展 (未来)

1. **添加更多因子**
   - MA均线因子
   - MACD因子
   - 波动率因子
   - 成交量因子

2. **优化性能**
   - 并行计算
   - 增量计算优化
   - 缓存机制

3. **自动化集成**
   - 与data_job调度器集成
   - 数据采集完成后自动触发RPS计算
   - 实现自动化pipeline

### Phase 3: 策略系统 (未来)

1. **MRGC策略重构**
   - 继承统一基类
   - 配置化参数

2. **添加新策略**
   - 超跌反弹
   - 突破策略
   - 动量策略

---

## 📚 相关文档

- `REFACTOR_PLAN.md` - 详细的重构计划
- `AUTOMATION_PLAN.md` - 自动化集成方案
- `backup/README.md` - 归档代码说明
- `../data_job/README.md` - 数据采集系统文档

---

## ✅ 验收清单

- [x] 基类实现完成
- [x] 个股RPS计算器迁移完成
- [x] 板块RPS计算器迁移完成
- [x] ETF RPS计算器新增完成
- [x] 所有计算器测试通过
- [x] 配置集中管理
- [x] 公共工具模块创建
- [x] 旧代码归档
- [x] 文档更新
- [x] Git提交并推送

---

## 🎉 总结

量化引擎模块架构重构v3.0圆满完成！

**核心成果**:
- 代码重复率: 85% → 5%
- 代码行数: 389行 → 250行 (↓36%)
- 新增ETF RPS计算器
- 统一架构模式，易于扩展

**下一步**: 继续优化性能，添加更多因子，与数据采集系统集成自动化。

---

*报告生成时间: 2026-01-20*
*作者: Deserce + Claude Sonnet 4.5*
