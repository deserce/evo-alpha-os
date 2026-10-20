可以。下面这版我建议作为新的主文档：

# EvoAlpha OS 白皮书 v0.8

## 双入口 Alpha 发现系统：技术强度扫描 × 产业研究股池 × 生命周期状态机

> 进化即自由
> Evolution is Freedom

---

# 0. 核心摘要

EvoAlpha OS 是一个面向 A 股市场的个人级 Alpha 机会发现与系统化投研平台。

它不是单纯的选股器，也不是自动交易机器人，而是一套把 **数据采集、RPS 强度、市场强度、行业强度、产业研究、结构健康、买点触发、持仓跟踪、复盘进化** 连接起来的智能投研操作系统。

系统的核心目标不是预测未来，而是回答一组稳定的问题：

```text
现在市场能不能做？
资金正在进攻哪些行业？
哪些股票已经被市场验证？
我手动研究的产业股池有没有开始被市场认可？
哪些股票结构健康，值得等待？
哪些股票触发低风险买点？
买入后强势是否延续？
退出后这笔交易是系统问题，还是执行问题？
```

EvoAlpha OS 的核心公式：

```text
Evo Alpha =
机构认可 × 市场强度 × 行业强度 × 个股强度 × 研究逻辑 × 结构健康 × 低风险买点 × 纪律复盘
```

系统采用：

```text
双入口
单中枢
同一生命周期
```

双入口是：

```text
入口 A：技术强度入口
市场强度 → 行业强度 → 个股 RPS → 结构健康 → 买点触发

入口 B：产业研究入口
研究主题 → 产业链节点 → 手动标的池 → 市场验证 → 技术状态跟踪
```

单中枢是：

```text
Evo 强势股生命周期状态机
```

同一生命周期是：

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

一句话定义：

> EvoAlpha OS 是一个用技术强度发现市场已经选择的强者，用产业研究沉淀长期跟踪标的，再用生命周期状态机等待低风险买点和纪律化退出的 A 股智能投研系统。

---

# 1. 项目定位

## 1.1 系统名称

```text
EvoAlpha OS
Evolution Alpha Operating System
```

内部工程名可以继续使用：

```text
EvoQuant OS
```

二者关系：

| 名称                 | 定位                                         |
| -------------------- | -------------------------------------------- |
| EvoAlpha OS          | 对外品牌名，强调 Alpha 机会发现              |
| EvoQuant OS          | 对内工程名，强调量化计算、数据管道、策略引擎 |
| D LAB                | 内容品牌、研究品牌、知识资产入口             |
| Evolution is Freedom | 系统精神内核                                 |

---

## 1.2 系统不是什么

EvoAlpha OS 不做这些事：

```text
不预测明天涨跌；
不承诺收益；
不自动替用户下单；
不追逐所有热点；
不依赖单一指标；
不让 AI 替代人的最终判断；
不把行业研究和交易执行混为一谈。
```

---

## 1.3 系统要做什么

EvoAlpha OS 要做的是：

```text
用数据识别强弱；
用研究沉淀逻辑；
用规则过滤噪音；
用状态机管理机会；
用买点模型触发交易计划；
用风控约束执行；
用复盘推动系统进化。
```

系统的长期目标：

> 把投资从混乱的信息流，变成一套清晰的决策流；
> 把主观经验，沉淀成可验证、可回测、可复盘的数据资产。

---

# 2. 系统第一性原理

## 2.1 不预测，只识别

EvoAlpha OS 的第一性原理是：

> 价格变化来自供需失衡，趋势来自持续的供需失衡。
> 系统不预测未来，而是识别市场已经发生的强弱变化。

所以系统不问：

```text
这只股票未来一定会涨吗？
```

而是问：

```text
市场是否支持交易？
行业是否正在变强？
个股是否处于行业前排？
是否有机构认可或研究逻辑支撑？
结构是否健康？
买点是否低风险？
如果错了，止损在哪里？
```

---

## 2.2 强度、逻辑、结构、买点必须分开

系统必须避免一个常见错误：

```text
逻辑好 = 买入
RPS 强 = 买入
行业强 = 买入
突破了 = 买入
```

Evo 的正确流程是：

```text
强度发现
    ↓
逻辑验证
    ↓
结构等待
    ↓
买点触发
    ↓
风险执行
    ↓
复盘进化
```

每一层有不同作用：

| 层级     | 解决的问题             |
| -------- | ---------------------- |
| 市场强度 | 今天能不能交易         |
| 行业强度 | 资金主战场在哪里       |
| 个股强度 | 行业内谁是前排         |
| 研究逻辑 | 为什么值得跟踪         |
| 结构健康 | 现在是否值得等待       |
| 买点质量 | 是否可以执行           |
| 风控复盘 | 错了怎么办，对了怎么拿 |

---

# 3. 工程架构：旧架构不推翻，只升级内核

EvoAlpha OS 保留原来的低成本云端架构。

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
本地重，云端轻；
计算前置，查询后置；
全量数据在本地，核心结果上云端；
AI 按需调用，不全量烧钱；
先跑通 MVP，再逐步商业化。
```

对应分工：

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

# 4. 双入口 Alpha 发现机制

这是新版系统的核心设计。

## 4.1 入口 A：技术强度入口

技术强度入口由系统每日自动扫描。

流程：

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

它回答：

> 市场正在选择谁？

适合发现：

```text
资金已经攻击的行业；
已经跑出来的行业前排；
即将触发买点的强势股；
持仓中开始出现风险变化的股票。
```

技术入口的优势：

```text
客观；
及时；
可自动化；
可回测；
适合每天运行。
```

技术入口的短板：

```text
容易结果导向；
可能只看到已经涨起来的股票；
需要研究逻辑解释为什么强。
```

---

## 4.2 入口 B：产业研究入口

产业研究入口由用户和 AI Agent 共同完成。

流程：

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

它回答：

> 哪些方向和公司值得长期跟踪？

适合发现：

```text
还没有完全被市场定价的产业机会；
通过行业研究提前发现的方向；
逻辑好但技术尚未启动的股票；
未来可能进入强势主线的潜在标的。
```

产业入口的优势：

```text
前瞻性强；
和用户行业研究能力结合；
能沉淀 D LAB 研究资产；
能把一级市场/产业链研究映射到二级市场。
```

产业入口的短板：

```text
逻辑好不等于股价上涨；
行业好不等于买点好；
必须经过市场强度、行业强度、个股强度和结构验证。
```

---

## 4.3 双入口如何融合

两个入口最终进入同一个中枢：

```text
技术强度入口
        ↓
        → Evo 生命周期状态机 → 交易计划 / 观察 / 复盘
        ↑
产业研究入口
```

系统中形成三个池：

| 股池       | 来源                     | 作用                                 |
| ---------- | ------------------------ | ------------------------------------ |
| 技术强度池 | 系统自动扫描             | 发现市场已经选择的强者               |
| 产业研究池 | 用户手动建立 + AI 辅助   | 沉淀长期研究标的                     |
| 核心共振池 | 技术强度池 ∩ 产业研究池 | 既有逻辑，又有市场验证的高优先级机会 |

最高优先级机会满足：

```text
研究逻辑强；
市场状态允许；
行业状态强；
个股 RPS 走强；
结构健康；
买点触发或接近触发。
```

这类股票进入：

```text
Evo Core Alpha Pool
核心 Alpha 池
```

---

# 5. 两套“行业”概念必须分开

这是手动行业股池能否顺利接入系统的关键。

系统里必须区分两类行业。

---

## 5.1 Market Industry：市场行业

这是系统自动计算用的行业。

来源：

```text
申万行业；
东方财富行业；
同花顺概念；
其他官方或半官方板块。
```

用途：

```text
计算行业强度；
计算行业涨幅排名；
计算行业成交额放大；
计算行业内 RPS90 股票占比；
识别行业龙头；
判断行业状态 I0-I4。
```

对应表：

```text
sector_daily_prices
stock_sector_map
industry_strength_daily
```

---

## 5.2 Research Theme：研究主题

这是你手动建立的产业研究框架。

来源：

```text
D LAB 行业研究；
用户手动整理；
AI Agent 产业链拆解；
VC / 一级市场项目研究；
硬科技产业链研究。
```

用途：

```text
建立产业研究池；
拆解产业链节点；
映射 A 股标的；
跟踪研究逻辑；
和技术状态做交叉验证。
```

对应表：

```text
research_themes
research_theme_nodes
research_watchlist
research_stock_daily_snapshot
```

---

## 5.3 为什么不能把手动股池塞进官方行业表

原因：

```text
官方行业用于市场统计，研究主题用于你的投研框架；
官方行业相对稳定，研究主题会频繁变化；
一只股票可能属于多个研究主题；
研究主题不是市场天然板块，不能直接参与行业强度排名；
混在一张表里会污染行业强度计算。
```

正确方式：

```text
官方行业表负责市场强度和行业强度；
研究主题表负责手动股池和产业链映射；
二者通过股票代码 symbol 关联。
```

---

# 6. 手动行业股池如何加入数据库

## 6.1 核心思路

你手动建立的行业股池，不直接进入官方行业表，而是进入：

```text
research_themes
research_theme_nodes
research_watchlist
```

系统每天自动关联：

```text
market_strength_daily
industry_strength_daily
stock_strength_daily
structure_health_daily
timing_signals_daily
stock_lifecycle_state
```

最终生成：

```text
research_stock_daily_snapshot
```

也就是说：

> 你负责建立研究主题、产业链节点、标的池；
> 系统负责每天告诉你这些标的有没有被市场验证、有没有进入强度池、有没有结构、有没有买点。

---

## 6.2 research_themes：研究主题表

记录你手动创建的研究主题。

```sql
CREATE TABLE research_themes (
    theme_id UUID PRIMARY KEY,
    theme_name TEXT NOT NULL,
    theme_type TEXT,
    description TEXT,
    research_thesis TEXT,
    value_chain_map JSONB,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

示例：

| 字段            | 示例                                                           |
| --------------- | -------------------------------------------------------------- |
| theme_name      | 激光器三大件                                                   |
| theme_type      | 硬科技 / 光电设备                                              |
| description     | 研究激光器核心部件：增益介质、泵浦源、谐振腔                   |
| research_thesis | 国产激光器产业链在高功率、精密加工、医疗和军工方向存在替代机会 |
| status          | active                                                         |

---

## 6.3 research_theme_nodes：产业链节点表

记录一个研究主题下的产业链环节。

```sql
CREATE TABLE research_theme_nodes (
    node_id UUID PRIMARY KEY,
    theme_id UUID REFERENCES research_themes(theme_id),
    node_name TEXT NOT NULL,
    node_type TEXT,
    description TEXT,
    importance_score NUMERIC,
    created_at TIMESTAMP DEFAULT now()
);
```

示例：

| 主题         | 节点         | 类型         |
| ------------ | ------------ | ------------ |
| 激光器三大件 | 增益介质     | 上游核心材料 |
| 激光器三大件 | 泵浦源       | 核心器件     |
| 激光器三大件 | 谐振腔       | 光学结构     |
| 激光器三大件 | 激光器整机   | 中游设备     |
| 激光器三大件 | 激光加工设备 | 下游应用     |

---

## 6.4 research_watchlist：研究标的池

这是你手动加入股票的核心表。

```sql
CREATE TABLE research_watchlist (
    watch_id UUID PRIMARY KEY,
    theme_id UUID REFERENCES research_themes(theme_id),
    node_id UUID REFERENCES research_theme_nodes(node_id),
    symbol TEXT NOT NULL,
    stock_name TEXT,
    role_in_theme TEXT,
    research_reason TEXT,
    logic_summary TEXT,
    catalyst TEXT,
    risk_note TEXT,
    conviction_level TEXT,
    research_status TEXT DEFAULT 'watching',
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

示例：

| 字段             | 示例                                           |
| ---------------- | ---------------------------------------------- |
| theme_id         | 激光器三大件                                   |
| node_id          | 泵浦源                                         |
| symbol           | 688025                                         |
| stock_name       | 杰普特                                         |
| role_in_theme    | MOPA 激光器 / 激光光源                         |
| research_reason  | 处于激光器核心环节，受益于精密制造和新能源加工 |
| logic_summary    | 激光器国产替代与高功率应用扩张                 |
| catalyst         | 下游订单恢复、国产替代、精密加工需求           |
| risk_note        | 行业价格竞争、毛利率下降                       |
| conviction_level | medium                                         |
| research_status  | watching                                       |

---

## 6.5 research_stock_daily_snapshot：研究池每日快照

这张表由系统每天自动生成，不手动填。

```sql
CREATE TABLE research_stock_daily_snapshot (
    trade_date DATE,
    theme_id UUID,
    symbol TEXT,
    stock_name TEXT,

    market_state TEXT,
    industry_state TEXT,
    official_sector_name TEXT,

    rps_5 NUMERIC,
    rps_20 NUMERIC,
    rps_50 NUMERIC,
    rps_120 NUMERIC,
    rps_250 NUMERIC,
    rps_label TEXT,

    institution_score NUMERIC,
    stock_strength_score NUMERIC,
    structure_score NUMERIC,
    structure_label TEXT,

    lifecycle_state TEXT,
    timing_signal TEXT,
    signal_score NUMERIC,

    next_action TEXT,
    created_at TIMESTAMP DEFAULT now(),

    PRIMARY KEY (trade_date, theme_id, symbol)
);
```

它的作用是：


```text
把手动研究池和系统量化状态连接起来。
```

示例：

| 主题         | 股票     | 研究逻辑       | RPS状态   | 结构状态 | 生命周期 | 动作     |
| ------------ | -------- | -------------- | --------- | -------- | -------- | -------- |
| 激光器三大件 | 杰普特   | 激光器核心环节 | RPS50改善 | VCP结构  | S4       | 等待突破 |
| 激光器三大件 | 柏楚电子 | 激光控制系统   | 长期强股  | 均线回踩 | S4       | 观察转强 |
| 激光器三大件 | 大族激光 | 设备龙头       | RPS弱     | 无结构   | S1       | 继续观察 |

---

# 7. 手动股池的导入方式

## 7.1 MVP 阶段：CSV 导入

最初不用做复杂前端，可以先用 CSV。

文件：

```text
research_watchlist.csv
```

字段：

```csv
theme_name,node_name,symbol,stock_name,role_in_theme,research_reason,logic_summary,catalyst,risk_note,conviction_level,research_status
```

示例：

```csv
theme_name,node_name,symbol,stock_name,role_in_theme,research_reason,logic_summary,catalyst,risk_note,conviction_level,research_status
激光器三大件,泵浦源,688025,杰普特,MOPA激光器,激光器核心部件,国产替代+精密加工需求,下游订单恢复,价格竞争,medium,watching
激光器三大件,控制系统,688188,柏楚电子,激光切割控制系统,激光设备核心控制系统,国产替代+高毛利软件属性,制造业复苏,估值偏高,high,watching
激光器三大件,激光加工设备,002008,大族激光,激光加工设备龙头,行业龙头,设备需求复苏,消费电子/新能源订单,周期波动,medium,watching
```

导入命令：

```bash
python import_research_watchlist.py --file research_watchlist.csv
```

导入逻辑：

```text
检查 theme_name 是否存在，不存在则创建 research_themes；
检查 node_name 是否存在，不存在则创建 research_theme_nodes；
检查 symbol 是否在 stock_info 中；
写入或更新 research_watchlist；
标记 source = csv_import。
```

---

## 7.2 中期阶段：前端手动添加

前端“产业研究池”页面提供：

```text
新建主题；
添加产业链节点；
添加股票；
填写研究逻辑；
设置跟踪状态。
```

表单字段：

```text
研究主题；
产业链环节；
股票代码；
股票名称；
在主题中的角色；
研究理由；
核心逻辑；
催化因素；
风险因素；
信心等级；
研究状态。
```

股票代码输入时自动联想并补全：

```text
股票名称；
官方行业；
当前价格；
RPS；
生命周期状态。
```

---

## 7.3 进阶阶段：Markdown / YAML 导入

适合 D LAB 行业研究报告结构化入库。

示例：

```yaml
theme_name: 激光器三大件
theme_type: 硬科技
research_thesis: 激光器核心器件国产替代与高端制造升级

nodes:
  - name: 增益介质
    companies:
      - symbol: "002222"
        name: 福晶科技
        role: 非线性晶体 / 光学元件

  - name: 泵浦源
    companies:
      - symbol: "688025"
        name: 杰普特
        role: MOPA 激光器 / 激光光源

  - name: 激光加工设备
    companies:
      - symbol: "002008"
        name: 大族激光
        role: 激光加工设备龙头
```

---

## 7.4 AI Agent 草稿生成

用户输入：

```text
帮我建立“半导体设备”研究池。
```

AI 生成草稿：

```text
刻蚀设备：中微公司、北方华创
薄膜沉积：拓荆科技、北方华创
清洗设备：盛美上海、至纯科技
量测检测：精测电子、中科飞测
电子特气：华特气体、金宏气体
零部件：富创精密、江丰电子
```

注意：

```text
AI 生成的研究池必须先进入 draft 状态；
用户确认后才写入正式 research_watchlist。
```

状态设计：

```text
draft
confirmed
watching
core
archived
```

---

# 8. Evo 生命周期状态机

## 8.1 状态总览

```text
S0：机构认可与基础过滤池
S1：市场可交易池
S2：强行业池
S3：行业内强股池
S4：结构健康准备池
S5：买点触发池
S6：持仓跟踪池
S7：退出复盘池
```

---

## 8.2 S0：机构认可与基础过滤池

解决：

```text
这只股票是否值得进入系统观察？
```

基础过滤：

```text
上市天数 >= 250；
非 ST；
非退市风险；
非长期停牌；
最近 20 日平均成交额达标；
价格不低于最低阈值；
无重大财务或监管风险；
机构认可条件通过。
```

机构认可：

```text
基金持股比例达标；
或基金持股家数达标；
或北向持股比例达标；
或最新可得北向数据满足条件；
或机构调研 / 研报覆盖较强。
```

核心规则：

```text
基金持股通过 OR 北向持股通过 OR 特殊豁免通过
```

---

## 8.3 S1：市场可交易池

市场状态：

| 状态 | 名称     | 动作                   |
| ---- | -------- | ---------------------- |
| M0   | 极弱市场 | 禁止新开仓             |
| M1   | 偏弱市场 | 只观察                 |
| M2   | 中性市场 | 小仓做最强             |
| M3   | 偏强市场 | 正常参与               |
| M4   | 强势市场 | 积极寻找主升，警惕高潮 |

---

## 8.4 S2：强行业池

行业状态：

| 状态 | 名称     | 含义             |
| ---- | -------- | ---------------- |
| I0   | 弱行业   | 跑输市场         |
| I1   | 轮动行业 | 短期脉冲         |
| I2   | 修复行业 | 开始转强         |
| I3   | 强势行业 | 主线候选         |
| I4   | 核心主线 | 当前资金主攻方向 |

---

## 8.5 S3：行业内强股池

五周期 RPS：

```text
RPS5 / RPS20 / RPS50 / RPS120 / RPS250
```

个股强度标签：

```text
短线异动；
当前强势；
中期启动；
中期主升；
长期强股；
高位衰竭。
```

---

## 8.6 S4：结构健康准备池

结构健康定义：

```text
趋势未破
+ 回撤有序
+ 成交量收缩
+ 波动收敛
+ 相对强度保持
+ 关键位未破
+ 止损空间可控
```

结构健康评分：

| 模块         | 分值 |
| ------------ | ---: |
| 趋势结构     |   20 |
| 回撤控制     |   15 |
| 成交量收缩   |   15 |
| 波动收敛     |   15 |
| 相对强度保持 |   15 |
| 关键位未破   |   10 |
| 止损空间可控 |   10 |

入池标准：

```text
Structure_score >= 70
AND market_state >= M2
AND industry_state >= I3
AND stock_state = S3
```

---

## 8.7 S5：买点触发池

第一阶段实现四类买点：

```text
口袋买点；
强市场突破买点；
抛压衰竭买点；
业绩跳空后的二次突破。
```

---

## 8.8 S6：持仓跟踪池

持仓状态：

```text
P0：正常持有
P1：风险观察
P2：减仓警告
P3：退出触发
```

---

## 8.9 S7：退出复盘池

复盘分类：

| 类型                 | 说明                 |
| -------------------- | -------------------- |
| A 类：系统内正常亏损 | 买点合格，止损执行   |
| B 类：系统内盈利退出 | 趋势走完或卖点触发   |
| C 类：买点错误       | 买点不符合规则       |
| D 类：市场环境错误   | 市场状态不支持       |
| E 类：行业选择错误   | 行业不是主线         |
| F 类：结构判断错误   | 结构不健康仍买入     |
| G 类：风控执行错误   | 没有按止损执行       |
| H 类：情绪交易       | 追高、恐慌、临时起意 |

---

# 9. 买点系统

## 9.1 口袋买点

适合：

```text
强股整理区内提前启动。
```

条件：

```text
market_state >= M2
AND industry_state >= I3
AND stock_state IN S3/S4
AND close > open
AND close_position >= 0.7
AND volume > 最近10日所有下跌日成交量最大值
AND close >= MA10 OR close >= MA20
AND distance_to_MA20 <= 10%
AND structure_score >= 70
AND stop_loss_pct <= 8%-10%
```

---

## 9.2 强市场突破买点

适合：

```text
强市场、强行业中的主升浪启动。
```

前置条件：

```text
market_state >= M3
AND industry_state >= I3，最好 I4
AND stock_state = S4
AND RPS20 >= 90
AND RPS50 >= 85
AND structure_score >= 70
AND 突破前有至少 10 个交易日以上整理
```

触发条件：

```text
close > 最近 N 日平台高点
AND volume > volume_ma20 × 1.5
AND close_position >= 0.7
AND 行业指数当日同步走强
AND 行业内不止一只股票走强
AND stop_loss_pct <= 8%
```

---

## 9.3 抛压衰竭买点

适合：

```text
主线清晰、强股正常回调后的再启动。
```

条件：

```text
market_state >= M3
AND industry_state >= I3，最好 I4
AND stock_rank_in_industry <= top 20%
AND pullback_from_high between 5% and 15%
AND close >= MA20 OR close >= MA50
AND pullback_volume_ratio <= 0.8
AND 最近3日成交量下降 OR 回调期间均量低于20日均量
AND 无放量长阴
AND RPS20 >= 80
AND RPS50 >= 85
AND reclaim_signal = true
AND stop_loss_pct <= 8%-10%
```

重新转强信号：

```text
close > 前一日 high
OR close 重新站上 MA10
OR 出现阳线反包
OR 成交量较昨日放大且收盘靠近日高
```

---

## 9.4 业绩跳空后的二次突破

适合：

```text
业绩驱动型趋势股。
```

条件：

```text
财报或业绩预告后跳空上涨；
缺口不快速回补；
RPS20 / RPS50 快速提升；
随后缩量整理；
再次放量突破整理平台；
市场和行业未退潮。
```

---

# 10. 数据采集与计算实现

## 10.1 本地数据工厂职责

```text
数据采集；
数据清洗；
全量历史存储；
技术指标计算；
RPS 计算；
市场强度计算；
行业强度计算；
结构健康计算；
买点信号计算；
生命周期状态更新；
研究池每日快照生成；
CSV / Parquet 导出；
R2 上传；
云端导入。
```

---

## 10.2 每日任务顺序

```text
Step 1：更新交易日历
Step 2：更新股票列表和基础信息
Step 3：更新个股日线
Step 4：更新指数日线
Step 5：更新行业 / 板块日线
Step 6：更新股票-行业映射
Step 7：更新 ETF 行情
Step 8：更新新闻 / 公告
Step 9：更新基金持股 / 北向最新可得快照
Step 10：计算均线、成交额、涨跌幅、ATR 等基础指标
Step 11：计算 RPS5 / 20 / 50 / 120 / 250
Step 12：计算市场强度 M0-M4
Step 13：计算行业强度 I0-I4
Step 14：计算结构健康评分
Step 15：扫描买点信号
Step 16：更新生命周期状态
Step 17：更新持仓跟踪
Step 18：生成研究池每日快照
Step 19：生成 Alpha 机会池
Step 20：导出云端核心表
Step 21：上传 R2
Step 22：导入 CockroachDB
Step 23：生成日报简报
```

---

## 10.3 本地工程目录

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
│   └── update_announcements.py
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
│   └── backtest/
│       ├── backtest_engine.py
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
└── scheduler/
    ├── daily_pipeline.py
    ├── weekly_pipeline.py
    └── monthly_pipeline.py
```

---

# 11. 云端 API 设计

API 围绕前端页面和使用场景设计。

## 11.1 市场强度

```http
GET /api/market/strength/latest
GET /api/market/strength/history?days=60
```

---

## 11.2 行业强度

```http
GET /api/industries/strength?date=latest&state=I3,I4
GET /api/industries/{sector_code}/dashboard
GET /api/industries/{sector_code}/stocks
```

---

## 11.3 Alpha 机会

```http
GET /api/alpha/opportunities?state=S4,S5&limit=50
GET /api/alpha/core-pool
GET /api/alpha/today
```

---

## 11.4 个股作战室

```http
GET /api/stocks/{symbol}/dashboard
GET /api/stocks/{symbol}/kline
GET /api/stocks/{symbol}/rps
GET /api/stocks/{symbol}/lifecycle
GET /api/stocks/{symbol}/signals
GET /api/stocks/{symbol}/trade-plan
```

---

## 11.5 买点信号

```http
GET /api/signals/today
GET /api/signals/today?signal_type=pocket_pivot
GET /api/signals/today?signal_type=breakout
GET /api/signals/today?signal_type=supply_exhaustion
```

---

## 11.6 产业研究池

```http
GET /api/research/themes
POST /api/research/themes
GET /api/research/themes/{theme_id}
POST /api/research/themes/{theme_id}/nodes
POST /api/research/watchlist
GET /api/research/watchlist?theme_id=xxx
GET /api/research/themes/{theme_id}/snapshot
```

---

## 11.7 AI Agent

```http
POST /api/ai/analyze-stock
POST /api/ai/analyze-industry
POST /api/ai/generate-trade-plan
POST /api/ai/generate-daily-report
POST /api/ai/review-trade
POST /api/ai/generate-research-theme-draft
```

AI 原则：

```text
不全量自动跑；
只对重点机会跑；
用户点击触发；
日报批量生成；
结果缓存；
控制成本。
```

---

# 12. 前端页面设计

## 12.1 首页：Evo Alpha 雷达

首页第一屏回答：

```text
今天能不能做？
主战场在哪里？
哪些股票接近买点？
哪些股票已经触发？
持仓有没有风险？
```

模块：

```text
市场状态卡片；
强行业榜；
S5 买点触发；
S4 结构准备；
核心共振池；
持仓风险提醒；
AI 每日作战简报。
```

---

## 12.2 市场温度页

目标：

```text
判断今天能不能交易。
```

展示：

```text
M0-M4 市场状态；
指数趋势；
上涨 / 下跌家数；
涨停 / 跌停结构；
创新高数量；
RPS90 股票数量；
全市场成交额；
建议仓位。
```

---

## 12.3 行业战役指挥所

目标：

```text
判断主战场在哪里。
```

展示：

```text
I0-I4 行业状态；
行业强度榜；
行业热力图；
行业 RPS；
行业成交额放大；
行业内 RPS90 股票比例；
行业内新高比例；
行业龙一 / 龙二 / 中军；
行业持续天数；
行业退潮风险。
```

---

## 12.4 产业研究池页面

目标：

```text
管理用户手动研究主题和标的池。
```

页面一：研究主题列表

| 主题         | 股票数 | S3 | S4 | S5 | 最新更新 |
| ------------ | -----: | -: | -: | -: | -------- |
| 激光器三大件 |     12 |  3 |  1 |  0 | 今天     |
| 半导体设备   |     25 |  8 |  4 |  1 | 今天     |
| 合成生物     |     18 |  2 |  0 |  0 | 昨天     |

页面二：主题详情

展示：

```text
产业链地图；
产业链节点；
节点下公司；
每家公司研究逻辑；
官方行业；
RPS 状态；
结构状态；
生命周期状态；
买点状态；
AI 主题总结。
```

示例表：

| 股票     | 产业环节 | 研究逻辑       | 官方行业 | RPS状态  | 结构     | 生命周期 | 动作     |
| -------- | -------- | -------------- | -------- | -------- | -------- | -------- | -------- |
| 杰普特   | 泵浦源   | 激光器核心环节 | 电子设备 | 中期启动 | VCP      | S4       | 等待突破 |
| 柏楚电子 | 控制系统 | 高毛利软件属性 | 专用设备 | 长期强股 | 回踩MA20 | S4       | 观察转强 |
| 大族激光 | 整机设备 | 激光设备龙头   | 专用设备 | 当前强势 | 无结构   | S3       | 等结构   |
| 福晶科技 | 晶体材料 | 上游材料       | 光学元件 | 较弱     | 无       | S1       | 继续观察 |

---

## 12.5 个股作战室

目标：

```text
判断一只股票是否值得交易。
```

结构：

```text
左侧：K线 + 成交量 + 均线 + 买点标记
中间：Evo 生命周期状态
右侧：AI 分析与交易计划
底部：新闻、公告、复盘记录
```

展示：

```text
S0 机构认可；
S1 市场状态；
S2 行业状态；
S3 RPS 强度；
S4 结构健康；
S5 买点信号；
S6 持仓状态；
S7 历史复盘。
```

---

## 12.6 买点信号页

分四个 Tab：

```text
口袋买点；
突破买点；
抛压衰竭买点；
业绩跳空后二次突破。
```

列表字段：

```text
股票；
所属行业；
市场状态；
行业状态；
RPS 状态；
结构分；
触发价；
止损价；
止损距离；
信号有效性；
否决原因。
```

---

## 12.7 持仓风控页

展示：

```text
P0 正常持有；
P1 风险观察；
P2 减仓警告；
P3 退出触发；
当前止损；
结构失效位；
行业状态变化；
RPS 变化；
AI 风险提示。
```

---

## 12.8 复盘进化页

展示：

```text
交易胜率；
盈亏比；
最大回撤；
买点类型表现；
市场状态分组表现；
行业状态分组表现；
错误类型统计；
规则优化建议。
```

---

# 13. 日常使用场景

## 13.1 每日收盘后：15-30 分钟

用户打开首页。

第一步看市场状态：

```text
今日市场：M3 偏强
建议仓位：40%-70%
结论：允许正常参与，但优先选择强行业前排
```

第二步看强行业：

```text
I4 核心主线：半导体设备、AI 算力
I3 强势行业：机器人、创新药
```

第三步看机会池：

```text
S5 买点触发：
股票 A：平台放量突破
股票 B：口袋买点
股票 C：抛压衰竭买点

S4 结构准备：
股票 D：VCP 收缩，等待突破
股票 E：均线缩量回踩，等待转强
```

第四步看产业研究池：

```text
激光器三大件：
杰普特进入 S4；
柏楚电子维持 S4；
福晶科技仍为 S1，暂未被市场验证。
```

第五步看持仓风险：

```text
股票 F：P1 风险观察
股票 G：P3 退出触发
```

最终输出：

```text
明天重点观察名单；
明天可能触发买点；
当前持仓风险；
研究股池中开始被市场验证的标的；
不该碰的行业和个股。
```

---

## 13.2 盘中使用

盘中不全市场乱看，只看：

```text
S5 买点是否触发；
S6 持仓是否破位；
行业强度是否突变。
```

用户只处理：

```text
买点触发提醒；
持仓风险提醒；
行业退潮提醒。
```

---

## 13.3 行业研究使用

用户研究一个主题，例如“激光器三大件”。

流程：

```text
创建研究主题；
拆产业链节点；
手动加入标的；
填写研究逻辑；
系统每日自动匹配技术状态；
观察哪些标的从 S1/S2 进入 S3/S4/S5。
```

系统输出：

```text
哪些标的只是逻辑好；
哪些开始被市场验证；
哪些进入结构准备；
哪些触发买点；
哪些需要移出研究池。
```

---

## 13.4 周末复盘使用

系统统计：

```text
本周系统内交易数量；
系统外交易数量；
不同买点胜率；
不同市场状态收益；
不同行业状态收益；
研究池转化率；
最大亏损来源；
主要错误类型。
```

输出建议：

```text
M1 市场不要开新仓；
口袋买点止损超过 8% 时只观察；
行业降级后后排股自动进入减仓警告；
突破买点需要行业同步走强；
研究池中长期未被市场验证的标的降级。
```

---

# 14. AI Agent 设计

产品层统一使用 Evo Agent 语言。

| Agent            | 职责                   |
| ---------------- | ---------------------- |
| Market Agent     | 解读市场状态和仓位     |
| Industry Agent   | 解读强行业和主线持续性 |
| Stock Agent      | 解读 RPS 和个股强度    |
| Structure Agent  | 解读结构健康           |
| Timing Agent     | 解读买点信号           |
| Research Agent   | 拆解产业链和公司映射   |
| Trade Plan Agent | 生成交易计划           |
| Review Agent     | 复盘交易错误           |

AI 成本控制：

```text
不自动全量分析；
只对重点股票、重点行业、日报、用户点击请求调用；
AI 结果写入 ai_analysis_cache；
设置 expire_at；
同一标的短期内复用缓存。
```

---

# 15. 开发路线图 v0.8

## Phase 1：数据采集与 RPS 修复

```text
修复行情采集；
跑通本地 SQLite；
完成日线全量数据；
完成 RPS5/20/50/120/250；
完成基础股票池过滤。
```

---

## Phase 2：研究池 MVP

```text
创建 research_themes；
创建 research_theme_nodes；
创建 research_watchlist；
支持 CSV 导入；
生成 research_stock_daily_snapshot。
```

这是把你的手动行业股池接入系统的关键阶段。

---

## Phase 3：S0 机构认可池

```text
接入基金持股数据；
接入最新可获得北向持股数据；
建立 institutional_holding_snapshot；
完成 S0 过滤规则。
```

---

## Phase 4：市场强度与行业强度

```text
完成 M0-M4 市场状态；
完成 I0-I4 行业状态；
完成强行业榜；
完成行业内强股比例。
```

---

## Phase 5：生命周期状态机

```text
完成 S0-S7 状态表；
完成每日状态更新；
完成 Alpha 机会池；
完成核心共振池。
```

---

## Phase 6：结构健康评分

```text
趋势结构；
回撤控制；
成交量收缩；
波动收敛；
相对强度；
止损空间。
```

---

## Phase 7：买点信号

```text
移植口袋买点；
实现突破买点；
实现抛压衰竭买点；
实现业绩跳空后二次突破。
```

---

## Phase 8：前端 MVP

```text
首页 Alpha 雷达；
市场温度页；
行业战役指挥所；
产业研究池；
个股作战室；
买点信号页。
```

---

## Phase 9：云端部署

```text
CSV / Parquet 导出；
R2 上传；
CockroachDB 导入；
FastAPI 查询；
Vercel 部署。
```

---

## Phase 10：AI Agent 与复盘

```text
AI 个股解释；
AI 行业解释；
AI 交易计划；
每日作战简报；
持仓跟踪；
交易复盘；
参数优化。
```

---

# 16. 最终定义

EvoAlpha OS v0.8 可以定义为：

> 一个以本地数据工厂为计算底座，以低成本云端架构为展示与分发层，以双入口 Alpha 发现机制为核心流程，以手动产业研究池为长期标的沉淀，以市场行业强度为资金方向识别，以五周期 RPS 为个股强弱识别，以结构健康评分为交易准备，以口袋买点、突破买点、抛压衰竭买点为执行触发，以持仓跟踪和复盘归因为进化机制的 A 股智能投研系统。

一句话版本：

> EvoAlpha OS 让用户手动研究方向，让系统自动验证强弱；让技术发现强者，让研究解释强者；最后用生命周期状态机等待低风险买点和纪律化退出。

---

# 17. 当前最重要的共识

这版白皮书里最重要的变化是：

```text
手动行业股池不进入官方行业表；
手动股池进入研究主题系统；
官方行业负责市场强度和行业强度；
研究主题负责产业链逻辑和标的沉淀；
二者通过股票代码关联；
系统每天自动生成研究池量化快照。
```

最终形成的使用方式是：

> 你负责研究方向和标的池，系统负责每天告诉你：
> 这些标的有没有被市场认可，
> 有没有进入强行业，
> 有没有变成行业内强股，
> 有没有形成健康结构，
> 有没有触发低风险买点。
