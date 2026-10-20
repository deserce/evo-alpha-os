# mootdx + qfq 高速 K 线工作流

## 目标

使用 `mootdx` 拉取原始日线，使用本地 `qfq` 算法计算前复权，并高速写入 `stock_daily_prices`。

## 入口脚本

脚本路径：

```bash
backend/scripts/standalone/collect_stock_kline_mootdx_qfq.py
```

## 最近三年下载

以 2026-04-23 为例，最近三年的起始日期为 `2023-04-23`：

```bash
cd backend
python3 scripts/standalone/collect_stock_kline_mootdx_qfq.py \
  --table stock_daily_prices \
  --start 2023-04-23 \
  --end 2026-04-23 \
  --workers 8 \
  --pool-size 4 \
  --batch-size 50 \
  --full-refresh
```

## 日常增量更新

```bash
cd backend
python3 scripts/standalone/collect_stock_kline_mootdx_qfq.py \
  --table stock_daily_prices \
  --workers 8 \
  --pool-size 4 \
  --batch-size 50 \
  --lookback-days 5
```

## 速度监测

运行时进度条会显示：

- `success`：成功股票数
- `fail`：失败股票数
- `skip`：跳过股票数
- `rows`：已写入行数
- `sym_s`：当前平均股票处理速度（只/秒）
- `rows_s`：当前平均写入速度（行/秒）

脚本会自动对 `mootdx` 返回的股票代码做顺序去重，避免重复采集同一只股票。

## 复权策略

- 数据源：`mootdx bars + xdxr`
- 复权方式：本地前复权 `qfq`
- 价格列：调整 `open/high/low/close`
- `pct_chg`：按复权后 `close` 重算
- `volume/amount`：保持原值，避免影响现有下游逻辑

## 增量规则

- 如果股票没有新除权事件：从最近落库日期往前回看 `5` 天做增量修补
- 如果股票出现新的除权事件：从 `--start` 指定日期整段重算，避免历史价格错误

## 注意事项

- SQLite 只有单写，多开同类采集任务容易锁库
- 正式跑全量时，不要同时运行其他大量写库脚本
- 若要先试跑，可加 `--limit 50` 或 `--symbol 600519`
