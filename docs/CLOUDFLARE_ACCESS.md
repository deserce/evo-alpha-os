# EvoAlpha 外网接入方案与验收

## 配置目标

- 域名：`alpha.deserce.dpdns.org`。
- Access Allow 策略：仅 `deserce@gmail.com`；不得使用 Everyone 或 Bypass。
- Access 应用覆盖整个主机名，路径留空，包含 `/api` 和静态资源。
- 登录采用邮箱一次性验证码，建议会话有效期 1 小时。
- 唯一 HTTP 源站：`http://127.0.0.1:3000`，由 Next.js 代理 API 到 `127.0.0.1:8001`。
- 启用 Tunnel 的 Protect with Access，绑定对应应用 AUD，由 cloudflared 校验 JWT。
- 不配置 SSH、RDP、TCP、私网网段路由或其它源站；不使用临时 Quick Tunnel。

## 接入顺序

1. 检查域名当前 DNS、Tunnel 和 Access 应用，避免覆盖其它服务。
2. 创建并核验覆盖全站的 Access 应用与精确邮箱白名单。
3. 备份本项目 frontend/backend LaunchAgent。前端增加 `--hostname 127.0.0.1`，后端将 `--host 0.0.0.0` 改为 `--host 127.0.0.1`；逐个重启并验证，不改其它项目。
4. 创建仅包含上述 HTTP 源站的 Tunnel，开启 Protect with Access，未知主机名拒绝访问；最后连接 DNS 与启动连接器。
5. Tunnel 以当前用户单实例运行，不安装 root 常驻服务；凭证文件仅当前用户可读，不进 Git、不输出到日志。
6. 日志置于 `~/Library/Logs/EvoAlpha`，采用有界轮转（单文件不超过 20 MB）；禁止 debug 及请求正文日志。

## 必须完成的验收

- 未登录访问 `/`、`/a/`、`/api/v1/market/dates` 都进入 Access 验证或被拒绝，不能返回业务内容。
- 伪造 `Cf-Access-Jwt-Assertion` 无法通过；无匹配邮箱的登录不能进入业务页面。
- 允许邮箱可登录，并完成只读页面和 API 检查；不向正式库写测试数据。
- 检查全部 DNS 路由，不能有指向本机公网 IP、绕过 Access 的替代入口。
- 验证本项目仅监听回环地址；没有新增路由器端口映射、SSH/RDP 或私网访问能力。
- 验证单实例、RSS、CPU/线程、句柄、网络连接和日志增长；不重复运行数据采集和计算。
- 验证撤销策略或停止本项目 Tunnel 后外网不能进入；登录失效后不能继续读取 API。

## 安全边界

Access 与 JWT 校验减少未授权访问，但不等于操作系统沙箱，无法承诺零漏洞风险。连接器仍是本机进程；登录邮箱失陷或应用漏洞仍可能影响应用及其用户权限可访问的资源。若需要强隔离，应将网页服务迁移到独立虚拟机或服务器，并单独设计数据同步，不能把 Tunnel 本身视为隔离环境。

## 2026-11-11 准备结果

- 已安装 Homebrew 官方 `cloudflared` 2026.10.0，由用户级 LaunchAgent `com.evoalpha.cloudflared` 管理。登录时启动，退出后按 30 秒间隔限制重启，凭证文件权限为 `0600`。
- 前端 `3000` 和后端 `8001` 均只监听 `127.0.0.1`。后端 `/health` 和前端代理 `/api/v1/market/dates?limit=1` 均返回 HTTP 200。
- 已核实 Cloudflare Access 应用 `EvoAlpha OS` 覆盖 `alpha.deserce.dpdns.org`，关联 `EvoAlpha owner only` 策略，会话时长 1 小时。
- 已发布 `evoalpha-local` 的专用 HTTP 路由和 DNS CNAME，源站为 `http://127.0.0.1:3000`，已启用 Protect with Access 并绑定应用 AUD。未登录请求首页和行情日期 API 均收到 302，重定向到本账户的 Access 登录页。
- 连接器运行中，本机 `/ready` 返回 200，Cloudflare 就绪连接数为 4。进程实测约 27 MiB RSS，采样 CPU 为 0.0%。诊断端口仅监听 `127.0.0.1:20246`，标准输出和错误不落盘。
- 连接器在线后复验首页、`/a/` 和行情日期 API，匿名请求均跳转至 Access 登录页。邮箱登录后的业务页面和 API 待验收。
