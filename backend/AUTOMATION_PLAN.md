# 自动化联动方案设计

> **版本**: v1.0
> **创建时间**: 2026-01-20
> **目标**: 数据采集结束后自动触发量化计算

---

## 📋 方案概述

### 当前问题

数据采集系统和量化引擎是**独立运行**的，需要手动联动。

### 解决方案

在 `data_job/utils/scheduler.py` 中添加**量化计算触发器**，在数据采集完成后自动执行。

---

## 🎯 方案设计

### 方案A：在调度器中直接添加（推荐）⭐

#### 优点
- ✅ 简单直接，代码集中管理
- ✅ 可复用日志和错误处理机制
- ✅ 统一的调度配置

#### 实现

修改 `data_job/utils/scheduler.py`：

```python
class CollectionScheduler:
    """数据采集定时调度器"""

    def run_daily_collection(self):
        """执行每日数据采集"""
        # ... 数据采集代码 ...

        # 数据采集完成后，触发量化计算
        if success_count >= len(collectors) * 0.8:  # 至少80%成功
            logger.info("\n" + "=" * 80)
            logger.info("🧮 数据采集完成，开始触发量化计算...")
            logger.info("=" * 80)
            self.run_quant_calculation()
        else:
            logger.warning("⚠️ 数据采集成功率过低，跳过量化计算")

    def run_quant_calculation(self):
        """触发量化计算"""
        try:
            # 导入量化计算器
            from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator
            from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
            from quant_engine.calculators.etf_rps_calculator import ETFRPSCalculator

            calculators = [
                ('StockRPS', StockRPSCalculator(), "10-20分钟"),
                ('SectorRPS', SectorRPSCalculator(), "5-10分钟"),
                ('ETFRPS', ETFRPSCalculator(), "2-5分钟"),
            ]

            for name, calculator, estimated_time in calculators:
                logger.info(f"\n▶️ 正在计算: {name} (预计耗时: {estimated_time})")
                try:
                    calculator.run_daily()
                    logger.info(f"✅ {name} 完成")
                except Exception as e:
                    logger.error(f"❌ {name} 失败: {e}")

            logger.info("\n✅ 所有量化因子计算完成")

            # 可选：触发策略选股
            # self.run_strategy_selection()

        except Exception as e:
            logger.error(f"❌ 量化计算触发失败: {e}")

    def run_strategy_selection(self):
        """触发策略选股"""
        try:
            from quant_engine.strategies.mrgc_strategy import MrgcStrategy

            logger.info("\n▶️ 开始执行策略选股...")
            strategy = MrgcStrategy()
            strategy.run()
            logger.info("✅ 策略选股完成")

        except Exception as e:
            logger.error(f"❌ 策略选股失败: {e}")
```

---

### 方案B：使用APScheduler的链式任务

#### 优点
- ✅ 任务解耦，独立维护
- ✅ 灵活的时间控制
- ✅ 支持失败重试

#### 实现

```python
def setup_jobs(self):
    """配置所有定时任务"""

    # 每日数据采集 - 15:30
    self.scheduler.add_job(
        self.run_daily_collection,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=30),
        id='daily_collection',
        name='每日数据采集'
    )

    # 量化因子计算 - 15:40 (采集结束后10分钟)
    self.scheduler.add_job(
        self.run_quant_calculation,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=40),
        id='quant_calculation',
        name='量化因子计算',
        misfire_grace_time=300  # 允许5分钟的延迟
    )

    # 策略选股 - 16:00
    self.scheduler.add_job(
        self.run_strategy_selection,
        trigger=CronTrigger(day_of_week='mon-fri', hour=16, minute=0),
        id='strategy_selection',
        name='策略选股',
        misfire_grace_time=600  # 允许10分钟的延迟
    )
```

---

### 方案C：使用事件驱动（高级）

#### 优点
- ✅ 真正的实时触发
- ✅ 灵活性最高
- ✅ 解耦最彻底

#### 实现

需要引入事件总线系统，这里暂不详细展开。

---

## 🎯 推荐方案：方案A + 方案B 混合

### 实现步骤

#### Step 1: 修改调度器（方案A）

在 `run_daily_collection()` 末尾添加量化计算触发。

#### Step 2: 添加链式任务（方案B）

在 `setup_jobs()` 中添加独立的时间任务，作为**兜底机制**。

### 时间安排

```
15:30 - 数据采集任务启动
15:35 - 数据采集完成（预计）
15:35 - 触发量化计算（方案A：实时触发）
15:40 - 量化计算兜底任务（方案B：定时任务）
15:50 - 因子计算完成
16:00 - 策略选股（兜底任务）
16:10 - 所有任务完成
```

### 错误处理

```python
def run_quant_calculation(self):
    """触发量化计算（带容错）"""
    try:
        from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator

        calculator = StockRPSCalculator()
        calculator.run_daily()

        logger.info("✅ 个股RPS计算完成")
        return True

    except ImportError as e:
        logger.error(f"❌ 量化模块未就绪: {e}")
        logger.info("💡 提示：请先完成 quant_engine 模块重构")
        return False

    except Exception as e:
        logger.error(f"❌ 量化计算失败: {e}")
        return False
```

---

## 📊 完整的时间线

### 当前（未联动）

```
15:30 - 数据采集开始
15:45 - 数据采集结束
        ↓
        手动运行 RPS 计算
        ↓
16:00 - RPS 计算
        ↓
        手动运行策略选股
```

### 联动后

```
15:30 - 数据采集开始
15:35 - 数据采集结束
        ↓ 自动触发
15:35 - 量化因子计算开始
15:50 - 量化因子计算结束
        ↓ 自动触发
15:50 - 策略选股开始
16:00 - 策略选股结束
        ↓
16:00 - 查看选股结果 ✅
```

---

## 🔧 配置选项

### 开关配置

在 `data_job/config/collection_schedule.yaml` 中添加：

```yaml
schedule:
  daily:
    trigger_time: "15:30"
    collectors:
      - name: "StockKline"
        estimated_time: "5-10分钟"
      - name: "SectorKline"
        estimated_time: "2-5分钟"
      - name: "ETFKline"
        estimated_time: "2-5分钟"

    # ⭐ 新增：量化计算联动
    enable_quant_calculation: true  # 是否自动触发量化计算
    quant_calculation_delay: 0      # 延迟分钟数（0=立即，5=5分钟后）

    enable_strategy_selection: true  # 是否自动触发策略选股
    strategy_selection_delay: 15     # 延迟分钟数
```

### 灵活控制

```python
def run_daily_collection(self):
    """执行每日数据采集"""
    # ... 数据采集 ...

    # 检查配置
    if self.config.get('enable_quant_calculation', True):
        delay = self.config.get('quant_calculation_delay', 0)
        if delay > 0:
            logger.info(f"⏳ 等待 {delay} 分钟后触发量化计算...")
            time.sleep(delay * 60)

        self.run_quant_calculation()
```

---

## 🎯 优势总结

### 方案A的优势（实时触发）

1. **✅ 实时响应**：采集完成立即计算，节省时间
2. **✅ 逻辑清晰**：在一个流程中完成，便于调试
3. **✅ 条件触发**：只有采集成功才计算，避免浪费

### 方案B的优势（定时兜底）

1. **✅ 容错性强**：如果触发失败，定时任务兜底
2. **✅ 独立运行**：可以单独运行量化计算
3. **✅ 时间可控**：固定时间，便于监控

### 混合方案的优势

1. **✅ 双重保障**：实时触发 + 定时兜底
2. **✅ 灵活配置**：可以选择开启/关闭联动
3. **✅ 易于调试**：开发时可以关闭联动，独立测试

---

## 📝 实施清单

### Phase 1: 基础联动（必须）

- [ ] 修改 `data_job/utils/scheduler.py`
- [ ] 添加 `run_quant_calculation()` 方法
- [ ] 在 `run_daily_collection()` 末尾调用
- [ ] 添加错误处理和日志

### Phase 2: 独立任务（推荐）

- [ ] 添加 `run_quant_calculation()` 到调度器
- [ ] 添加 `run_strategy_selection()` 到调度器
- [ ] 配置 `misfire_grace_time`

### Phase 3: 配置管理（可选）

- [ ] 创建 `collection_schedule.yaml` 配置
- [ ] 添加开关控制
- [ ] 支持延迟配置

---

## ⚠️ 注意事项

1. **模块依赖**：确保 quant_engine 模块已完成重构
2. **错误隔离**：量化计算失败不应影响数据采集
3. **日志区分**：使用不同的日志文件便于排查
4. **性能监控**：记录每次计算的耗时
5. **数据验证**：计算前验证K线数据完整性

---

## 🎉 总结

**推荐采用**：方案A（实时触发） + 方案B（定时兜底）混合方案

**核心优势**：
- 🚀 自动化程度高，无需手动干预
- 🛡️ 双重保障，可靠性高
- ⚙️ 灵活配置，易于控制

**实施优先级**：
1. 先完成 quant_engine 模块重构
2. 再实施自动化联动
3. 最后添加高级配置

---

**文档版本**: v1.0
**创建时间**: 2026-01-20
**状态**: ✅ 设计完成
