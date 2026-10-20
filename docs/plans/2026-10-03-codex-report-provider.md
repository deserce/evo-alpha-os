# 本地 Codex CLI 日报解读接入方案

用户目标：统一日报解读改用本机 Codex 已登录 ChatGPT 的模型；三个视图共享输出，邮件继续仅预览。已核验 Codex CLI 0.160.0、ChatGPT 登录、本机配置 gpt-6.1-sol / low。本地 CLI 发起请求，推理仍由模型服务完成，不是离线模型。

## 实现与文件范围

阶段1：新增 backend/quant_engine/watch/codex_report_provider.py 与 tests/test_codex_report_provider.py。使用 exec、stdin事实、JSON Schema、临时空目录、read-only/ephemeral、隔离用户工具配置；显式复用登录认证且不读取/复制凭证。禁止模型调用工具，返回结构化总结/行业/个股解释。默认沿用已核验模型和低推理，参数可配置。

阶段2：改 backend/quant_engine/watch/mrgc_report_ai.py 与 app/core/config.py，以及对应AI/API tests，选择Codex为日报provider；模型/提供方进入缓存匹配，保留事实校验、非阻塞单实例锁与30天/10MB缓存。调用失败、未登录、额度或超时继续展示规则事实；无循环重试、无新增后台常驻进程。成功结果共用问助手、完整日报、邮件预览。

阶段3：改 frontend/src/components/stock/MrgcDailyReportPanel.tsx的生成超时/进度与模型标识。Codex一次最长90秒、前端留足处理时间；规则GET不延长。进程使用有界输出、独立进程组、超时终止并回收所有子进程，临时文件及时清理。

## 风险与验证

一次真实日报模型验证会消耗现有Codex账户用量；不自动发送邮件、不写行情数据库。先mock验证成功/畸形JSON/未知股票引用/未登录或失败/超时清理/缓存幂等与并发拒绝，再真实点击单日生成，核对模型标识、三视图一致、重读不重复调用。检查进程退出、RSS/CPU/输出文件上限。前端独立目录生产预构建后原子切换，失败回滚。

当前只完成只读核验与方案，尚未改实现或发起真实模型请求。按照项目AGENTS.md复杂任务先批准方案后实施。

## 用户取消接入（2026-10-03）

用户决定不直接接入本地 Codex，继续之前规则日报处理并留档，未来接在线API。已移除Codex运行时适配器、调用配置及相关测试，恢复原有可选GLM/规则分析接口，生产恢复接入前已验证的统一三组版本。取消期间不存在本地Codex生成进程。此前只验证了短输入结构化调用；完整输入曾超时，不能宣称完整日报模型接入已验收。短输出方案作为设计记录保留，不再执行。

恢复验收：相关后端 48 项测试通过，原生产包已恢复；规则报告及邮件预览保存至 `backend/data/report_archives/2026-09-30.{json,html}`。未发送邮件。
