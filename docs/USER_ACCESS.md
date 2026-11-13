# 用户管理、共享 Watchlist 和我的自选

启用账户功能前，保持 Cloudflare Access 的现有单人白名单。后端环境需要同时配置以下变量。

| 变量 | 用途 |
|---|---|
| `EVOALPHA_ACCESS_ISSUER` | Access 团队域名，格式为 `https://<team>.cloudflareaccess.com` |
| `EVOALPHA_ACCESS_AUDIENCE` | 当前 Access 应用的 AUD |
| `EVOALPHA_ADMIN_EMAIL` | 初始化空账户库时的管理员邮箱 |
| `EVOALPHA_USERS_DB` | 独立 SQLite 文件绝对路径，父目录需已存在 |

四项均未设置时，业务保持单人部署行为。设置任意一项后，业务 API 开始校验身份，请一次性提供全部变量。原域名需把 Access 签发的 JWT 透传到 Next.js 的 API 代理。启用后，通过现有域名确认 `/api/v1/users/me` 返回管理员，验证设置菜单的「用户管理」和导航的「我的自选」。切换账户前退出 Access 并重新加载页面。

管理员可在用户管理页预授权、启用或停用普通账户。共享 Watchlist 由管理员维护。「我的自选」按账户保存个人分组和股票代码，无独立后台任务、行情采集或邮件投递。账户库需要纳入一致性备份。

当前 Portfolio 和系统管理 API 仅管理员可用。个人 Portfolio、研究产物和逐户邮件仍需单独完成数据归属改造。已有 Watchlist 监控和邮件继续使用现有的正式配置。邀请前保留 Cloudflare 单人策略。
