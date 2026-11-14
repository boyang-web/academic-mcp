# 架构

主入口是 `gateways/whu`，而非三个并列数据库插件。注册表为入口标明 `primary` 或 `supplementary`，CLI 与 MCP instructions 均公开这一角色。

`whu_search` 从珞珈页面实际发出的 PNX 响应读取元数据，结合页面上的真实链接生成 `access_routes` 和 `preferred_url`。路径规划将图书馆链接排在出版商直链之前，不伪造代理 URL，不把其他站点上的副本算作目标平台成功。

`whu_list_databases` 读取官网公开的常用数据库导航配置；`whu_open_database` 只打开其中唯一匹配的真实条目。该导航不是完整订购清单，存在条目也不能证明当前账号具有访问权。

`whu_access_plan` 对已有 URL 给出下一步操作。识别到知网或国泰安时可附带补充直连工具，但不自动转移登录凭据、切换身份或触发国泰安配额消耗。

## 会话与失败

WHU 浏览器的机构认证、中转和出版商访问共享一个本地 profile。知网、国泰安各自独立。进程锁避免多个 MCP 会话同时驱动同一 profile。人工认证后会话可保存并在下次启动恢复；旧 `luojia` profile 在未创建 `whu` profile 时直接复用。

普通调用和下载都有总时限，覆盖等待串行锁、浏览器启动、网络与解析。超时返回不等待可能很慢的取消清理。HTTP 状态、认证页、验证码以及部分返回 200 的出版商错误页分别识别，不能报告为零结果或全文成功。

PDF 优先流式 HTTP 下载；仅在当前页面同源且返回拒绝/非 PDF 时尝试正常浏览器 fetch。保留身份校验与 CORS，不绕过网站控制。两条路径均检查大小、PDF 文件头与 SHA256，并以不覆盖目标文件的方式发布文件。

## 扩展

新增机构入口放在 `gateways/<institution>`；新增专库直连放在 `connectors/<database>`。两者使用同一 `ProviderRuntime` / `ToolSpec` 契约，通过注册表或 `academic_data_mcp.providers` entry point 挂载。公共浏览器代码不依赖任何具体 connector。

扩展必须报告实际通路和权限状态。公开元数据检索、公开副本下载、机构授权下载应分别记录，不能互相替代。
