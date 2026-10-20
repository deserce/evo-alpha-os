# EvoAlpha 本地数据存储与清理策略

更新时间：2026-08-26

这份文档记录本地 Data Core 的可重复清理规则。目标是降低磁盘和 I/O 占用，同时保证 K 线、RPS、Watch 和 MRGC 可以从保留的原始数据重建。

## 口径

- Git 和 SQLite 备份是两套不同的保护机制。Git 保留代码版本和清理逻辑，不替代数据库快照；数据库快照也不替代 Git。
- 原始行情不按本策略删除。`stock_daily_prices`、板块/指数原始日线和需要追溯的原始表继续保留完整本地历史。
- 可重复生成的派生特征默认只保留最近 **500 个已完成交易日**：
  `quant_feature_rps`、`quant_feature_sector_rps`、`quant_feature_sector_rps_v2`、
  `quant_feature_etf_rps`、`market_strength_daily`、`industry_strength_daily`、
  `sector_composite_daily`、`sector_composite_ohlc_daily`、
  `sector_daily_prices_rebuild`、`stock_lifecycle_state`、
  `structure_health_daily`、`timing_signals_daily`。
- 500 日窗口覆盖 RPS 最大 250 日周期及展示/复核缓冲。窗口起点从 `stock_daily_prices` 的已完成交易日集合计算，不使用自然日倒推，也不删除原始 K 线。
- 仅删除已确认完全冗余且排序方向一致的索引；不会因为“看起来相似”删除普通查询索引。比如带 `DESC` 的最新记录索引要保留。SQLite `VACUUM` 只在删除完成、市场收盘且没有其他写入进程时执行。

## 备份策略

生产 Data Core 目录只保留一份最新可直接恢复的数据库快照。`run_local_data_skill.py` 的 `ensure_database_backup` 和 `run_retention_maintenance` 会统一处理以下命名：

- 自动快照：`local_quant_<date>_<timestamp>.db` 或 `.db.gz`；
- 手工/修复快照：`local_quant_before_*.db`、`local_quant.*.db` 及历史修复命名；
- 中断复制留下的 `local_quant*.db-wal`、`local_quant*.db-shm`、`local_quant*.tmp` 会清理，不能当作独立备份。

每次有破坏性维护前都先创建新的快照，并执行 SQLite `PRAGMA quick_check`。维护期间短暂保留新旧两个回滚点；清理成功且主库再次 `quick_check=ok` 后，才收敛为一份最新快照。旧快照先移入系统废纸篓或由用户确认后再清空，便于恢复误删；文件移入废纸篓不会立即释放磁盘空间。

## 执行方式

脚本默认只读盘点，不会改数据库：

```bash
cd /Users/deserce/Documents/项目/EvoAlpha-OS/backend
/opt/anaconda3/bin/python3 scripts/optimize_local_storage.py
```

收盘后，先让盘中监控自然退出，并暂时停止 EvoAlpha 后端（只停止本项目，不动其他项目）；确认 `lsof` 不再显示数据库/`-wal`/`-shm` 被占用且 Data Core 锁空闲时，才允许应用清理：

```bash
/opt/anaconda3/bin/python3 scripts/optimize_local_storage.py --apply --vacuum
```

脚本复用 `data/.daily_data_workflow.lock`，不创建第二套调度器；锁冲突、盘中时间或仍有进程打开数据库时直接退出。删除使用 10,000 行一批并逐批提交，避免长事务占满内存。`--vacuum` 会重写整个 SQLite 文件，必须预留额外磁盘并在盘后执行；完成后再启动 EvoAlpha 后端并复测接口。

日常无人值守仍由本地无大模型调度器负责。存储优化脚本作为受控的盘后维护命令使用，不能在盘中任务、数据采集或 RPS/MRGC 计算进行时并发运行。

## Git 策略

- 保留 Git；先用当前工作树/提交记录保护代码和清理逻辑。
- 不在脏工作树上做历史重写，不使用 `filter-repo` 或删除远端历史。
- 收盘后确认没有 Git 操作和构建进程，再运行轻量的：

  ```bash
  git gc --prune=14.days.ago
  git count-objects -vH
  ```

- `frontend/node_modules`、`.next`、临时构建目录和运行时数据应由 `.gitignore` 排除；清理 Git 对象不会删除工作树中用户未提交的改动。

## 恢复

1. 停止当前 EvoAlpha Data Core 写入任务，确认锁空闲。
2. 将最新 `local_quant_*.db` 快照复制为 `backend/data/local_quant.db`，不要直接使用 `.db-wal`/`.db-shm` 旁车文件。
3. 执行 `PRAGMA quick_check`，再运行现有 health、RPS 和 MRGC 验证。
4. 只有质量门通过后，才重新启动本项目服务；不停止或修改其他项目的服务。

## 资源预算与验收

- 维护期间单批最多 10,000 行；不启动额外常驻进程，不调用 LLM，不新增定时器。
- 验收至少记录：数据库文件/WAL 体积、可用磁盘、`quick_check`、保留窗口起止日期、删除行数、重复索引数、RPS/MRGC 日期一致性和 API 响应。
- 若可用磁盘低于安全阈值，先停止 `VACUUM`，保留快照并报告，不通过扩大并发或删除原始行情来换取空间。
