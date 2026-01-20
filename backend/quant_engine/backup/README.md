# Quant Engine 代码归档说明

> **归档时间**: 2026-01-20
> **版本**: v3.0 重构

---

## 📁 归档文件

### legacy_calculators/（旧的计算器）

| 文件 | 说明 | 替代为 |
|------|------|--------|
| `calc_indicators.py` | 个股RPS计算器 | `calculators/stock_rps_calculator.py` |
| `calc_sector_rps.py` | 板块RPS计算器 | `calculators/sector_rps_calculator.py` |

**归档原因**：
- 代码重复率 ~85%
- 没有统一的基类
- 命名不统一

**新实现**：
- 统一的 `BaseFeatureCalculator` 基类
- 代码复用率 ~80%
- 统一命名规范

---

### legacy_runner/（旧的运行器）

| 文件 | 说明 | 替代为 |
|------|------|--------|
| `config.py` | 配置和运行器 | `config/calculator_config.py` + `scripts/quant_scheduler.py` |
| `runner.py` | 策略运行器 | `runner/strategy_runner.py` |

**归档原因**：
- 功能混杂（配置+运行）
- 不符合新架构

**新实现**：
- 配置独立到 `config/`
- 运行器独立到 `runner/`

---

## 📊 重构成果

### 代码复用率

```
重构前：
- calc_indicators.py: 183行
- calc_sector_rps.py: 206行
- 重复代码: ~170行
- 重复率: ~85%

重构后：
- BaseFeatureCalculator: 150行（基类）
- StockRPSCalculator: 30行（配置）
- SectorRPSCalculator: 40行（配置+过滤）
- ETFRPSCalculator: 30行（配置）
- 总代码: 250行
- 复用率: ~80%
```

### 新增功能

- ✅ ETF RPS计算器（周期与板块一致）
- ✅ 统一的命名规范
- ✅ 配置集中管理
- ✅ 公共工具模块

---

## 🧹 清理建议

### 确认新代码稳定后（建议1个月后）

```bash
# 确认新代码运行正常后，可以删除备份
rm -rf backup/
```

### 保留说明

- ✅ 保留旧代码作为参考
- ✅ 记录归档时间和版本
- ✅ 便于对比和回滚
