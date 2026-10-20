# EU Alpha A股数据源中台白皮书 v0.1

> 从“接口拼接”升级为“稳定、可验证、可回滚的数据基础设施”
>
> 版本：v0.1  
> 更新日期：2026-05-16  
> 工程落地对象：EvoAlpha OS / EU Alpha Data Core

---

## 0. 核心结论

EU Alpha 当前阶段的优先级不是继续堆叠投研功能，而是先把 A股数据底座做稳。

过去的问题链路是：

```text
数据源不稳定
↓
行情表不完整或口径混乱
↓
RPS、板块热图、强势股池计算失真
↓
AI分析引用脏数据
↓
投资判断不可复盘
```

因此，当前系统应从“智能投研系统”阶段性收敛为：

```text
A股数据源中台 / Data Core v0.1
```

Data Core v0.1 的首要目标不是分析更复杂，而是做到：

```text
每天能稳定更新
接口失败能发现
数据缺失能补跑
字段口径能统一
异常数据能标记
历史数据能回滚
上层模块能复用
```

新版原则：

```text
高频行情靠稳定源
低频资料靠补充源
研究增强靠语义源
AI分析只读取标准化后的中间数据
```

五条硬规则：

1. 高频行情不用脆弱源，优先使用通达信协议源（mootdx/pytdx）和腾讯财经兜底。
2. akshare 主要承担低频补充、公告新闻、特色数据，不承担核心行情主链路。
3. 所有外部数据必须先标准化、落库、校验，再给策略和 AI 使用。
4. 数据健康报告是系统第一输出，策略结果是第二输出。
5. Data Core 当前阶段是 A股数据可靠性工程，不是自动荐股系统。

---

## 一、系统定位

### 1.1 Data Core 是什么

EU Alpha Data Core v0.1 是面向 A股投研、量化筛选、主题归因、AI分析和投资复盘的数据基础设施。

它不是单一数据源封装，也不是临时爬虫集合，而是一个具备以下能力的数据中台：

```text
1. 多数据源接入
2. 数据字段标准化
3. 主备源切换
4. 数据完整性校验
5. 数据准确性校验
6. 本地缓存与标准落库
7. 失败日志与可观测性
8. 每日数据健康报告
9. 指定日期、指定股票补数据
10. 为上层策略和 AI 模块提供统一读取接口
```

### 1.2 Data Core 不是什么

v0.1 不追求以下目标：

```text
不做复杂页面
不做分钟级高频交易
不做自动买卖
不做新闻驱动交易
不做完整研报 RAG
不做过度 AI 分析
不让 Claude Code 或其他 Agent 直接到处抓接口
```

核心边界：

```text
外部数据源 → Adapter → 标准化 → 质量校验 → 标准表/API → 策略/AI
```

任何上层模块不得绕过标准表直接读取外部数据源。

---

## 二、总体架构

### 2.1 总体架构图

```text
                         EU Alpha
                    A股智能投研系统
                              │
                              ▼
                    ┌────────────────┐
                    │   AI分析层       │
                    │ Decision Ticket │
                    └────────┬───────┘
                             │ 只读标准数据
                    ┌────────▼───────┐
                    │  策略计算层      │
                    │ RPS / 强势股池   │
                    │ 板块热图 / 趋势   │
                    └────────┬───────┘
                             │ 只读标准数据
                    ┌────────▼───────┐
                    │  Data Core      │
                    │ 标准化/质量/存储 │
                    └────────┬───────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│ 高频行情源   │      │ 低频资料源   │      │ 研究增强源   │
│ mootdx/tdx   │      │ akshare      │      │ iwencai      │
│ 腾讯财经      │      │ 巨潮/东财     │      │ 同花顺热点    │
└─────────────┘      └─────────────┘      └─────────────┘
```

### 2.2 五层架构

| 层级 | 名称 | 作用 |
| --- | --- | --- |
| L1 | 数据源接入层 | 连接 mootdx/pytdx、腾讯财经、akshare、问财、同花顺热点等 |
| L2 | 数据标准化层 | 统一代码、字段、单位、日期、市场、复权口径 |
| L3 | 数据质量层 | 校验完整性、准确性、连续性、异常值和跨源一致性 |
| L4 | 本地存储层 | SQLite 优先，后续按需引入 DuckDB / PostgreSQL |
| L5 | 上层应用层 | RPS、板块热图、强势股池、AI分析、Decision Ticket |

### 2.3 标准写入流程

外部数据不得直接写入最终业务表，建议采用三段式流程：

```text
fetch
  ↓
stage_raw / stage_normalized
  ↓
validate
  ↓
promote to canonical tables
  ↓
quality report
```

关键收益：

```text
接口失败时不污染正式表
字段口径错误可以在入库前阻断
同一 run_id 可追踪、可审计、可回滚
补数据和每日更新使用同一套校验逻辑
```

---

## 三、数据源选型原则

### 3.1 不追求万能数据源

A股不存在一个免费、稳定、完整、实时、无限制的数据源。

正确方式不是寻找“唯一神源”，而是建立分工明确的多源体系：

```text
不同数据源各司其职
高频数据用稳定接口
低频数据用补充接口
语义数据用专门工具
所有数据先落本地再使用
```

### 3.2 数据源角色划分

| 类型 | 原则 | 典型数据源 |
| --- | --- | --- |
| 核心行情源 | 稳定优先，字段够用即可 | mootdx/pytdx |
| 行情备用源 | 兜底补缺，做跨源校验 | 腾讯财经、akshare |
| 低频资料源 | 可接受延迟，字段更丰富 | akshare、巨潮、东方财富 |
| 研究增强源 | 服务主题扩展和解释，不做底层行情源 | iwencai、同花顺热点 |
| 人工补充源 | 只用于重点标的或异常修复 | 手动 CSV / PDF / 复盘记录 |

### 3.3 数据源接入契约

每个 Adapter 必须声明自己的数据契约：

```text
source_name
api_name
data_domain
input_params
output_schema
unit_mapping
code_format
date_format
adjustment_mode
timeout_seconds
retry_policy
rate_limit
freshness_sla
known_risks
```

没有契约的数据源不得进入 Data Core 主链路。

---

## 四、核心数据源矩阵

### 4.1 v0.1 主矩阵

| 模块 | 主数据源 | 备用/补充数据源 | 调用频率 | 优先级 |
| --- | --- | --- | --- | --- |
| 交易日历 | akshare / 本地缓存 | 手工维护 | 每日盘前/年度初始化 | P0 |
| 股票基础池 | mootdx | akshare | 每日/每周 | P0 |
| 日K行情 | mootdx | 腾讯财经 / akshare 低频补充 | 每日收盘后 | P0 |
| 实时行情 | mootdx | 腾讯财经 | 盘中按需 | P1 |
| 分钟K线 | mootdx | 暂不强制备用 | 盘中/按需 | P2 |
| 估值市值 | 腾讯财经 | akshare | 每日收盘后 | P0 |
| 公司基础资料 | mootdx | akshare | 每周/按需 | P1 |
| 公告 | 巨潮资讯 via akshare | 交易所/公司官网 | 每日低频 | P2 |
| 新闻快讯 | 财联社/东财 via akshare | 手动补充 | 盘中/每日 | P2 |
| 研报 | 东财研报 via akshare | 手动 PDF | 每周/按需 | P2 |
| 一致预期 | akshare / 同花顺 | 手动校验 | 每周 | P2 |
| 热点归因 | 同花顺热点 | iwencai / 人工复盘 | 每日 | P1 |
| 语义搜索 | iwencai | 人工主题池 | 按需 | P1/P2 |

### 4.2 关键优化：通达信协议源统一命名

工程中可能同时出现以下名称：

```text
mootdx
pytdx
tdx_adapter
通达信协议源
```

白皮书统一抽象为：

```text
TDX Source
```

工程实现可以继续使用 `mootdx_adapter.py` 或 `tdx_adapter.py`，但对上层暴露时只认标准数据域，不暴露底层库差异。

---

## 五、数据标准化规范

### 5.1 股票代码标准

系统内部统一使用：

```text
000001.SZ
600000.SH
300750.SZ
688981.SH
```

外部适配示例：

| 数据源 | 外部格式 | 内部格式 |
| --- | --- | --- |
| mootdx/pytdx | 000001 | 000001.SZ |
| 腾讯财经 | sz000001 | 000001.SZ |
| akshare | 000001 | 000001.SZ |
| baostock 类格式 | sz.000001 | 000001.SZ |

代码转换必须集中在 `symbol_normalizer` 一类工具里，禁止散落在各 Collector 内部。

### 5.2 日期和时间标准

| 类型 | 标准 |
| --- | --- |
| 日频数据 | `YYYY-MM-DD` |
| 分钟/实时数据 | `YYYY-MM-DD HH:MM:SS` |
| 系统运行时间 | 带 timezone，默认 `Asia/Shanghai` |

交易日判断必须依赖交易日历，不得简单用工作日替代。

### 5.3 单位标准

| 字段 | 系统单位 |
| --- | --- |
| price/open/high/low/close | 元 |
| volume | 股 |
| amount | 元 |
| total_mv | 元 |
| float_mv | 元 |
| pct_chg | 百分比数值，例如 `5.32` |
| turnover_rate | 百分比数值，例如 `3.21` |

市值、成交额、股本这类字段必须有单位映射测试，避免“元 / 万元 / 亿元”混用。

### 5.4 字段命名标准

统一使用英文小写 + 下划线：

```text
trade_date
stock_code
open_price
close_price
total_mv
float_mv
turnover_rate
quality_flag
source
run_id
updated_at
```

### 5.5 复权口径标准

复权口径是行情系统的硬约束。

v0.1 建议：

```text
原始行情：保留 raw / none_adjusted 数据，用于审计和跨源对账
策略行情：默认使用 qfq 前复权数据，用于 RPS、趋势和强势股池
复权因子：单独记录，避免以后重算时无法解释历史差异
```

日K表必须显式记录：

```text
adjustment_mode: none / qfq / hfq
```

任何策略读取行情时必须声明读取哪种复权口径。

---

## 六、核心模块规划

### 6.1 交易日历模块 P0

交易日历是所有完整性校验的基准。

数据内容：

```text
trade_date
is_open
market
pretrade_date
next_trade_date
source
updated_at
```

要求：

```text
每日盘前检查
年度初始化
节假日变更可手动修正
所有日频任务先判断 trade_date 是否开市
```

没有交易日历，不能可靠判断“今天没数据”是休市、接口失败，还是采集漏跑。

### 6.2 股票基础池模块 P0

目标：建立全市场股票基础池，作为所有后续数据更新的基准表。

数据内容：

```text
stock_code
stock_name
market
list_date
status
is_st
is_delisted
industry
source
updated_at
```

推荐数据源：

| 数据 | 主源 | 备用源 |
| --- | --- | --- |
| 股票代码 | mootdx | akshare |
| 股票名称 | mootdx | akshare |
| 市场标识 | mootdx | akshare |
| 上市日期 | akshare | 腾讯财经 |
| 交易状态 | mootdx | akshare |

更新频率：

```text
每日盘前检查一次
每周做一次全量校验
新股、退市、ST 状态可单独增量更新
```

阻断规则：

```text
股票基础池失败 → 阻断全流程
```

### 6.3 日K行情模块 P0

目标：稳定获取全市场日线行情，用于 RPS、趋势、涨跌幅、成交额和强势股筛选。

数据内容：

```text
trade_date
stock_code
open_price
high_price
low_price
close_price
volume
amount
pct_chg
adjustment_mode
source
quality_flag
run_id
updated_at
```

推荐数据源：

| 角色 | 数据源 |
| --- | --- |
| 主源 | mootdx / TDX Source |
| 备用源 | 腾讯财经 |
| 低频补充源 | akshare |

调用策略：

```text
每日收盘后批量更新
支持指定日期补数据
支持单只股票重拉
支持失败后断点续跑
支持按 run_id 回滚
```

核心校验：

| 校验项 | 规则 |
| --- | --- |
| 数据完整性 | 实际更新数量 / 应更新股票数量 |
| 唯一性 | `trade_date + stock_code + adjustment_mode` 不允许重复 |
| 价格合理性 | `high_price >= low_price`，收盘价在高低区间内 |
| 涨跌幅校验 | `pct_chg` 与前收盘价计算值比对，停牌/复权日特殊处理 |
| 成交量校验 | `volume >= 0` |
| 成交额校验 | `amount >= 0`，异常为 0 需标记 |
| 连续性 | 非停牌股票不应出现无故断档 |

质量闸门：

| 完整率 | 状态 | 后续动作 |
| --- | --- | --- |
| `>= 99%` | PASS | 允许 RPS |
| `95% - 99%` | WARNING | 允许 RPS，但报告缺失清单 |
| `< 95%` | FAIL | 阻断 RPS |

日K行情是全系统最高优先级模块。

只要日K不稳定，后面不要计算 RPS。

### 6.4 估值与市值模块 P0

目标：补充行情之外的估值、市值、换手率等衍生指标。

数据内容：

```text
trade_date
stock_code
pe_ttm
pb
ps
total_mv
float_mv
turnover_rate
source
quality_flag
run_id
updated_at
```

推荐数据源：

| 角色 | 数据源 |
| --- | --- |
| 主源 | 腾讯财经 |
| 备用源 | akshare |
| 辅助校验 | 自己根据价格和股本计算 |

更新频率：

```text
每日收盘后更新一次
不做高频调用
```

核心校验：

| 校验项 | 说明 |
| --- | --- |
| 单位统一 | 市值统一为元 |
| 异常 PE | 亏损股 PE 为空或负值需特殊处理 |
| PB 异常 | PB 小于 0 或极端值需标记 |
| 市值校验 | 总市值不应小于流通市值 |
| 换手率校验 | 超出合理范围需标记 |

阻断规则：

```text
估值失败不阻断行情
估值失败不阻断日K落库
估值缺失可阻断依赖估值的策略，但不阻断 RPS
```

### 6.5 实时行情模块 P1

目标：用于盘中观察，不作为 v0.1 核心计算基础。

数据内容：

```text
quote_time
stock_code
price
pct_chg
volume
amount
bid1
ask1
bid_volume1
ask_volume1
source
updated_at
```

推荐数据源：

| 角色 | 数据源 |
| --- | --- |
| 主源 | mootdx |
| 备用源 | 腾讯财经 |

当前阶段定位：

```text
实时行情可以做
但不得影响每日行情更新和 RPS 稳定性
初期 1-5 分钟级别按需拉取即可
```

### 6.6 分钟K线模块 P2

目标：用于更精细的盘中结构分析，但不是第一阶段必须模块。

当前建议：

```text
分钟数据体量大
更新频率高
存储压力大
稳定性要求高
v0.1 不把它放进主链路
```

### 6.7 研究增强模块 P1/P2

研究增强模块包括：

```text
公司基础资料
公告
新闻资讯
研报
一致预期
热点归因
语义搜索
```

它们的共同原则：

```text
可以增强解释力
不能污染底层行情
失败不阻断日K和 RPS
必须保留 source、url、confidence、updated_at
```

---

## 七、本地数据库规划

### 7.1 数据库选择

v0.1 推荐：

| 用途 | 推荐 |
| --- | --- |
| 本地开发和每日更新 | SQLite |
| 大量历史行情分析 | DuckDB |
| 后续 Web 多用户和云端服务 | PostgreSQL |

当前工程优先沿用 SQLite，避免过早迁移引入新风险。

### 7.2 核心标准表

v0.1 必做表：

```text
trading_calendar
stock_basic
stock_daily
stock_valuation
data_source_log
data_quality_report
```

v0.2 以后扩展表：

```text
stock_realtime
stock_minute
stock_theme
hotspot_reason
stock_news
stock_announcement
stock_report
consensus_forecast
rps_daily
```

### 7.3 主键和唯一约束

| 表 | 推荐唯一键 |
| --- | --- |
| trading_calendar | `trade_date, market` |
| stock_basic | `stock_code` |
| stock_daily | `trade_date, stock_code, adjustment_mode` |
| stock_valuation | `trade_date, stock_code` |
| stock_realtime | `quote_time, stock_code` |
| stock_theme | `trade_date, stock_code, theme, source` |
| data_source_log | `run_id, source, api_name` |
| data_quality_report | `run_id, trade_date, module` |

### 7.4 兼容旧表原则

当前 EvoAlpha OS 已存在部分旧表和采集器。

迁移策略：

```text
先建立标准表和标准读取接口
旧表通过 view 或 adapter 兼容
避免一次性重命名大量表
等 Data Core v0.1 稳定后再做表结构收敛
```

---

## 八、数据质量体系

### 8.1 为什么必须做数据质量报告

系统不能只告诉用户“跑完了”。

它必须回答：

```text
今天哪些数据成功了？
哪些接口失败了？
失败是否影响后续计算？
哪些股票缺失？
哪些字段异常？
是否允许进入 RPS 计算？
如果不允许，应该补跑什么？
```

### 8.2 质量检查项

| 检查类型 | 检查内容 |
| --- | --- |
| 完整性 | 应更新多少，实际更新多少 |
| 唯一性 | 主键是否重复 |
| 合法性 | high >= low，close 是否在区间内 |
| 连续性 | 是否存在非交易日之外的断档 |
| 一致性 | 不同源之间价格是否偏差过大 |
| 单位检查 | 成交额、市值是否异常放大或缩小 |
| 异常值 | 涨跌幅、成交额、换手率是否极端 |
| 依赖检查 | 日K不完整时是否阻止 RPS |

### 8.3 数据质量状态

| 状态 | 含义 | 后续动作 |
| --- | --- | --- |
| PASS | 数据正常 | 允许计算指标 |
| WARNING | 有少量缺失或异常 | 允许计算，但标记风险 |
| FAIL | 缺失严重或关键校验失败 | 阻止依赖该数据的后续计算 |
| SKIP | 模块未执行 | 不影响主流程 |

### 8.4 quality_flag 建议

单条记录级别可以使用：

```text
ok
missing
filled_by_fallback
price_invalid
amount_invalid
pct_chg_mismatch
unit_suspect
source_conflict
manual_patch
```

模块级别使用 PASS / WARNING / FAIL / SKIP。

### 8.5 健康报告样例

```text
EU Alpha Data Health Report
日期：2026-05-16
run_id：daily-20260516-153000

一、行情数据
数据源：mootdx
应更新股票数：5300
实际成功：5278
缺失：22
重复：0
异常：3
状态：WARNING

二、估值数据
数据源：腾讯财经
应更新股票数：5300
实际成功：5190
缺失：110
状态：WARNING

三、质量明细
收盘价异常：0
成交额异常：3
涨跌幅异常：2
重复记录：0
日期断档：0

四、是否允许计算 RPS
结论：允许
原因：核心日K完整率超过 99%

五、建议动作
补跑缺失股票：22 只
复查成交额异常：3 只
```

---

## 九、失败降级与回滚机制

### 9.1 降级原则

系统不能因为一个接口失败而整体崩溃。

标准降级链路：

```text
主源失败
↓
备用源补充
↓
备用源失败
↓
使用昨日缓存或保留旧数据
↓
缓存不可用
↓
标记缺失
↓
缺失严重
↓
阻断后续依赖计算
```

### 9.2 分模块降级策略

| 模块 | 主源失败 | 备用策略 |
| --- | --- | --- |
| 股票池 | mootdx 失败 | akshare 补充；仍失败则阻断全流程 |
| 日K | mootdx 失败 | 腾讯 / akshare 补充，或指定日期重跑 |
| 估值 | 腾讯失败 | akshare 补充，失败则留空 |
| 新闻 | 财联社 / 东财失败 | 跳过，不影响行情 |
| 公告 | 巨潮失败 | 延后重试 |
| 研报 | 东财失败 | 周末重试 |
| 热点 | 同花顺失败 | 问财或人工复盘 |
| 问财 | API 失败 | 暂停语义扩展 |

### 9.3 任务阻断规则

| 情况 | 是否阻断后续 |
| --- | --- |
| 股票池失败 | 阻断全流程 |
| 交易日历缺失 | 阻断日频任务 |
| 日K完整率低于 95% | 阻断 RPS |
| 估值数据缺失 | 不阻断行情和 RPS |
| 新闻失败 | 不阻断行情 |
| 公告失败 | 不阻断行情 |
| 热点失败 | 不阻断 RPS |

### 9.4 run_id 与回滚

每次任务运行都必须生成 `run_id`。

建议格式：

```text
daily-YYYYMMDD-HHMMSS
repair-YYYYMMDD-HHMMSS
backfill-YYYYMMDD-HHMMSS
```

每条核心记录写入时保留：

```text
run_id
source
updated_at
quality_flag
```

回滚策略：

```text
正式表写入前先备份被覆盖的旧记录
新数据校验失败则不 promote
新数据 promote 后发现问题，可按 run_id 定位并回滚
```

v0.1 可以先实现轻量回滚：

```text
按 trade_date + stock_code 删除本次 run_id 写入记录
再从 backup/stage 表恢复上一版本
```

---

## 十、调度与运行计划

### 10.1 调用频率分层

| 频率类型 | 数据模块 | 调用建议 |
| --- | --- | --- |
| 高频 | 实时行情、盘口 | 盘中按需，不做过度请求 |
| 中频 | 新闻、热点、快讯 | 盘中定时或盘后集中 |
| 低频 | 日K、估值、公告、研报、公司资料 | 每日/每周 |
| 按需 | 问财、研报 PDF、公告详情 | 用户触发或重点股票触发 |

### 10.2 建议运行时间表

| 时间 | 任务 |
| --- | --- |
| 盘前 | 检查股票池、交易日历、昨日数据完整性 |
| 盘中 | 按需拉取实时行情、热点、新闻 |
| 收盘后 | 更新日K、估值、市值、成交额 |
| 晚间 | 跑数据质量报告、RPS、强势股池 |
| 周末 | 更新公司资料、研报、一致预期、主题库 |

### 10.3 v0.1 推荐命令形态

```bash
python3 -m data_core.cli update-daily --date 2026-05-16
python3 -m data_core.cli repair-missing --date 2026-05-16 --module stock_daily
python3 -m data_core.cli health-report --date 2026-05-16
python3 -m data_core.cli rollback --run-id daily-20260516-153000
```

实际命令可继续复用现有 `data_job` 和 `auto_pipeline.py`，但对外语义应逐步收敛为 Data Core。

---

## 十一、Claude Code Skill 封装规划

### 11.1 Skill 不直接替代数据中台

Claude Code Skill 的定位是调用已经封装好的 Data Core，而不是每次临时抓接口。

错误方式：

```text
Claude Code → 直接请求 mootdx / 腾讯 / akshare / 问财
```

正确方式：

```text
Claude Code → 调用 Data Core 标准接口 → 读取本地标准化数据
```

### 11.2 v0.1 Skill 能力

初期 Skill 只封装稳定能力：

```text
get_stock_daily(code, start_date, end_date, adjustment_mode="qfq")
get_stock_valuation(code, date)
get_rps_rank(date)
get_strong_stock_pool(date)
get_data_health_report(date)
update_daily_data(date)
repair_missing_data(date)
```

后续再增加：

```text
get_hotspot_reason(code, date)
search_theme_stocks(query)
get_stock_news(code)
get_stock_report(code)
generate_decision_ticket(code)
```

### 11.3 Skill 安全边界

Skill 必须遵守：

```text
不直接访问外部源
不绕过 quality_report
不在数据质量 FAIL 时生成策略结论
不把新闻、热点作为单独买入依据
所有输出都带数据日期和数据状态
```

---

## 十二、阶段性实施路线

### 阶段一：Data Core v0.1

目标：

```text
解决基础行情和估值稳定问题
```

只做：

```text
1. 交易日历
2. 股票基础池
3. mootdx 日K
4. 腾讯财经估值
5. 本地标准落库
6. data_source_log
7. data_quality_report
8. 指定日期补数据
9. 失败重试
10. 每日数据健康报告
```

成功标准：

```text
连续运行 10 个交易日
日K完整率 > 99%
估值完整率 > 95%
失败接口可定位
缺失数据可补跑
RPS计算可稳定恢复
```

### 阶段二：Strategy Base v0.2

目标：

```text
在稳定数据基础上恢复基础策略指标
```

新增：

```text
1. RPS计算
2. 成交额筛选
3. 涨幅榜 / 跌幅榜
4. 强势股池
5. 趋势候选池
```

### 阶段三：Theme Engine v0.3

目标：

```text
增加主题和热点归因能力
```

新增：

```text
1. 同花顺热点
2. iwencai主题搜索
3. 主题-股票映射
4. 热点归因表
5. 每日主题复盘
```

### 阶段四：Evidence Engine v0.4

目标：

```text
增加新闻、公告、研报和一致预期作为证据链
```

新增：

```text
1. 新闻快讯
2. 公告
3. 研报
4. 一致预期
5. 重点股票证据摘要
```

### 阶段五：Decision Ticket v0.5

目标：

```text
从数据系统升级为投资决策训练系统
```

新增：

```text
1. AI分析模板
2. Decision Ticket
3. 仓位计划
4. 买点条件
5. 止损条件
6. 复盘记录
```

---

## 十三、当前优先开发清单

### P0：马上做

```text
1. 统一股票代码格式
2. 建立 trading_calendar
3. 建立 stock_basic
4. 接入 mootdx 日K
5. 明确日K复权口径
6. 接入腾讯财经估值
7. 写入 SQLite 标准表
8. 建立 data_source_log
9. 建立 data_quality_report
10. 支持指定日期补数据
11. 支持失败重试
12. 支持 run_id 追踪
13. 每日输出数据健康报告
```

### P1：第二阶段做

```text
1. RPS计算
2. 强势股池
3. 成交额筛选
4. 趋势筛选
5. 简单页面展示
6. 同花顺热点
7. iwencai主题搜索
```

### P2：第三阶段做

```text
1. 新闻快讯
2. 公告
3. 研报
4. 一致预期
5. AI摘要
6. Decision Ticket
7. 复盘系统
```

---

## 十四、验收标准

### 14.1 功能验收

```text
可以每日更新股票基础池
可以每日更新全市场日K
可以每日更新估值市值
可以生成 data_source_log
可以生成 data_quality_report
可以指定日期补数据
可以指定股票补数据
可以在数据质量 FAIL 时阻断 RPS
```

### 14.2 可靠性验收

```text
连续运行 10 个交易日
日K完整率 > 99%
估值完整率 > 95%
接口失败能定位到 source/api/error
缺失股票能输出清单
补跑后健康报告能更新
```

### 14.3 回滚验收

至少演练一次：

```text
错误数据写入
按 run_id 定位
回滚本次写入
恢复上一版本
重新补跑
健康报告重新生成
```

---

## 十五、关键风险与设计约束

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 免费接口不稳定 | 更新失败 | 主备源、重试、补跑 |
| 字段单位混乱 | 指标失真 | unit_mapping 和校验测试 |
| 股票代码格式混乱 | join 失败 | 统一 symbol_normalizer |
| 复权口径不一致 | RPS 失真 | 显式 adjustment_mode |
| 交易日判断错误 | 健康报告误判 | trading_calendar P0 |
| SQLite 并发写 | 数据库锁 | 单进程写入、事务、队列化 |
| AI 直接抓源 | 数据不可复盘 | Skill 只能调 Data Core |
| 旧表迁移风险 | 破坏现有功能 | 标准表 + view/adapter 兼容 |

---

## 十六、最终系统目标

EU Alpha Data Core 的最终目标不是变成一个爬虫集合，而是成为投资系统底座。

最终形态：

```text
稳定数据源
↓
标准化数据库
↓
数据质量报告
↓
RPS与强势股池
↓
主题与热点归因
↓
新闻公告研报证据链
↓
AI辅助分析
↓
Decision Ticket
↓
复盘与策略进化
```

它服务的不是“自动推荐股票”，而是：

```text
把投资判断过程结构化、证据化、可复盘化。
```

---

## 十七、下一份工程文档

本白皮书是总设计图。

下一步应拆成更工程化的文档：

```text
《EU Alpha Data Core v0.1 开发任务拆解》
```

建议内容：

```text
1. 目录结构
2. 数据库表 DDL
3. Adapter 职责
4. Normalizer 职责
5. Validator 规则
6. Writer / Repository 规则
7. 每日运行脚本
8. 日志格式
9. CLI 参数
10. 测试用例
11. 第一周开发计划
```

这份任务拆解完成后，才进入代码实现。
