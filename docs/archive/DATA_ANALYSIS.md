# EvoAlpha OS - 数据采集模块分析报告

> **分析时间**：2025-01-18
> **目的**：对比现有数据采集脚本与蓝图需求，找出缺失部分
> **状态**：✅ 分析完成

---

## 📊 现有数据采集模块清单

### ✅ 已实现的数据采集脚本

| 文件名 | 功能描述 | 对应数据表 | 状态 |
|--------|---------|-----------|------|
| `update_stock_list.py` | 股票名单采集 | `stock_info` | ✅ 已有（占位） |
| `update_stock_kline.py` | 个股K线数据 | `stock_daily_prices` | ✅ 完整实现 |
| `update_sector_kline.py` | 板块K线数据 | `sector_daily_prices` | ✅ 完整实现 |
| `update_stock_sector_list.py` | 股票-板块映射 | `stock_sector_map` | ✅ 完整实现 |
| `update_capital_flow.py` | 资金流向数据 | 需确认表名 | ✅ 完整实现 |
| `update_finance_summary.py` | 财务摘要数据 | 需确认表名 | ✅ 完整实现 |
| `update_stock_valuation.py` | 估值数据 | 需确认表名 | ✅ 完整实现 |
| `update_kline.py` | K线数据（占位） | `stock_daily_prices` | ⚠️ 占位文件 |

---

## 🎯 蓝图需求对比

### 模块0：新闻舆情数据层（基础支撑）

**蓝图要求**：
- 数据采集（东方财富、新浪、第一财经）
- 情绪分析（利好/利空提取）
- 催化剂识别（政策/业绩/事件）
- 舆情打分（-10到+10）

**对应数据表**：
```sql
news_articles              -- 新闻文章
news_stock_relation        -- 新闻-股票关联
sentiment_keywords         -- 情绪关键词
```

**现状**：
- ❌ **缺失**：没有新闻采集脚本
- ❌ **缺失**：没有舆情分析脚本

---

### 模块6：ETF 全天候配置

**蓝图要求**：
- ETF 基础信息（5类资产：科技、红利、纳指、黄金、豆粕）
- ETF 行情数据
- ETF RPS 计算

**对应数据表**：
```sql
etf_info                  -- ETF 基础信息
etf_daily_prices          -- ETF 行情
etf_feature_rps           -- ETF RPS
all_weather_allocations   -- 全天候配置记录
```

**现状**：
- ❌ **缺失**：没有 ETF 信息采集脚本
- ❌ **缺失**：没有 ETF K线采集脚本
- ❌ **缺失**：没有 ETF RPS 计算脚本

---

### 模块1-5：其他数据需求

#### Alpha 机会表
```sql
alpha_opportunities        -- Alpha 机会列表
opportunity_tracking       -- 机会跟踪
```
**现状**：❌ **缺失**（由策略生成，非数据采集）

#### AI 分析表
```sql
ai_analysis_cache          -- AI 分析缓存
```
**现状**：❌ **缺失**（由 AI Agent 生成）

#### 日报表
```sql
daily_reports              -- 日报内容
report_sending_log         -- 发送记录
```
**现状**：❌ **缺失**（由日报系统生成）

---

## ❌ 缺失数据采集脚本清单

### 优先级 1（高）：核心业务数据

#### 1. 新闻舆情采集
**文件**：`backend/data_job/update_news.py`

**功能需求**：
- 从东方财富、新浪、第一财经采集新闻
- 提取标题、内容、来源、发布时间
- 识别相关股票代码
- 情绪分类（利好/利空/中性）
- 保存到 `news_articles` 和 `news_stock_relation` 表

**数据源**：
- 东方财富：https://www.eastmoney.com/
- 新浪财经：https://finance.sina.com.cn/
- 第一财经：https://www.yicai.com/

**AkShare 接口**：
```python
ak.stock_news_em(symbol="000001")  # 个股新闻
ak.stock_news_em()  # 全部新闻
```

---

#### 2. ETF 数据采集
**文件**：`backend/data_job/update_etf_info.py`

**功能需求**：
- ETF 基本信息采集（名称、代码、类型、标的指数）
- ETF 行情数据采集（K线）
- 保存到 `etf_info` 和 `etf_daily_prices` 表

**ETF 列表**：
- 科技 ETF：159915（科创板）、515000（5GETF）
- 红利 ETF：515080（红利ETF）
- 纳指 ETF：159941（纳指ETF）、513100（纳指ETF）
- 黄金 ETF：518880（黄金ETF）
- 豆粕 ETF：159987（豆粕ETF）

**AkShare 接口**：
```python
ak.fund_etf_category_sina(symbol="ETF基金")  # ETF列表
ak.fund_etf_hist_sina(symbol="159915")  # ETF行情
```

---

#### 3. 连板数据采集
**文件**：`backend/data_job/update_limit_boards.py`

**功能需求**：
- 每日涨停板数据采集
- 连板统计（高度板）
- 封板率计算
- 保存到 `limit_board_trading` 和 `consecutive_boards_stats` 表

**AkShare 接口**：
```python
ak.stock_zt_pool_em(date="20250118")  # 涨停板行情
```

---

### 优先级 2（中）：扩展数据

#### 4. 板块成分股更新
**文件**：`backend/data_job/update_sector_constituents.py`

**功能需求**：
- 更新板块成分股列表
- 更新股票-板块权重
- 补充 `stock_sector_map` 表

---

#### 5. 宏观经济数据
**文件**：`backend/data_job/update_macro_data.py`

**功能需求**：
- GDP、CPI、PMI 等宏观数据
- 国债收益率
- 汇率数据
- 保存到 `macro_indicators` 表

---

### 优先级 3（低）：可选数据

#### 6. 研报数据采集
**文件**：`backend/data_job/update_research_reports.py`

**功能需求**：
- 券商研报采集
- 研报摘要
- 评级信息

**数据源**：东方财富研报中心

---

#### 7. 北向资金详细数据
**文件**：`backend/data_job/update_north_flow_detail.py`

**功能需求**：
- 北向资金持股明细
- 持股变动

---

## 📋 数据表字段补充

### 需要创建的新表

#### 1. 新闻相关表
```sql
CREATE TABLE news_articles (
    article_id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(200),
    content TEXT,
    source VARCHAR(50),
    publish_time TIMESTAMP,
    url VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE news_stock_relation (
    article_id VARCHAR(50),
    symbol VARCHAR(20),
    relevance_score FLOAT,
    sentiment_type VARCHAR(10),  -- 'positive', 'negative', 'neutral'
    PRIMARY KEY (article_id, symbol),
    FOREIGN KEY (article_id) REFERENCES news_articles(article_id),
    FOREIGN KEY (symbol) REFERENCES stock_info(symbol)
);
```

#### 2. ETF 相关表
```sql
CREATE TABLE etf_info (
    symbol VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    fund_type VARCHAR(50),  -- 'tech', 'dividend', 'nasdaq', 'gold', 'soybean'
    underlying_index VARCHAR(100),
    launch_date DATE,
    expense_ratio FLOAT,
    fund_company VARCHAR(100)
);

CREATE TABLE etf_daily_prices (
    symbol VARCHAR(20),
    trade_date DATE,
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume FLOAT,
    amount FLOAT,
    PRIMARY KEY (symbol, trade_date),
    FOREIGN KEY (symbol) REFERENCES etf_info(symbol)
);

CREATE TABLE etf_feature_rps (
    symbol VARCHAR(20),
    trade_date DATE,
    rps_20 FLOAT,
    rps_50 FLOAT,
    rps_250 FLOAT,
    PRIMARY KEY (symbol, trade_date),
    FOREIGN KEY (symbol) REFERENCES etf_info(symbol)
);

CREATE TABLE all_weather_allocations (
    allocation_date DATE PRIMARY KEY,
    tech_ratio FLOAT,
    dividend_ratio FLOAT,
    nasdaq_ratio FLOAT,
    gold_ratio FLOAT,
    soybean_ratio FLOAT,
    total_value FLOAT,
    rebalance_reason TEXT
);
```

#### 3. 连板数据表
```sql
CREATE TABLE limit_board_trading (
    trade_date DATE,
    symbol VARCHAR(20),
    limit_time TIME,
    limit_price FLOAT,
    turnover_ratio FLOAT,
    amount FLOAT,  -- 成交额（万）
    is_new_high BOOLEAN,  -- 是否新高
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE consecutive_boards_stats (
    trade_date DATE,
    boards INT,  -- 连板数（如3连板）
    stock_count INT,  -- 多少只股票
    PRIMARY KEY (trade_date, boards)
);
```

---

## 🗓️ 开发优先级建议

### 第一批（必须）：核心数据
1. **ETF 数据采集** - `update_etf_info.py`
   - ETF 基础信息
   - ETF K线数据

2. **新闻舆情采集** - `update_news.py`
   - 新闻文章采集
   - 股票关联识别

3. **连板数据采集** - `update_limit_boards.py`
   - 涨停板数据
   - 连板统计

### 第二批（重要）：增强数据
4. **板块成分股更新** - `update_sector_constituents.py`

5. **宏观经济数据** - `update_macro_data.py`

### 第三批（可选）：扩展数据
6. **研报数据采集** - `update_research_reports.py`

7. **北向资金明细** - `update_north_flow_detail.py`

---

## 📝 总结

### 现有数据采集：7 个脚本
✅ 股票名单、K线、板块K线、资金流向、财报、估值、板块映射

### 缺失数据采集：7 个脚本
❌ 新闻舆情、ETF数据、连板数据、板块成分股、宏观数据、研报、北向明细

### 数据完整度
- **基础行情**：90% ✅
- **新闻舆情**：0% ❌
- **ETF 数据**：0% ❌
- **连板数据**：0% ❌
- **宏观数据**：0% ❌

---

## 🚀 下一步行动

**建议优先实现**：
1. `update_etf_info.py` - ETF 数据（模块6核心）
2. `update_news.py` - 新闻舆情（模块0核心）
3. `update_limit_boards.py` - 连板数据（首页展示）

**预计工作量**：
- 每个脚本：2-3小时
- 总计：6-9小时
- 建议：分3天完成，每天2-3个脚本

---

**分析完成时间**：2025-01-18
**下次更新**：实现第一批数据采集脚本后
