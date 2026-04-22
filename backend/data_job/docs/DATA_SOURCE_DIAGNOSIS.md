# 数据源问题诊断与解决方案

## 当前问题

### 测试结果

❌ **所有数据源均无法连接**

```
错误: Connection aborted, Remote end closed connection without response
```

**原因分析**:
1. 当前网络环境可能有限制（公司网络、防火墙等）
2. 数据源服务器可能暂时不可用
3. 可能需要代理设置

### 测试的数据源

| 数据源 | 状态 | 错误 |
|--------|------|------|
| AKShare | ❌ 连接失败 | Remote end closed connection |
| efinance | ❌ 连接失败 | Connection aborted |
| 聚宽 | ❌ 需要付费 | - |

## 实际可行的解决方案

### 方案1: 使用本地数据缓存（推荐）

既然网络数据源无法使用，可以：

1. **使用已有的本地数据**
2. **从其他地方导出数据**
3. **使用数据库备份**

```python
# 检查本地数据库中的数据
from app.core.database import get_engine
import pandas as pd

engine = get_engine()

# 检查K线数据
df = pd.read_sql("SELECT * FROM stock_daily_prices WHERE symbol = '000001' ORDER BY trade_date DESC LIMIT 10", engine)
print("本地K线数据:")
print(df)

# 检查数据范围
stats = pd.read_sql("""
    SELECT
        symbol,
        MIN(trade_date) as min_date,
        MAX(trade_date) as max_date,
        COUNT(*) as records
    FROM stock_daily_prices
    GROUP BY symbol
    ORDER BY records DESC
    LIMIT 10
""", engine)
print("\n数据统计:")
print(stats)
```

### 方案2: 使用BaoStock（可能需要注册）

BaoStock是一个免费的证券数据平台，可能需要不同的网络连接方式。

```bash
pip3 install baostock
```

```python
import baostock as bs
import pandas as pd

# 登录系统
lg = bs.login()

# 获取历史K线
rs = bs.query_history_k_data_plus(
    "sz.000001",
    "date,open,high,low,close,volume,amount",
    start_date='2024-01-01',
    end_date='2024-12-31',
    frequency="d",
    adjustflag="2"  # 前复权
)

print('query_history_k_data_plus respond error_code:' + rs.error_code)
print('query_history_k_data_plus respond  error_msg:' + rs.error_msg)

# 打印结果集
data_list = []
while (rs.error_code == '0') & rs.next():
    data_list.append(rs.get_row_data())

result = pd.DataFrame(data_list, columns=rs.fields)
print(result)

# 登出系统
bs.logout()
```

### 方案3: 使用Tushare Pro（有限额但稳定）

虽然Tushare有积分限制，但新用户有一定的免费额度。

```bash
pip3 install tushare
```

```python
import tushare as ts

# 免费版使用（不需要token）
df = ts.get_hist_data('000001', start='2024-01-01', end='2024-12-31')
print(df)
```

### 方案4: 从同花顺/东方财富等网站手动下载

很多财经网站提供数据下载功能：

1. **东方财富网**: https://data.eastmoney.com/
2. **新浪财经**: https://finance.sina.com.cn/
3. **网易财经**: https://money.163.com/

下载后导入数据库：

```python
import pandas as pd
from app.core.database import get_engine

# 读取下载的CSV文件
df = pd.read_csv('downloaded_data.csv')

# 转换列名
df = df.rename(columns={
    '日期': 'trade_date',
    '开盘': 'open',
    '收盘': 'close',
    # ... 其他列名映射
})

# 保存到数据库
engine = get_engine()
df.to_sql('stock_daily_prices', engine, if_exists='append', index=False)
```

### 方案5: 检查并修复网络问题

#### 测试网络连通性

```python
import requests

def test_connection(url):
    try:
        response = requests.get(url, timeout=10)
        print(f"✅ {url}: {response.status_code}")
        return True
    except Exception as e:
        print(f"❌ {url}: {e}")
        return False

# 测试各个数据源
test_connection('https://www.akshare.xyz')
test_connection('https://push2.eastmoney.com')
test_connection('https://api.baostock.com')
test_connection('https://api.tushare.pro')
```

#### 配置代理

如果需要代理：

```bash
# .env
AKSHARE_PROXY=http://proxy.example.com:8080
```

```python
import os
import requests

proxies = {
    'http': os.getenv('HTTP_PROXY'),
    'https': os.getenv('HTTPS_PROXY')
}

response = requests.get('https://api.example.com', proxies=proxies)
```

### 方案6: 使用VPN或更换网络

如果当前网络有限制：

1. 尝试使用手机热点
2. 使用VPN服务
3. 更换网络环境（如从公司网络切换到家庭网络）

## 立即可行的操作

### 1. 检查本地数据

```bash
cd backend
python3 -c "
from app.core.database import get_engine
import pandas as pd

engine = get_engine()

# 检查表是否存在
tables = pd.read_sql('SELECT name FROM sqlite_master WHERE type=\"table\"', engine)
print('数据库表:')
print(tables)

# 检查K线数据量
if 'stock_daily_prices' in tables['name'].values:
    stats = pd.read_sql('''
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT symbol) as total_stocks,
            MIN(trade_date) as earliest_date,
            MAX(trade_date) as latest_date
        FROM stock_daily_prices
    ''', engine)
    print('\nK线数据统计:')
    print(stats)
"
```

### 2. 生成数据补齐计划

```bash
python3 -c "
from app.core.database import get_engine
import pandas as pd
from datetime import datetime, timedelta

engine = get_engine()

# 检查每个股票的数据完整性
df = pd.read_sql('''
    SELECT
        symbol,
        MIN(trade_date) as min_date,
        MAX(trade_date) as max_date,
        COUNT(*) as records,
        (julianday(MAX(trade_date)) - julianday(MIN(trade_date))) as expected_days
    FROM stock_daily_prices
    GROUP BY symbol
    HAVING records < 100  # 数据量少的股票
    ORDER BY records ASC
''', engine)

print('需要补齐数据的股票:')
print(df.head(20))

# 导出为CSV
df.to_csv('data_gap_analysis.csv', index=False)
print('\n已导出到 data_gap_analysis.csv')
"
```

### 3. 使用手动下载补齐关键数据

对于核心股票（如上证50、沪深300），可以手动下载：

```python
# 生成需要下载的股票列表
core_stocks = [
    '600519',  # 贵州茅台
    '000001',  # 平安银行
    '000002',  # 万科A
    '600036',  # 招商银行
    # ... 添加其他核心股票
]

print("需要手动下载的股票列表:")
for stock in core_stocks:
    print(f"  - {stock}")
```

## 长期解决方案

### 1. 建立数据备份机制

定期备份数据库：

```bash
# 备份脚本
#!/bin/bash
DATE=$(date +%Y%m%d)
cp data/local_quant.db data/backup/local_quant_$DATE.db
echo "备份完成: local_quant_$DATE.db"
```

### 2. 使用多个数据源交叉验证

即使某个数据源不可用，也有备用方案：

```python
# 配置多个数据源
DATA_SOURCE_STRATEGY=priority
ENABLE_AKSHARE=true
ENABLE_EFINANCE=true
ENABLE_BAOSTOCK=true  # 如果实现了
```

### 3. 实现数据去重和验证

```python
def validate_data_quality(df):
    """验证数据质量"""
    issues = []

    # 检查空值
    if df.isnull().any().any():
        issues.append("存在空值")

    # 检查价格合理性
    if (df['close'] <= 0).any():
        issues.append("存在非正价格")

    # 检查连续性
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date')
    gaps = df['trade_date'].diff().dt.days > 7
    if gaps.any():
        issues.append(f"存在 {gaps.sum()} 个数据缺口")

    return issues
```

## 总结

### 当前情况

- ❌ 所有在线数据源都无法连接（网络问题）
- ✅ 本地数据库架构完整
- ✅ 多数据源架构已实现

### 建议

1. **短期**: 使用本地已有数据，手动补齐关键数据
2. **中期**: 更换网络环境后测试各数据源
3. **长期**: 建立多数据源+本地缓存+定期备份的完整方案

### 下一步操作

1. ✅ 检查本地数据完整性
2. ✅ 确认哪些数据需要补齐
3. ⏳ 更换网络环境（如家庭网络、手机热点）
4. ⏳ 测试各个数据源的可用性
5. ⏳ 补齐缺失数据

---

**创建时间**: 2026-03-07
**状态**: 网络问题，需更换环境测试
