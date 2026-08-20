# Yuxi + ai-mcp-gateway 企业 MCP 接入与身份权限设计

> 适用场景：Yuxi 智能体需要接入 WMS、ERP、OA、MES 等企业业务系统，同时未来允许 OpenClaw、客服 Agent、财务 Agent 等其他 Agent 平台复用同一套 MCP 能力。

---

## 1. 设计目标

当前企业内部多个系统都以钉钉作为统一身份入口，因此可以将 **钉钉 userId** 作为企业 Agent 体系中的统一“用户身份标识”。

整体目标：

1. Yuxi 不直接调用各业务系统 REST API，而是通过 MCP 统一接入。
2. `ai-mcp-gateway` 作为公司统一 MCP Gateway，负责 MCP 路由、鉴权、权限、审计和凭据治理。
3. WMS、ERP、OA、MES 等系统分别通过独立 MCP Server 进行业务能力适配。
4. 用户身份在整个调用链中保持一致。
5. 不允许仅凭一个 `userId` 直接访问 Gateway。
6. 权限必须同时考虑用户、Agent、Client、Tool 和业务数据范围。
7. Yuxi 当前先作为主要 Control Plane，Gateway 从第一天开始按独立服务设计。
8. 后续其他 Agent 平台也可以在完成身份认证后复用同一个 Gateway。

---

# 2. 总体架构

```text
                              钉钉
                               │
                         DingTalk userId
                               │
             ┌─────────────────┼─────────────────┐
             │                 │                 │
             ▼                 ▼                 ▼
           Yuxi             OpenClaw          其他 Agent
             │                 │                 │
             │ userId          │ userId          │ userId
             │ agentId         │ agentId         │ agentId
             │ clientId        │ clientId        │ clientId
             └─────────────────┼─────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    ai-mcp-gateway    │
                    │                      │
                    │ Client Authentication│
                    │ User Identity        │
                    │ Agent Authorization  │
                    │ Tool Authorization   │
                    │ Data Scope           │
                    │ Credential           │
                    │ Rate Limit           │
                    │ Audit / Trace        │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
           WMS MCP          ERP MCP          OA MCP
              │                │                │
              ▼                ▼                ▼
             WMS              ERP              OA
```

核心定位：

```text
Yuxi
= Agent Platform / Control Plane

ai-mcp-gateway
= 企业统一 MCP Data Plane

xxx-mcp
= 具体业务系统能力适配层
```

---

# 3. Yuxi 与 ai-mcp-gateway 的职责边界

## 3.1 Yuxi 负责

Yuxi 继续负责业务管理和控制面能力：

```text
用户
部门
角色
Agent
知识库
Skill

Agent 权限
MCP 配置管理
Tool 权限配置
数据权限配置

管理后台
```

Yuxi 可以继续使用现有 PostgreSQL。

Yuxi 内部第一阶段保留权限模块：

```text
backend/package/yuxi/permissions/
```

新增 MCP 权限逻辑，例如：

```text
backend/package/yuxi/permissions/
├── resource_permission.py
└── mcp_permission.py
```

---

## 3.2 ai-mcp-gateway 负责

Gateway 不负责 Yuxi 的用户业务。

它主要负责：

```text
Client Authentication
用户身份上下文
Agent 身份
MCP Server Registry
MCP 路由
Tool 鉴权
Credential Resolution
限流
审计
Trace
调用统计
```

原则：

> ai-mcp-gateway 不直接查询 Yuxi 的 users、roles、departments 等业务表。

Gateway 如果需要权限判断，通过 Yuxi 的内部 Permission API 获取结果。

---

## 3.3 MCP Server 负责

例如：

```text
wms-mcp
erp-mcp
oa-mcp
mes-mcp
```

这些 MCP Server 只负责：

> 将业务系统 API 转换成适合 Agent 调用的 MCP Tool。

例如 `wms-mcp`：

```text
query_inventory
query_material
query_order
create_order
cancel_order
transfer_inventory
```

不要把几十个业务系统全部塞进一个巨大的：

```text
business-mcp
```

更推荐：

```text
WMS → wms-mcp
ERP → erp-mcp
OA  → oa-mcp
MES → mes-mcp
```

---

# 4. Yuxi 仓库建议目录

结合 Yuxi 当前项目结构，建议：

```text
Yuxi/
│
├── backend/
│   ├── package/
│   │   └── yuxi/
│   │       │
│   │       ├── agents/
│   │       │   └── mcp/
│   │       │       └── service.py
│   │       │
│   │       ├── permissions/
│   │       │   ├── resource_permission.py
│   │       │   └── mcp_permission.py
│   │       │
│   │       ├── repositories/
│   │       ├── services/
│   │       └── storage/
│   │
│   ├── server/
│   │   └── routers/
│   │       ├── mcp_router.py
│   │       └── mcp_auth_router.py
│   │
│   └── test/
│
├── services/
│   │
│   ├── ai-mcp-gateway/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── README.md
│   │   │
│   │   ├── src/
│   │   │   └── ai_mcp_gateway/
│   │   │       ├── main.py
│   │   │       │
│   │   │       ├── auth/
│   │   │       │   ├── client_auth.py
│   │   │       │   └── permission_client.py
│   │   │       │
│   │   │       ├── routing/
│   │   │       ├── registry/
│   │   │       ├── policy/
│   │   │       ├── credential/
│   │   │       ├── audit/
│   │   │       └── tracing/
│   │   │
│   │   └── tests/
│   │
│   └── mcp-servers/
│       ├── wms-mcp/
│       ├── erp-mcp/
│       ├── oa-mcp/
│       └── mes-mcp/
│
├── web/
├── docker/
└── docker-compose.yml
```

---

# 5. Yuxi 当前 MCP 模块的定位

Yuxi 当前已有：

```text
backend/package/yuxi/agents/mcp/
```

这部分不要改成 Gateway。

它继续负责：

> Yuxi 作为 MCP Client，获取和调用 MCP Tool。

即：

```text
Yuxi Agent
   │
   ▼
Yuxi MCP Client
   │
   ▼
ai-mcp-gateway
```

原来如果是：

```text
Yuxi
 ├── WMS MCP
 ├── ERP MCP
 └── OA MCP
```

后续调整成：

```text
Yuxi
  │
  ▼
ai-mcp-gateway
  │
  ├── WMS MCP
  ├── ERP MCP
  └── OA MCP
```

---

# 6. 用户身份设计

## 6.1 企业用户主身份

如果公司内部各系统基本都通过钉钉登录，可以统一：

```text
Enterprise User Identity
=
DingTalk userId
```

例如：

```text
DingTalk userId = 07651234
```

在各层统一表达为：

```text
Yuxi              user_id = 07651234
Agent Runtime      user_id = 07651234
MCP Gateway        user_id = 07651234
Audit              user_id = 07651234
```

如果 Yuxi 当前：

```text
users.uid
```

本身就是钉钉 userId，那么可以直接复用。

---

# 7. 一个非常重要的原则

## userId 是身份标识，不是认证凭证

错误方式：

```http
POST /mcp/wms
X-User-Id: 07651234
```

然后 Gateway 直接相信。

这样攻击者只需要修改：

```text
X-User-Id
```

就可能冒充其他用户。

因此：

> Gateway 必须同时验证“哪个受信任的 Client 正在代表这个用户调用”。

---

# 8. 两类身份

企业 MCP Gateway 应区分：

```text
Human Identity
+
Client / Workload Identity
```

例如：

```text
Human Identity

user_id = 07651234
```

以及：

```text
Client Identity

client_id = yuxi
```

一次调用的完整上下文：

```json
{
  "user_id": "07651234",
  "client_id": "yuxi",
  "agent_id": "inventory-agent",
  "run_id": "run_xxx",
  "trace_id": "trace_xxx"
}
```

---

# 9. Client 身份认证

第一阶段推荐采用简单的 Service-to-Service Authentication。

例如：

```text
Yuxi
  └── client_id = yuxi

OpenClaw
  └── client_id = openclaw

客服 Agent
  └── client_id = customer-service-agent
```

每个调用方有独立凭据：

```text
client_id
client_secret
```

示例：

```http
Authorization: Bearer <YUXI_GATEWAY_SERVICE_TOKEN>

X-User-Id: 07651234
X-Agent-Id: inventory-agent
X-Run-Id: run_xxx
X-Trace-Id: trace_xxx
```

Gateway 首先验证：

```text
这个请求是不是 Yuxi 发来的？
```

验证成功以后，才信任：

```text
user_id = 07651234
```

---

# 10. 为什么第一版不一定需要“每个用户生成 5 分钟 Token”

针对当前公司内部环境，如果主要是：

```text
Yuxi Backend
        ↓
ai-mcp-gateway
```

可以先使用：

```text
Service Authentication
+
User Context
```

也就是：

```text
服务身份
证明“这个请求来自 Yuxi”

+

userId
说明“Yuxi 当前代表哪个用户”
```

这样比给每个用户额外生成 Gateway Token 更简单。

后续如果 Gateway 接入多个独立平台，可以升级为：

```text
OIDC
JWT Assertion
mTLS
OAuth2 Client Credentials
```

但调用上下文模型不需要改变。

---

# 11. Gateway 权限模型

最终权限不能只看用户。

推荐：

```text
最终权限
=
User Permission
∩
Client Permission
∩
Agent Permission
∩
Tool Permission
∩
Data Scope
```

例如：

```text
张三
├── WMS 查询权限             ✅
├── 武汉仓数据权限           ✅
└── 深圳仓数据权限           ❌

Yuxi
└── 允许访问 WMS MCP         ✅

inventory-agent
├── query_inventory          ✅
├── query_order              ✅
└── adjust_inventory         ❌
```

最终：

```text
query_inventory(武汉仓)
→ ALLOW

query_inventory(深圳仓)
→ DENY

adjust_inventory(武汉仓)
→ DENY
```

---

# 12. Tool 级权限

不要只做：

```text
用户是否可以访问 WMS MCP？
```

而应该做到 Tool 粒度：

```text
WMS MCP

inventory.query
inventory.adjust

order.query
order.create
order.cancel

warehouse.query
warehouse.transfer
```

例如：

| Tool | 普通用户 | 仓库管理员 | 主管 |
|---|---:|---:|---:|
| inventory.query | ✅ | ✅ | ✅ |
| inventory.adjust | ❌ | ✅ | ✅ |
| order.query | ✅ | ✅ | ✅ |
| order.create | ❌ | ✅ | ✅ |
| order.cancel | ❌ | ❌ | ✅ |

---

# 13. 数据权限 Data Scope

Tool 权限只能解决：

```text
张三能不能调用 inventory.query？
```

还需要继续判断：

```text
张三能查询哪些仓库？
```

例如：

```text
张三

武汉仓      ✅
深圳仓      ❌
上海仓      ❌
```

因此最终权限链：

```text
Identity
   ↓
RBAC
   ↓
Client Permission
   ↓
Agent Permission
   ↓
MCP Permission
   ↓
Tool Permission
   ↓
Data Scope
   ↓
Business API
```

---

# 14. Yuxi Permission API

Gateway 不直接查 Yuxi 数据库。

建议 Yuxi 增加内部接口：

```text
backend/server/routers/mcp_auth_router.py
```

提供：

```http
POST /api/internal/mcp/authorize
```

请求：

```json
{
  "user_id": "07651234",
  "client_id": "yuxi",
  "agent_id": "inventory-agent",
  "server": "wms",
  "tool": "query_inventory",
  "resource": {
    "warehouse_id": "WH001"
  }
}
```

返回：

```json
{
  "allowed": true,
  "data_scope": {
    "warehouse_ids": [
      "WH001"
    ]
  }
}
```

拒绝：

```json
{
  "allowed": false,
  "reason": "NO_WAREHOUSE_PERMISSION"
}
```

---

# 15. 为什么 Gateway 不直接查询 Yuxi 数据库

不推荐：

```text
ai-mcp-gateway
      ↓
SELECT users
SELECT roles
SELECT permissions
```

否则会产生：

```text
Yuxi 修改表结构
        ↓
Gateway 跟着修改

Yuxi 修改 RBAC
        ↓
Gateway 跟着修改
```

推荐：

```text
ai-mcp-gateway
       │
       ▼
Permission API
       │
       ▼
Yuxi Permission Module
       │
       ▼
PostgreSQL
```

这样未来：

```text
Yuxi 内部 Permission Module
```

如果变成：

```text
独立 Permission Service
```

Gateway 不需要改变权限调用逻辑。

这就是：

> 先模块化，后服务化。

---

# 16. Gateway MCP 路由

Yuxi 可以继续保存 MCP Server 配置。

例如：

```text
wms
→ http://ai-mcp-gateway:9000/mcp/wms

erp
→ http://ai-mcp-gateway:9000/mcp/erp

oa
→ http://ai-mcp-gateway:9000/mcp/oa
```

Gateway 内部：

```text
/mcp/wms
   ↓
wms-mcp:8000/mcp

/mcp/erp
   ↓
erp-mcp:8000/mcp

/mcp/oa
   ↓
oa-mcp:8000/mcp
```

这样 Yuxi 现有 MCP Client 的修改范围比较小。

---

# 17. 业务系统身份传递

## 场景 A：下游业务系统也使用钉钉 userId

这是最理想的情况。

```text
DingTalk userId
      ↓
Yuxi
      ↓
Gateway
      ↓
WMS MCP
      ↓
WMS
```

WMS 可以继续基于：

```text
userId = 07651234
```

判断业务数据权限。

例如：

```text
07651234

武汉仓   ✅
深圳仓   ❌
```

---

## 场景 B：业务系统用户 ID 不同

例如：

```text
DingTalk userId
07651234

WMS User
WMS00128
```

则增加映射：

```text
external_identity
```

示例：

```text
user_id
system_code
external_user_id
status
```

例如：

```text
07651234
wms
WMS00128
enabled
```

---

## 场景 C：老系统只有 API Key / Service Account

链路：

```text
张三
 ↓
Yuxi
 ↓
Gateway
 ↓
权限检查
 ↓
WMS MCP
 ↓
Service Account
 ↓
老 WMS
```

虽然老 WMS 不知道真实调用人是谁，但 Gateway 必须完整记录：

```text
user_id
client_id
agent_id
tool
arguments
resource_scope
result
trace_id
timestamp
```

---

# 18. Credential 管理原则

不要让：

```text
Agent
LLM
Prompt
```

直接看到：

```text
ERP Token
WMS API Key
Database Password
Client Secret
```

凭据应该由：

```text
ai-mcp-gateway
```

或专门 Credential Manager 管理。

推荐结构：

```text
Agent
   ↓
Gateway
   ↓
Credential Resolution
   ↓
WMS Credential
   ↓
WMS MCP
```

---

# 19. 不要把 Yuxi Token 一直透传到业务系统

错误：

```text
Yuxi Login Token
      ↓
Gateway
      ↓
WMS
```

正确：

```text
用户
 │
 │ Yuxi 登录身份
 ▼
Yuxi
 │
 │ userId + trusted client identity
 ▼
Gateway
 │
 │ 下游业务凭据 / 下游用户映射
 ▼
WMS MCP
 │
 ▼
WMS
```

不同安全域使用不同凭据。

---

# 20. Audit / Trace

每次 MCP Tool 调用至少记录：

```text
timestamp
user_id
client_id
agent_id
run_id
trace_id

mcp_server
tool_name

arguments
resource_scope

permission_result
result
latency
error
```

示例：

```text
时间：
2026-08-13 10:30

用户：
07651234

Client：
yuxi

Agent：
inventory-agent

MCP：
wms-mcp

Tool：
query_inventory

参数：
material_id=R13574700086
warehouse_id=WH001

权限：
ALLOW

结果：
SUCCESS

Trace：
trace_123456
```

---

# 21. 自动 Agent / 后台任务

未来可能存在无人值守 Agent：

```text
凌晨库存巡检 Agent
定时财务检查 Agent
自动告警 Agent
```

此时没有真实钉钉用户。

不要伪造：

```text
user_id = 某个管理员
```

统一身份模型最好支持：

```text
principal_type = user
user_id = DingTalk userId
```

或者：

```text
principal_type = service
service_id = inventory-monitor-agent
```

因此长期身份模型建议：

```text
Principal

├── Human
│   └── DingTalk userId
│
└── Machine
    └── Service / Agent Identity
```

---

# 22. 多 Agent 平台共享 Gateway

如果公司其他 Agent 平台也都通过钉钉登录，就可以共享同一个 Gateway：

```text
Yuxi ────────────────┐
OpenClaw ────────────┤
客服 Agent ──────────┤
财务 Agent ──────────┼──→ ai-mcp-gateway
研发 Agent ──────────┤
其他 Agent ──────────┘
```

Gateway 不需要理解每个平台自身的登录系统。

Gateway 只统一处理：

```text
user_id
client_id
agent_id
run_id
trace_id
```

---

# 23. 推荐的 Gateway Request Context

内部统一定义：

```python
class GatewayRequestContext:
    principal_type: str

    user_id: str | None
    service_id: str | None

    client_id: str
    agent_id: str | None

    run_id: str | None
    trace_id: str

    tenant_id: str | None
    department_id: str | None
```

第一阶段如果公司没有多租户，可以暂时不使用：

```text
tenant_id
```

但字段可以预留。

---

# 24. 第一阶段 MVP

第一阶段不要过度设计。

建议只实现：

```text
1. ai-mcp-gateway 独立服务
2. Yuxi → Gateway 服务认证
3. 钉钉 userId 透传为用户身份上下文
4. client_id
5. agent_id
6. MCP 路由
7. Tool 白名单
8. Yuxi Permission API
9. Audit Log
10. WMS MCP 作为第一个真实业务 MCP
```

架构：

```text
钉钉
 ↓
Yuxi
 ↓
ai-mcp-gateway
 ↓
wms-mcp
 ↓
WMS
```

---

# 25. 第二阶段

加入：

```text
Tool RBAC
Data Scope
Client Permission

ERP MCP
OA MCP
MES MCP

Credential Manager
Redis 权限缓存
Rate Limit

Trace
Metrics
```

---

# 26. 第三阶段

Gateway 从 Yuxi 的附属基础设施升级为公司级 AI Tool Platform：

```text
Yuxi
OpenClaw
客服 Agent
财务 Agent
研发 Agent
第三方 Agent
       │
       ▼
统一 ai-mcp-gateway
```

进一步增加：

```text
OIDC
OAuth2
mTLS
JWT Assertion

Token Exchange
ABAC
Policy Engine

Credential Vault
完整审计

MCP Server Registry
Tool Registry
Usage Dashboard
```

---

# 27. 最终推荐架构

```text
                               钉钉
                                │
                         DingTalk userId
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
        Yuxi                 OpenClaw             其他 Agent
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                │
                         Client Identity
                                +
                          User Identity
                                │
                                ▼
                   ┌────────────────────────┐
                   │     ai-mcp-gateway     │
                   │                        │
                   │ Authentication         │
                   │ Authorization          │
                   │ MCP Routing            │
                   │ Tool RBAC              │
                   │ Data Scope             │
                   │ Credential             │
                   │ Rate Limit             │
                   │ Audit                  │
                   │ Trace                  │
                   └───────────┬────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
       wms-mcp              erp-mcp              oa-mcp
          │                    │                    │
          ▼                    ▼                    ▼
         WMS                  ERP                   OA
```

---

# 28. 核心架构原则

### 原则 1

> MCP 是业务能力适配层，不应该成为权限绕过层。

---

### 原则 2

> 钉钉 userId 用于标识“谁”，Client Credential 用于证明“谁正在代表这个用户调用 Gateway”。

---

### 原则 3

> Gateway 不直接依赖 Yuxi 用户、角色、部门等数据库表。

---

### 原则 4

> 权限判断应该至少做到 Tool 粒度，并逐步增加 Data Scope。

---

### 原则 5

> 用户权限、Client 权限、Agent 权限、Tool 权限和数据权限取交集。

```text
Effective Permission
=
User
∩ Client
∩ Agent
∩ Tool
∩ Data Scope
```

---

### 原则 6

> Agent 不直接持有业务系统 Secret。

---

### 原则 7

> 所有高风险 Tool 必须可审计。

对于以下操作：

```text
查询
新增
修改
删除
审批
付款
转账
```

建议按照风险等级逐步增加：

```text
自动执行
强权限校验
二次确认
人工审批
```

---

# 29. 当前项目最推荐的落地结论

当前不需要立即拆很多微服务。

先采用：

```text
Yuxi
├── 用户
├── Agent
├── 权限模块
├── MCP 管理页面
└── Permission API

services/
├── ai-mcp-gateway
└── mcp-servers/
    └── wms-mcp
```

部署：

```text
Docker Compose

yuxi-backend
yuxi-web
postgresql
redis

ai-mcp-gateway
wms-mcp
```

第一版 Yuxi 与 Gateway 可以同仓库开发，但：

> `ai-mcp-gateway` 从第一天开始必须是独立进程、独立容器、独立 API 边界。

这样未来如果 Gateway 需要升级成公司统一基础设施，只需要将：

```text
services/ai-mcp-gateway
```

整体拆成独立仓库，不需要重新设计 Yuxi。

---

# 30. 一句话总结

> **钉钉负责统一“人”的身份，Yuxi/其他 Agent 平台负责确认当前用户并代表用户发起调用，ai-mcp-gateway 负责统一验证调用方、执行权限治理和路由 MCP，WMS/ERP/OA MCP 负责适配具体业务能力。**

最终：

```text
DingTalk Identity
        ↓
Agent Platform
        ↓
ai-mcp-gateway
        ↓
Business MCP
        ↓
Business System
```

这套设计既能满足当前 Yuxi 接业务系统，也为后续多个 Agent 平台共享企业 MCP 能力预留了完整扩展空间。
