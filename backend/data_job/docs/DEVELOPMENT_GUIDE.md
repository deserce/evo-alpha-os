# EvoAlpha OS 数据采集系统 - 开发规范指南

> **版本**: v2.1.0 - 增量更新与自动化版
> **更新时间**: 2026-01-19

---

## 📋 目录

1. [开发环境设置](#开发环境设置)
2. [代码规范](#代码规范)
3. [新增采集器指南](#新增采集器指南)
4. [测试规范](#测试规范)
5. [提交规范](#提交规范)
6. [文档规范](#文档规范)

---

## 开发环境设置

### 环境要求

- Python 3.8+
- SQLite 3.x
- 网络连接

### 安装依赖

```bash
# 进入backend目录
cd backend

# 安装依赖
pip install -r data_job/requirements.txt
```

### 目录结构

```
backend/data_job/
├── collectors/           # 采集器实现
├── core/                # 核心框架
├── common/              # 公共工具
├── config/              # 配置管理
├── utils/               # 工具脚本
├── scripts/             # 独立脚本
├── tests/               # 测试套件
└── docs/                # 文档
```

---

## 代码规范

### 命名规范

#### 文件命名

```python
# 采集器文件：{数据源}_collector.py
stock_kline_collector.py    ✅
news_collector.py          ✅
fund_holdings_collector.py ✅

# 工具文件：{功能}.py
validate_data.py          ✅
scheduler.py               ✅
```

#### 类命名

```python
# 采集器类名：{数据源}Collector
class StockKlineCollector(BaseCollector):    ✅
class NewsCollector(BaseCollector):          ✅
class FundHoldingsCollector(BaseCollector):  ✅
```

#### 方法命名

```python
# 获取数据：fetch_*
def fetch_stock_list():          ✅
def fetch_data():                 ✅

# 处理数据：process_*
def process_data(df):          ✅

# 保存数据：save_*
def save_data(df):              ✅
def save_with_deduplication():  ✅

# 运行任务：run
def run():                      ✅
```

### 代码组织

#### 标准结构

```python
"""
EvoAlpha OS - {数据源}数据采集器
{功能描述}
"""

import time
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta, date

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class XxxCollector(BaseCollector):
    """{数据源}数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="xxx",           # 小写，下划线分隔
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "xxx_table"

    def _init_table(self):
        """初始化数据库表"""
        pass

    def fetch_data(self):
        """获取数据"""
        pass

    def process_data(self, df):
        """处理数据"""
        pass

    def save_data(self, df):
        """保存数据"""
        pass

    def run(self):
        """执行采集"""
        pass


if __name__ == "__main__":
    collector = XxxCollector()
    collector.run()
```

---

## 新增采集器指南

### Step 1: 创建采集器文件

```bash
cd data_job/collectors
# 创建新文件
touch my_collector.py
```

### Step 2: 编写采集器代码

#### 最小化模板

```python
from data_job.core.base_collector import BaseCollector
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger
import pandas as pd

setup_backend_path()
setup_network_emergency_kit()
logger = setup_logger(__name__)


class MyCollector(BaseCollector):
    """我的数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="my_data",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "my_data"

    def _init_table(self):
        """初始化表"""
        pass

    def run(self):
        """执行采集"""
        self.log_collection_start()
        logger.info("🚀 开始采集数据...")

        try:
            self._health_check()
            self._init_table()

            # 采集逻辑
            df = self._retry_call(ak.some_api)

            if df is not None and not df.empty:
                # 保存
                self.save_with_deduplication(
                    df=df,
                    table_name=self.table_name,
                    key_columns=['id'],
                    date_column='date'
                )

            self.log_collection_end(True, f"采集了 {len(df)} 条数据")

        except Exception as e:
            logger.error(f"❌ 采集失败: {e}")
            self.log_collection_end(False, str(e))


if __name__ == "__main__":
    collector = MyCollector()
    collector.run()
```

### Step 3: 实现增量更新（推荐）

#### 方式1: 检查最后日期

```python
def get_last_date(self):
    """获取最后采集日期"""
    with self.engine.connect() as conn:
        query = text(f"SELECT MAX(date) as last_date FROM {self.table_name}")
        result = conn.execute(query).scalar()
        return result

def run(self):
    """执行采集"""
    self.log_collection_start()

    # 获取最后日期
    last_date = self.get_last_date()

    if last_date:
        # 从最后日期+1天开始采集
        start_date = last_date + timedelta(days=1)
        logger.info(f"📅 增量模式：从 {start_date} 至今")
    else:
        # 首次采集
        start_date = date.today() - timedelta(days=30)
        logger.info(f"🆕 首次采集：从 {start_date} 至今")

    # 采集数据
    df = self._retry_call(
        ak.some_api,
        start_date=start_date.strftime('%Y%m%d'),
        end_date=date.today().strftime('%Y%m%d')
    )

    # 保存
    self.save_data(df)
    self.log_collection_end(True, "采集完成")
```

#### 方式2: 删除今日数据

```python
def save_data(self, df):
    """保存数据（幂等性）"""
    if df.empty:
        return

    today = date.today()
    date_str = today.strftime('%Y-%m-%d')

    with self.engine.begin() as conn:
        # 删除今天的数据
        conn.execute(text(f"DELETE FROM {self.table_name} WHERE date = :dt"), {"dt": date_str})

        # 插入新数据
        df.to_sql(self.table_name, conn, if_exists='append', index=False)
```

### Step 4: 添加到导出

```python
# data_job/collectors/__init__.py

from .my_collector import MyCollector

__all__ = [
    # ... 其他采集器
    'MyCollector',
]
```

### Step 5: 测试

```bash
# 运行测试
python -m data_job.collectors.my_collector

# 检查数据
python data_job/scripts/preview_database.py
```

---

## 测试规范

### 单元测试

#### 创建测试文件

```bash
# 在 tests/ 目录创建
touch data_job/tests/test_my_collector.py
```

#### 测试模板

```python
import unittest
from data_job.collectors import MyCollector

class TestMyCollector(unittest.TestCase):

    def setUp(self):
        """测试前准备"""
        self.collector = MyCollector()

    def test_collector_initialization(self):
        """测试初始化"""
        self.assertEqual(self.collector.collector_name, "my_data")
        self.assertIsNotNone(self.collector.engine)

    def test_fetch_data(self):
        """测试数据获取"""
        df = self.collector.fetch_data()
        self.assertIsNotNone(df)
        self.assertIsInstance(df, pd.DataFrame)

    def test_run(self):
        """测试完整流程"""
        self.collector.run()
        # 检查数据是否保存成功
```

### 运行测试

```bash
# 运行所有测试
pytest data_job/tests/

# 运行特定测试
pytest data_job/tests/test_my_collector.py -v
```

---

## 提交规范

### Git 提交消息

#### 新增采集器

```bash
feat: 添加MyCollector采集器

功能：
- 采集XXX数据
- 支持增量更新
- 自动去重保存

文件：
- data_job/collectors/my_collector.py
- data_job/collectors/__init__.py
```

#### 优化采集器

```bash
perf: 优化MyCollector性能

改进：
- 添加增量更新逻辑
- 优化API调用频率
- 减少内存占用

性能提升：
- 采集时间：10分钟 → 2分钟
- API调用：减少80%
```

#### Bug修复

```bash
fix: 修复MyCollector日期解析错误

问题：
- 某些日期格式无法解析

解决：
- 添加日期格式兼容性处理
- 增加异常捕获

影响：
- 修复后数据完整性提升
```

---

## 文档规范

### 代码文档

#### 模块文档字符串

```python
"""
EvoAlpha OS - XXX数据采集器
采集XXX的历史数据

功能：
- 采集每日/历史数据
- 支持增量更新
- 自动数据清洗

数据表：
- xxx_table (主表)
- xxx_detail (明细表)

作者: XXX
创建: 2026-01-19
"""
```

#### 类文档字符串

```python
class XxxCollector(BaseCollector):
    """
    XXX数据采集器

    功能：
    - 从AkShare采集XXX数据
    - 数据清洗和格式转换
    - 增量更新机制

    采集频率: 每日
    数据范围: 2023年至今

    使用示例:
        >>> collector = XxxCollector()
        >>> collector.run()
    """
```

#### 方法文档字符串

```python
def fetch_data(self, symbol: str) -> pd.DataFrame:
    """
    获取XXX数据

    Args:
        symbol: 股票代码

    Returns:
        pd.DataFrame: 包含XXX字段的DataFrame

    Raises:
        NetworkError: 网络连接失败
        DataSourceError: 数据源返回错误

    Example:
        >>> collector = XxxCollector()
        >>> df = collector.fetch_data("000001")
    """
```

### README文档

#### 采集器使用说明

在主README.md中添加：

```markdown
### XxxCollector

**功能**: 采集XXX数据

**数据表**: `xxx_table`

**采集频率**: 每日

**使用方法**:
```python
from data_job.collectors import XxxCollector

collector = XxxCollector()
collector.run()
```

**数据字段**:
- field1: 说明1
- field2: 说明2
```

---

## 代码审查清单

### 提交前检查

- [ ] 代码符合命名规范
- [ ] 添加了完整的文档字符串
- [ ] 实现了增量更新（如适用）
- [ ] 错误处理完善
- [ ] 日志输出清晰
- [ ] 测试通过
- [ ] README已更新（如需要）
- [ ] 在 `__init__.py` 中导出

### 性能检查

- [ ] 避免重复查询
- [ ] 使用批量操作
- [ ] 合理设置延迟时间
- [ ] 适当使用缓存
- [ ] 数据库索引正确

### 安全检查

- [ ] SQL注入防护
- [ ] 密码和密钥不硬编码
- [ ] 异常信息不泄露敏感数据
- [ ] 输入验证

---

## 最佳实践

### 1. 增量更新

**推荐**: 所有采集器都应该支持增量更新

```python
# ✅ 好的做法
last_date = self.get_last_date()
if last_date:
    start_date = last_date + timedelta(days=1)

# ❌ 不好的做法
start_date = "20200101"  # 每次都从2020年开始
```

### 2. 错误处理

**推荐**: 详细的错误处理和日志

```python
# ✅ 好的做法
try:
    df = self._retry_call(ak.some_api)
    if df is None or df.empty:
        logger.warning(f"⚠️  无数据")
        return
except NetworkError as e:
    logger.error(f"❌ 网络错误: {e}")
    return
except Exception as e:
    logger.error(f"❌ 未知错误: {e}")
    raise

# ❌ 不好的做法
df = ak.some_api()  # 无错误处理
```

### 3. 日志输出

**推荐**: 使用emoji和结构化日志

```python
# ✅ 好的做法
logger.info("🚀 开始采集数据...")
logger.info(f"📊 已采集 {len(df)} 条记录")
logger.info(f"✅ 采集完成")

# ❌ 不好的做法
logger.info("开始")
logger.info("完成")
```

### 4. 数据验证

**推荐**: 保存前验证数据

```python
# ✅ 好的做法
if df.empty:
    logger.warning("⚠️  数据为空，跳过")
    return

df = df.dropna(subset=['symbol', 'date'])  # 去除必要字段为空的行
df['symbol'] = df['symbol'].str.zfill(6)  # 格式化股票代码

# ❌ 不好的做法
df.to_sql(...)  # 直接保存，不验证
```

---

## 开发工作流

### 1. 创建新功能

```bash
# 1. 创建分支
git checkout -b feature/my-collector

# 2. 开发
# - 创建采集器
# - 编写代码
# - 添加测试

# 3. 测试
python -m data_job.collectors.my_collector

# 4. 提交
git add .
git commit -m "feat: 添加MyCollector采集器"

# 5. 合并
git checkout main
git merge feature/my-collector
```

### 2. Bug修复

```bash
# 1. 创建分支
git checkout -b fix/issue-xxx

# 2. 修复bug
# - 定位问题
# - 修复代码
# - 添加测试

# 3. 验证
python -m data_job.collectors.xxx_collector

# 4. 提交
git add .
git commit -m "fix: 修复xxx问题"
```

### 3. 性能优化

```bash
# 1. 分析性能
python -m cProfile -o profile.stats data_job/collectors/xxx_collector

# 2. 优化代码
# - 减少API调用
# - 优化查询
# - 添加缓存

# 3. 验证
python -m data_job.collectors.xxx_collector

# 4. 提交
git commit -m "perf: 优化MyCollector性能"
```

---

## 常见问题

### Q1: 如何实现增量更新？

**A**: 根据数据类型选择策略：

- **K线数据**: 使用 `get_last_dates()` 检查每个代码的最后日期
- **估值数据**: 使用 `DELETE today + INSERT` 策略
- **财务数据**: 使用 `检查是否存在` 策略

详见：[ARCHITECTURE.md](ARCHITECTURE.md)

### Q2: 如何添加到定时任务？

**A**: 编辑 `utils/scheduler.py`:

```python
def run_custom_collection():
    from data_job.collectors import MyCollector
    collector = MyCollector()
    collector.run()

# 然后在 setup_jobs() 中添加定时任务
self.scheduler.add_job(
    run_custom_collection,
    trigger=CronTrigger(hour=16, minute=0),
    id='custom_collection'
)
```

### Q3: 如何测试采集器？

**A**:

```bash
# 直接运行
python -m data_job.collectors.my_collector

# 运行测试套件
pytest data_job/tests/test_my_collector.py -v

# 验证数据
python data_job/scripts/preview_database.py
```

---

## 工具和资源

### 开发工具

- **IDE**: PyCharm, VSCode
- **数据库工具**: DB Browser for SQLite
- **日志分析**: `tail -f logs/*.log`

### 有用的命令

```bash
# 查看采集器日志
tail -f logs/*.log

# 预览数据库
python data_job/scripts/preview_database.py

# 验证数据
python data_job/utils/validate_data.py

# 运行测试
pytest data_job/tests/ -v
```

### 参考文档

- [README.md](../README.md) - 主文档
- [QUICKSTART.md](docs/QUICKSTART.md) - 快速开始
- [ARCHITECTURE.md](ARCHITECTURE.md) - 架构设计
- [BaseCollector源码](core/base_collector.py) - 基类实现

---

## 总结

遵循本开发规范可以：

1. ✅ **保证代码质量** - 统一的代码风格和结构
2. ✅ **提高可维护性** - 清晰的文档和注释
3. ✅ **确保稳定性** - 完善的错误处理
4. ✅ **便于协作** - 规范的提交流

---

**版本**: v2.1.0
**最后更新**: 2026-01-19
**维护者**: EvoAlpha OS Team
