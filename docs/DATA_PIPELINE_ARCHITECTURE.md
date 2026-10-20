# EvoAlpha 数据流与定时任务架构

> 最后更新: 2026-07-02

## 一、调度层：4 个 launchd 定时任务

| 任务 | 时间 | 模式 | 作用 | 超时 |
|------|------|------|------|------|
| `com.evoalpha.datacore.early` | 工作日 16:00 | early | 盘中数据采集，跳过 RPS/策略 | 120min |
| `com.evoalpha.datacore.daily` | 工作日 20:00 | daily | 完整流水线，含 RPS + MRGC 策略 | 240min |
| `com.evoalpha.datacore.retry` | 工作日 22:30 | retry | 补数重试，等锁 45 分钟 | 150min |
| `com.evoalpha.datacore.health` | 周二-周六 09:45 | health | 轻量健康检查，只写状态报告 | - |

### 锁机制
- 文件锁：`backend/data/.daily_data_workflow.lock` (fcntl flock + JSON metadata)
- 同一时刻只允许一个 workflow 运行
- stale lock = 20 分钟无心跳自动清理
- retry 模式等锁 45 分钟，避免和 daily 冲突

### 管理命令
```bash
cd backend
python3 scripts/install_local_data_launchd.py status      # 查看状态
python3 scripts/install_local_data_launchd.py install     # 安装
python3 scripts/install_local_data_launchd.py uninstall   # 卸载
python3 scripts/install_local_data_launchd.py print-plist # 查看配置
```

### 日志位置
- launchd 日志：`backend/logs/launchd/com.evoalpha.datacore.{mode}.{out,err}.log`
- workflow 日志：`backend/logs/daily_data_workflow_{timestamp}.log`
- 状态报告：`backend/logs/data_core_status/latest.json` + `DATA_CORE_DAILY_STATUS.md`

---

## 二、Workflow 流水线：daily 模式 27 步

入口：`scripts/run_local_data_skill.py` → `scripts/run_daily_data_workflow.py`

### Stage 0 · 预检
| # | 任务 | 说明 | critical |
|---|------|------|----------|
| 0 | preflight | 交易日校验 → DB完整性 → 磁盘空间 → 获取锁 → DB备份 → 清理旧日志 | - |

### Stage 1 · 数据源检测
| # | 任务 | 写入表 | critical |
|---|------|--------|----------|
| 1 | tdx_source_capability | - (只检测) | ✅ |

### Stage 2 · 核心 K线采集
| # | 任务 | 写入表 | critical |
|---|------|--------|----------|
| 2 | mootdx_qfq_stock_kline | `stock_daily_prices` | ✅ |
| 3 | quality_preview | `data_quality_report` | ❌ |
| 4 | quality_repair | `stock_daily_prices` (修复) | ❌ |
| 5 | **P0 质量门禁** | `data_quality_report` | ✅ |

> **early 模式到此结束**（`--skip-rps --skip-p0 --skip-p1`）

### Stage 3 · 估值 + 市场数据
| # | 任务 | 写入表 | critical |
|---|------|--------|----------|
| 6 | stock_valuation | `stock_valuation_daily` | ❌ |
| 7 | index_daily | `index_daily` | ❌ |
| 8 | sector_daily_prices | `sector_daily_prices` | ❌ |
| 9 | etf_daily_prices | `etf_daily_prices` | ❌ |
| 10 | limit_boards | `limit_board_trading`, `limit_down_trading` | ❌ |
| 11 | news + macro | `news_articles`, `macro_indicators` | ❌ |

### Stage 4 · RPS 因子计算
| # | 任务 | 读 | 写 | critical |
|---|------|----|----|----------|
| 12 | rps_features | `stock_daily_prices`, `sector_daily_prices` | `quant_feature_rps`, `quant_feature_sector_rps` | ✅ |
| 13 | etf_rps_features | `etf_daily_prices` | `quant_feature_etf_rps` | ❌ |

### Stage 5 · 强度 + 结构
| # | 任务 | 写 | critical |
|---|------|----|----------|
| 14 | strength_features | `market_strength_daily`, `industry_strength_daily` | ✅ |
| 15 | structure_health | `structure_health_daily` | ✅ |

### Stage 6 · 股票池 + 生命周期
| # | 任务 | 写 | critical |
|---|------|----|----------|
| 16 | core_stock_pool | `quant_stock_pool`, `stock_pool_members` | ✅ |
| 17 | lifecycle_base | `stock_lifecycle_state` (base) | ✅ |

### Stage 7 · 策略 + 信号 + 风控
| # | 任务 | 读 | 写 | critical |
|---|------|----|----|----------|
| 18 | **mrgc_preselect** | `stock_daily_prices` + `quant_feature_rps` + `quant_stock_pool` | `quant_preselect_results` | ❌ |
| 19 | timing_signals | `stock_daily_prices` | `timing_signals_daily` | ✅ |
| 20 | risk_plans | - | `trade_plans` | ✅ |

### Stage 8 · 收尾
| # | 任务 | 写 | critical |
|---|------|----|----------|
| 21 | lifecycle_final | `stock_lifecycle_state` (final) | ✅ |
| 22 | research_snapshot | `research_stock_daily_snapshot` | ❌ |
| 23 | verify + status | `logs/data_core_status/latest.json` | - |

---

## 三、数据依赖关系（4 层）

```
Layer 1: 原始数据层 (每日采集)
  stock_daily_prices  ← mootdx/pytdx (前复权K线)
  sector_daily_prices ← AKShare
  etf_daily_prices    ← AKShare
  index_daily         ← mootdx
      ↓
Layer 2: 因子层 (每日计算)
  quant_feature_rps        ← 读 stock_daily_prices
  quant_feature_sector_rps ← 读 sector_daily_prices
  quant_feature_etf_rps    ← 读 etf_daily_prices
  structure_health_daily   ← 读 stock_daily_prices
      ↓
Layer 3: 状态层
  quant_stock_pool    ← 读 stock_info + 基金持仓
  stock_lifecycle_state ← 读 RPS + structure + stock_pool
  timing_signals_daily ← 读 stock_daily_prices
      ↓
Layer 4: 策略输出层
  quant_preselect_results ← 读 RPS + stock_daily_prices + stock_pool (MRGC策略)
  trade_plans             ← 读 lifecycle + timing
  research_stock_daily_snapshot ← 读以上全部
```

### 为什么顺序不能乱
1. **RPS** 读 `stock_daily_prices` → 必须先完成 K线采集
2. **MRGC** 读 RPS + `stock_daily_prices` → 必须先完成 RPS 计算
3. **lifecycle** 读 `stock_pool` + RPS → 必须先完成股票池维护
4. **research_snapshot** 读以上全部 → 必须放最后
5. **P0 质量门禁**在 K线之后、RPS之前 → 挡住脏数据进入因子层

---

## 四、early vs daily vs retry 对比

| 维度 | early (16:00) | daily (20:00) | retry (22:30) |
|------|---------------|---------------|---------------|
| K线采集 | ✅ | ✅ | ✅ |
| 质量门禁 | ✅ | ✅ | ✅ |
| 估值/市场数据 | ✅ | ✅ | ✅ |
| RPS 计算 | ❌ skip | ✅ | ✅ |
| 强度/结构 | ❌ skip | ✅ | ✅ |
| 股票池 | ✅ | ✅ | ✅ |
| MRGC 策略 | ❌ skip | ✅ `--include-strategy` | ✅ |
| 信号/风控 | ❌ skip | ✅ | ✅ |
| 等锁时间 | 5min | 10min | 45min |
| 超时 | 120min | 240min | 150min |

**设计意图**：
- early (16:00)：盘中收盘后立刻拿数据，保证当晚能看到行情
- daily (20:00)：跑完整流水线，产出 RPS 和策略选股结果
- retry (22:30)：如果 daily 失败或数据不全，自动重试

---

## 五、已知问题与改进方向

1. **BSE 321 只股票未前复权**：`adjustment_mode=none, source=tdx_standard_bj`，mootdx 不支持北交所前复权
2. **retry 经常因锁冲突跳过**：daily 平均运行 30 分钟，22:30 时 retry 经常拿不到锁
3. **research_snapshot 表为空**：Stage 8 的 research 计算器可能未正确执行
4. **launchd 日志体积大**：daily.out.log 达 47MB，early.out.log 33MB（有自动截断机制但阈值 50MB）
5. **Python 路径**：launchd 用系统 Python `/opt/anaconda3/bin/python3`，非项目 venv

## 六、本地存储维护

原始行情与可重算派生特征的保留窗口、备份轮换、重复索引清理和收盘后执行约束，统一见
[`backend/docs/DATA_STORAGE_RETENTION.md`](../backend/docs/DATA_STORAGE_RETENTION.md)。
维护脚本默认只读盘点，只有在复用 Data Core 锁并完成收盘前置检查后才允许 `--apply --vacuum`。
