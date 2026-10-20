# EvoAlpha OS - 技术蓝图 v0.5

## 基于低成本云端架构的 A 股强势股生命周期系统

### Slogan

> 进化即自由
> Evolution is Freedom

---

## 0. 核心摘要

EvoAlpha OS 是一个面向 A 股市场的个人级 Alpha 机会发现平台。

它不是单纯的选股器，也不是全自动交易机器人，而是一套融合 **本地数据工厂、低成本云端服务、五周期 RPS、市场强度、行业强度、个股强度、结构买点、AI Agent 解释与复盘** 的智能投研操作系统。

系统保持原有三段式技术架构不变：

```text
本地工厂 MBP
    ↓
Cloudflare R2 中转
    ↓
CockroachDB Serverless + FastAPI + Next.js 云端展示
```

系统保持原有成本思路不变：

```text
重计算在本地
重数据在本地
云端只存近 2 年核心结果
AI 按需触发
前端用 Vercel 免费额度
数据库用 Serverless 控成本
```

系统策略内核升级为：

```text
市场强度 → 行业强度 → 个股强度 → 结构质量 → 买点质量 → 风控复盘
```

核心公式：

```text
Evo Alpha =
强市场 × 强行业 × 强个股 × 强结构 × 低风险买点 × 纪律复盘
```

系统目标不是预测未来，而是识别市场已经发生的强弱变化，并在风险可控的位置参与趋势。

---

# 一、项目定位

## 1.1 项目名称

```text
EvoAlpha OS
Evolution Alpha Operating System
```

也可以在内部技术文档中使用：

```text
EvoQuant OS
```

两者关系建议这样定义：

| 名称        | 作用                               |
| ----------- | ---------------------------------- |
| EvoAlpha OS | 对外品牌名，强调 Alpha 机会发现    |
| EvoQuant OS | 对内工程名，强调量化系统与投研引擎 |

---

## 1.2 品牌定位

```text
品牌：D LAB / Evolution Lab
Slogan：进化即自由
核心理念：量化筛选做减法，AI Agent 做加法
```

EvoAlpha OS 的长期定位是：

> 个人投资者的 Alpha 机会发现平台。

它要帮助用户完成：

```text
发现强市场
识别强行业
筛选强个股
等待好结构
捕捉低风险买点
生成交易计划
跟踪持仓风险
完成复盘进化
```

---

## 1.3 系统边界

EvoAlpha OS 不做：

```text
不预测明天涨跌
不承诺收益
不自动替用户下单
不追逐所有热点
不依赖单一指标
不让 AI 直接替代决策
```

EvoAlpha OS 要做：

```text
用数据识别强弱
用规则过滤噪音
用 AI 解释逻辑
用状态机管理股池
用风控约束交易
用复盘推动进化
```

---

# 二、系统总架构

## 2.1 原三段式架构保持不变

你之前设计的三段式架构是对的，特别适合个人开发者和低成本商业化验证。

```text
┌──────────────────────────────────────────────┐
│                 用户层                        │
│  Next.js 前端 - Vercel                        │
│  - 首页 Alpha 机会                            │
│  - 个股作战室                                │
│  - 行业强度页                                │
│  - 交易卡片                                  │
│  - 日报系统                                  │
└──────────────────────┬───────────────────────┘
                       │ REST API
┌──────────────────────▼───────────────────────┐
│                云端 API 层                    │
│  FastAPI - Railway / Render / VPS             │
│  - 用户请求                                   │
│  - Alpha 机会查询                             │
│  - 个股详情查询                               │
│  - 行业强度查询                               │
│  - AI 分析按需触发                            │
└──────────────────────┬───────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
┌───────▼──────────┐          ┌───────▼──────────┐
│  CockroachDB      │          │  外部服务         │
│  Serverless       │          │  GLM API          │
│  近 2 年核心数据  │          │  Resend 邮件       │
└──────────────────┘          └──────────────────┘

┌──────────────────────────────────────────────┐
│              本地数据工厂 MBP                 │
│  AkShare 数据采集                             │
│  SQLite 全量历史库                            │
│  Pandas / NumPy 计算                          │
│  RPS / 市场强度 / 行业强度 / 策略信号          │
│  CSV 导出                                     │
│  Cloudflare R2 上传                           │
│  CockroachDB 批量导入                         │
└──────────────────────────────────────────────┘
```

---

## 2.2 架构原则

这套架构的核心原则是：

```text
本地重，云端轻；
计算前置，查询后置；
全量在本地，结果上云端；
AI 按需调用，不自动烧钱；
先跑通 MVP，再扩展商业化。
```

---

## 2.3 为什么这个架构适合你？

因为你当前的目标不是一开始做机构级高频系统，而是做一个：

```text
个人可维护
成本可控制
策略可迭代
页面可展示
未来可商业化
```

的投研系统。

所以最合理的分工是：

| 层级        | 职责                         | 成本逻辑             |
| ----------- | ---------------------------- | -------------------- |
| 本地 MBP    | 数据采集、全量存储、批量计算 | 免费，适合重计算     |
| R2          | CSV 中转、冷数据文件         | 成本低，适合批量导入 |
| CockroachDB | 近 2 年查询数据              | 控制容量和 RU        |
| FastAPI     | 提供 API 和 AI 调用          | 按需部署             |
| Vercel      | 前端展示                     | 免费额度可用         |
| GLM API     | AI 分析                      | 点击触发，控制成本   |

---

# 三、系统策略内核升级

原架构解决的是：

```text
数据怎么采？
数据怎么存？
结果怎么展示？
云端怎么省钱？
```

现在新增的策略内核解决的是：

```text
什么是好机会？
什么市场可以做？
什么行业值得做？
什么个股值得看？
什么结构值得等？
什么买点值得执行？
错了怎么退出？
交易后怎么复盘？
```

所以 EvoAlpha OS v0.5 的核心框架升级为：

```text
L0 市场强度
L1 行业强度
L2 个股强度
L3 结构质量
L4 买点质量
L5 风控复盘
```

---

# 四、L0：市场强度系统

## 4.1 市场强度的定位

市场强度是系统第一层。

它不是普通指标，而是：

```text
交易开关
仓位上限
策略适用环境
```

如果市场弱，即使个股强，也容易假突破。

所以系统第一问不是：

```text
今天哪只股票好？
```

而是：

```text
今天这个市场适合开新仓吗？
```

---

## 4.2 市场强度指标

| 模块       | 指标                                                           |
| ---------- | -------------------------------------------------------------- |
| 指数趋势   | 上证、沪深300、创业板指、中证1000 是否站上 MA20 / MA50 / MA120 |
| 趋势斜率   | MA20 / MA50 是否向上                                           |
| 市场宽度   | 上涨家数、下跌家数、上涨比例                                   |
| 赚钱效应   | 涨停数、连板高度、封板率                                       |
| 风险信号   | 跌停数、破位股数量、大阴线数量                                 |
| 成交活跃度 | 全市场成交额 / 20 日均成交额                                   |
| 强势扩散   | RPS90 以上个股数量                                             |
| 新高扩散   | 创 20 / 60 / 120 日新高个股数量                                |

---

## 4.3 市场状态分级

| 等级 | 名称     | 含义                     | 系统动作     |
| ---- | -------- | ------------------------ | ------------ |
| M0   | 极弱市场 | 系统性下跌，亏钱效应扩散 | 禁止新开仓   |
| M1   | 偏弱市场 | 局部机会，持续性差       | 只观察       |
| M2   | 中性市场 | 有轮动，但无强共振       | 小仓做最强   |
| M3   | 偏强市场 | 主线清晰，强股扩散       | 正常参与     |
| M4   | 强势市场 | 多方向共振，赚钱效应强   | 积极寻找主升 |

---

## 4.4 市场强度输出

系统每日输出：

```text
今日市场状态：M3 偏强
交易权限：允许正常开仓
建议总仓位：40%-70%
风险提示：强势股分化，需要优先选择强行业前排
```

---

# 五、L1：行业强度系统

## 5.1 行业强度的定位

行业强度决定：

```text
资金主攻方向在哪里？
```

EvoAlpha OS 不做孤立强股，而是优先做：

```text
强市场中的强行业
强行业中的强个股
```

行业强度是简放“主线思想”和陶博士“强股扎堆现象”的统一表达。

---

## 5.2 行业强度指标

| 模块     | 指标                                   |
| -------- | -------------------------------------- |
| 行业涨幅 | 5日、20日、50日、120日涨幅排名         |
| 行业 RPS | 行业指数 RPS20 / RPS50 / RPS120        |
| 成交放大 | 行业成交额 / 20 日均成交额             |
| 强股比例 | 行业内 RPS90 以上个股占比              |
| 新高比例 | 行业内创 60 / 120 / 250 日新高个股占比 |
| 龙头表现 | 龙头是否持续新高、是否抗跌             |
| 扩散程度 | 是否从龙头扩散到中军、补涨、细分方向   |
| 持续性   | 是否连续多日进入强势行业榜             |

---

## 5.3 行业状态分级

| 等级 | 名称     | 含义             |
| ---- | -------- | ---------------- |
| I0   | 弱行业   | 跑输市场，不参与 |
| I1   | 轮动行业 | 短期脉冲，谨慎   |
| I2   | 修复行业 | 开始转强，观察   |
| I3   | 强势行业 | 进入主线候选     |
| I4   | 核心主线 | 当前资金主攻方向 |

---

## 5.4 行业强度评分

```text
Industry_score =
25% × 行业20日涨幅排名
15% × 行业50日涨幅排名
15% × 行业成交额放大
15% × 行业内RPS强股比例
10% × 行业内新高比例
10% × 龙头强度
10% × 连续强势天数
```

---

# 六、L2：个股强度系统

## 6.1 五周期 RPS 保持不变

你之前系统已经有 RPS 计算，这个方向不变。

但 RPS 周期统一调整为你偏好的：

```text
RPS5 / RPS20 / RPS50 / RPS120 / RPS250
```

原架构里的 `rps_10` 可以保留兼容，但核心页面和策略以这五个周期为主。

---

## 6.2 五周期含义

| 周期   | 含义       | 用途                 |
| ------ | ---------- | -------------------- |
| RPS5   | 极短期强度 | 异动、加速、事件刺激 |
| RPS20  | 月度强度   | 当前市场关注         |
| RPS50  | 中期启动   | 趋势启动、主升前段   |
| RPS120 | 半年趋势   | 中期主趋势           |
| RPS250 | 年度强度   | 长期大牛候选         |

---

## 6.3 个股强度评分

```text
Stock_strength_score =
10% × RPS5
20% × RPS20
25% × RPS50
25% × RPS120
20% × RPS250
```

---

## 6.4 RPS 状态标签

系统不要只显示分数，还要显示标签。

| 标签     | 条件示例                     | 解释             |
| -------- | ---------------------------- | ---------------- |
| 短线异动 | RPS5 > 95，中长期弱          | 短线事件刺激     |
| 当前强势 | RPS20 > 90，RPS50 > 85       | 已进入关注       |
| 中期启动 | RPS50 > 90，RPS120 改善      | 可能进入主升前段 |
| 中期主升 | RPS50 > 90，RPS120 > 90      | 趋势成型         |
| 长期强股 | RPS120 > 90，RPS250 > 90     | 大级别强势       |
| 高位衰竭 | RPS250 高，RPS20 / RPS5 走弱 | 短期退潮         |

---

# 七、L3：结构质量系统

## 7.1 结构质量的定位

个股强，不等于立刻买。

系统要判断：

```text
强势股现在是否处于值得等待的位置？
```

结构质量用于把“强股”进一步筛成“可交易候选”。

---

## 7.2 Evo 结构信号

统一使用 Evo 自己的结构语言：

| 结构         | 解释                     |
| ------------ | ------------------------ |
| 阶段新高结构 | 接近或突破长期高点       |
| 波动收缩结构 | 回调幅度递减、成交量递减 |
| 趋势回踩结构 | 回踩 MA20 / MA50 不破    |
| 顺向均线结构 | MA10 > MA20 > MA50       |
| 业绩跳空结构 | 财报/预告后跳空上涨      |
| 整理吸筹结构 | 整理区内出现主动资金迹象 |

---

## 7.3 结构池输出

系统每日输出：

```text
S4 结构准备池：
- 所属市场状态：M3
- 所属行业状态：I4
- RPS状态：中期启动
- 结构类型：波动收缩 + 接近新高
- 当前动作：等待放量突破
```

---

# 八、L4：买点质量系统

## 8.1 四类核心买点

EvoAlpha OS 第一阶段只做四类买点，避免复杂化。

```text
1. 波动收缩后突破
2. 平台 / 新高放量突破
3. 均线缩量回踩后转强
4. 业绩跳空后的二次突破
```

---

## 8.2 买点触发规则

### 买点一：波动收缩后突破

```text
市场强度 ≥ M2
行业强度 ≥ I3
RPS50 / RPS120 强
多轮回调幅度递减
成交量递减
放量突破平台
止损距离 ≤ 8%
```

### 买点二：平台 / 新高放量突破

```text
接近或突破阶段新高
成交量 > 20日均量 × 1.5
收盘价靠近日高
行业同步走强
不是连续暴涨后的高位巨量
```

### 买点三：均线缩量回踩后转强

```text
MA10 > MA20 > MA50
RPS50 / RPS120 保持强势
回踩 MA20 或 MA50 不破
成交量明显萎缩
重新放量站回 MA10
```

### 买点四：业绩跳空后的二次突破

```text
业绩跳空后缺口不回补
RPS20 / RPS50 快速提升
缩量整理
再次放量突破整理平台
行业强度未退潮
```

---

## 8.3 买点否决规则

```text
市场强度低于 M2
行业强度低于 I3
个股不是行业前排
买点距离过远
止损空间超过 10%
突破当日放量滞涨
高位连续暴涨后首次巨量
板块龙头已经走弱
财务或监管风险未排除
```

---

# 九、L5：风控与复盘系统

## 9.1 仓位规则

仓位由四个变量决定：

```text
市场强度
行业强度
买点质量
止损距离
```

建议：

| 市场状态 | 单只仓位 | 总仓位  |
| -------- | -------- | ------- |
| M0       | 0%       | 0%      |
| M1       | 0%-5%    | 0%-10%  |
| M2       | 5%-10%   | 10%-30% |
| M3       | 10%-20%  | 40%-70% |
| M4       | 15%-25%  | 60%-90% |

单笔风险：

```text
单笔最大亏损 ≤ 账户资金的 1%
```

---

## 9.2 卖点规则

### 买点失败卖出

```text
突破后跌回平台
回踩买入后跌破回踩低点
跌破止损价
买入后没有按预期转强
```

### 趋势破坏卖出

```text
跌破 MA20 且无法快速收回
跌破 MA50
RPS20 / RPS50 持续下降
下跌放量，反弹缩量
行业强度降级
```

### 高位衰竭卖出

```text
连续大涨后放巨量滞涨
高位长上影
行业龙头集体冲高回落
强势股大面积补跌
市场赚钱效应下降
```

---

# 十、Evo 强势股生命周期状态机

## 10.1 状态定义

```text
S0 排除池
S1 市场可交易
S2 强行业池
S3 行业内强股池
S4 结构准备池
S5 买点触发池
S6 持仓跟踪池
S7 退出复盘池
```

| 状态 | 条件               | 动作         |
| ---- | ------------------ | ------------ |
| S0   | 基础条件不合格     | 不看         |
| S1   | 市场强度 ≥ M2     | 开始扫描     |
| S2   | 行业强度 ≥ I3     | 加入方向池   |
| S3   | 行业内 RPS 前排    | 加入强股池   |
| S4   | 结构健康           | 等待买点     |
| S5   | 买点触发           | 生成交易计划 |
| S6   | 已买入，趋势未破   | 持有跟踪     |
| S7   | 买点失败或趋势破坏 | 退出复盘     |

---

# 十一、旧数据库结构的升级方案

你之前的 16 张核心表可以保留，但建议新增或调整几类表。

## 11.1 保留原表

继续保留：

```text
stock_info
stock_daily_prices
sector_daily_prices
stock_sector_map
quant_feature_rps
quant_feature_sector_rps
quant_stock_pool
quant_strategy_results
news_articles
news_stock_relation
sentiment_keywords
alpha_opportunities
opportunity_tracking
ai_analysis_cache
daily_reports
report_sending_log
ETF 相关表
users / subscriptions
```

---

## 11.2 修改 RPS 表

原来：

```sql
quant_feature_rps (
    symbol, trade_date,
    rps_5, rps_10, rps_20, rps_50, rps_120, rps_250,
    ma_20, ma_50, ma_250
)
```

建议升级为：

```sql
quant_feature_rps (
    symbol,
    trade_date,
    rps_5,
    rps_20,
    rps_50,
    rps_120,
    rps_250,
    rps_abs_score,
    rps_momentum_score,
    rps_label,
    ma_10,
    ma_20,
    ma_50,
    ma_120,
    ma_250,
    created_at
)
```

`rps_10` 可以不删，作为兼容字段保留，但核心策略不用它。

---

## 11.3 新增市场强度表

```sql
market_strength_daily (
    trade_date,
    index_code,
    index_name,
    close,
    ma_20,
    ma_50,
    ma_120,
    above_ma20,
    above_ma50,
    up_count,
    down_count,
    new_high_20_count,
    new_high_60_count,
    new_high_120_count,
    limit_up_count,
    limit_down_count,
    total_amount,
    amount_ratio_20,
    rps90_count,
    market_strength_score,
    market_state,
    created_at
)
```

---

## 11.4 新增行业强度表

```sql
industry_strength_daily (
    trade_date,
    sector_code,
    sector_name,
    sector_type,
    return_5d,
    return_20d,
    return_50d,
    return_120d,
    sector_rps_20,
    sector_rps_50,
    sector_rps_120,
    amount,
    amount_ratio_20,
    rps90_stock_count,
    rps90_stock_ratio,
    new_high_stock_count,
    new_high_stock_ratio,
    leader_symbol,
    leader_strength_score,
    industry_strength_score,
    industry_state,
    created_at
)
```

---

## 11.5 新增结构信号表

```sql
structure_signals_daily (
    trade_date,
    symbol,
    near_stage_high,
    stage_high_ratio,
    vcp_structure,
    pullback_structure,
    ma_trend_structure,
    earnings_gap_structure,
    accumulation_structure,
    structure_score,
    structure_label,
    created_at
)
```

---

## 11.6 新增买点信号表

```sql
timing_signals_daily (
    trade_date,
    symbol,
    signal_type,
    signal_name,
    trigger_price,
    stop_loss_price,
    stop_loss_pct,
    volume_ratio,
    close_position,
    timing_score,
    is_valid,
    invalid_reason,
    created_at
)
```

---

## 11.7 新增生命周期状态表

```sql
stock_lifecycle_state (
    trade_date,
    symbol,
    market_state,
    industry_state,
    stock_strength_score,
    structure_score,
    timing_score,
    lifecycle_state,
    state_reason,
    next_action,
    created_at
)
```

---

## 11.8 新增交易计划表

```sql
trade_plans (
    plan_id,
    trade_date,
    symbol,
    lifecycle_state,
    buy_signal,
    buy_trigger_price,
    stop_loss_price,
    stop_loss_pct,
    suggested_position_pct,
    max_loss_pct,
    reason_market,
    reason_industry,
    reason_stock,
    reason_structure,
    reason_timing,
    risk_note,
    status,
    created_at
)
```

---

# 十二、旧模块与新模块融合

## 12.1 原模块0：新闻舆情数据层

保留，但从“泛新闻”升级为：

```text
L2 逻辑验证数据源
事件催化识别
风险提示来源
AI Agent 解释材料
```

输出不再只是情绪分，而是：

```text
产业催化
政策催化
业绩催化
风险事件
公司公告变化
```

---

## 12.2 原模块1：Alpha 机会雷达

升级为首页核心。

原来的 Alpha 机会卡片，建议改成 Evo 生命周期卡片。

卡片展示：

```text
股票名称
生命周期状态：S4 / S5
市场状态：M3
行业状态：I4
RPS 标签：中期启动
结构标签：波动收缩
买点状态：等待突破 / 已触发
置信度评分
一句话理由
```

---

## 12.3 原模块2：个股深度作战室

保留布局，但右侧 AI 面板升级为：

```text
Tab1：Evo 强度雷达
Tab2：结构与买点
Tab3：基本面与事件
Tab4：AI 交易计划
Tab5：历史复盘
```

左侧 K 线图叠加：

```text
MA10 / MA20 / MA50 / MA120 / MA250
成交量
RPS 副图
买点标记
止损线
结构区间
```

---

## 12.4 原模块3：板块战役指挥所

这是你系统里非常重要的一页，应该升级为  **行业强度中枢** 。

新增展示：

```text
行业状态 I0-I4
行业强度分
行业 RPS
行业成交额放大
行业内 RPS90 个股比例
行业内新高比例
行业龙一 / 龙二 / 中军
行业持续天数
行业退潮风险
```

---

## 12.5 原模块4：日报研报系统

日报升级为 Evo 每日作战简报。

模板建议：

```markdown
# EvoAlpha 日报

## 1. 市场强度
- 今日市场状态：M3 偏强
- 交易权限：允许正常参与
- 建议仓位：40%-70%

## 2. 行业强度
- I4 核心主线：AI算力、半导体设备
- I3 强势行业：机器人、创新药

## 3. 强股池变化
- 新增 S3 行业内强股
- 新增 S4 结构准备股
- 新增 S5 买点触发股

## 4. 今日 Alpha 机会
- 股票 A：波动收缩后突破
- 股票 B：均线缩量回踩后转强

## 5. 风险提示
- 哪些行业降级
- 哪些强股破位
- 哪些持仓触发止损

## 6. 明日计划
- 等待突破
- 等待回踩
- 观察行业是否延续
```

---

## 12.6 原模块5：量化兵工厂

保留，并升级回测任务。

新增回测方向：

```text
强市场 + 强行业 + RPS 前排
行业强度过滤是否提升收益
市场状态分组后的策略表现
VCP 突破策略
均线缩量回踩策略
业绩跳空后二次突破策略
不同止损参数比较
不同仓位模型比较
```

---

## 12.7 原模块6：ETF 全天候配置

保留，但定位要和强势股系统分开。

建议定义为：

| 系统                | 定位               |
| ------------------- | ------------------ |
| EvoAlpha 强势股系统 | 进攻型 Alpha 机会  |
| ETF 全天候配置      | 防守型资产配置底仓 |

也就是说：

```text
强势股系统负责进攻
ETF 全天候负责防守
```

未来可以在首页展示：

```text
当前市场强度 M0-M1：以 ETF 防守为主
当前市场强度 M3-M4：提高强势股策略权重
```

---

# 十三、AI Agent 架构升级

你之前的“大师 Agent”概念可以保留，但建议对外改成 Evo 系统语言。

原来：

```text
陶博士 Agent
简放 Agent
巴菲特 Agent
欧奈尔 Agent
```

建议内部可以保留参考，但产品层统一为：

```text
Evo 强度 Agent
Evo 行业 Agent
Evo 结构 Agent
Evo 基本面 Agent
Evo 风险 Agent
Evo 复盘 Agent
```

这样更像你自己的系统，而不是简单模仿他人。

---

## 13.1 AI Agent 分工

| Agent             | 职责                           |
| ----------------- | ------------------------------ |
| Market Agent      | 解读市场强度、仓位建议         |
| Industry Agent    | 解读强行业、主线持续性         |
| Stock Agent       | 解读 RPS、个股强度             |
| Structure Agent   | 解读 VCP、回踩、新高、均线结构 |
| Fundamental Agent | 解读财报、公告、产业逻辑       |
| Trade Plan Agent  | 生成交易计划                   |
| Review Agent      | 判断系统内/外交易并复盘        |

---

## 13.2 AI 成本控制

保持旧架构决策：

```text
AI 不自动全量分析
AI 只在用户点击后调用
热门 Alpha 机会可提前缓存
日报摘要可批量生成
个股深度分析设置缓存有效期
```

对应表：

```sql
ai_analysis_cache (
    analysis_type,
    target_id,
    analysis_result,
    model_version,
    created_at,
    expire_at
)
```

保留，非常重要。

---

# 十四、本地工厂模块升级

你原来的本地工厂结构很合理，只需要升级 quant_engine。

建议新版：

```text
backend/
├── data_job/
│   ├── update_stock_list.py
│   ├── update_kline.py
│   ├── update_sector.py
│   ├── update_news.py
│   └── update_finance.py
│
├── quant_engine/
│   ├── features/
│   │   ├── calc_indicators.py
│   │   ├── calc_rps.py
│   │   ├── calc_market_strength.py
│   │   ├── calc_industry_strength.py
│   │   └── calc_structure_signals.py
│   │
│   ├── pool/
│   │   ├── maintain_lifecycle_pool.py
│   │   ├── maintain_alpha_pool.py
│   │   └── maintain_watchlist.py
│   │
│   ├── strategies/
│   │   ├── evo_vcp_breakout.py
│   │   ├── evo_stage_high_breakout.py
│   │   ├── evo_ma_pullback.py
│   │   ├── evo_earnings_gap.py
│   │   └── evo_industry_resonance.py
│   │
│   └── risk/
│       ├── position_sizing.py
│       ├── stop_loss.py
│       └── lifecycle_state.py
│
└── app/sync/
    ├── csv_exporter.py
    ├── r2_manager.py
    └── cloud_importer.py
```

---

# 十五、云端 API 升级

原 API 保留：

```text
/api/alpha/opportunities
/api/stock/{symbol}/info
/api/stock/{symbol}/kline
/api/sector/list
/api/ai/analyze
```

建议新增：

```text
/api/market/strength
/api/industry/strength
/api/stock/{symbol}/rps
/api/stock/{symbol}/lifecycle
/api/signals/today
/api/trade-plans/today
/api/reports/daily
/api/review/trades
```

---

# 十六、前端页面升级

你之前规划的页面整体保留，但要重命名为 Evo 系统语言。

## 16.1 首页：Alpha 机会雷达

展示：

```text
市场状态
强行业榜
今日 Alpha 机会
S5 买点触发
S4 结构准备
风险提示
```

---

## 16.2 市场温度页

新增页面。

展示：

```text
M0-M4 市场状态
指数趋势
市场宽度
涨跌停结构
创新高数量
成交额变化
建议仓位
```

---

## 16.3 行业战役指挥所

原板块页升级。

展示：

```text
I0-I4 行业状态
行业强度榜
行业热图
行业 RPS
行业内强股比例
龙头股
行业生命周期
```

---

## 16.4 个股深度作战室

展示：

```text
K线图
RPS雷达
行业状态
结构信号
买点信号
AI交易计划
风险提示
```

---

## 16.5 量化兵工厂

展示：

```text
策略回测
参数优化
市场状态分组表现
行业强度过滤效果
买点胜率
最大回撤
```

---

## 16.6 ETF 全天候配置

保留，作为防守配置模块。

---

# 十七、商业化逻辑保持不变，但功能分层可优化

你原来的商业模式可以保留：

| 版本   | 价格     | 权限                      |
| ------ | -------- | ------------------------- |
| 免费版 | ¥0      | Top 3 机会 + 市场状态     |
| Pro版  | ¥199/月 | 全部机会 + AI 分析 + 日报 |
| VIP版  | ¥999/季 | Pro + 定制策略 + 1对1咨询 |

建议功能分层调整为：

## 免费版

```text
市场状态 M0-M4
Top 3 Alpha 机会
Top 3 强行业
部分日报
```

## Pro 版

```text
完整 Alpha 机会池
行业强度榜
个股作战室
AI 个股分析
每日作战简报
历史信号
```

## VIP 版

```text
定制观察池
策略参数定制
回测报告
个性化复盘
1对1咨询
```

---

# 十八、开发路线更新

你之前 1 个月开发计划偏激进，但方向可以保留。建议重新排序为：

## Phase 1：数据与 RPS 修复

优先级最高。

```text
修复 AkShare 数据采集
跑通本地 SQLite
完成日线全量数据
完成 RPS5/20/50/120/250
完成本地 CSV 导出
```

---

## Phase 2：市场强度与行业强度

这是我们这次补上的核心。

```text
市场状态 M0-M4
行业状态 I0-I4
行业强度榜
行业内强股比例
行业内新高比例
```

---

## Phase 3：Alpha 生命周期状态机

```text
S0-S7 生命周期
强股池
结构准备池
买点触发池
交易计划表
```

---

## Phase 4：前端 MVP

```text
首页 Alpha 机会
市场温度页
行业强度页
个股详情页
```

---

## Phase 5：云端同步与低成本部署

```text
CSV → R2
R2 → CockroachDB IMPORT
FastAPI 查询接口
Vercel 前端部署
```

---

## Phase 6：AI Agent 与日报系统

```text
AI 个股解释
AI 行业解释
每日作战简报
邮件推送
AI 缓存
```

---

## Phase 7：回测系统与商业化

```text
策略回测
参数优化
会员权限
订阅系统
```

---

# 十九、新版核心定义

最终可以这样定义 EvoAlpha OS：

> EvoAlpha OS 是一个以本地数据工厂为计算底座，以低成本云端架构为展示与分发层，以市场强度为交易开关，以行业强度为方向选择，以五周期 RPS 为个股强弱识别，以结构信号为交易准备，以量价买点为执行触发，以 AI Agent 为解释与复盘助手的 A 股 Alpha 机会发现系统。

一句话版本：

> EvoAlpha OS 不是单纯找强股，而是在强市场中寻找强行业，在强行业中寻找强个股，再等待强结构里的低风险买点。

---

# 二十、最关键的修正版系统语言

这部分建议放进白皮书开头，也可以作为产品宣言：

```text
我们不预测市场，我们识别强弱。
我们不追逐噪音，我们等待结构。
我们不迷信单一指标，我们寻找系统共振。
我们不害怕止损，我们害怕失控。
我们不追求每次正确，我们追求长期进化。

市场强度决定交易环境；
行业强度决定资金方向；
个股强度决定候选标的；
结构质量决定等待价值；
买点质量决定风险收益比；
风控复盘决定系统生命力。

Evolution is Freedom.
```

---

# 二十一、这次整合后的重点变化

和你旧版架构相比，新版不是改技术路线，而是补齐了策略内核。

| 旧版           | 新版                                   |
| -------------- | -------------------------------------- |
| Alpha 机会雷达 | Evo 生命周期机会池                     |
| RPS 因子       | 五周期 RPS + 状态标签                  |
| 板块共振       | 行业强度 I0-I4                         |
| MRGC 策略      | Evo 强势股生命周期策略                 |
| 大师会诊       | Evo 多 Agent 解释系统                  |
| 个股作战室     | 强度 + 结构 + 买点 + 风控作战室        |
| 日报系统       | 每日市场/行业/机会/风险作战简报        |
| 回测系统       | 按市场强度、行业强度、买点类型分组回测 |
| 云端架构       | 保持本地重计算 + 云端轻查询            |

最重要的一点是：

> 旧系统是“技术蓝图”，新版系统是“技术蓝图 + 投资方法论内核”。

这才是完整的 EvoAlpha OS。
