# nanobot 的门户托管登录

浏览器从门户打开应用时，门户的同源 HTML 网关注入 `portal-instance-id="nanobot:<uid>"`、`portal-app="nanobot"` 与 `portal-url="http://localhost:8760/"` 三个 meta。URL 必须为不含用户名 / 密码的绝对 HTTP(S) 地址。缺省元数据时，独立运行的原有密码和短期 token 流程不变；部分或无效元数据不能降级为独立登录。

门户模式仍使用应用签发的短期 API / WebSocket token。应用 bootstrap secret 只由门户代理在服务端注入，前端不读取、使用或保存 URL / localStorage 中的 bootstrap secret，也不把门户密码传给应用。

每次受管理的同源 HTTP 请求发送 `X-Portal-Expected-Instance`，bootstrap 和 `/webui/terminal` 探测不能带入浏览器旧密码。网关所有应用 API 响应须包含匹配的 `X-Portal-Instance-ID`。未登录的 401 / 403 可以没有响应实例头；前端显示返回门户入口。旧标签获得 409 或另一实例响应时会停止使用客户端并要求从门户重开。正常同实例 API 的 403 权限错误仍由原调用方处理。

门户 WebSocket URL 必须使用当前应用页面的 origin 与对应 ws / wss 协议，并带 `portal_instance` 查询参数。前端拒绝向其他 origin 发送请求或 token，并禁止 HTTP 自动跟随重定向；门户反代负责校验登录 cookie、实例 ID、Origin 以及会话过期时关闭存量 WebSocket。门户模式的退出动作返回门户，不使用 GET 调用退出接口；实际门户退出沿用其 CSRF 保护流程。

这些标记只绑定当前 HTML 文档，不将门户用户配置或工作区导入应用。每用户每应用独立实例、凭据和工作区的隔离由门户服务端负责；本改动不更改科学层、后台认证或运行中部署。

验证覆盖独立模式、门户无密码 bootstrap、terminal 探测、旧账号响应 / 409、会话过期、跨 origin 拒绝，以及 WebSocket 实例绑定。这里只描述代码合同，不代表已部署实例的端到端验收。
