# EvoAlpha OS 白皮书 v0.9

## 双入口 Alpha 发现系统:技术强度扫描 × 产业研究股池 × 生命周期状态机

> 进化即自由
> Evolution is Freedom

---

# 0. 核心摘要

EvoAlpha OS 是一个面向 A 股市场的个人级 Alpha 机会发现与系统化投研平台。

它不是单纯的选股器,也不是自动交易机器人,而是一套把 **数据采集、RPS 强度、市场强度、行业强度、产业研究、结构健康、买点触发、持仓跟踪、风控止损、复盘进化** 连接起来的智能投研操作系统。

系统的核心目标不是预测未来,而是回答一组稳定的问题:

```text
现在市场能不能做?
资金正在进攻哪些行业?
哪些股票已经被市场验证?
我手动研究的产业股池有没有开始被市场认可?
哪些股票结构健康,值得等待?
哪些股票触发低风险买点?
买入后强势是否延续?
止损怎么设、什么时候加仓、什么时候减仓?
退出后这笔交易是系统问题,还是执行问题?
```

EvoAlpha OS 的核心公式:

```text
Evo Alpha =
机构认可 × 市场强度 × 行业强度 × 个股强度 × 研究逻辑 × 结构健康 × 低风险买点 × 风控纪律 × 复盘进化
```

系统采用:

```text
双入口
单中枢
同一生命周期
统一风控
```

双入口是:

```text
入口 A:技术强度入口
市场强度 → 行业强度 → 个股 RPS → 结构健康 → 买点触发

入口 B:产业研究入口
研究主题 → 产业链节点 → 手动标的池 → 市场验证 → 技术状态跟踪
```

单中枢是:

```text
Evo 强势股生命周期状态机
```

同一生命周期是:

```text
S0 机构认可与基础过滤池
S1 市场可交易池
S2 强行业池
S3 行业内强股池
S4 结构健康准备池
S5 买点触发池
S6 持仓跟踪池
S7 退出复盘池
```

统一风控是:

```text
所有买点共用一套止损、仓位、加减仓规则;
所有持仓共用一套结构破位、行业降级、市场降级触发规则;
所有交易必须可追溯、可归因。
```

一句话定义:

> EvoAlpha OS 是一个用技术强度发现市场已经选择的强者,用产业研究沉淀长期跟踪标的,再用生命周期状态机等待低风险买点和纪律化退出的 A 股智能投研系统。

---

# 1. 项目定位

## 1.1 系统名称

```text
EvoAlpha OS
Evolution Alpha Operating System
```

| 名称                 | 定位                                        |
| -------------------- | ------------------------------------------- |
| EvoAlpha OS          | 对外品牌名,强调 Alpha 机会发现              |
| EvoQuant OS          | 对内工程名,强调量化计算、数据管道、策略引擎 |
| D LAB                | 内容品牌、研究品牌、知识资产入口            |
| Evolution is Freedom | 系统精神内核                                |

---

## 1.2 系统不是什么

```text
不预测明天涨跌;
不承诺收益;
不自动替用户下单;
不追逐所有热点;
不依赖单一指标;
不让 AI 替代人的最终判断;
不把行业研究和交易执行混为一谈。
```

---

## 1.3 系统要做什么

```text
用数据识别强弱;
用研究沉淀逻辑;
用规则过滤噪音;
用状态机管理机会;
用买点模型触发交易计划;
用风控约束执行;
用复盘推动系统进化。
```

---

# 2. 系统第一性原理

## 2.1 不预测,只识别

> 价格变化来自供需失衡,趋势来自持续的供需失衡。
> 系统不预测未来,而是识别市场已经发生的强弱变化。

---

## 2.2 强度、逻辑、结构、买点、风控必须分开

```text
强度发现 → 逻辑验证 → 结构等待 → 买点触发 → 风险执行 → 复盘进化
```

| 层级     | 解决的问题             |
| -------- | ---------------------- |
| 市场强度 | 今天能不能交易         |
| 行业强度 | 资金主战场在哪里       |
| 个股强度 | 行业内谁是前排         |
| 研究逻辑 | 为什么值得跟踪         |
| 结构健康 | 现在是否值得等待       |
| 买点质量 | 是否可以执行           |
| 风控纪律 | 仓位多少、止损在哪     |
| 复盘归因 | 错了为什么、对了为什么 |

---

# 3. 工程架构

## 3.1 三段式架构

```text
本地 MBP 数据工厂
    ↓
SQLite 全量历史库
    ↓
CSV / Parquet 导出
    ↓
Cloudflare R2 文件中转
    ↓
CockroachDB Serverless 云端核心结果库
    ↓
FastAPI 云端接口
    ↓
Next.js / Vercel 前端展示
    ↓
AI Agent 按需触发
```

---

## 3.2 架构原则

```text
本地重,云端轻;
计算前置,查询后置;
全量数据在本地,核心结果上云端;
AI 按需调用,不全量烧钱;
先跑通 MVP,再逐步商业化。
```

| 层级             | 职责                           | 成本逻辑           |
| ---------------- | ------------------------------ | ------------------ |
| 本地 MBP         | 数据采集、清洗、全量计算、回测 | 免费重计算         |
| SQLite           | 本地全量历史库                 | 低成本             |
| Cloudflare R2    | CSV / Parquet 文件中转         | 低成本             |
| CockroachDB      | 云端近两年核心数据             | Serverless 控成本  |
| FastAPI          | 查询接口、AI 调度              | 轻服务             |
| Next.js / Vercel | 前端展示                       | 免费额度优先       |
| GLM / 其他模型   | AI 分析                        | 点击触发、缓存复用 |

---

## 3.3 数据源选型(新增)

数据源是整个系统的根基,必须明确主备方案。

### 3.3.1 主数据源

```text
主数据源:akshare
原因:免费、开源、覆盖广、社区活跃。
```

覆盖能力:

```text
A 股日线 / 分钟线;
申万行业 / 概念板块;
基金持股 / 北向持股;
财报数据;
公告 / 新闻;
ETF;
指数。
```

### 3.3.2 备份数据源

```text
备份 1:Tushare Pro(部分接口需要积分)
备份 2:Baostock(免费,日线稳定)
备份 3:同花顺 iFinD / Wind(付费,后期升级)
```

### 3.3.3 数据源调度策略

```text
每日采集任务先用 akshare;
某接口失败时,自动切换到 Baostock;
关键基础数据(日线、复权因子)双源校验;
异常数据自动进入 data_quality_issue 表,等待人工审核。
```

---

## 3.4 数据质量保障(新增)

### 3.4.1 必须处理的 corner case

```text
1. 除权除息:统一用前复权价格做技术分析;
2. 停复牌:停牌期间不参与 RPS 排名,复牌后重新进入;
3. ST 与 *ST:打标签,默认排除出 S0 池;
4. 退市:打标签,从所有活跃池中移除;
5. 新股 / 次新股:上市天数 < 250 时,RPS250 用上市以来全周期替代;
6. 一字板:不计入 RPS 涨跌幅排名(避免噪声);
7. 分红派息日:校验复权因子;
8. 单位变更 / 股本变动:校验市值连续性;
9. 同名不同代码:统一以最新代码为准;
10. 节假日 / 半日市:严格按官方交易日历。
```

### 3.4.2 数据质量监控表

```sql
CREATE TABLE data_quality_issue (
    issue_id UUID PRIMARY KEY,
    issue_date DATE,
    table_name TEXT,
    symbol TEXT,
    issue_type TEXT,
    issue_detail JSONB,
    severity TEXT,
    status TEXT DEFAULT 'open',
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
```

---

# 4. 双入口 Alpha 发现机制

## 4.1 入口 A:技术强度入口

```text
市场强度 M0-M4
    ↓
行业强度 I0-I4
    ↓
个股 RPS5 / 20 / 50 / 120 / 250
    ↓
结构健康评分
    ↓
买点信号扫描
    ↓
Alpha 机会池
```

回答:

> 市场正在选择谁?

---

## 4.2 入口 B:产业研究入口

```text
研究主题
    ↓
产业链拆解
    ↓
自定义产业链节点
    ↓
手动加入 A 股标的
    ↓
形成研究股池
    ↓
系统每日自动匹配市场强度、行业强度、RPS、结构、买点
```

回答:

> 哪些方向和公司值得长期跟踪?

---

## 4.3 双入口融合

| 股池       | 来源                     | 作用                                |
| ---------- | ------------------------ | ----------------------------------- |
| 技术强度池 | 系统自动扫描             | 发现市场已经选择的强者              |
| 产业研究池 | 用户手动建立 + AI 辅助   | 沉淀长期研究标的                    |
| 核心共振池 | 技术强度池 ∩ 产业研究池 | 既有逻辑,又有市场验证的高优先级机会 |

最高优先级机会进入  **Evo Core Alpha Pool(核心 Alpha 池)** ,需同时满足:

```text
研究逻辑强;
市场状态 >= M2;
行业状态 >= I3;
个股 RPS20 >= 80 且 RPS50 >= 80;
结构健康分 >= 70;
买点触发或接近触发(stop_loss_pct <= 10%)。
```

---

# 5. 两套"行业"概念分离

## 5.1 Market Industry:市场行业

```text
来源:申万行业 / 东方财富行业 / 同花顺概念
用途:计算行业强度、行业排名、行业内龙头识别
对应表:sector_daily_prices, stock_sector_map, industry_strength_daily
```

## 5.2 Research Theme:研究主题

```text
来源:D LAB 行业研究 / 用户手动整理 / AI Agent 产业链拆解
用途:沉淀产业研究池、产业链映射、研究逻辑跟踪
对应表:research_themes, research_theme_nodes, research_watchlist, research_stock_daily_snapshot
```

## 5.3 关联方式

```text
官方行业用于市场统计;
研究主题用于投研框架;
二者通过 symbol 关联,不混表。
```

---

# 6. 量化定义层(本版本核心新增)

这一层是 v0.8 缺失最严重的部分。下面给出每个核心指标的可计算定义,工程师可以直接据此编码。

## 6.1 RPS 计算定义

### 6.1.1 基础口径

```text
RPS_n(t) = 个股过去 n 个交易日累计涨幅 在全市场(或基准池)的百分位排名。

涨幅计算:使用前复权收盘价。
公式:return_n = close(t) / close(t-n) - 1

百分位:
RPS_n = ( 1 - rank(return_n) / N ) * 100
其中 rank 是从大到小排序后的名次,N 是参与排名的股票总数。
```

### 6.1.2 基准池

```text
RPS 基准池 = S0 通过的股票全集
排除:ST、*ST、退市、停牌当日、上市未满 60 个交易日、一字板当日。
```

### 6.1.3 周期

```text
RPS5    短线异动
RPS20   当前强势
RPS50   中期启动
RPS120  中期主升 / 长期强股候选
RPS250  长期强股
```

### 6.1.4 标签

```text
若 RPS20 >= 90 且 RPS50 < 70  → 短线异动
若 RPS20 >= 90 且 RPS50 >= 80 且 RPS120 < 70 → 中期启动
若 RPS50 >= 90 且 RPS120 >= 80 → 中期主升
若 RPS120 >= 90 且 RPS250 >= 80 → 长期强股
若 RPS5 < 30 且 RPS20 >= 80 → 高位衰竭(短期失速)
```

### 6.1.5 新股 / 次新股处理

```text
若上市交易日 < 250,则:
  - RPS250 用 RPS_listed(上市以来全周期百分位)替代,字段单独标记 is_truncated=true;
  - 上市未满 60 个交易日的股票不进入任何 RPS 排名,直到累积满 60 日。
```

---

## 6.2 市场强度 M0-M4 计算定义

市场强度由 6 个维度加权打分,总分 0-100。

### 6.2.1 评分维度

| 维度                 | 权重 | 计算口径                                                                 |
| -------------------- | ---: | ------------------------------------------------------------------------ |
| 指数趋势             |   25 | 沪深300 / 中证500 / 中证1000 三个指数,close > MA50 各得 1/3,合计折算为分 |
| 上涨家数比           |   15 | 当日上涨家数 / 全市场家数,>0.6 满分,0.4-0.6 线性,<0.4 为 0               |
| 涨停跌停净值         |   15 | (涨停数 - 跌停数) / 全市场,>0.02 满分,<-0.01 为 0                        |
| 创新高家数           |   15 | 当日 60 日新高家数 / 全市场,>0.05 满分,<0.01 为 0                        |
| 高 RPS 股票占比      |   15 | RPS90 股票数 / 全市场,>0.10 满分,<0.05 为 0                              |
| 全市场成交额(相对值) |   15 | 当日成交额 / 过去 20 日成交额均值,>1.2 满分,<0.8 为 0                    |

### 6.2.2 状态映射

```text
M0 极弱市场     score < 20    建议仓位 0-10%   禁止新开仓
M1 偏弱市场     20 <= score < 40   建议仓位 10-30%  只观察
M2 中性市场     40 <= score < 60   建议仓位 30-50%  小仓做最强
M3 偏强市场     60 <= score < 80   建议仓位 50-80%  正常参与
M4 强势市场     score >= 80   建议仓位 70-100% 积极寻找主升,警惕高潮
```

### 6.2.3 平滑处理

```text
为避免单日噪声造成状态频繁切换,实际状态采用"3 日确认":
最近 3 个交易日的 score 平均落在某区间,才更新为该状态。
```

---

## 6.3 行业强度 I0-I4 计算定义

### 6.3.1 评分维度

| 维度              | 权重 | 计算口径                                    |
| ----------------- | ---: | ------------------------------------------- |
| 行业 RPS20        |   25 | 行业指数 RPS20 百分位                       |
| 行业 RPS50        |   20 | 行业指数 RPS50 百分位                       |
| 行业内 RPS90 占比 |   20 | 行业内 RPS20>=90 的股票数 / 行业总股票数    |
| 行业成交额放大    |   15 | 行业成交额 / 过去 20 日均值                 |
| 行业新高比        |   10 | 行业内 60 日新高家数 / 行业股票数           |
| 行业龙头表现      |   10 | 行业内市值前 5 的股票当日平均涨跌幅排名分位 |

### 6.3.2 状态映射

```text
I0 弱行业    score < 30     不参与
I1 轮动行业   30 <= score < 50    短线脉冲,观察不重仓
I2 修复行业   50 <= score < 65    开始转强,可以候补
I3 强势行业   65 <= score < 80    主线候选,可参与
I4 核心主线   score >= 80    资金主攻方向,优先参与
```

### 6.3.3 持续性追踪

```text
新增字段:industry_state_streak (该状态已持续天数)
若 I3/I4 持续 >= 5 个交易日,标记 is_main_line=true。
若 I4 持续 >= 20 个交易日且行业 RPS20 开始下降,标记 high_potential_exhaustion=true(主线退潮预警)。
```

---

## 6.4 结构健康评分计算定义

总分 100,7 个模块。

### 6.4.1 趋势结构(20 分)

```text
close > MA20:+5
close > MA50:+5
MA20 > MA50:+5
MA50 > MA120:+5
```

### 6.4.2 回撤控制(15 分)

```text
计算 pullback_from_high = (high_60d - close) / high_60d

pullback < 8%:15 分
8% <= pullback < 15%:10 分
15% <= pullback < 25%:5 分
pullback >= 25%:0 分
```

### 6.4.3 成交量收缩(15 分)

```text
ratio = volume_ma5 / volume_ma20

ratio < 0.7:15 分(明显缩量)
0.7 <= ratio < 0.9:10 分
0.9 <= ratio < 1.1:5 分
ratio >= 1.1:0 分(整理期不应该放量)
```

### 6.4.4 波动收敛(15 分)

```text
计算最近 10 日真实波幅 ATR10 与最近 50 日 ATR50 比值
ratio = ATR10 / ATR50

ratio < 0.7:15 分
0.7 <= ratio < 0.85:10 分
0.85 <= ratio < 1.0:5 分
ratio >= 1.0:0 分
```

### 6.4.5 相对强度保持(15 分)

```text
最近 10 个交易日的 RPS20 平均值 >= 80:15 分
70-80:10 分
60-70:5 分
< 60:0 分
```

### 6.4.6 关键位未破(10 分)

```text
close > MA20:+4
close > 最近 20 日低点:+3
close > 最近箱体下沿:+3
```

### 6.4.7 止损空间可控(10 分)

```text
计算最近一个有效低点(回撤的最低点)到当前 close 的距离
stop_loss_pct = (close - recent_low) / close

stop_loss_pct <= 5%:10 分
5%-8%:7 分
8%-10%:4 分
> 10%:0 分
```

### 6.4.8 入池标准

```text
Structure_score >= 70  → 进入 S4
Structure_score < 70 但 RPS20 >= 90 → 标记为 S3,等待结构修复
Structure_score < 50 → 退出关注
```

---

## 6.5 生命周期状态转移条件(新增)

每日批处理时执行下列状态机更新。

```text
S0 → S1   股票通过 S0 过滤,且 market_state >= M1
S1 → S2   stock 所在 sector 的 industry_state >= I3
S2 → S3   RPS20 >= 80 且 RPS50 >= 70
S3 → S4   structure_score >= 70 且 RPS20 维持 >= 80
S4 → S5   触发任一买点信号
S5 → S6   用户记录建仓
S6 → S7   用户记录退出 / 系统自动判定退出条件触发

降级:
S4 → S3   structure_score < 60 持续 3 日
S3 → S2   RPS20 < 60 持续 5 日
S2 → S1   industry_state < I3 持续 3 日
S1 → S0   market_state = M0 持续 3 日
任意 → S7  持仓中触发退出条件
```

---

# 7. 风控与仓位管理(新增完整章节)

这是 v0.8 最弱的环节,这里补齐。

## 7.1 仓位管理三层规则

### 7.1.1 总仓位

```text
总仓位上限 = 市场状态对应的建议仓位上限。

M0:0%
M1:30%
M2:50%
M3:80%
M4:100%
```

### 7.1.2 行业集中度

```text
单一行业最大仓位 = 总仓位 × 40%
即使行业 = I4,也不超过 40%。
```

### 7.1.3 单笔仓位

```text
基准单笔仓位 = 总资金 × 8%-12%
高确信度(核心共振池 + structure_score >= 85):可加至 15%
最低单笔仓位:5%(低于此不交易,避免摩擦成本)

单笔最大持仓数:8-10 只
```

### 7.1.4 R 值仓位法

```text
R = 单笔愿意承担的风险 = 总资金 × 1%

position_size = R / stop_loss_pct
即:如果止损距离 8%,单笔投入 = R / 0.08 = 总资金 × 12.5%

止损距离越大,仓位越小。
```

---

## 7.2 止损系统

### 7.2.1 初始止损

```text
所有买点必须在下单前确定初始止损价。

类型 A:结构止损
止损价 = 最近 20 日低点 × 0.99
适用:有清晰平台 / 箱体的买点。

类型 B:ATR 止损
止损价 = entry_price - 2 × ATR20
适用:无清晰结构的趋势加速点。

类型 C:百分比止损
止损价 = entry_price × (1 - 8%)
适用:兜底保护。

最终初始止损 = MAX(结构止损, entry_price × (1-10%))
即:不允许止损距离超过 10%。
```

### 7.2.2 移动止损

```text
盈利 > 1R 时:止损上移到成本价(保本止损);
盈利 > 2R 时:止损上移到 entry + 1R;
盈利 > 3R 时:启用追踪止损 = MAX(MA20, 最近 10 日低点);
盈利 > 5R 时:启用更紧的追踪 = MAX(MA10, 最近 5 日低点)。
```

### 7.2.3 时间止损

```text
若买入后 10 个交易日内未创出 entry × 1.05 高点,
且未触发结构止损,系统建议减仓 50%。

若买入后 20 个交易日内仍未走出趋势,系统建议清仓。
```

### 7.2.4 结构止损

```text
持仓股票 close < MA20 且当日成交额放大,触发减仓警告。
持仓股票 close < MA50,触发清仓警告。
```

### 7.2.5 行业 / 市场降级止损

```text
持仓股票所在行业从 I4/I3 降到 I1/I0,触发减仓 50%。
市场从 M3/M4 降到 M0,触发全仓减半。
```

---

## 7.3 加仓规则

```text
只能加仓盈利头寸,不允许补仓亏损头寸。

加仓条件(必须同时满足):
1. 当前盈利 >= 1R;
2. 出现新的合格买点(任一类型);
3. 加仓后单一持仓不超过 15%;
4. 加仓后行业集中度仍合规;
5. 加仓部分使用独立止损,不与原仓位共用。
```

---

## 7.4 减仓规则

```text
触发减仓的情形(任一):
1. 价格触及移动止损;
2. 结构破位(close < MA20 + 放量);
3. 行业降级到 I1/I0;
4. 市场降级到 M0;
5. RPS20 跌破 60 持续 3 日;
6. 时间止损触发。

减仓比例:
首次警告:减仓 30%
二次警告:减仓 50%
退出信号:清仓。
```

---

# 8. 手动行业股池如何加入数据库

(本章保持 v0.8 内容,数据模型见第 12 章统一整理)

## 8.1 核心思路

```text
你手动建立的行业股池,不直接进入官方行业表,而是进入:
research_themes
research_theme_nodes
research_watchlist

系统每天自动关联市场强度、行业强度、个股强度、结构、买点,
最终生成 research_stock_daily_snapshot。
```

## 8.2 导入方式

### MVP:CSV 导入

```csv
theme_name,node_name,symbol,stock_name,role_in_theme,research_reason,logic_summary,catalyst,risk_note,conviction_level,research_status
激光器三大件,泵浦源,688025,杰普特,MOPA激光器,激光器核心部件,国产替代+精密加工需求,下游订单恢复,价格竞争,medium,watching
激光器三大件,控制系统,688188,柏楚电子,激光切割控制系统,激光设备核心控制系统,国产替代+高毛利软件属性,制造业复苏,估值偏高,high,watching
激光器三大件,激光加工设备,002008,大族激光,激光加工设备龙头,行业龙头,设备需求复苏,消费电子/新能源订单,周期波动,medium,watching
```

### 中期:前端手动添加

### 进阶:Markdown / YAML 导入

### AI Agent 草稿生成

```text
状态:draft → confirmed → watching → core → archived
AI 草稿必须用户确认后才入正式库。
```

---

# 9. Evo 生命周期状态机

## 9.1 状态总览

```text
S0 机构认可与基础过滤池
S1 市场可交易池
S2 强行业池
S3 行业内强股池
S4 结构健康准备池
S5 买点触发池
S6 持仓跟踪池
S7 退出复盘池
```

## 9.2 S0 过滤规则

基础过滤:

```text
上市天数 >= 60(原 250 改为 60,新股进入观察更宽松,但 RPS250 单独标记 is_truncated)
非 ST / *ST(可豁免:用户手动加入研究池且明确知晓风险)
非退市风险警示
非长期停牌(连续停牌 > 10 个交易日则移出)
最近 20 日平均成交额 >= 5000 万元(可配置,小盘活跃股专用阈值另设)
价格 >= 2 元(避免极端低价股)
```

机构认可(三选一):

```text
A. 基金持股比例 >= 5% 或 持股基金家数 >= 10
B. 北向持股比例 >= 1%(用最新可得快照)
C. 近 60 日机构调研次数 >= 3 或券商研报覆盖 >= 3 家
```

特殊豁免:

```text
若进入用户的产业研究池,且用户明确标记 conviction_level=high,
可绕过机构认可要求,但仍需通过基础过滤。
```

## 9.3 S1-S7 状态定义见第 6.5 节状态转移条件

---

# 10. 买点系统

(本章保留 v0.8 四类买点,补充冷静期与买点失效规则)

## 10.1 口袋买点

```text
触发条件:
market_state >= M2
AND industry_state >= I3
AND stock_state IN (S3, S4)
AND close > open
AND close_position(当日收盘在当日振幅中位置) >= 0.7
AND volume > 最近 10 个下跌日成交量最大值
AND close >= MA10 OR close >= MA20
AND distance_to_MA20 <= 10%
AND structure_score >= 70
AND stop_loss_pct <= 10%
```

## 10.2 强市场突破买点

```text
前置条件:
market_state >= M3
AND industry_state >= I3(最好 I4)
AND stock_state = S4
AND RPS20 >= 90
AND RPS50 >= 85
AND structure_score >= 70
AND 突破前有至少 10 个交易日整理

触发条件:
close > 最近 N 日平台高点(N >= 10)
AND volume > volume_ma20 × 1.5
AND close_position >= 0.7
AND 行业指数当日同步走强(行业涨跌幅 > 0)
AND 行业内不止一只股票走强
AND stop_loss_pct <= 8%
```

## 10.3 抛压衰竭买点

```text
触发条件:
market_state >= M3
AND industry_state >= I3(最好 I4)
AND stock_rank_in_industry <= top 20%
AND 5% <= pullback_from_high <= 15%
AND close >= MA20 OR close >= MA50
AND pullback_volume_ratio (回调期间均量 / 上涨期间均量) <= 0.8
AND 最近 3 日成交量持续下降
AND 无放量长阴(无单日跌幅 > 5% 且放量)
AND RPS20 >= 80
AND RPS50 >= 85
AND reclaim_signal = true
AND stop_loss_pct <= 10%

reclaim_signal 任一满足:
- close > 前一日 high
- close 重新站上 MA10
- 出现阳线反包
- 当日放量且收盘靠近日高
```

## 10.4 业绩跳空后的二次突破

```text
触发条件:
财报或业绩预告后跳空 >= 5%
AND 跳空缺口未在 5 个交易日内回补
AND RPS20 / RPS50 跳空后明显提升
AND 跳空后缩量整理 >= 5 个交易日
AND 再次放量突破整理平台
AND 市场和行业未退潮
```

## 10.5 买点冷静期(新增)

```text
同一股票同一买点类型,7 个交易日内只能触发一次有效信号。
若买入后亏损止损,该股票进入 30 天冷静期,期间不再触发买点信号。
若买入后正收益退出,该股票进入 14 天冷静期。
```

## 10.6 买点失效规则(新增)

```text
信号生成后第二个交易日仍未跌破止损位时,信号有效;
否则标记为 invalid,从 S5 退回 S4 或更低。

信号生成后第二个交易日开盘价 > entry × 1.05,标记为 chase_risk(追高风险),仓位减半。
```

---

# 11. 数据采集与计算实现

## 11.1 每日任务顺序

```text
Step 1   更新交易日历
Step 2   更新股票列表和基础信息
Step 3   更新个股日线(主源 akshare,失败转 baostock)
Step 4   更新指数日线
Step 5   更新行业 / 板块日线
Step 6   更新股票-行业映射
Step 7   更新 ETF 行情
Step 8   更新新闻 / 公告
Step 9   更新基金持股 / 北向最新可得快照
Step 10  数据质量校验(写入 data_quality_issue)
Step 11  计算均线、成交额、涨跌幅、ATR 等基础指标
Step 12  计算 RPS5 / 20 / 50 / 120 / 250
Step 13  计算市场强度 M0-M4
Step 14  计算行业强度 I0-I4
Step 15  计算结构健康评分
Step 16  扫描买点信号(含冷静期 / 失效规则)
Step 17  更新生命周期状态(状态转移)
Step 18  更新持仓跟踪 / 触发风控警报
Step 19  生成研究池每日快照
Step 20  生成 Alpha 机会池
Step 21  导出云端核心表
Step 22  上传 R2
Step 23  导入 CockroachDB
Step 24  生成日报简报
```

## 11.2 失败重试与补数(新增)

```text
任意 Step 失败:
1. 记录到 pipeline_run_log 表;
2. 触发同步告警(本地通知 + 邮件);
3. 该 Step 自动重试 3 次,间隔 5 分钟;
4. 仍失败则跳过下游依赖该数据的 Step,但其他独立 Step 继续;
5. 第二天补数任务自动检测前一日缺失数据,优先补齐。

补数策略:
- 日线缺失:用备份数据源重新采集;
- 行业映射缺失:沿用前一日;
- RPS 计算因数据缺失失败:跳过当日,标记为 stale。
```

## 11.3 工程目录(完整)

```text
backend/
├── data_job/
│   ├── update_calendar.py
│   ├── update_stock_list.py
│   ├── update_stock_kline.py
│   ├── update_index_kline.py
│   ├── update_sector_kline.py
│   ├── update_stock_sector_map.py
│   ├── update_etf_kline.py
│   ├── update_finance.py
│   ├── update_institutional_holding.py
│   ├── update_news.py
│   ├── update_announcements.py
│   └── data_quality_check.py
│
├── research/
│   ├── import_research_watchlist.py
│   ├── import_research_yaml.py
│   ├── update_research_snapshot.py
│   └── research_theme_service.py
│
├── quant_engine/
│   ├── features/
│   │   ├── calc_indicators.py
│   │   ├── calc_rps.py
│   │   ├── calc_market_strength.py
│   │   ├── calc_industry_strength.py
│   │   ├── calc_structure_health.py
│   │   └── calc_timing_signals.py
│   │
│   ├── lifecycle/
│   │   ├── build_s0_universe.py
│   │   ├── update_lifecycle_state.py
│   │   ├── update_position_state.py
│   │   └── generate_trade_plan.py
│   │
│   ├── strategies/
│   │   ├── pocket_pivot.py
│   │   ├── breakout.py
│   │   ├── supply_exhaustion.py
│   │   ├── earnings_gap_second_breakout.py
│   │   └── industry_resonance.py
│   │
│   ├── risk/
│   │   ├── position_sizing.py
│   │   ├── stop_loss.py
│   │   ├── trailing_stop.py
│   │   └── portfolio_risk.py
│   │
│   └── backtest/
│       ├── backtest_engine.py
│       ├── walk_forward.py
│       ├── metrics.py
│       └── parameter_sweep.py
│
├── storage/
│   ├── sqlite_manager.py
│   ├── csv_exporter.py
│   ├── parquet_exporter.py
│   ├── r2_manager.py
│   └── cloud_importer.py
│
├── monitoring/
│   ├── pipeline_run_log.py
│   ├── alert_manager.py
│   └── system_health.py
│
└── scheduler/
    ├── daily_pipeline.py
    ├── weekly_pipeline.py
    └── monthly_pipeline.py
```

---

# 12. 数据模型完整 Schema(新增完整章节)

## 12.1 基础数据表

### stock_info

```sql
CREATE TABLE stock_info (
    symbol TEXT PRIMARY KEY,
    stock_name TEXT,
    list_date DATE,
    delist_date DATE,
    is_st BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    market TEXT,
    updated_at TIMESTAMP DEFAULT now()
);
```

### stock_daily_kline

```sql
CREATE TABLE stock_daily_kline (
    symbol TEXT,
    trade_date DATE,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    amount NUMERIC,
    adj_factor NUMERIC,
    is_suspended BOOLEAN DEFAULT false,
    is_one_word_limit BOOLEAN DEFAULT false,
    PRIMARY KEY (symbol, trade_date)
);
```

### sector_daily_prices

```sql
CREATE TABLE sector_daily_prices (
    sector_code TEXT,
    sector_name TEXT,
    sector_source TEXT,
    trade_date DATE,
    close NUMERIC,
    pct_change NUMERIC,
    amount NUMERIC,
    PRIMARY KEY (sector_code, trade_date)
);
```

### stock_sector_map

```sql
CREATE TABLE stock_sector_map (
    symbol TEXT,
    sector_code TEXT,
    sector_source TEXT,
    is_primary BOOLEAN DEFAULT false,
    PRIMARY KEY (symbol, sector_code, sector_source)
);
```

---

## 12.2 强度计算结果表

### stock_strength_daily

```sql
CREATE TABLE stock_strength_daily (
    symbol TEXT,
    trade_date DATE,
    rps_5 NUMERIC,
    rps_20 NUMERIC,
    rps_50 NUMERIC,
    rps_120 NUMERIC,
    rps_250 NUMERIC,
    rps_label TEXT,
    is_truncated BOOLEAN DEFAULT false,
    PRIMARY KEY (symbol, trade_date)
);
```

### market_strength_daily

```sql
CREATE TABLE market_strength_daily (
    trade_date DATE PRIMARY KEY,
    score NUMERIC,
    state TEXT,
    state_streak INT,
    suggested_position_min NUMERIC,
    suggested_position_max NUMERIC,
    detail JSONB,
    created_at TIMESTAMP DEFAULT now()
);
```

### industry_strength_daily

```sql
CREATE TABLE industry_strength_daily (
    sector_code TEXT,
    trade_date DATE,
    score NUMERIC,
    state TEXT,
    state_streak INT,
    is_main_line BOOLEAN DEFAULT false,
    high_potential_exhaustion BOOLEAN DEFAULT false,
    detail JSONB,
    PRIMARY KEY (sector_code, trade_date)
);
```

### structure_health_daily

```sql
CREATE TABLE structure_health_daily (
    symbol TEXT,
    trade_date DATE,
    total_score NUMERIC,
    trend_score NUMERIC,
    pullback_score NUMERIC,
    volume_score NUMERIC,
    volatility_score NUMERIC,
    relative_strength_score NUMERIC,
    key_level_score NUMERIC,
    stop_loss_score NUMERIC,
    structure_label TEXT,
    PRIMARY KEY (symbol, trade_date)
);
```

---

## 12.3 状态机与机会表

### stock_lifecycle_state

```sql
CREATE TABLE stock_lifecycle_state (
    symbol TEXT,
    trade_date DATE,
    state TEXT,
    previous_state TEXT,
    state_changed_at DATE,
    market_state TEXT,
    industry_state TEXT,
    rps_label TEXT,
    structure_score NUMERIC,
    next_action TEXT,
    PRIMARY KEY (symbol, trade_date)
);
```

### timing_signals_daily

```sql
CREATE TABLE timing_signals_daily (
    signal_id UUID PRIMARY KEY,
    symbol TEXT,
    trade_date DATE,
    signal_type TEXT,
    signal_score NUMERIC,
    entry_price NUMERIC,
    stop_loss_price NUMERIC,
    stop_loss_pct NUMERIC,
    suggested_position_pct NUMERIC,
    risk_amount_pct NUMERIC,
    is_chase_risk BOOLEAN DEFAULT false,
    is_invalidated BOOLEAN DEFAULT false,
    invalidation_reason TEXT,
    cooling_until DATE,
    detail JSONB,
    created_at TIMESTAMP DEFAULT now()
);
```

---

## 12.4 持仓与交易表

### positions

```sql
CREATE TABLE positions (
    position_id UUID PRIMARY KEY,
    user_id UUID,
    symbol TEXT,
    open_date DATE,
    avg_entry_price NUMERIC,
    quantity NUMERIC,
    initial_stop_price NUMERIC,
    current_stop_price NUMERIC,
    signal_id UUID REFERENCES timing_signals_daily(signal_id),
    theme_id UUID,
    state TEXT,
    last_updated TIMESTAMP DEFAULT now()
);
```

### trades

```sql
CREATE TABLE trades (
    trade_id UUID PRIMARY KEY,
    position_id UUID REFERENCES positions(position_id),
    trade_date DATE,
    action TEXT,
    price NUMERIC,
    quantity NUMERIC,
    reason TEXT,
    pnl NUMERIC,
    pnl_pct NUMERIC,
    r_multiple NUMERIC,
    market_state_at_trade TEXT,
    industry_state_at_trade TEXT,
    created_at TIMESTAMP DEFAULT now()
);
```

### trade_review

```sql
CREATE TABLE trade_review (
    review_id UUID PRIMARY KEY,
    position_id UUID,
    review_type TEXT,
    review_summary TEXT,
    error_category TEXT,
    lessons_learned TEXT,
    rule_change_suggested TEXT,
    created_at TIMESTAMP DEFAULT now()
);
```

---

## 12.5 研究池表

(沿用 v0.8 设计)

```sql
research_themes
research_theme_nodes
research_watchlist
research_stock_daily_snapshot
```

---

## 12.6 监控与运维表

### pipeline_run_log

```sql
CREATE TABLE pipeline_run_log (
    run_id UUID PRIMARY KEY,
    run_date DATE,
    step_name TEXT,
    status TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    error_msg TEXT,
    retry_count INT DEFAULT 0
);
```

### ai_analysis_cache

```sql
CREATE TABLE ai_analysis_cache (
    cache_id UUID PRIMARY KEY,
    symbol TEXT,
    analysis_type TEXT,
    prompt_hash TEXT,
    response TEXT,
    cited_data JSONB,
    expire_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
```

---

## 12.7 用户与权限表(新增,为后续 SaaS 化做准备)

```sql
CREATE TABLE users (
    user_id UUID PRIMARY KEY,
    email TEXT UNIQUE,
    name TEXT,
    plan TEXT DEFAULT 'free',
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE user_research_themes (
    user_id UUID,
    theme_id UUID,
    role TEXT,
    PRIMARY KEY (user_id, theme_id)
);

-- 一个研究主题可以是私有(只有所有者可见)、共享(团队可见)、公开(D LAB 内容)
ALTER TABLE research_themes ADD COLUMN visibility TEXT DEFAULT 'private';
ALTER TABLE research_themes ADD COLUMN owner_id UUID;
```

---

# 13. 回测框架(新增完整章节)

回测是验证策略有效性的核心,v0.8 完全没有展开。

## 13.1 回测目标

```text
1. 验证四类买点的历史胜率与盈亏比;
2. 验证不同市场状态下买点的表现差异;
3. 验证不同行业状态下买点的表现差异;
4. 优化结构健康评分阈值;
5. 优化止损与仓位参数。
```

---

## 13.2 回测引擎设计

```text
输入:
  - 起始日期 / 结束日期
  - 策略类型(pocket_pivot / breakout / supply_exhaustion / earnings_gap)
  - 参数集(止损 / 移动止损 / 时间止损)
  - 风控规则(单笔仓位 / 行业集中度)
  - 市场状态过滤(M2+/M3+ 等)

流程:
  1. 历史数据回放,逐日扫描信号;
  2. 触发信号 → 模拟下单(下一交易日开盘价);
  3. 持有期间每日检查止损 / 移动止损 / 退出条件;
  4. 触发退出 → 记录交易;
  5. 全周期结束 → 计算指标。

输出:
  - 总收益率 / 年化收益率
  - 最大回撤 / 最长回撤期
  - 胜率 / 盈亏比
  - 平均持有天数
  - 交易次数
  - 按市场状态 / 行业状态 / 买点类型分组统计
```

---

## 13.3 关键指标定义

```text
胜率 = 盈利交易 / 总交易
盈亏比 = 平均盈利 / 平均亏损
期望值 = 胜率 × 平均盈利 - (1-胜率) × 平均亏损
夏普比 = (年化收益 - 无风险收益) / 年化波动
卡玛比 = 年化收益 / 最大回撤
R 倍数分布 = 每笔交易 R 值的分布(目标:平均 > 0.5R)
```

---

## 13.4 Walk Forward 验证(关键)

```text
避免过拟合的核心方法。

流程:
1. 数据分为训练期(in-sample)和验证期(out-of-sample);
2. 在训练期上找最优参数;
3. 用最优参数在验证期上跑一遍;
4. 滚动向前(每 6 个月滚一次);
5. 累积所有验证期结果。

判断标准:
- 验证期表现 < 训练期表现的 70%:过拟合,参数不可信;
- 验证期表现 ≈ 训练期表现:参数稳健,可上线;
- 验证期表现 > 训练期表现:警惕巧合,继续观察。
```

---

## 13.5 回测的局限性(必须警惕)

```text
1. 历史数据中无法复现真实滑点 / 冲击成本;
2. 涨停板买不进、跌停板卖不出的情况需在回测中模拟;
3. 退市股票的"幸存者偏差"必须修正;
4. 历史行业分类与当前可能不同(申万分类有调整);
5. 北向 / 基金持股数据存在披露滞后,回测时不能"偷看未来"。

回测引擎必须:
- 加入合理滑点(默认 0.2%);
- 模拟 T+1;
- 模拟涨跌停限制;
- 包含已退市股票数据;
- 严格 point-in-time(只用当时可获得的数据)。
```

---

# 14. 系统效果衡量(新增)

## 14.1 基准选择

```text
主基准:沪深 300(代表大盘风格)
次基准:中证 500 + 中证 1000 等权(代表中小盘)
风格基准:申万一级行业等权(剔除行业 Beta 后看 Alpha)
```

## 14.2 系统跟踪指标

```text
每日跟踪:
- S5 触发数 / 触发后 5 日 / 10 日 / 20 日平均涨幅
- S6 持仓加权收益 vs 基准
- S4 池整体表现 vs 基准

每月跟踪:
- 系统外推荐(技术强度池 + 研究池)的 forward returns
- 不同状态分组的 IC(Information Coefficient)

每季度跟踪:
- 总系统 vs 基准的累积超额
- 错误归因分布
- 规则迭代日志
```

---

# 15. 云端 API 设计

## 15.1 市场强度

```http
GET /api/market/strength/latest
GET /api/market/strength/history?days=60
```

## 15.2 行业强度

```http
GET /api/industries/strength?date=latest&state=I3,I4
GET /api/industries/{sector_code}/dashboard
GET /api/industries/{sector_code}/stocks
```

## 15.3 Alpha 机会

```http
GET /api/alpha/opportunities?state=S4,S5&limit=50
GET /api/alpha/core-pool
GET /api/alpha/today
```

## 15.4 个股作战室

```http
GET /api/stocks/{symbol}/dashboard
GET /api/stocks/{symbol}/kline
GET /api/stocks/{symbol}/rps
GET /api/stocks/{symbol}/lifecycle
GET /api/stocks/{symbol}/signals
GET /api/stocks/{symbol}/trade-plan
```

## 15.5 买点信号

```http
GET /api/signals/today
GET /api/signals/today?signal_type=pocket_pivot
GET /api/signals/today?signal_type=breakout
GET /api/signals/today?signal_type=supply_exhaustion
GET /api/signals/today?signal_type=earnings_gap
```

## 15.6 产业研究池

```http
GET /api/research/themes
POST /api/research/themes
GET /api/research/themes/{theme_id}
POST /api/research/themes/{theme_id}/nodes
POST /api/research/watchlist
GET /api/research/watchlist?theme_id=xxx
GET /api/research/themes/{theme_id}/snapshot
```

## 15.7 持仓与交易

```http
GET /api/positions
POST /api/positions
PATCH /api/positions/{position_id}
POST /api/trades
GET /api/trades/history
GET /api/portfolio/risk
```

## 15.8 回测

```http
POST /api/backtest/run
GET /api/backtest/results/{job_id}
GET /api/backtest/strategies
```

## 15.9 AI Agent

```http
POST /api/ai/analyze-stock
POST /api/ai/analyze-industry
POST /api/ai/generate-trade-plan
POST /api/ai/generate-daily-report
POST /api/ai/review-trade
POST /api/ai/generate-research-theme-draft
```

AI 原则:

```text
不全量自动跑;
只对重点机会跑;
用户点击触发;
日报批量生成;
结果缓存;
控制成本;
所有 AI 输出必须附带 cited_data,绑定具体数据字段,可追溯。
```

---

# 16. 前端页面设计

## 16.1 首页:Evo Alpha 雷达

```text
模块:
- 市场状态卡片
- 强行业榜
- S5 买点触发
- S4 结构准备
- 核心共振池
- 持仓风险提醒
- AI 每日作战简报
```

## 16.2 市场温度页

```text
M0-M4 状态;
指数趋势;
上涨 / 下跌家数;
涨停 / 跌停结构;
创新高数量;
RPS90 股票数量;
全市场成交额;
建议仓位区间;
6 个评分维度的明细展示。
```

## 16.3 行业战役指挥所

```text
I0-I4 状态;
行业强度榜;
行业热力图;
行业 RPS;
行业成交额放大;
行业内 RPS90 占比;
行业内新高比例;
行业龙一/龙二/中军;
行业持续天数;
行业退潮风险预警。
```

## 16.4 产业研究池页面

```text
页面一:研究主题列表
页面二:主题详情(产业链地图 + 节点 + 公司 + 量化状态)
```

## 16.5 个股作战室

```text
左侧:K线 + 成交量 + 均线 + 买点标记
中间:Evo 生命周期状态 + 结构评分 + RPS 趋势
右侧:AI 分析与交易计划(附数据引用)
底部:新闻、公告、复盘记录
```

## 16.6 买点信号页

```text
四个 Tab,每个买点列出:
股票、行业、市场状态、行业状态、RPS 状态、结构分、
触发价、止损价、止损距离、建议仓位、是否追高风险、是否冷静期。
```

## 16.7 持仓风控页(增强)

```text
持仓清单 + 实时盈亏;
每只股票当前 R 值;
当前止损 / 移动止损;
组合层风险:
- 总仓位 vs 建议仓位
- 行业集中度
- 单股集中度
- 当前总 R 风险敞口

风险事件流:
- 结构破位
- 行业降级
- 市场降级
- RPS 衰退
- 时间止损接近
```

## 16.8 复盘进化页

```text
胜率 / 盈亏比 / 最大回撤;
按买点类型 / 市场状态 / 行业状态分组;
错误类型统计;
规则优化建议;
回测对比模拟;
系统 vs 基准累积曲线。
```

## 16.9 系统健康页(新增)

```text
管理员页面。
展示:
- 每日 pipeline 各 Step 状态
- 数据质量问题数量与详情
- 数据源调用状态
- AI 调用次数 / 成本
- 数据库大小 / 配额使用
```

---

# 17. AI Agent 设计

| Agent            | 职责                   |
| ---------------- | ---------------------- |
| Market Agent     | 解读市场状态和仓位     |
| Industry Agent   | 解读强行业和主线持续性 |
| Stock Agent      | 解读 RPS 和个股强度    |
| Structure Agent  | 解读结构健康           |
| Timing Agent     | 解读买点信号           |
| Research Agent   | 拆解产业链和公司映射   |
| Trade Plan Agent | 生成交易计划           |
| Risk Agent       | 解读组合风险和单股风险 |
| Review Agent     | 复盘交易错误           |

## 17.1 AI 输出可追溯性(新增)

```text
所有 Agent 输出必须包含 cited_data:

{
  "summary": "该股票处于 S4 状态,结构健康分 78,...",
  "cited_data": [
    {"field": "structure_score", "value": 78, "table": "structure_health_daily", "date": "2026-04-28"},
    {"field": "rps_20", "value": 87, "table": "stock_strength_daily", "date": "2026-04-28"},
    ...
  ]
}

前端展示时,鼠标悬停在 AI 结论的具体数字上,可以高亮显示对应数据来源。
这样可以避免 AI 幻觉,用户始终知道结论从哪里来。
```

## 17.2 AI 成本控制

```text
每日预算上限(默认 10 元 / 用户 / 日);
单次调用前检查 ai_analysis_cache;
缓存有效期默认 24 小时;
重大事件(财报 / 涨停 / 跳空)主动失效缓存;
用户主动点击"刷新"才重新调用。
```

---

# 18. 失败场景与降级方案(新增完整章节)

## 18.1 数据源故障

```text
场景:akshare 接口失败。
降级:
1. 自动切换到 baostock;
2. 仍失败 → 跳过当日数据,使用前一日数据 + stale 标记;
3. 通知用户"数据延迟,部分指标可能不准";
4. 第二天补数任务自动追赶。
```

## 18.2 计算任务失败

```text
场景:RPS 计算因数据缺失失败。
降级:
1. 不发布当日 Alpha 机会池;
2. 前端展示前一日数据 + "数据延迟"提示;
3. 用户仍可使用历史数据回看。
```

## 18.3 云端配额超限

```text
场景:CockroachDB 超配额。
降级:
1. 缩减云端数据保留期(从 2 年缩到 1 年);
2. 关闭非核心查询接口;
3. 保留首页 + 个股 + 持仓三个核心页面;
4. 触发用户付费升级提示。
```

## 18.4 AI 调用超预算

```text
场景:GLM 调用超日预算。
降级:
1. 当日 AI 功能切换为"只读缓存";
2. 用户仍可看历史 AI 分析,但不能生成新的;
3. 第二天恢复。
```

---

# 19. 日常使用场景

## 19.1 每日收盘后:15-30 分钟

```text
1. 看市场状态(M3 偏强);
2. 看强行业(I4 主线 = 半导体设备 / AI 算力);
3. 看 S5 触发与 S4 准备名单;
4. 看产业研究池中开始被市场验证的标的;
5. 看持仓风险与移动止损位置;
6. 输出:明日观察名单 + 明日可能买点 + 当前持仓动作 + 不该碰的清单。
```

## 19.2 盘中

```text
只看三件事:
- S5 买点是否触发
- S6 持仓是否破位
- 行业强度是否突变
```

## 19.3 行业研究

```text
1. 创建研究主题;
2. 拆产业链节点;
3. 手动加入标的;
4. 填写研究逻辑;
5. 系统每日自动匹配技术状态;
6. 观察哪些标的从 S1/S2 进入 S3/S4/S5。
```

## 19.4 周末复盘

```text
统计:
- 本周交易;
- 不同买点胜率;
- 不同市场状态收益;
- 研究池转化率;
- 错误类型分布。

输出建议:
- M1 不开新仓;
- 口袋买点止损 > 8% 只观察;
- 突破买点必须行业同步;
- 研究池 90 天未被验证的标的降级。
```

---

# 20. 开发路线图 v0.9

## Phase 1:数据采集与质量保障(1-2 周)

```text
- 接入 akshare 主源 + baostock 备份
- 跑通日线、行业、指数数据
- data_quality_issue 表与校验逻辑
- pipeline_run_log 与告警
```

## Phase 2:RPS 与基础指标(1 周)

```text
- 计算 RPS5/20/50/120/250
- 计算 ATR / 均线 / 复权
- 新股 / 次新股截断处理
```

## Phase 3:研究池 MVP(1 周)

```text
- research_themes / nodes / watchlist
- CSV 导入
- research_stock_daily_snapshot
```

## Phase 4:S0 机构认可池(1 周)

```text
- 基金持股 / 北向数据
- S0 过滤规则
- 特殊豁免逻辑
```

## Phase 5:市场强度与行业强度(1-2 周)

```text
- M0-M4 计算
- I0-I4 计算
- 平滑处理与状态持续追踪
```

## Phase 6:结构健康评分(1 周)

```text
- 7 个评分模块
- structure_health_daily 表
```

## Phase 7:生命周期状态机(1 周)

```text
- S0-S7 状态表
- 每日状态转移
- Alpha 机会池
- 核心共振池
```

## Phase 8:买点信号(2 周)

```text
- 口袋买点
- 突破买点
- 抛压衰竭
- 业绩跳空二次突破
- 冷静期 / 失效规则
```

## Phase 9:风控系统(1-2 周)

```text
- 仓位计算
- 初始 / 移动 / 时间 / 结构止损
- 加减仓规则
- 组合层风险监控
```

## Phase 10:回测框架(2-3 周)

```text
- 回测引擎
- 滑点 / T+1 / 涨跌停模拟
- Walk forward 验证
- 参数 sweep
- 历史买点性能报告
```

## Phase 11:前端 MVP(2-3 周)

```text
- 首页 Alpha 雷达
- 市场温度页
- 行业战役指挥所
- 产业研究池
- 个股作战室
- 买点信号页
- 持仓风控页
- 复盘进化页
- 系统健康页
```

## Phase 12:云端部署(1 周)

```text
- CSV / Parquet 导出
- R2 上传
- CockroachDB 导入
- FastAPI 查询
- Vercel 部署
```

## Phase 13:AI Agent(2 周)

```text
- 8 个 Agent
- cited_data 可追溯机制
- ai_analysis_cache
- 成本控制
```

## Phase 14:复盘进化(1 周)

```text
- trade_review 表
- 错误归因
- 规则优化建议
- 系统 vs 基准跟踪
```

## Phase 15:多用户与 SaaS 化(后期)

```text
- 用户系统
- 研究主题可见性
- 订阅层级
- D LAB 内容订阅
```

---

# 21. 最终定义

EvoAlpha OS v0.9 可以定义为:

> 一个以本地数据工厂为计算底座,以低成本云端架构为展示与分发层,以双入口 Alpha 发现机制为核心流程,以手动产业研究池为长期标的沉淀,以可量化的市场强度、行业强度、结构健康评分为客观判断依据,以五周期 RPS 为个股强弱识别,以四类买点信号为执行触发,以 R 值仓位法和多层止损为风控基础,以严格的回测与 walk forward 验证为参数依据,以 cited_data 可追溯的 AI Agent 为分析助手,以持仓跟踪和复盘归因为进化机制的 A 股智能投研系统。

一句话版本:

> EvoAlpha OS 让用户手动研究方向,让系统自动验证强弱;让技术发现强者,让研究解释强者;最后用生命周期状态机等待低风险买点,用统一风控控制下行,用可追溯的 AI 与回测推动系统持续进化。

---

# 22. v0.8 → v0.9 关键变更

```text
新增:
1. 量化定义层(第 6 章):RPS、市场强度、行业强度、结构健康全部给出可计算公式;
2. 状态转移条件(6.5):S0-S7 之间的升降级规则形式化;
3. 风控与仓位管理(第 7 章):R 值仓位、四类止损、加减仓规则;
4. 买点冷静期与失效规则(10.5、10.6);
5. 数据源选型与数据质量(3.3、3.4);
6. 数据模型完整 Schema(第 12 章):核心 11 张表全部给出;
7. 回测框架(第 13 章):引擎设计、Walk forward、回测局限性;
8. 系统效果衡量(第 14 章):基准选择、跟踪指标;
9. 失败场景与降级方案(第 18 章);
10. AI 输出可追溯性(17.1):cited_data 机制防幻觉;
11. 用户与权限模型(12.7):为 SaaS 化预留;
12. 系统健康页(16.9);
13. 持仓与交易表(12.4):positions / trades / trade_review;
14. 监控运维表(12.6):pipeline_run_log / ai_analysis_cache;
15. 失败重试与补数(11.2)。

调整:
1. S0 上市天数从 250 改为 60,新股可进入观察但 RPS250 标记 is_truncated;
2. 研究池豁免机构认可的条件明确化;
3. 路线图从 10 个 Phase 扩展到 15 个 Phase,新增风控、回测、SaaS 化阶段。
```

---

# 23. 当前最重要的共识

```text
1. 双入口必须真实独立、最终汇入同一状态机;
2. 官方行业与研究主题严格分离,通过 symbol 关联;
3. 每个量化指标必须有可计算定义,不允许"模糊概念";
4. 风控不是装饰,是与买点同等优先级的核心模块;
5. 回测是上线的前提,没有 walk forward 验证的策略不进入实盘;
6. AI 是助手不是决策者,所有结论必须可追溯到具体数据;
7. 失败场景必须有降级方案,系统不能"全部失败"。
```

---

# 24. 下一步

如果按本路线图推进,建议:

```text
本周:Phase 1 + Phase 2(数据采集 + RPS 计算)
本月内:跑通 Phase 3 + Phase 4 + Phase 5(研究池 + S0 + 市场行业强度)
两个月内:跑通 Phase 6-9(结构 + 状态机 + 买点 + 风控)
三个月内:Phase 10 回测验证 + Phase 11 前端 MVP
四个月内:Phase 12-13 云端 + AI 上线
五个月内:Phase 14 复盘进化闭环
```

> 进化即自由
> Evolution is Freedom
>
