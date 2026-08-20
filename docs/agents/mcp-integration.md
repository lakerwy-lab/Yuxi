# MCP 集成

MCP（Model Context Protocol）是扩展智能体能力的重要方式。系统支持通过管理界面动态配置 MCP 服务器，无需修改代码。

内置 MCP 服务器以代码为事实源：系统启动时会自动补齐缺失项，并用代码中的最新连接与展示字段覆盖数据库定义；是否“已添加”以及工具级禁用列表仍保留数据库状态。

## 企业业务 MCP

会议室和 HR 考勤查询由一个独立 `enterprise-mcp` 网关服务发布。网关监听 8010，并分别挂载 `/mcp/meeting` 与 `/mcp/hr`；同一信任边界内新增业务域时注册新的子应用和稳定路径，不默认新增容器。Yuxi 与该服务分别维护 uv 环境，但都精确锁定 `mcp==1.27.2`；独立服务当前通过 editable path 依赖复用 `yuxi.services.dingtalk_meeting_service`，不得导入 `yuxi.agents.mcp`。

调用身份不来自模型工具参数。API/worker 会从已持久化的 AgentRun 回查真实用户、Agent、线程、来源和钉钉身份，构造不可变调用上下文，并在每次 `tools/call` 前签发 90 秒 Ed25519 Token。meeting 和 HR 实际调用均要求签名 claims 中存在完整钉钉身份，并以 `dingtalk_user_id` 验证主体；模型不能传入或覆盖 userId。`enterprise-mcp` 只持有公钥，在 `tools/list` 与 `tools/call` 两层校验各 endpoint 的 audience 和允许工具集合；发现阶段 Token 不进入可执行工具的连接缓存。

开发环境启用步骤：

1. 在 `.env.local` 配置现有钉钉应用凭据；会议应用有独立凭据时使用 `DINGTALK_MEETING_CLIENT_ID` / `DINGTALK_MEETING_CLIENT_SECRET`。使用 HR endpoint 时另配置 `HR_API_BASE_URL` 与 `HR_API_TOKEN`。
2. 正常执行 `docker compose up -d --build`。`mcp-keygen` 会在 `docker/volumes/yuxi/mcp` 创建或复用 Ed25519 密钥，API/worker 读取私钥，`enterprise-mcp` 只挂载公钥。
3. 新部署会默认添加“企业会议室”和“HR 考勤查询”；已有数据库保留管理员设置的启停状态。Agent 未显式配置 `mcps` 时会使用当前已启用 MCP，也可按 Agent 只选择 `meeting` 或 `hr`。

`enterprise-mcp` 直接访问同一 PostgreSQL 会议表、钉钉开放接口和 HR 内部 API。会议与 HR 调用用户都必须具有完整钉钉身份；HR 永久 Token 只保存在网关环境中，不签入 AgentRun Token、不透传给模型。未配置 HR Token 时只有 HR 工具显式失败，会议 endpoint 与网关健康检查不受影响。未配置独立会议凭据时兼容读取通用钉钉应用凭据。生产环境不发布该服务的宿主机端口。

| 业务域 | 内部 URL | 身份要求 |
|---|---|---|
| 会议室 | `http://enterprise-mcp:8010/mcp/meeting` | 可信钉钉 userId、unionId 与 corpId |
| HR 考勤 | `http://enterprise-mcp:8010/mcp/hr` | 可信钉钉 userId、unionId 与 corpId |

HR endpoint 仅发布 `hr_attendance_sign_records`、`hr_attendance_daily_detail`、`hr_attendance_summary` 三个只读工具。模型只提供起止日期，网关把已验签上下文中的 `dingtalk_user_id` 原样映射为 HR API 的 `ftalkId`，因此不能通过工具参数代查其他员工。

::: warning 当前耦合
当前 path 依赖会安装完整 `yuxi` 依赖树，镜像较大。这是会议领域尚未拆包期间的短期同库复用，不是长期服务边界；上线扩容前应将会议模型、仓储和服务抽成轻量领域包。
:::

## 支持的传输协议

| 协议 | 说明 | 适用场景 |
|------|------|----------|
| Streamable HTTP | 流式 HTTP 连接 | 远程 MCP 服务 |
| SSE | Server-Sent Events | 标准 HTTP 长连接 |
| Stdio | 标准输入输出 | 仅限代码中维护的系统内置 MCP |

## 配置示例

### 远程 MCP 服务

```json
{
    "name": "custom-remote-mcp",
    "transport": "streamable_http",
    "url": "https://example.com/mcp"
}
```

管理接口只允许配置 `streamable_http` 与 `sse` 远程服务。`stdio` 会在 API / worker 容器内启动本地进程，
因此仅允许 `_DEFAULT_MCP_SERVERS` 中代码定义的系统内置 MCP；管理员不能通过接口新增 stdio 服务，
也不能修改内置 MCP 的连接配置。升级前已保存的用户 stdio 配置会被禁用，需要迁移为远程 MCP。

## 添加系统内置 stdio MCP

只有经过代码审查、确实需要在 Yuxi 容器内启动本地进程的 MCP 才应使用 stdio。能够部署为远程服务时，
优先使用 SSE 或 Streamable HTTP，通过管理界面添加即可。

编辑 `backend/package/yuxi/agents/mcp/service.py` 中的 `_DEFAULT_MCP_SERVERS`，新增一个全局唯一的 slug。
下面的包名和版本仅作结构参考，实际提交时应替换为经过审查并固定版本的 MCP 包：

```python
_DEFAULT_MCP_SERVERS = {
    # 已有内置 MCP ...
    "example-mcp": {
        "command": "npx",
        "args": ["-y", "@scope/example-mcp@1.2.3"],
        "transport": "stdio",
        "description": "示例内置 MCP，请替换为真实用途说明",
        "icon": "🧩",
        "tags": ["内置", "示例"],
    },
}
```

常用字段如下：

| 字段 | 要求 |
|------|------|
| `command` | 容器内已安装或明确可用的可执行程序，不接受用户输入 |
| `args` | 固定参数列表；使用包执行器时应固定包版本，不使用动态脚本参数 |
| `transport` | 固定为 `stdio` |
| `description` | 说明 MCP 的具体能力和使用场景 |
| `icon` / `tags` | 管理界面的展示信息 |
| `env` | 仅允许非敏感固定值；密钥不得提交到代码或同步进数据库 |

新增 slug 前应确认数据库和 `_DEFAULT_MCP_SERVERS` 中没有同名项。运行时只信任
`_DEFAULT_MCP_SERVERS` 的固定 slug 白名单；`created_by` 仅用于审计，不能通过复用用户记录或手工修改
`created_by` 来创建内置 MCP。

开发环境会在 API / worker 热重载后的启动阶段调用 `ensure_builtin_mcp_servers_in_db()`；生产部署需要重新
构建并启动 API 与 worker。新内置项首次同步时默认 `enabled=false`，管理员需要在 MCP 管理页中“添加”后
才会进入运行时。后续启动会用代码定义覆盖连接与展示字段，同时保留启用状态和工具禁用列表。

添加后执行一次验证：

```bash
docker compose up -d --build api worker
docker logs api-dev --tail 100
docker logs worker-dev --tail 100
```

确认日志中没有同步异常，并在管理页添加该 MCP，检查能够发现预期工具。验证过程不得执行文件写入、
Shell 命令或其他无关副作用。

::: danger 安全边界
stdio MCP 与在 API / worker 容器内执行程序等价。提交前必须审查可执行程序、依赖来源、固定版本、参数、
网络访问和工具副作用；不得从 HTTP 请求、数据库用户配置或环境中的非受信任内容拼接 `command`、`args`
或 `env`，也不得通过把用户记录改成 `created_by=system` 绕过运行时限制。
:::

## 服务器管理

管理界面使用“添加 / 移除”语义管理 MCP 服务器：

- 已添加：`enabled=true`；远程 MCP 读取数据库中的最新连接配置，内置 stdio MCP 使用代码中的固定连接配置
- 可添加：`enabled=false`，记录保留但不会进入运行时

Agent 配置中的 `mcps` 决定本次运行可使用哪些已添加服务器；未显式配置时使用当前用户可见的全部服务器。普通 MCP 工具对象会按配置哈希做本地缓存；企业 MCP 只缓存不含授权头的工具描述，每次执行重新签发调用令牌。更新服务器配置后会自动使用新的缓存键，不需要重启服务。

## 工具管理

MCP 工具支持粒度控制：管理员可以单独启用或禁用某个 MCP 服务器下的特定工具，实现精细化的权限管理。
