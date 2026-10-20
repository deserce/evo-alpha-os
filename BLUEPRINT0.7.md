---
# EvoAlpha OS 白皮书 v0.7

## 基于低成本云端架构的双入口 A 股 Alpha 发现系统

### Slogan

> 进化即自由
> Evolution is Freedom
---
# 0. 核心摘要

EvoAlpha OS 是一个面向 A 股市场的个人级 Alpha 机会发现与系统化投研平台。

它不是单纯的选股器，也不是自

动交易机器人，而是一套结合：

```text
本地数据工厂
低成本云端架构
五周期 RPS
市场强度
行业强度
机构认可
结构健康
买点触发
AI Agent
交易复盘
```

的智能投研操作系统。

系统保持原有工程架构不变：

```text
本地 MBP 数据工厂
    ↓
SQLite 全量历史库
    ↓
CSV / Parquet 导出
    ↓
Cloudflare R2 文件中转
    ↓
CockroachDB Serverless 云端近两年核心结果
    ↓
FastAPI 云端接口
    ↓
Next.js / Vercel 前端展示
    ↓
AI Agent 按需触发
```

系统新增策略内核：

```text
机构认可
    ↓
市场强度
    ↓
行业强度
    ↓
个股强度
    ↓
结构健康
    ↓
买点触发
    ↓
持仓跟踪
    ↓
退出复盘
```

系统采用“双入口、单中枢、同一生命周期”的 Alpha 发现机制：

```text
入口 A：技术强度入口
市场强度 → 行业强度 → 个股 RPS → 结构健康 → 买点触发

入口 B：产业研究入口
产业研究 → 公司映射 → 研究池 → 技术验证 → 买点等待
```

两个入口最终统一进入 Evo 生命周期状态机：

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

> EvoAlpha OS 不是单纯找强股，而是在机构认可的股票中，等待强市场、强行业、强个股、健康结构和低风险买点的系统共振。

---

# 1. 项目定位

## 1.1 项目名称

```text
EvoAlpha OS
Evolution Alpha Operating System
```

内部工程名可继续使用：

```text
EvoQuant OS
```

建议后续区分为：

| 名称                 | 定位                               |
| -------------------- | ---------------------------------- |
| EvoAlpha OS          | 对外品牌名，强调 Alpha 机会发现    |
| EvoQuant OS          | 对内工程名，强调量化计算与策略引擎 |
| D LAB                | 内容品牌 / 研究品牌 / 个人品牌     |
| Evolution is Freedom | 系统精神内核                       |

---

## 1.2 系统定位

EvoAlpha OS 是：

```text
个人投资者的 Alpha 机会发现平台；
系统化交易训练平台；
行业研究与二级市场映射平台；
AI 辅助投研与复盘平台。
```

系统不直接替用户做决策，而是帮助用户把投资过程变成：

```text
可筛选
可解释
可跟踪
可执行
可复盘
可迭代
```

的工作流。

---

## 1.3 系统核心理念

```text
量化筛选做减法，AI Agent 做加法。
```

量化筛选负责：

```text
过滤不值得看的股票；
识别市场强弱；
识别行业强弱；
识别个股强弱；
识别结构和买点。
```

AI Agent 负责：

```text
解释为什么强；
解释产业逻辑；
解释风险因素；
生成交易计划；
辅助复盘纠错。
```

人负责：

```text
最终判断；
最终执行；
仓位控制；
承担结果；
持续优化系统。
```

---

# 2. 系统第一性原理

## 2.1 市场不是被预测的，而是被识别的

EvoAlpha OS 不预测未来涨跌。

系统只回答：

```text
现在市场强不强？
资金在哪些行业？
哪些股票已经被市场验证？
哪些股票有机构认可？
哪些股票结构健康？
哪些股票出现了低风险买点？
如果判断错了，在哪里退出？
```

---

## 2.2 强势股交易的完整链条

系统的核心公式是：

```text
Evo Alpha =
机构认可 × 强市场 × 强行业 × 强个股 × 健康结构 × 低风险买点 × 风险纪律
```

也可以表达为：

```text
先过滤不值得看的股票；
再判断市场能不能做；
然后寻找资金主攻行业；
在强行业里寻找前排强股；
在强股中等待健康结构；
在健康结构中等待低风险买点；
买入后跟踪强势是否延续；
强势破坏后退出复盘。
```

---

# 3. 双入口 Alpha 发现机制

这是新版系统最核心的架构变化。

EvoAlpha OS 不应该只有一种选股路径，而应该有两套入口：

```text
入口 A：技术强度入口
入口 B：产业研究入口
```

两个入口不是并列割裂，而是互相验证。

---

## 3.1 入口 A：技术强度入口

技术强度入口是系统每天自动扫描的路径。

它从市场事实出发：

```text
市场强度
    ↓
行业强度
    ↓
个股 RPS
    ↓
结构健康
    ↓
买点触发
```

它回答的是：

> 市场正在选择谁？

适合发现：

```text
已经被资金攻击的行业；
已经跑出来的行业前排；
正在接近买点的强势股；
当天出现买点触发的股票；
持仓中风险开始变化的股票。
```

这条路径高度自动化，主要由本地数据工厂完成。

---

## 3.2 入口 B：产业研究入口

产业研究入口是人和 AI Agent 共同完成的路径。

它从研究判断出发：

```text
产业主题
    ↓
产业链拆解
    ↓
A 股公司映射
    ↓
产业研究池
    ↓
市场强度验证
    ↓
行业强度验证
    ↓
个股 RPS 验证
    ↓
结构与买点验证
```

它回答的是：

> 哪些方向未来值得跟踪？

适合发现：

```text
还没有完全被市场定价的产业机会；
你通过行业研究提前发现的方向；
基本面逻辑很好但技术尚未启动的公司；
未来可能进入强势主线的潜在标的。
```

这条路径更符合你的 VC / 行业研究 / D LAB 内容体系能力。

---

## 3.3 双入口统一到同一个生命周期

最终所有股票都进入同一个状态机。

```text
技术强度入口
        ↓
        → Evo 生命周期中枢 → 交易计划 / 观察 / 复盘
        ↑
产业研究入口
```

系统中可以设置三个池：

| 股池       | 来源                     | 作用                                 |
| ---------- | ------------------------ | ------------------------------------ |
| 技术强度池 | 系统自动扫描             | 发现市场已经选择的强者               |
| 产业研究池 | 用户 + AI 研究           | 跟踪产业逻辑与公司映射               |
| 核心共振池 | 技术强度池 ∩ 产业研究池 | 既有逻辑，又有市场确认的高优先级机会 |

最高质量机会不是单一路径出现的，而是：

```text
产业逻辑强
市场环境支持
行业强度提升
个股 RPS 走强
结构健康
买点触发
```

这种机会进入：

```text
Evo Core Alpha Pool
```

也就是核心 Alpha 池。

---

# 4. Evo 强势股生命周期状态机

新版系统的核心状态机为：

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

## 4.1 S0：机构认可与基础过滤池

S0 解决的问题是：

> 这只股票是否值得进入系统观察？

基础条件：

```text
上市天数 >= 250；
非 ST；
非退市风险；
非长期停牌；
最近 20 日平均成交额达到最低流动性要求；
价格不低于最低阈值；
没有重大财务或监管风险；
机构认可条件通过。
```

机构认可条件：

```text
基金持股比例达到阈值；
或基金持股家数达到阈值；
或北向持股比例达到阈值；
或最近可得北向数据满足条件；
或机构调研 / 研报覆盖较强。
```

建议不要设置成“必须基金持股 + 必须北向持股”，否则会错过早期成长股。

建议规则：

```text
基金持股通过 OR 北向持股通过 OR 特殊豁免通过
```

核心参数示例：

```text
fund_hold_ratio >= 1%
OR fund_holder_count >= 3
OR latest_available_northbound_hold_ratio >= 0.5%
```

高质量池：

```text
fund_hold_ratio >= 3%
OR fund_holder_count >= 10
OR latest_available_northbound_hold_ratio >= 1%
```

特殊豁免池：

```text
无明显基金 / 北向持股，
但行业状态 I4，
RPS50 / RPS120 快速提升，
成交额显著放大，
并出现重大事件催化或业绩跳空。
```

这类股票可以进入：

```text
S0E 实验观察池
```

但默认不进入重仓交易池。

---

## 4.2 S1：市场可交易池

S1 解决的问题是：

> 当前市场是否允许交易？

市场状态分为：

| 状态 | 名称     | 动作                       |
| ---- | -------- | -------------------------- |
| M0   | 极弱市场 | 禁止新开仓                 |
| M1   | 偏弱市场 | 只观察                     |
| M2   | 中性市场 | 小仓做最强                 |
| M3   | 偏强市场 | 正常参与                   |
| M4   | 强势市场 | 积极寻找主升，同时警惕高潮 |

市场强度指标：

```text
指数是否站上 MA20 / MA50 / MA120；
MA20 / MA50 是否向上；
上涨家数占比；
创 20 / 60 / 120 日新高数量；
涨停数量；
跌停数量；
全市场成交额 / 20 日均成交额；
RPS90 以上股票数量；
强势股扩散速度；
亏钱效应是否扩散。
```

交易权限：

```text
M0 / M1：不进入买点扫描，只更新观察池；
M2：只允许强行业龙头小仓参与；
M3 / M4：正常运行强行业、强股、结构、买点扫描。
```

---

## 4.3 S2：强行业池

S2 解决的问题是：

> 资金正在进攻哪些行业？

行业状态分为：

| 状态 | 名称     | 含义             |
| ---- | -------- | ---------------- |
| I0   | 弱行业   | 跑输市场         |
| I1   | 轮动行业 | 短期脉冲         |
| I2   | 修复行业 | 开始转强         |
| I3   | 强势行业 | 可进入主线候选   |
| I4   | 核心主线 | 当前资金主攻方向 |

行业强度指标：

```text
行业 5 日涨幅；
行业 20 日涨幅；
行业 50 日涨幅；
行业 120 日涨幅；
行业指数 RPS20 / RPS50 / RPS120；
行业成交额放大倍数；
行业内 RPS90 股票占比；
行业内阶段新高股票占比；
行业龙头是否持续新高；
行业持续强势天数；
行业内涨停扩散情况。
```

行业强度评分：

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

## 4.4 S3：行业内强股池

S3 解决的问题是：

> 在强行业里，谁是真正的前排？

核心工具：

```text
RPS5
RPS20
RPS50
RPS120
RPS250
```

五周期含义：

| 周期   | 含义     |
| ------ | -------- |
| RPS5   | 短线异动 |
| RPS20  | 当前强势 |
| RPS50  | 中期启动 |
| RPS120 | 中期趋势 |
| RPS250 | 长期强势 |

个股强度评分：

```text
Stock_strength_score =
10% × RPS5
20% × RPS20
25% × RPS50
25% × RPS120
20% × RPS250
```

S3 入池规则示例：

当前强势型：

```text
RPS20 >= 90
AND RPS50 >= 85
AND 行业状态 >= I3
```

中期启动型：

```text
RPS50 >= 90
AND RPS120 正在改善
AND 行业状态 >= I3
```

中期主升型：

```text
RPS50 >= 90
AND RPS120 >= 90
AND 行业内强度排名前 20%
```

长期强股回踩型：

```text
RPS120 >= 90
AND RPS250 >= 90
AND RPS20 / RPS5 重新转强
```

---

## 4.5 S4：结构健康准备池

S4 解决的问题是：

> 这只强股是否处在值得等待买点的位置？

结构健康不能凭感觉，必须量化。

结构健康定义：

```text
结构健康 =
趋势未破
+ 回撤有序
+ 成交量收缩
+ 波动收敛
+ 相对强度保持
+ 关键位未破
+ 止损空间可控
```

结构健康评分建议 100 分制。

| 模块         | 分值 | 说明                                 |
| ------------ | ---: | ------------------------------------ |
| 趋势结构     |   20 | close > MA20，MA20 > MA50，MA50 向上 |
| 回撤控制     |   15 | 近 20 日最大回撤可控                 |
| 成交量收缩   |   15 | 回调期间缩量，下跌量不放大           |
| 波动收敛     |   15 | ATR / 振幅下降，区间收窄             |
| 相对强度保持 |   15 | RPS 不明显恶化，强于行业             |
| 关键位未破   |   10 | 不破 MA20 / MA50 / 平台 / 缺口       |
| 止损空间可控 |   10 | 当前价到结构失效位 <= 8%-10%         |

S4 入池标准：

```text
Structure_score >= 70
AND 市场状态 >= M2
AND 行业状态 >= I3
AND 个股处于 S3
```

高质量结构：

```text
Structure_score >= 85
```

结构标签：

```text
VCP 收缩结构；
平台整理结构；
均线回踩结构；
新高前蓄势结构；
业绩跳空整理结构；
结构恶化。
```

---

## 4.6 S5：买点触发池

S5 解决的问题是：

> 是否出现了可以执行的低风险买点？

第一阶段建议实现四类买点：

```text
1. 口袋买点
2. 市场环境好的突破买点
3. 主线清晰情况下的抛压衰竭买点
4. 业绩跳空后的二次突破
```

### 买点一：口袋买点

适合捕捉强股整理区内的提前启动。

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

其中：

```text
close_position = (close - low) / (high - low)
```

后续可以移植通达信口袋买点代码，转换为 Python 策略函数。

---

### 买点二：强市场突破买点

适合主升浪启动。

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

N 可先设为：

```text
20 / 50 / 60
```

后续通过回测优化。

---

### 买点三：抛压衰竭买点

这个买点的含义是：

> 在市场环境好、主线清晰、个股仍处于强趋势的情况下，短期回调成交量萎缩，抛压释放接近尾声，随后出现重新转强信号。

适用场景：

```text
market_state >= M3
AND industry_state >= I3，最好 I4
AND stock_rank_in_industry <= top 20%
AND 个股已经经历一轮上涨
AND 当前是上涨后的正常回调
AND 趋势未破
```

量化条件：

```text
pullback_from_high between 5% and 15%
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

### 买点四：业绩跳空后的二次突破

适合业绩驱动型趋势股。

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

## 4.7 S6：持仓跟踪池

买入后不能每天重新问“要不要买”，而应该进入持仓管理逻辑。

S6 状态分为：

```text
P0：正常持有
P1：风险观察
P2：减仓警告
P3：退出触发
```

P0 正常持有：

```text
股价在止损位上方；
行业状态未降级；
RPS20 / RPS50 没有明显恶化；
价格仍在 MA20 或关键平台上方；
没有放量破位。
```

P1 风险观察：

```text
股价跌破 MA10；
RPS20 连续下降；
行业状态从 I4 降到 I3；
成交量放大但价格不涨；
出现长上影或分歧放量。
```

P2 减仓警告：

```text
跌破 MA20；
跌破短期平台；
行业状态降至 I2；
RPS20 跌破 80；
下跌放量，反弹缩量；
浮盈明显回撤。
```

P3 退出触发：

```text
跌破初始止损价；
跌破结构失效位；
跌破 MA50；
放量跌破平台；
买点失败；
行业主线退潮；
重大利空破坏逻辑。
```

---

## 4.8 S7：退出复盘池

S7 是系统进化入口。

退出后每笔交易必须归因：

| 类型                 | 说明                           |
| -------------------- | ------------------------------ |
| A 类：系统内正常亏损 | 买点合格，止损执行，亏损可接受 |
| B 类：系统内盈利退出 | 趋势走完或卖点触发             |
| C 类：买点错误       | 买点不符合规则                 |
| D 类：市场环境错误   | 市场状态不支持交易             |
| E 类：行业选择错误   | 行业不是主线或已退潮           |
| F 类：结构判断错误   | 结构不健康仍然买入             |
| G 类：风控执行错误   | 没有按止损执行                 |
| H 类：情绪交易       | 追高、恐慌、临时起意           |

复盘输出：

```text
是否系统内交易？
买点是否合格？
市场状态是否支持？
行业是否够强？
个股是否前排？
结构是否健康？
止损是否提前设置？
是否按计划退出？
需要优化哪条规则？
```

---

# 5. 后端数据采集设计

老版架构中的本地工厂继续保留，并升级为 Evo 数据工厂。

## 5.1 本地工厂职责

本地 MBP 负责：

```text
数据采集；
数据清洗；
全量历史存储；
量化因子计算；
市场强度计算；
行业强度计算；
结构信号计算；
买点信号计算；
生命周期状态更新；
CSV / Parquet 导出；
R2 上传；
云端批量导入。
```

云端不做重计算，只做：

```text
查询；
展示；
AI 分析；
日报生成；
用户系统；
轻量任务调度。
```

---

## 5.2 数据源设计

第一阶段以免费和低成本为主。

| 数据类型        | 数据源建议                      | 更新频率       | 备注       |
| --------------- | ------------------------------- | -------------- | ---------- |
| 股票列表        | AkShare / 东方财富              | 每周或每月     | 本地缓存   |
| 个股日线        | AkShare / 东方财富              | 每日收盘后     | 核心数据   |
| 指数日线        | AkShare                         | 每日           | 市场强度   |
| 行业 / 板块日线 | AkShare / 东方财富 / 同花顺导出 | 每日           | 行业强度   |
| 股票-行业映射   | 申万 / 东方财富 / 同花顺        | 每周或手动维护 | 重要       |
| ETF 行情        | AkShare                         | 每日           | 全天候配置 |
| 财报数据        | AkShare / 巨潮 / 东方财富       | 季度           | L2 逻辑    |
| 基金持股        | AkShare / 基金季报数据          | 季度           | S0 过滤    |
| 北向持股        | 最新可获得免费数据              | 不定期         | 作为慢变量 |
| 新闻舆情        | 东方财富 / 新浪 / 财联社等      | 每日           | AI 解释    |
| 公告数据        | 巨潮 / 东方财富                 | 每日           | 催化和风险 |

北向数据说明：

```text
如果无法获得实时免费接口，则只使用最新可获得快照；
北向持股只作为 S0 机构认可慢变量；
不作为短线买点依据。
```

---

## 5.3 数据更新任务顺序

每日收盘后，本地任务建议这样跑：

```text
Step 1：更新交易日历
Step 2：更新股票列表和基础信息
Step 3：更新个股日线
Step 4：更新指数日线
Step 5：更新行业 / 板块日线
Step 6：更新股票-行业映射
Step 7：更新 ETF 行情
Step 8：更新新闻 / 公告
Step 9：增量计算技术指标
Step 10：计算五周期 RPS
Step 11：计算市场强度 M0-M4
Step 12：计算行业强度 I0-I4
Step 13：计算结构健康 S4
Step 14：计算买点信号 S5
Step 15：更新持仓跟踪 S6
Step 16：生成生命周期状态 S0-S7
Step 17：生成 Alpha 机会表
Step 18：导出云端需要的核心表
Step 19：上传 R2
Step 20：触发 CockroachDB 批量导入
Step 21：生成日报内容
```

---

## 5.4 本地目录结构

建议升级为：

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

# 6. 数据计算实现设计

## 6.1 RPS 计算

周期：

```text
5 / 20 / 50 / 120 / 250
```

计算逻辑：

```text
1. 计算每只股票过去 N 日收益率；
2. 在全市场股票中排序；
3. 转换为 0-100 分位数；
4. 写入 stock_strength_daily。
```

RPS 状态标签：

```text
短线异动；
当前强势；
中期启动；
中期主升；
长期强股；
高位衰竭。
```

---

## 6.2 市场强度计算

输入：

```text
指数数据；
全市场涨跌家数；
创新高数量；
涨跌停数量；
成交额；
RPS90 以上股票数量。
```

输出：

```text
market_strength_score
market_state: M0-M4
suggested_position_min
suggested_position_max
```

---

## 6.3 行业强度计算

输入：

```text
行业指数行情；
行业成交额；
行业内成分股；
行业内 RPS90 股票数量；
行业内新高数量；
行业龙头。
```

输出：

```text
industry_strength_score
industry_state: I0-I4
leader_symbol
rps90_stock_ratio
new_high_stock_ratio
```

---

## 6.4 结构健康计算

输入：

```text
个股 K 线；
均线；
成交量；
ATR；
RPS；
行业状态；
支撑位；
阶段高点。
```

输出：

```text
trend_score
drawdown_score
volume_contraction_score
volatility_contraction_score
relative_strength_score
support_score
stop_distance_score
structure_score
structure_label
is_structure_healthy
```

---

## 6.5 买点信号计算

买点类型：

```text
pocket_pivot
breakout
supply_exhaustion
earnings_gap_second_breakout
```

每个买点输出：

```text
signal_type
signal_score
trigger_price
stop_loss_price
stop_loss_pct
volume_ratio
close_position
is_valid
invalid_reason
```

---

# 7. 数据库设计

旧版 16 张核心表保留，同时新增生命周期相关表。

## 7.1 保留旧表

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
users
subscriptions
```

---

## 7.2 新增核心表

### S0 机构认可表

```sql
institutional_holding_snapshot (
    symbol,
    report_date,
    fund_hold_ratio,
    fund_holder_count,
    northbound_hold_ratio,
    northbound_hold_change,
    institution_score,
    data_source,
    data_age_days,
    holding_confidence,
    created_at
)
```

### 市场强度表

```sql
market_strength_daily (
    trade_date,
    market_state,
    market_strength_score,
    index_trend_score,
    market_breadth_score,
    amount_score,
    new_high_score,
    limit_up_score,
    limit_down_risk_score,
    rps90_count,
    suggested_position_min,
    suggested_position_max,
    created_at
)
```

### 行业强度表

```sql
industry_strength_daily (
    trade_date,
    sector_code,
    sector_name,
    sector_type,
    industry_state,
    industry_strength_score,
    return_5d,
    return_20d,
    return_50d,
    return_120d,
    sector_rps_20,
    sector_rps_50,
    amount_ratio_20,
    rps90_stock_ratio,
    new_high_stock_ratio,
    leader_symbol,
    leader_strength_score,
    created_at
)
```

### 个股强度表

```sql
stock_strength_daily (
    trade_date,
    symbol,
    rps_5,
    rps_20,
    rps_50,
    rps_120,
    rps_250,
    rps_abs_score,
    rps_momentum_score,
    rps_label,
    industry_rank,
    stock_strength_score,
    created_at
)
```

### 结构健康表

```sql
structure_health_daily (
    trade_date,
    symbol,
    trend_score,
    drawdown_score,
    volume_contraction_score,
    volatility_contraction_score,
    relative_strength_score,
    support_score,
    stop_distance_score,
    structure_score,
    structure_label,
    is_structure_healthy,
    invalid_reason,
    created_at
)
```

### 买点信号表

```sql
timing_signals_daily (
    trade_date,
    symbol,
    signal_type,
    signal_name,
    signal_score,
    trigger_price,
    stop_loss_price,
    stop_loss_pct,
    volume_ratio,
    close_position,
    is_valid,
    invalid_reason,
    created_at
)
```

### 生命周期状态表

```sql
stock_lifecycle_state (
    trade_date,
    symbol,
    lifecycle_state,
    market_state,
    industry_state,
    institution_score,
    stock_strength_score,
    structure_score,
    timing_score,
    state_reason,
    next_action,
    created_at
)
```

### 交易计划表

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

### 持仓跟踪表

```sql
position_tracking_daily (
    position_id,
    trade_date,
    symbol,
    buy_price,
    current_price,
    initial_stop_price,
    structure_stop_price,
    trailing_stop_price,
    market_state,
    industry_state,
    rps_20,
    rps_50,
    position_state,
    risk_level,
    next_action,
    created_at
)
```

### 复盘记录表

```sql
trade_review_records (
    trade_id,
    symbol,
    buy_date,
    sell_date,
    buy_signal_type,
    sell_signal_type,
    profit_loss_pct,
    market_state_at_buy,
    industry_state_at_buy,
    rps_label_at_buy,
    structure_score_at_buy,
    timing_score_at_buy,
    is_system_trade,
    mistake_type,
    review_note,
    rule_update_suggestion,
    created_at
)
```

### 产业研究池表

```sql
research_watchlist (
    research_id,
    theme_name,
    industry_chain_node,
    symbol,
    company_name,
    research_reason,
    logic_summary,
    catalyst,
    risk_note,
    research_status,
    created_at,
    updated_at
)
```

### 产业主题表

```sql
research_themes (
    theme_id,
    theme_name,
    theme_type,
    industry_summary,
    value_chain_map,
    key_companies,
    ai_summary,
    created_at,
    updated_at
)
```

---

# 8. 云端 API 设计

API 设计要围绕前端页面和使用场景，而不是围绕数据库表。

## 8.1 市场强度 API

```http
GET /api/market/strength/latest
```

返回：

```json
{
  "trade_date": "2026-04-28",
  "market_state": "M3",
  "market_strength_score": 78,
  "summary": "市场偏强，允许正常参与",
  "suggested_position": {
    "min": 0.4,
    "max": 0.7
  },
  "metrics": {
    "up_count": 3200,
    "down_count": 1800,
    "limit_up_count": 62,
    "limit_down_count": 8,
    "rps90_count": 430
  }
}
```

---

## 8.2 行业强度 API

```http
GET /api/industries/strength?date=latest&state=I3,I4
```

返回强行业榜。

```json
{
  "items": [
    {
      "sector_code": "BK001",
      "sector_name": "半导体设备",
      "industry_state": "I4",
      "industry_strength_score": 88,
      "return_20d": 0.18,
      "amount_ratio_20": 1.9,
      "rps90_stock_ratio": 0.32,
      "leader_symbol": "XXXXXX"
    }
  ]
}
```

---

## 8.3 Alpha 机会 API

```http
GET /api/alpha/opportunities?state=S4,S5&limit=50
```

返回：

```json
{
  "items": [
    {
      "symbol": "XXXXXX",
      "name": "示例公司",
      "lifecycle_state": "S5",
      "market_state": "M3",
      "industry_state": "I4",
      "rps_label": "中期启动",
      "structure_label": "VCP收缩结构",
      "signal_type": "breakout",
      "confidence_score": 86,
      "reason": "强行业前排，结构健康，放量突破平台"
    }
  ]
}
```

---

## 8.4 个股作战室 API

```http
GET /api/stocks/{symbol}/dashboard
```

返回个股全景数据：

```json
{
  "basic": {},
  "kline": [],
  "market_state": {},
  "industry_state": {},
  "rps": {},
  "structure": {},
  "signals": [],
  "trade_plan": {},
  "news": [],
  "ai_cache": {}
}
```

---

## 8.5 买点信号 API

```http
GET /api/signals/today?signal_type=pocket_pivot,breakout,supply_exhaustion
```

返回当天触发的信号。

---

## 8.6 生命周期 API

```http
GET /api/lifecycle/stocks?state=S3,S4,S5
GET /api/lifecycle/{symbol}
```

用于展示某只股票从 S0 到 S7 的变化过程。

---

## 8.7 研究入口 API

```http
POST /api/research/themes
GET /api/research/themes
GET /api/research/themes/{theme_id}
POST /api/research/watchlist
GET /api/research/watchlist?theme_id=xxx
```

用于产业研究池。

---

## 8.8 AI 分析 API

```http
POST /api/ai/analyze-stock
POST /api/ai/analyze-industry
POST /api/ai/generate-trade-plan
POST /api/ai/generate-daily-report
POST /api/ai/review-trade
```

重要原则：

```text
AI 不自动全量分析；
AI 只在用户点击、日报生成、重点机会缓存时调用；
所有 AI 结果写入 ai_analysis_cache；
设置 expire_at 控制成本。
```

---

# 9. 前端页面设计

前端不应该只是展示股票列表，而要展示“决策流”。

## 9.1 首页：Evo Alpha 雷达

首页是用户每天打开的第一屏。

模块：

```text
市场状态卡片
强行业榜
今日 S5 买点触发
今日 S4 结构准备
持仓风险提醒
AI 每日作战简报
```

首页逻辑：

```text
先告诉用户今天能不能做；
再告诉用户主战场在哪里；
再告诉用户哪些股票接近买点；
再告诉用户哪些股票已经触发；
最后告诉用户持仓风险。
```

---

## 9.2 市场温度页

页面目标：

> 判断今天能不能交易。

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

可视化：

```text
市场温度仪表盘；
指数趋势图；
市场宽度柱状图；
创新高数量趋势图；
涨跌停情绪图。
```

---

## 9.3 行业战役指挥所

页面目标：

> 判断主战场在哪里。

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

点击某个行业后进入行业详情页：

```text
行业指数 K 线；
行业强度变化；
成分股强弱排序；
行业内 S3/S4/S5 股票；
AI 行业逻辑拆解；
产业链映射。
```

---

## 9.4 个股作战室

页面目标：

> 判断一只股票是否值得交易。

页面结构：

```text
左侧：K线 + 成交量 + 均线 + 买点标记
中间：Evo 生命周期状态
右侧：AI 分析与交易计划
底部：新闻、公告、复盘记录
```

核心展示：

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

一只股票应该有一张“Evo 交易卡片”：

```text
股票名称；
生命周期状态；
市场状态；
行业状态；
RPS 标签；
结构评分；
买点类型；
触发价格；
止损价格；
建议仓位；
系统结论；
风险提示。
```

---

## 9.5 买点信号页

页面目标：

> 只看今天真正触发的信号。

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

## 9.6 产业研究页

页面目标：

> 从研究主题出发构建产业研究池。

功能：

```text
创建研究主题；
AI 拆解产业链；
映射 A 股公司；
建立研究观察池；
自动匹配技术状态；
生成研究报告。
```

一个主题页面展示：

```text
产业链地图；
关键环节；
相关上市公司；
每家公司当前生命周期状态；
哪些已经被市场验证；
哪些仍需等待。
```

---

## 9.7 持仓风控页

页面目标：

> 买入后不再纠结，系统跟踪强势是否延续。

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

## 9.8 复盘进化页

页面目标：

> 系统进化。

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

# 10. 系统完成后的日常使用场景

## 10.1 每日收盘后 15-30 分钟

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

第四步看持仓风险：

```text
股票 F：P1 风险观察
股票 G：P3 退出触发
```

最终输出：

```text
明天重点观察名单；
明天可能触发买点；
当前持仓风险；
不该碰的行业和个股。
```

---

## 10.2 盘中使用

盘中不全市场乱看，只看：

```text
S5 买点是否触发；
S6 持仓是否破位；
行业强度是否突变。
```

用户只处理三类信息：

```text
买点触发提醒；
持仓风险提醒；
行业退潮提醒。
```

---

## 10.3 行业研究使用

当用户研究一个行业，比如“半导体设备”：

```text
输入研究主题；
AI 拆产业链；
映射上市公司；
生成研究池；
系统自动匹配这些公司的技术状态。
```

输出：

```text
公司 A：产业逻辑强，RPS 弱，继续观察；
公司 B：RPS 强，行业 I4，结构 S4，重点跟踪；
公司 C：买点已触发，进入交易计划；
公司 D：无机构认可，进入实验池。
```

---

## 10.4 周末复盘使用

系统统计：

```text
本周系统内交易数量；
系统外交易数量；
不同买点胜率；
不同市场状态收益；
不同行业状态收益；
最大亏损来源；
主要错误类型。
```

给出建议：

```text
M1 市场不要开新仓；
口袋买点止损超过 8% 时只观察；
行业降级后后排股自动进入减仓警告；
突破买点需要行业同步走强。
```

---

# 11. AI Agent 设计

旧版“大师 Agent”可以保留为内部参考，但产品层统一成 Evo Agent。

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

AI 使用原则：

```text
不全量自动跑；
只对重点机会跑；
用户点击触发；
日报批量生成；
结果缓存；
控制成本。
```

---

# 12. 开发路线图 v0.7

## Phase 1：数据采集与 RPS 修复

```text
修复 AkShare 行情采集；
跑通本地 SQLite；
完成日线全量数据；
完成 RPS5/20/50/120/250；
完成基础股票池过滤。
```

## Phase 2：S0 机构认可池

```text
接入基金持股数据；
接入最新可获得北向持股数据；
建立 institutional_holding_snapshot；
完成 S0 过滤规则。
```

## Phase 3：市场强度与行业强度

```text
完成 M0-M4 市场状态；
完成 I0-I4 行业状态；
完成强行业榜；
完成行业内强股比例。
```

## Phase 4：生命周期状态机

```text
完成 S0-S7 状态表；
完成每日状态更新；
完成 Alpha 机会池。
```

## Phase 5：结构健康评分

```text
趋势结构；
回撤控制；
成交量收缩；
波动收敛；
相对强度；
止损空间。
```

## Phase 6：买点信号

```text
移植口袋买点；
实现突破买点；
实现抛压衰竭买点；
实现业绩跳空后二次突破。
```

## Phase 7：前端 MVP

```text
首页 Alpha 雷达；
市场温度页；
行业战役指挥所；
个股作战室；
买点信号页。
```

## Phase 8：云端部署

```text
CSV / Parquet 导出；
R2 上传；
CockroachDB 导入；
FastAPI 查询；
Vercel 部署。
```

## Phase 9：AI Agent 与日报

```text
AI 个股解释；
AI 行业解释；
AI 交易计划；
每日作战简报；
邮件推送。
```

## Phase 10：复盘与回测

```text
持仓跟踪；
交易复盘；
买点胜率统计；
市场/行业状态分组回测；
参数优化。
```

---

# 13. 最终定义

EvoAlpha OS v0.7 可以定义为：

> 一个以本地数据工厂为计算底座，以低成本云端架构为展示与分发层，以双入口 Alpha 发现机制为投研流程，以机构认可为基础过滤，以市场强度为交易开关，以行业强度为方向选择，以五周期 RPS 为个股强度识别，以结构健康评分为交易准备，以口袋买点、突破买点、抛压衰竭买点为执行触发，以持仓跟踪和复盘归因为进化机制的 A 股智能投研系统。

一句话版本：

> EvoAlpha OS 用技术强度发现市场已经选择的强者，用产业研究解释强者为什么值得跟踪，再用生命周期状态机等待低风险买点和纪律化退出。

---

# 14. 当前最重要的开发顺序

我建议不要同时做太多，优先级如下：

```text
第一优先级：数据采集修复 + RPS5/20/50/120/250；
第二优先级：S0 机构认可池；
第三优先级：市场强度 M0-M4；
第四优先级：行业强度 I0-I4；
第五优先级：生命周期状态机 S0-S7；
第六优先级：结构健康评分；
第七优先级：三个核心买点；
第八优先级：前端 MVP；
第九优先级：AI Agent；
第十优先级：复盘和回测。
```

这版可以作为  **EvoAlpha OS v0.7 工程白皮书主文档** 。后续我们每做一个模块，就把它拆成更具体的开发文档，比如 `market_strength.md`、`industry_strength.md`、`structure_health.md`、`api_spec.md` 和 `frontend_pages.md`。
