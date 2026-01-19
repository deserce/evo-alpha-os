# EvoAlpha OS - 代码移植计划

> 从 EvoQuant OS 移植可用代码到 EvoAlpha OS
> 创建时间：2025-01-18

---

## 📊 旧项目分析

### 旧项目结构（EvoQuant OS）

```
EvoQuant OS/
├── backend/
│   ├── app/
│   │   ├── core/           # 核心配置 ✅ 可移植
│   │   ├── api/v1/         # API 接口 ✅ 可移植
│   │   ├── services/       # 服务层 ✅ 可移植
│   │   ├── agents/         # AI Agent ✅ 可移植
│   │   └── models/         # 数据模型 ✅ 可移植
│   │
│   ├── data_job/           # 数据采集 ✅ 可移植
│   │   ├── update_stock_kline.py
│   │   ├── update_stock_sector_list.py
│   │   ├── update_capital_flow.py
│   │   └── update_finance_summary.py
│   │
│   └── quant_engine/       # 量化引擎 ✅ 可移植
│       ├── core/           # 核心工具
│       ├── features/       # 因子计算
│       ├── pool/           # 股票池
│       └── strategies/     # 策略
│
└── frontend/
    └── src/
        ├── app/            # 页面路由 ✅ 可移植
        └── components/     # 组件 ✅ 可移植
```

### 代码统计

- **Python 文件**：56 个（不含 venv）
- **前端文件**：未知（待统计）

---

## 🎯 移植策略

### 原则

1. **保留三段式架构**：本地工厂 → R2 → 云端
2. **使用 GLM-4**：不用 Ollama，改用智谱 API
3. **调整 API 结构**：符合新的蓝图设计
4. **保留业务逻辑**：量化计算、数据采集、策略逻辑

### 映射关系

| 旧项目（EvoQuant） | 新项目（EvoAlpha） | 变化 |
|------------------|------------------|------|
| `APP_MODE` (local/cloud/hybrid) | 固定三段式 | 简化 |
| Ollama LLM | GLM-4 API | 替换 |
| Gemini | GLM-4 | 替换 |
| 单一数据库 | 本地 + 云端 | 增强 |
| `app/services/` | `app/agents/` | 重命名 |

---

## 📋 移植清单

### Phase 1：核心配置（优先级：高）

- [ ] **config.py**：保留双引擎逻辑，调整 GLM-4 配置
- [ ] **database.py**：保留 `get_active_engines()` 逻辑
- [ ] **requirements.txt**：合并旧项目依赖

### Phase 2：数据采集（优先级：高）

- [ ] **update_stock_kline.py**：个股 K 线采集
- [ ] **update_stock_sector_list.py**：股票名单
- [ ] **update_sector_kline.py**：板块 K 线
- [ ] **update_capital_flow.py**：资金流向
- [ ] **update_finance_summary.py**：财报数据

### Phase 3：量化引擎（优先级：高）

- [ ] **calc_indicators.py**：技术指标计算
- [ ] **calc_sector_rps.py**：板块 RPS
- [ ] **base_strategy.py**：策略基类
- [ ] **mrgc_strategy.py**：MRGC 策略

### Phase 4：API 接口（优先级：中）

- [ ] **dashboard.py**：仪表盘 API
- [ ] **stock.py**：个股 API
- [ ] **report.py**：报告 API
- [ ] **graph.py**：图表数据 API

### Phase 5：AI Agent（优先级：中）

- [ ] **services/gemini.py** → **agents/**：改用 GLM-4
- [ ] **services/akshare_service.py**：数据服务

### Phase 6：前端（优先级：中）

- [ ] **页面组件**：首页、个股详情、板块详情
- [ ] **图表组件**：K 线图、热力图

### Phase 7：测试（优先级：低）

- [ ] 单元测试
- [ ] 集成测试
- [ ] 端到端测试

---

## 🔧 具体移植步骤

### Step 1：更新核心配置

**旧代码**：
```python
# EvoQuant OS
LLM_MODEL_NAME: str = "qwen2.5-32b"
OLLAMA_BASE_URL: str = "http://localhost:11434"
```

**新代码**：
```python
# EvoAlpha OS
GLM_API_KEY: str = os.getenv("GLM_API_KEY")
GLM_API_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
GLM_MODEL: str = "glm-4-flash"
```

### Step 2：调整数据库逻辑

**保留**：
- `get_active_engines()` 函数
- `local_engine` + `cloud_engine` 双引擎
- SessionLocal 会话工厂

**调整**：
- 云端 URL 构建逻辑（使用配置参数）
- 异步支持（FastAPI 需要）

### Step 3：移植数据采集脚本

**原样保留**：
- AkShare 调用逻辑
- 数据清洗逻辑
- 增量更新逻辑

**需要调整**：
- 数据库写入（使用新的双引擎）
- 错误处理（适配新架构）

### Step 4：移植量化引擎

**原样保留**：
- RPS 计算公式
- 技术指标计算
- 策略逻辑

**需要调整**：
- 数据库读取（使用新的双引擎）
- 结果写入（本地 + 云端）

---

## ⚠️ 注意事项

### 1. API 密钥配置

旧项目的 `.env` 需要调整：
```bash
# 旧配置（保留）
DATABASE_URL=...
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...

# 新配置（添加）
GLM_API_KEY=...  # 智谱 API 密钥
```

### 2. 依赖更新

`requirements.txt` 需要合并：
```txt
# 保留旧项目依赖
akshare
pandas
numpy
sqlalchemy
...

# 添加新依赖
zhipuai  # GLM-4 SDK
```

### 3. 路径调整

旧项目：
```python
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
```

新项目：
```python
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
```

---

## 🚀 执行计划

### Day 1：核心模块移植

1. 更新 `config.py`
2. 更新 `database.py`
3. 测试双引擎连接

### Day 2：数据采集移植

1. 移植 `update_stock_kline.py`
2. 移植 `update_stock_sector_list.py`
3. 测试数据采集

### Day 3：量化引擎移植

1. 移植 `calc_indicators.py`
2. 移植 `mrgc_strategy.py`
3. 测试策略运行

### Day 4：API 移植

1. 移植 `dashboard.py`
2. 移植 `stock.py`
3. 测试 API 接口

### Day 5：前端移植

1. 移植页面组件
2. 移植图表组件
3. 测试前端展示

---

## ✅ 验收标准

### 后端

- [x] 数据采集成功运行
- [x] RPS 计算正确
- [x] 策略选出股票
- [x] API 返回数据
- [x] 数据同步云端

### 前端

- [x] 首页显示 Alpha 机会
- [x] 个股详情页加载 K 线
- [x] 板块详情页显示成分股
- [x] API 调用成功

---

**创建时间**：2025-01-18
**状态**：🚧 进行中
