# rag-agent → Yuxi 迁移规划（方向 A）与设计 Token 迁移方案

> 文档日期：2026-08-11
> 状态：设计 Token、钉钉登录、通讯录同步和表单问答对已实现并完成真实验证；源知识库数据导入仍受外部依赖闸门约束。
> 2026-08-11 审查修订：对照 rag-agent 代码逐条核对，修正 3.1（删"启动时同步"+补 departments 加列+保留快照表+3 条红线）、3.2（QA 改"编辑即发布"+返原文改"控制流短路"+砍 revision+不保留 20 短语黑名单）、3.3（补图片 URL 第四种+document_assets 三列唯一键+两张表 ID 格式不同+embedding_model_id 一起导出+csv/json 无存量）、3.5（确认交互改方式 2 ask_user_question+app 级 token 新写+补房间翻页+相对日期解析坑）
> 2026-08-11 代码审查：对照 GPT 已实现代码逐模块审查 + 浏览器实测 + git stash 对比，发现 1 个阻塞性 bug（agent run 失败 CallableSchema）+ 8 个 P0 功能性缺陷 + 若干偏差。详见第七章"已实现代码审查记录"，Checklist 已据实修正。
> 2026-08-11 二次执行审查：发现并修正 6 个阻塞问题——目录快照表模型不完整、钉钉身份缺少 corp 隔离、`create_all` 不能迁移已有表、同步锁不能只靠进程内锁、QA 索引缺少可恢复的一致性边界、知识库导入缺少只读预检闸门；会议预订改为一次用户确认，不增加二次审批。只读 SQL 工具继续延期，不在本轮实现。

### 0.1 本轮执行口径

- **先执行基础能力，再执行数据迁移**：先完成钉钉身份字段/快照模型/同步任务边界，随后才能做通讯录同步；知识库导入必须先生成只读 manifest 和预检报告。
- **不直接改写来源系统**：本轮只允许读取 `D:\Workspace\rag-agent` 的代码和经过授权的源库快照，不对来源 PostgreSQL、MinIO 或生产钉钉数据执行写操作。
- **迁移必须可恢复**：目录同步失败保留上一份有效快照；知识库导入按 KB 分批、可重跑、可校验；Milvus 只允许通过 Yuxi 现有索引链路写入，未经维度/模型/度量校验不得直灌向量。
- **外部依赖是执行闸门**：实际通讯录同步需要钉钉应用权限和 corp 配置；实际知识库导入需要源 PostgreSQL/MinIO 只读连接、备份、目标 embedding 模型和容量预检。缺任一项时只执行 dry-run，不宣称数据已迁移。
- **明确延期**：只读 SQL 不进入本轮，继续独立设计为 Skill；文档/图片分析、统计、问题升级和客服前端本轮已补齐最小可用链路，但与 P0 数据迁移分开验收。

### 0.2 本轮已执行

- [x] 用户模型新增 corp-scoped 钉钉身份字段，并兼容历史 `dingtalk:<unionId>` 账号回填；同一 unionId 在不同 corp 下不会复用账号。
- [x] 新增 `dingtalk_departments`、`dingtalk_user_departments`、`dingtalk_directory_sync_runs` 三张快照/状态表，并在本地 PostgreSQL 验证已落库。
- [x] `ensure_business_schema()` 显式补齐已有 `users` 表字段和唯一索引；定向认证、schema、权限回归测试通过。
- [x] 完成通讯录分页快照、跨进程 advisory lock、失败任务回收、主表增量投影和管理员查询接口；worker 已注册同步任务。
- [x] 完成会议室 Skill/API：app 级 token、房间翻页、一次用户确认、幂等预订、日程补偿删除、取消部分失败状态和确认令牌清理任务。
- [x] 完成 QA 对持久化索引/可恢复任务、Agent 运行前精确命中短路、图片 URL 规范化、转人工记录/通知接口、统计 API 和管理员页面。
- [x] 完成知识库迁移只读 manifest、目标 embedding 预检、checksum 幂等导入入口和管理员页面；导入统一走 Yuxi MinIO/解析/索引链路。
- [x] 已执行真实钉钉通讯录拉取：同步 483 个部门、4954 名成员，快照与主表投影一致。
- [ ] 尚未执行知识库数据导入：仍需源 PG/MinIO 只读快照和 embedding 预检；不直接复制生产数据。

## 一、背景与决策

### 1.1 两个项目的定位差异

| 维度 | Yuxi（目标基座） | rag-agent（业务来源） |
|---|---|---|
| 定位 | 通用智能体**平台** | 企业客服**应用** |
| Agent 编排 | deepagents 0.6.7 + LangChain v1 `create_agent` + 自定义中间件栈 | 手写编排（`agent_service.py` 直接调模型 + 规则路由） |
| 知识库 | 自研 Milvus 向量库 + Neo4j 图谱 + 多租户权限 | RAGFlow + 自研 simplerag（实际内容在 knowledge-server PG/矿量级） |
| 多用户 | superadmin / admin / user 三角色 + 部门 + 资源权限 | 客服坐席（无多租户平台能力） |
| 前端 | Vue 3 + Ant Design Vue v4 + Less | React 19 + antd v6 + Tailwind CSS v4 |
| 认证 | 账号密码 + 通用 OIDC + 钉钉原生 OAuth 适配 | 钉钉原生 OAuth |

### 1.2 决策结论

- **选定方向 A**：以 Yuxi 为基座，把 rag-agent 的**业务功能**迁移到 Yuxi（做成工具 / API / 前端页面）。
- **放弃方向 B**：不再把 Yuxi 的 deepagents 能力往 rag-agent 的 `services/deepagents`（pilot）迁移——那等于在应用里重建平台。
- **唯一反向借鉴点**：rag-agent 的图文混排后处理方案（`[[IMG:...]]` 锚点 + 后处理插图）比 Yuxi 当前的提示词方案更可靠，Yuxi 可借鉴该机制。

---

## 二、方向 A：业务功能迁移规划

### 2.1 rag-agent 功能清单（迁移源）

| 功能 | 代码位置（rag-agent） | 说明 |
|---|---|---|
| 钉钉免登 | `app/api/auth.py`、`dingtalk_auth_service.py` | 钉钉原生 OAuth 免登 |
| 钉钉用户/部门同步 | `app/api/dingtalk_sync.py`、`services/dingtalk_directory_service.py`、`services/dingtalk_client.py` | 通讯录全量快照同步（3 张表） |
| 会议室预订 | `app/api/bookings.py`、`tools/meeting_room_tools.py`、`services/meeting_room_service.py` | 查询/预订会议室（两段式 preview/confirm） |
| 只读 SQL 查询 | `tools/database_tools.py` | **本次暂缓，后续独立设计为 Skill** |
| 知识库问答 | `tools/ragflow_tools.py` + knowledge-server | 检索（**引擎不迁移**，内容迁移） |
| 表单问答对 | `app/api/qa_pairs_admin.py`、`qa_index.py`、`qa_pair_db.py` | 高频问答对维护（发布写索引 + 命中返原文） |
| 问题升级 | `app/api/qa_escalate.py` | 转人工客服 |
| 文档/图片分析 | `tools/document_tools.py` | 附件解析 + VLM 描述 |
| 统计报表 | `app/api/qa_statistics_admin.py` | 问答统计 |
| 管理员增删 | `app/api/knowledge_admin.py`（`kb-admins`） | 管理员提升/撤销（无超级管理员层级） |

### 2.2 迁移映射（功能 → Yuxi 形态）

| rag-agent 功能 | 迁到 Yuxi 的形态 | 工作量 | 优先级 |
|---|---|---|---|
| 知识库内容（IT 手册等文档） | **内容导入 Yuxi 知识库**（Milvus + PG + MinIO，见 3.3） | 中 | P0 |
| 钉钉原生 OAuth 免登 | 复用 Yuxi 登录页 OIDC 入口 + 钉钉 OAuth 适配层（`dingtalk_auth_service.py`） | ✅ 已完成 | P0 |
| 钉钉用户/部门同步 | Yuxi 同步 Service + 手动按钮/定时任务（见 3.1） | 中 | P0 |
| 会议室预订 | **Skill 化**（Python tool 调钉钉 API + 前端确认，见 3.5） | 中 | P1 |
| 表单问答对 / 问题升级 | Yuxi 功能 + 页面（见 3.2） | 中 | P2 |
| 只读 SQL 查询 | **暂缓，后续独立实现为 Skill** | - | 暂缓 |
| 文档/图片分析 | 复用 Yuxi 附件 + OCR/多模态链路 | 中 | P2 |
| 客服前端页面 | Yuxi（Vue）重做，参考 rag-agent 视觉 | 中 | P2 |
| 管理员增删 | **Yuxi 已具备，仅补钉钉身份绑定**（见 3.4） | 小 | P1 |

### 2.3 明确不迁移的部分

| rag-agent 组件 | 结论 | 原因 |
|---|---|---|
| RAGFlow / knowledge-server 知识库引擎 | ❌ 不迁移 | Yuxi 自研 Milvus 知识库有图谱/权限/多租户 |
| 手写 agent 编排 | ❌ 不迁移 | 被 Yuxi deepagents 编排取代 |
| React 前端代码 | ❌ 不迁移 | Yuxi 用 Vue，页面重做 |
| 生产数据（订单/会议室） | ⚠️ 只接入不搬迁 | 通过工具连原有系统（钉钉 API） |

### 2.4 分阶段路线

```

P0（先行）：① 知识库内容导入  ② 钉钉原生 OAuth 免登（已完成）  ③ 钉钉用户/部门同步
P1：④ 钉钉会议室预订 Skill  ⑤ 管理员权限补钉钉身份绑定
P2：⑥ 表单问答对/升级  ⑦ 文档/图片分析  ⑧ 客服前端页面
后续独立：只读 SQL Skill
```

---

## 三、各功能迁移方案详述

### 3.1 钉钉用户/部门同步

#### 3.1.1 rag-agent 现状

- **数据范围**：部门树（`dept_id/name/parent_id`，从根 `dept_id=1` BFS 递归）+ 直属成员（`unionid/userid/name/job_number/email/dept_id`）。用户身份稳定主键是 **unionId**；一人多部门生成多行 `(union_id, dept_id)`。
- **触发**：两种且均为全量——手动按钮 `POST /api/v1/admin/dingtalk/sync`（异步 + 前端轮询）、定时任务（默认 3600s，`main.py:51-60`，循环体先 sleep 后同步故首次同步在 3600s 后）。启动时只调 `reap_stale_sync_runs`（`main.py:38-43`，把僵尸 running 标 failed），**不执行同步**。
- **落库**：3 张快照表 `dingtalk_departments` / `dingtalk_user_departments` / `dingtalk_directory_sync_runs`（`pg_schema.py:125-162`）。merge 为**快照替换模式**：单事务内先全部 `active=0`，再 `INSERT ... ON CONFLICT DO UPDATE ... active=1`；离职/调岗自动 inactive（不物理删除）。同步失败保留旧快照，`sync_runs` 状态机 + 僵尸 running 回收。
- **钉钉 API**（`dingtalk_client.py`）：access_token 内存缓存 + 提前 300s 刷新（靠提前刷新规避 token 过期，目录同步 oapi 端点走 `_with_retry` 只重试网络+限流关键词，**不重试 401**）；`topapi/v2/department/listsub`（2 并发递归）+ `topapi/v2/user/list`（cursor/size=100 分页，3 并发）；网络错误 + 限流(errcode 90018)指数退避 5 次。注意 `topapi/v2/user/listbypage` 已下线，迁移参考旧钉钉文档会被误导。
- **前端**：`DirectorySyncPanel.tsx`（同步按钮 + 5s 轮询状态 + 10min 超时兜底 `POLL_TIMEOUT`，与后端 `reap_stale_sync_runs` 双保险）、用户管理页内嵌、`ResourcePicker`（部门树/用户选择器）。
- **部门缓存**：`_department_cache` 进程内缓存（TTL 默认 300s），`list_all_departments` 读缓存，快照替换末尾 `invalidate_department_cache` 失效。`dept_path` 形如 `/1/100/120/`，`list_users_by_dept(include_children=True)` 用 `dept_path LIKE '/1/100/%'` 前缀查询——这是 ACL"含子部门"功能的基础。
- **增量失效**：`replace_directory_snapshot` 先读旧 active 关系，与 new 关系做 `symmetric_difference` 算 `changed_users`，逐个 `invalidate_user_cache` 清 ACL 缓存——调岗/离职后 ACL 实时失效的关键。

#### 3.1.2 Yuxi 现状

- 已有 `departments` 表（`id` 自增 / `name` UNIQUE / `description` / `created_at`）+ `User.department_id` 单 FK（`models_business.py:38-44,67`），`auth_dept_router.py` 提供部门 CRUD（admin 权限）。
- 已有钉钉 OAuth 登录（`dingtalk_auth_service.py`），登录时解析钉钉身份。
- 用户模型有 `username`（UNIQUE）/`uid`（UNIQUE）/`phone_number`（UNIQUE, nullable）/`password_hash`（NOT NULL）/`avatar`/`role`/`department_id` + 软删除（`is_deleted`）。
- **Department 与 User 的结构限制**（与钉钉同步需求对照）：
  - `Department.name` UNIQUE —— 钉钉同名部门常见（各分公司都有"研发部"），upsert 会撞约束
  - `Department.id` 自增，**无钉钉 ID 列** —— 二次同步无法 upsert
  - `Department` 无 `parent_id` / `dept_path` / `active` —— 无法承载部门树和 include_children
  - `User.department_id` 单 FK —— 一人多部门会丢归属，且 `Department.users` cascade="all, delete-orphan"，任何 DELETE department 会**级联删该部门所有 User**（含 API keys/agent_env/operation_logs）
  - `User.password_hash` NOT NULL —— 钉钉用户无密码需占位；`username`/`phone_number` UNIQUE —— 钉钉姓名可重名、可能无手机

#### 3.1.3 迁移形态

- **核心决策修正：保留钉钉快照表做同步落点，增量合并到 Yuxi 主表**（不再"直接写主表"）。
  - 规划初版"不新增通讯录快照表，直接写主表"的出发点是避免双份事实来源，但 rag-agent 快照的"事实"（多部门/不删/dept_path/include_children）和 Yuxi 主表的"事实"（单 FK/唯一约束/级联删除）语义不同，强行合并会撞约束、丢数据、甚至级联删用户。改为"快照表存钉钉完整状态 + 增量步骤合并到主表"既不破坏主表约束与本地账号语义，又能保留钉钉的多部门/不删除/dept_path/include_children 能力。
- **新增**：钉钉目录同步 Service（`yuxi.services.dingtalk_directory_service`）+ 与来源保持语义一致的 **3 张规范化表**：`dingtalk_departments`、`dingtalk_user_departments`、`dingtalk_directory_sync_runs`。不能用 1 张宽表代替：一个用户可以属于多个部门，部门树和用户-部门关系是不同粒度；同步状态也不应和业务行混在一起。另加管理端接口与前端"同步通讯录"按钮。
- **身份主表加列**（现有数据库必须显式迁移，不能只依赖 `create_all`）：
  - `users` 加 `dingtalk_corp_id` / `dingtalk_union_id` / `dingtalk_user_id`，以 `(dingtalk_corp_id, dingtalk_union_id)` 做租户内唯一绑定；兼容当前 `uid=dingtalk:<unionId>` 的历史账号时先回填，冲突或无法判定的账号进入人工处理队列，禁止按姓名/手机号静默合并。
  - **不把钉钉外部树字段塞进 `departments`**。`dept_id`、`parent_dept_id`、`dept_path`、`active` 只落在钉钉快照表；Yuxi `Department` 仍是本地资源归属，最多由独立投影步骤更新 `User.department_id`，不新增含义不清的 `parent_id INT` 外键。
- **落点流程**（两步）：
  1. **快照落点**：同步结果写入 3 张钉钉表；在一个事务内对本 corp 的旧快照做 inactive，再 upsert 本次完整结果，不物理删除。同步失败不提交，保留上一份有效快照。
  2. **增量合并到主表**：根据快照算变更集，独立步骤合并——创建缺失用户/部门、更新档案（姓名/工号/手机号）、按明确规则选主部门写 `department_id`，离职置 `is_deleted`。**范围限定**：只处理同一 `dingtalk_corp_id` 下已绑定钉钉身份的用户，绝不覆盖本地账号的 `password_hash`、`role` 或其他资源权限。
- **身份绑定**：钉钉 unionId 与 Yuxi `users.dingtalk_union_id` 关联，登录 OAuth 时回填。
- **同步策略**：rag-agent 是"通讯录唯一事实来源"，Yuxi 是"本地账号为主体 + 钉钉登录"。同步负责**创建缺失用户/部门、更新档案、按部门归属调整 `department_id`、离职软删除**，但**绝不删除**本地用户（离职用户用 `is_deleted` 软删除标记，保留其资源归属）。
- **🚨 红线（绝不能违反）**：
  1. **绝不把 rag-agent 的"快照替换模式"照搬到 Yuxi `users` 表** —— 那会在一个事务里把所有用户（含本地账号密码登录的、与钉钉无关的）全标记离职再 upsert 钉钉拉来的，误伤全部本地用户。快照替换只作用于 `dingtalk_directory_snapshot` 表，主表合并必须是增量。
  2. **绝不 DELETE Yuxi 的 Department 行** —— `Department.users` cascade="all, delete-orphan"，删部门会级联删该部门所有 User（含 API keys/agent_env/operation_logs）。离职部门用 `dingtalk_active=false` 标记，不删行。
  3. **主表合并必须带 corp 作用域和身份绑定条件** —— 以 `dingtalk_corp_id + dingtalk_union_id` 匹配，严格区分"本 corp 钉钉用户"与"其他 corp/本地账号"，否则同步会覆盖本地用户的 role/password_hash。
- **权限**：同步接口仅 superadmin 可触发；同步后失效相关 ACL 缓存（Yuxi 用 Redis 缓存，需对应实现 rag-agent 的"算 changed_users 变化集 + 按 key 失效"，不能简单全量清）。

#### 3.1.4 关键移植点

access_token 缓存/刷新、退避重试、分页并发、快照事务语义、**跨进程互斥**（PostgreSQL advisory lock 或 Redis 分布式锁，不能只用进程内 `asyncio.Lock`）、持久化 `sync_runs`、僵尸任务回收。定时同步放在可重试的 worker/队列中，不依赖 API 进程内 Tasker。前端参考 `DirectorySyncPanel` 的交互即可，用 Vue 重做。

---

### 3.2 表单问答对

#### 3.2.1 rag-agent 现状

- **定位**：精选 FAQ 问答库，知识库内容形态 `chunk_method='qa_pair'`。每条 = 标准问题 + 相似问法（aliases）+ Markdown 答案；发布后合成"隐藏文档 + 单 chunk"写入 chunks 表（content 含**标准问题 + 全部 aliases**，**答案在 metadata**），检索时只匹配问题，命中后**直接返回答案原文，不让 LLM 改写**。
- **数据模型**：3 张表 `qa_pair` / `qa_pair_revision` / `qa_pair_publish_jobs`（`pg_schema.py:417-475`），原因是跨服务（rag-agent ↔ knowledge-server 用 HTTP + 幂等 job）。**注意答案正文实际存在 `qa_pair_revision` 表**（不在主表），主表用 `active_revision`/`current_revision` 双指针指向"已发布版"和"草稿版"——改草稿不影响线上索引。
- **命中门控**（`ragflow_tools.py:51-78,173-237`）：`qa_question_match_score`（去停用问词归一化：完全一致=1.0、去噪核心一致=0.98、字符集合重合加权 query 0.7/candidate 0.3 + 二元 gram Dice 取 max）→ 阈值 ≥0.72 且第一名与第二名分差 ≥0.10（防歧义）+ 检索信号门（QA：vector ≥0.82 或词法重合 ≥0.45；**普通文档：vector ≥0.86**）→ **置信命中只返回该条 QA**；有候选不置信则返回空强制走未命中，不让普通文档抢答。
- **停用问词表**：24 个中文停用问词（请问/如何/怎么/怎样/能否/是否/可以/我想/想要/帮我/请帮我/请/一下/吗/呢/什么/怎么办/哪些/哪个/怎么弄/哪里/在哪/在哪里/能不能），去噪用子串删除（按长度倒序，顺序敏感）。
- **管理流**：draft/published/disabled 状态 + row_version 乐观锁；发布 = 建 job + 幂等 + 写索引；无批量导入导出、无审核流。**delete 是软删**（设 disabled），物理删 chunk 在 knowledge-server 侧。
- **返原文实现**：rag-agent 是**控制流短路**（`agent_service.py:947-965`：`yield qa_pair_answer; return` 跳过 LLM 生成），不是 prompt 约束——这是"不改写"的真正保证。
- **未命中升级**：无有效证据 → SSE `qa.escalatable` → 前端"转人工"按钮 → 钉钉群机器人 webhook。**还有第二个触发点**：模型答"暂未包含/未收录"等 20 个短语（`_NO_RESULT_PHRASES`）时也触发——这是中文客服特化逻辑。

#### 3.2.2 Yuxi 迁移形态（重新设计，编辑即发布）

- **定位**：Milvus 知识库的**能力开关**（不新建 KB 类型），问答对复用同一 Milvus 集合，chunk 带 `source_type=qa_pair` 元数据。
- **数据模型**：**新增 1 张表** `qa_pairs`（按 Yuxi 风格重新设计，不照搬 rag-agent 3 表）：
  ```
  qa_pairs(
    id UUID PK,
    kb_id FK → knowledge_bases,
    status VARCHAR(16) DEFAULT 'published',   -- published / disabled（无 draft）
    standard_question TEXT NOT NULL,
    aliases JSONB DEFAULT '[]',                 -- 相似问法，最多 50 条
    answer_markdown TEXT NOT NULL,             -- Markdown 答案正本（统一 Yuxi 全链路 markdown 体系）
    category VARCHAR(64),
    tags JSONB DEFAULT '[]',
    content_hash VARCHAR(64),                  -- 变更检测用
    index_status VARCHAR(16) DEFAULT 'synced', -- synced / pending / failed
    index_error TEXT,
    created_by FK → users,
    updated_by FK → users,
    created_at, updated_at
  )
  ```
  - **与 Yuxi 统一 markdown 体系**：Yuxi 全链路是 markdown——所有文档类型解析后统一转 md（`unified.py:335-409`）、chunk content 存 markdown 片段、前端用 `MarkdownPreview` 渲染、检索进 LLM 不转纯文本。QA 答案统一用 `answer_markdown` 一个字段存 markdown，**砍掉 rag-agent 的 `answer_text`（Markdown 转纯文本）字段**——Yuxi 不做 md→纯文本转换，留着是双份事实来源容易不一致。
  - **不照搬完整 revision 历史，但不能把编辑和索引写入伪装成一个事务**：保存先写 `index_status=pending`，由持久化 outbox/任务记录驱动 Milvus 更新；必须保留 `index_version`、`indexed_content_hash`、`index_error`、`next_retry_at` 或等价字段，索引失败时继续服务上一份有效索引，并支持幂等重试和人工修复。MVP 可以不做完整历史版本，但不能删除可恢复的一致性边界。
  - **编辑即发布的用户语义改为“保存后进入发布队列”**：页面不提供 draft 状态，但接口只有在索引成功后才把 `index_status` 标为 `synced`；不能在 PG 已是新答案、Milvus 仍是旧答案时宣称发布完成。
- **Repository/Service**：`repositories/qa_pair_repository.py` + `services/qa_pair_service.py`（CRUD/启用/停用/删除），路由 `server/routers/qa_pair_router.py`。载荷规范化只做 `aliases/tags` 去重（casefold，上限 50 条）和 `content_hash` 计算，保留 `answer_markdown` 原文，不再额外生成 Markdown→纯文本副本。
- **检索门控**：在 `MilvusKB.aquery()` 返回后加 `filter_qa_pair_hits`（从 rag-agent `ragflow_tools.py:51-237` **移植打分与门控**，含 24 词停用问词表）。
  - **只移植 rag-agent 版**，knowledge-server 服务端还有第二份 `filter_qa_pair_hits`（`retrieval_service.py:302-323`，无分差防歧义、保留 best-0.18 内全部，只在"全 QA KB"触发）——Yuxi 单集合设计永远走混合路径，**服务端版不用移植**。
  - **注意普通文档 vector 门是 0.86**，移植时别误用 QA 的 0.82，否则放宽普通文档准入污染检索。
  - **policy_mode 旁路**：rag-agent 的 `_passes_retrieval_signal` 有 `relevance_policy_mode == "enforce"` 时用 `decision == "pass"` 跳过硬门的逻辑。Yuxi 移植时去掉这段（Yuxi 无 relevance policy 信号，去掉后全靠 0.82/0.45 硬门，更保守）。
- **返原文实现（关键修正）**：rag-agent 是**控制流短路**（`yield qa_pair_answer; return` 跳过 LLM），不是 prompt 约束。Yuxi 用 deepagents + `create_agent`，工具返回值会进 LLM 上下文让模型生成最终回复，**仅靠 prompt"逐字引用"模型不保证遵守**（会加寒暄、换措辞、丢格式）。
  - **正确做法**：增加 agent 运行前的 QA gate，或在可结束当前 run 的编排节点中处理 `filter_qa_pair_hits` 结果；命中后直接产生最终消息并结束 run，不让请求进入 deepagents 的 LLM 生成节点。单独改 `MilvusKB.aquery()` 返回值或给工具结果加提示词都不够，因为工具结果默认仍会回到模型上下文。
  - **QA 答案里的图片 URL 也要改写**：rag-agent 在 yield 前调 `normalize_markdown_image_urls`（`ragflow_tools.py:631-646`）。Yuxi 侧复用现有 MinIO/资源代理的规范化函数和权限语义，不直接拼接未经鉴权的 `/minio/public/...`，否则命中 QA 时可能图片 404 或越权。
  - **集成测试**：命中 QA 断言模型输出 == 答案原文。如果测试失败，根因是编排没短路，不是 prompt 不够强。
- **QA chunk 不参与图谱抽取**（`source_type=qa_pair` 在 Neo4j 抽取链路排除，避免污染图谱）。**注意**：rag-agent 无图谱链路（grep neo4j/lightrag 零命中），这是 **Yuxi 侧新设计**，不是从 rag-agent 移植的。
- **前端**：`DataBaseInfoView.vue` 加"问答对"Tab（列表 + 编辑弹窗），复用 Yuxi Markdown 渲染。**QA KB 拒收普通文档**（rag-agent `documents.py:94-95` 上传时 `if kb.chunk_method == "qa_pair": raise 400`）——Yuxi 复用同一 Milvus 集合不拒收，但前端列表按 `source_type` 区分展示。
- **权限**：直接继承知识库可见性（复用 KB ACL），不需要 rag-agent 的 `kb_admins`。
- **资源引用同步**：保留 rag-agent 的 `_sync_asset_refs`（QA 引用的图片资产同步到 asset_refs 表），否则孤儿清理任务误删 QA 答案里的图。
- **删除语义**：明确软删（设 `disabled`）还是硬删（删行 + 删 Milvus chunk）。建议软删，与 rag-agent 一致。
- **升级转人工**：**不保留** rag-agent 的 20 短语黑名单兜底检测（中文客服特化逻辑，Yuxi 通用平台不需要）。P2 阶段如需"转人工"，用 Yuxi 已有 `ask_user_question` 工具或钉钉通知工具承载"前端按钮 → 后端通知"流程，不做钉钉群 webhook 特例。

#### 3.2.3 关键风险

- **"返原文不改写"在 LLM harness 下必须用控制流短路，不是 prompt 约束**（这是迁移设计的最大隐患）。MVP 阶段写集成测试：命中 QA 断言模型输出 == 答案原文。测试失败时查编排是否短路，而非加 prompt。
- **"本地事务直接同步索引"对 Milvus 不成立**：Milvus 不支持分布式事务，PG 写成功但 Milvus 写入失败会出现状态不一致。需 `index_status` 状态机（synced/pending/failed）+ 补偿任务对齐，不能纯靠本地事务同步。规划砍 `publish_jobs` 对 PG 部分正确（无需幂等 job），对 Milvus 部分需补补偿任务。
- 检索门控改动在 Milvus 核心路径，必须回归普通文档检索（尤其别误把普通文档 vector 门设成 0.82）。

---

### 3.3 rag-agent 知识库内容导入

#### 3.3.1 rag-agent 现状

- **内容实际存储**：**不在 RAGFlow 引擎**，在 knowledge-server 的 PostgreSQL `knowledge` 库（pgvector）+ MinIO。RAGFlow 仅残留图片代理端点（`/ragflow/images/{id}`，可能被历史 answer 文本引用）。
- **表结构**：
  - `knowledge_bases`（chunk_method/permission/similarity_threshold/vector_similarity_weight/doc_count/chunk_count/**embedding_model_id 外键→model_configs**/parser_config/description/created_by）—— **导出 KB 必须连 embedding 模型配置一起导**，否则到 Yuxi 后不知道该用哪个模型重算向量
  - `documents`（filename/file_type/file_path(MinIO key,非完整URL)/checksum/status/progress/doc_metadata/active_parse_version/file_size/parse_task_id）
  - `chunks`（content/content_tsv/**jieba 预分词 + PG `to_tsvector('simple',...)` 归一化，非纯 PG tsvector**/embedding vector(512)/chunk_index/page_number/section_title/important_keywords/question_keywords/chunk_metadata/parse_version/is_enabled）
  - `document_assets`（解析提取图片，**唯一键是 `(doc_id, parse_version, sha256)` 三列联合，不是 sha256 单列**；含 kb_id/storage_path(MinIO key)/content_type/file_size/width/height；asset id 是 UUID）
- **文档类型**：代码支持 pdf/docx/xlsx/pptx/md/txt/html + 图片（VLM 描述）。**csv/json 无独立 parser**，上传白名单也无这两个后缀。**实测 rag-agent 存量文档只有 qa/docx/pdf 三种**（2026-08-11 查 knowledge 库 documents 表），导入脚本只需覆盖这三种。
- **图片引用**：正文里**四种** URL 写法都要改写：
  1. `asset://{32hex}` — 草稿内部占位符
  2. `/api/v1/knowledge-assets/{id}` — 旧编辑器预览地址
  3. `/api/v1/knowledge/assets/{id}?datasetId=...` — 当前对外正式地址
  4. **`/api/v1/internal/knowledge-assets/{32hex}/content`** — RAGFlow 内部抓取地址，加载旧 Markdown 草稿时归一化为 `asset://`。**前三种有正则统一匹配，第四种只在 `knowledge_document_service.py` 单独处理，三种正则都不匹配，迁移时容易漏**
- **两张 assets 表 ID 格式不同**：
  - knowledge-server 的 `document_assets`（UUID 主键，解析提取的图，走 MinIO `storage_path`）
  - rag-agent 主服务的 `knowledge_document_assets`（**32 位 hex** 主键 `VARCHAR(64) UNIQUE`，旧编辑器上传的图，`object_key` 在 MinIO）
  - 两表 ID 格式不同，**合并到 Yuxi 不能直接拼**，需分别建映射表；旧表图片可能被 chunk content 的 `asset://32hex` 引用，迁移必须同时处理
- **权限**：在 rag-agent 库 `knowledgebase_acl_config`（visibility public/private）+ `knowledgebase_department_acl`（含 include_children）+ `knowledgebase_user_acl` + `kb_admins` + `knowledgebase_acl_audit_logs`（审计表）。**ACL 表 `kb_id` 是 VARCHAR(64)**（KB UUID 字符串），Yuxi 映射要做 ID 翻译。
- **无现成导出脚本**（`scripts/` 只有 MySQL→PG 迁移、钉钉 smoke test、RAGFlow smoke test 三个无关脚本），需自行开发。
- **knowledge-server 物理位置**：`services/knowledge-server`，独立 FastAPI 服务端口 8000，容器名 `rag-knowledge-server`。rag-agent 连接方式 HTTP + Bearer 内部密钥。**两服务共用同一 PG 实例不同库**（knowledge-server 用 `knowledge` 库，rag-agent 用 `rag_agent` 库）+ 同一 MinIO。

#### 3.3.2 迁移方案

- **导出侧**（两条路，建议后者批量快照）：
  1. knowledge-server 内部 API（带校验）：`GET /api/knowledge-bases` → `/documents` → `/documents/{doc_id}/preview`（原始文件）→ `/documents/{doc_id}/chunks`（分页）→ `/assets/{asset_id}`（图片）
  2. 直连 PG 读 4 张表 + MinIO 取对象（更快，写一次性迁移脚本）
  - **导出必须按 `documents.active_parse_version` 过滤**：同一图在不同解析版本会重复存（唯一键三列联合），不按 active 过滤会灌入重复/过期 chunk 和图片
- **导入侧**（先 dry-run，满足外部依赖闸门后才写入 Yuxi）：
  - 知识库：`knowledge_bases`（名称/描述/**embedding_model_id 关联的模型配置**）→ Yuxi KB（Milvus 集合）；ACL 映射到 Yuxi 知识库共享范围（`none/read/manage`）与 ResourcePermission，`kb_id` UUID 字符串做 ID 翻译
  - 文档：原始文件传 MinIO 后**优先走 Yuxi 既有解析链路**；只有完成 embedding 模型、分词、chunk 元数据、Milvus metric 和维度预检，且单独验证过回滚/重跑，才允许考虑保留原 chunk 的专用导入路径。
  - chunks：`content` 保留；源 `embedding`（512 维）不能默认直灌 Yuxi 集合。推荐按目标 embedding 模型重算；若保留原向量，必须使用隔离集合并校验维度、模型、metric、metadata schema，不能让两个向量空间混用。
  - 图片：`document_assets` + MinIO 对象搬入，**重写正文四种 URL**（含第四种 `/api/v1/internal/knowledge-assets/...`）和 `ragflow_assets.py` 的 `/ragflow/images/{id}` 残留引用；URL 生成复用 Yuxi 现有 MinIO/资源代理，不把固定 `/minio/public/...` 当作通用鉴权方案。
  - 问答对（`chunk_method=qa_pair` 的合成 chunk）→ 落 3.2 的 `qa_pairs` 表
- **dry-run 产物**：按 KB 生成 manifest（文档数、chunk 数、资源数、ACL、embedding_model_id、维度、checksum、预计容量）和阻塞项；dry-run 不写目标 PG、MinIO、Milvus。
- **正式分批执行**：仅在 manifest 审核通过、源库已做只读快照/备份、目标模型已确认后，按 KB 导入，使用 `sync_runs` 式进度表 + 失败重试 + 幂等（checksum 去重），每批完成后做数量、checksum、抽样检索和图片访问校验。

#### 3.3.3 关键风险

- **embedding 维度不一致**：rag-agent chunks 是 512 维（Chinese-CLIP-ViT-B-16），Yuxi 默认 bge-m3 是 1024 维，且 **Yuxi 切 embedding 模型会删库重建 Milvus 集合**（`milvus.py:361-376`）。决策：
  - 方案 a：导入时用目标 KB 的 embedding 模型**重算全部 chunk 向量**（推荐，统一向量空间，检索一致）
  - 方案 b：保留原 512 维向量建独立集合（不推荐，与平台默认检索割裂）
- 图片 URL 改写遗漏会导致回答丢图（记忆：Yuxi 已知的丢图根因）。
- ACL 映射口径（rag-agent 按知识库 public/private + 部门/用户 ACL，需与 Yuxi 共享范围语义对齐）。

---

### 3.4 超管设置管理员权限

#### 3.4.1 rag-agent 现状

- **无 role 字段、无超级管理员层级**：管理员用独立表 `kb_admins(corp_id, union_id, user_id, user_name)`（`pg_schema.py:176-185`），身份对象只有 `is_knowledge_admin` 一个权限位（30s 缓存实时查表）。
- **能力已具备**：`POST/DELETE /admin/kb-admins` 提升/撤销（任意 admin 可操作他人，前后端双校验）；前端用户管理页 `RoleEditorModal`（普通用户/管理员 Radio）。
- 提升时前端从钉钉通讯录快照选人传 unionId，后端直接写库，不调钉钉验证；离职不自动清理 `kb_admins`。

#### 3.4.2 Yuxi 现状 —— 已具备，无需迁移核心逻辑

- **Yuxi 三角色模型更完善**：`User.role`（superadmin/admin/user）+ 部门 + ResourcePermission（`permissions/resource_permission.py`）。
- **已有完整用户管理**：`auth_router.py` `create_user`（:530）/ `update_user`（:705，含角色与部门调整）/ `delete_user`（:795），角色变更受控：
  - 仅 superadmin 可创建/提升为 superadmin；admin 只能建/改普通 user（`auth_router.py:572-586,729-730`）
  - 撤销保护：不能删除/改 superadmin 自身（`auth_router.py:723,809`）
- 前端已有账户设置页的"用户管理 / 部门管理"标签（superadmin 可见）。

#### 3.4.3 需要补的部分

1. **钉钉身份绑定**：钉钉 unionId → `users` 表（新增 `dingtalk_union_id` 列，OAuth 登录回填），让"从钉钉通讯录选人提升管理员"成为可能。
2. **从通讯录提升管理员**（可选）：用户管理页集成钉钉目录选择器（复用 3.1 同步数据），选人后调 Yuxi 现有 `update_user` 改 role。
3. 角色映射：rag-agent 的 `kb_admins`（知识库管理员）语义 → Yuxi 的 `admin` 角色；如需"仅管理某知识库"的细粒度，用 Yuxi 知识库共享范围（read/manage）承载。

---

### 3.5 钉钉会议室预订（Skill 化）

#### 3.5.1 rag-agent 现状

- **数据源**：实时调钉钉 API + 10 分钟内存缓存（按 `(corp_id, operator_union_id)` 隔离，LRU 256）。
- **查询**（`meeting_room_service.search_rooms`）：参数 title/start_time/end_time/capacity/building/floor/equipment/user_work_place；过滤停用楼（`building == "已停用"` 字符串硬匹配——隐性约定）、容量、设施全匹配；`building` 过滤是**宽松子串匹配 + region 回退**（`r.building == building or building in r.building or building in r.location`，严格相等会查不到）；忙闲查询 `POST /v1.0/calendar/users/{unionId}/meetingRooms/schedules/query`；返回前 10 间 + parsedRequest（只含 title/startTime/endTime/capacity）。
- **房间分页限制**：`list_meeting_rooms` 只取第一页 `maxResults=100`，service 层不翻页。**企业房间 >100 会静默截断**，查询/预订漏房间。
- **预订 = 两段式 preview/confirm**：
  - `preview`（:250）：校验时间（同一天、≤8h）→ 忙闲检查 → 生成一次性 `confirmToken`（TTL 300s，`booking_confirmations` 表）→ 返回预览
  - `confirm`（:336）：token 校验 → 幂等键 `sha256(user+room+start+end+token)` → **重查忙闲**（被抢 409，注意预览→确认之间有 TOCTOU 窗口，x-client-token 幂等只防重复提交不防被抢）→ 先写 `room_bookings` 状态=CREATING → 钉钉 `create_schedule` → `reserve_meeting_room` → BOOKED；日程建好但订房失败时**补偿删除日程**
  - `cancel`（:551）：归属校验 → CANCELLING → 钉钉 `cancel_meeting_room` + `delete_schedule` → CANCELLED/CANCEL_PARTIAL（**CANCEL_PARTIAL 是真实会卡死的终态**，cancel 业务层失败不重试，需后台扫描补偿）
- **钉钉 7 个 API**（`dingtalk_client.py`）：会议室列表、忙闲查询、创建日程（x-client-token 幂等）、预订会议室、取消订房、删除日程、access_token。**access_token 是 app 级**（`x-acs-dingtalk-access-token` 头），不是 userAccessToken。
- **身份依赖**：`operator_user_id`（钉钉 userId）必须登录时用 unionId 反查存 session（`auth_service.py:144`），bookings 从 session 读。`create_schedule`/`reserve_meeting_room` 用 **unionId**，但 `room_bookings` 表存 `organizer_user_id` 是 **userId**，幂等键也用 userId。Yuxi 的 Identity 体系需同时提供 userId + unionId。
- **Agent 暴露**：**关键词路由 + LLM 参数提取**（`extract_meeting_params` 返回 JSON：title/start_time/end_time/duration_minutes/capacity/region/building/floor/equipment，temperature 0.1、相对日期按 Asia/Shanghai——**system prompt 注入完整 `now.isoformat()` + 时区**）+ SSE 结果卡片；**最终预订/取消由前端用户点击确认走 REST，agent 不代下单**。注意 `extract_meeting_params` 的 `conversation_history` 实际总传 `[]`，"多轮收集"是假多轮。
- **表**：`room_bookings`（状态机 CREATING/BOOKED/FAILED/COMPENSATION_REQUIRED/CANCELLING/CANCELLED/CANCEL_PARTIAL + idempotency_key + `event_id` 死列 + `schedule_id` 实际存 event_id 的命名混淆 + `room_location` INSERT 时硬编码 NULL + `dingtalk_error_code` 从不写）+ `booking_confirmations`（token/payload/expires/used，**无过期清理任务**，token 表无限增长）。

#### 3.5.2 Yuxi Skill 化形态

- **Skill**：`meeting-room`（`agents/skills/buildin/meeting-room/SKILL.md`，frontmatter 声明 `tool_dependencies`）。
- **工具**（`agents/toolkits/buildin` 或独立 `agents/toolkits/dingtalk`）：`search_meeting_rooms`、`preview_booking`、`confirm_booking`、`cancel_booking`、`my_bookings`——参数 schema 直接搬 rag-agent `schemas/bookings.py`。
- **保留核心**：两段式 preview/confirm、`confirmToken` TTL、幂等键、CREATING 先行 + 补偿删除日程、取消补偿。**这是防重复预订/防脏数据的关键，必须原样保留**。
- **确认交互（一次用户确认）**：
  - agent 调 `search_meeting_rooms` → 拿到房间列表
  - agent 调 **`ask_user_question`**（Yuxi 已有工具，让 agent 在对话里一次性展示房间、时间和冲突提示）→ “找到 3 间空会议室，你确认预订 A 吗？请选择 A/B/C 或取消”。用户的这次选择/确认就是唯一的用户确认。
  - 用户确认后，后端内部依次执行可用性校验、创建日程、预订会议室和必要的补偿；不再弹出第二个确认环节。取消仍需用户明确提出。
  - **不绕过用户确认**：`confirm_booking` 只能在 `ask_user_question` 收到明确确认后执行；Yuxi `SENSITIVE_BACKEND_TOOLS` 未覆盖会议预订不影响本流程，因为确认边界由该交互承载。全链路仍需记录用户、房间、时间和幂等键。
  - **内部 confirmToken 风险**：token 仅作为后端短时幂等凭证，过期或已使用时返回明确失败并要求重新选择，禁止静默重订。
  - **相对日期解析坑**：rag-agent 在 extract 的 system prompt 注入完整 `now.isoformat()（Asia/Shanghai）`（含时间+时区）。**Yuxi chatbot 系统 prompt（`prompt.py:54-55`）只注入 `当前日期：YYYY-MM-DD`，没时间没时区**——"明天下午3点"会算错。迁移时必须把 Yuxi prompt 改成注入完整 `shanghai_now().isoformat()`，或在会议室工具 description 里强制要求 ISO 8601 +08:00 且自行处理（不可靠，需集成测试断言：给定"明天下午2点到3点"必须输出合法 ISO 区间）。
- **房间翻页**：`list_meeting_rooms` >100 房间会静默截断，迁移**必须补翻页循环**（dingtalk_client 已支持 `next_token`/`has_more`，service 层没用）。
- **卡片渲染**：搜索结果用结构化消息让前端识别渲染房间卡片（参考 rag-agent App.tsx 交互）。**不能直接复用 `present_artifacts`**（面向文件交付物，要求路径在 /outputs 下，不适合传结构化房间列表）——需新增结构化 artifact 类型或特殊结构化消息。
- **数据表**：`room_bookings` + `booking_confirmations` 迁到 Yuxi（`models_business.py` 或独立模型文件）。**表迁移口径决策**：rag-agent 用 `VARCHAR(32)` 存 ISO 时间字符串、`id` 是 `bk_`+hex。迁到 Yuxi 若沿用 UUID/TIMESTAMPTZ 惯例，会与 rag-agent 的字符串时间比较逻辑冲突，要么全改时间列类型+全改比较逻辑，要么保留 VARCHAR(32) ISO 字符串约定。**建议统一改成 `room_bookings` 的死列**：`event_id` 从不写、`schedule_id` 实际存 event_id，迁移时统一成 `schedule_id` 单列存 event_id、删 `event_id` 列。`booking_confirmations` 补**过期清理任务**（或改 Redis TTL）。
- **钉钉客户端**：**需新写 app 级 access_token 管理**（提前 300s 刷新 + 双重检查锁），**不是复用 Yuxi `dingtalk_auth_service`**——Yuxi 那套是 OAuth/userAccessToken，会议室预订全部用 app 级 token（`x-acs-dingtalk-access-token` 头）。新增会议室/日历 7 个 API 封装。
- **LLM 参数提取**：rag-agent 的 `looks_like_meeting_room` + `classify_intent` + `extract_meeting_params` 三套机制（关键词短路 + LLM 意图分类 + LLM 参数抽取），在 Yuxi deepagents 下**全砍**——模型直接决定调哪个工具，参数由 tool schema 承载，工具 description 写清时间格式与相对日期语义即可。但 temperature 0.1 的确定性在 Yuxi 下不可单独调（由 Yuxi agent 配置决定），时间格式稳定性需集成测试断言。

#### 3.5.3 关键风险

- 钉钉订房 API 的幂等（x-client-token / idempotency_key）与补偿删除日程，任何一环丢失都可能导致"日程建了没订房 / 订了房取消不掉"。`CANCEL_PARTIAL` 是真实会卡死的终态，需后台扫描补偿。
- 相对日期解析依赖系统提示注入完整时间戳（Yuxi 现状只注入日期），必须改 prompt 否则时间计算错误。
- `extract_meeting_params` 的 LLM 参数提取在 Yuxi harness 下由模型工具调用直接承担（不需要独立关键词路由），工具 description 要写清时间格式与相对日期语义。
- 房间 >100 静默截断，必须补翻页。

---

## 四、设计 Token 迁移方案（已完成）

### 4.1 rag-agent 设计 Token 提取

来源：`D:\Workspace\rag-agent\web\src\index.css` + `web/src/main.tsx`（antd ConfigProvider）

```css
/* 品牌主色（antd colorPrimary） */
--brand-accent: #0b806a        /* 墨绿，antd 主题色 */
--brand-accent-soft: #e8f3ef   /* 品牌柔和底色 */
--brand-ink: #1b2421           /* 品牌深色文字 */

/* 语义色 */
--blue: #0f766e;  --green: #0f766e      /* 信息/成功（同色） */
--red: #b4232c;   --amber: #9a6700      /* 危险/警告 */
--blue-soft: #eaf5f2;  --red-soft: #fff1f2;  --amber-soft: #fff7df

/* 中性色 */
--ink: #202123          /* 主文本 */
--muted: #676767        /* 次要文本 */
--faint: #909090        /* 弱化文本 */
--line: #e7e7e4         /* 分隔线 */
--line-strong: #d7d7d2  /* 强分隔线 */

/* 背景 */
--canvas: #ffffff
--paper: #ffffff
--paper-soft: #f7f7f5

/* 阴影 */
--shadow: 0 18px 48px rgba(24, 24, 22, 0.14)
--small-shadow: 0 8px 24px rgba(24, 24, 22, 0.08)

/* 布局 */
--sidebar-width: 264px
--conversation-width: 820px

/* 视觉特征 */
顶部栏：background: rgba(255,255,255,0.9); backdrop-filter: blur(18px); border-bottom: 1px solid var(--line)
圆角：borderRadius: 8（antd token）
```

### 4.2 Yuxi 现有设计体系（迁移目标）

| 位置 | 现状 |
|---|---|
| `web/src/stores/theme.js` | antd token：`colorPrimary: '#24839b'`（青）、`borderRadius: 8` |
| `web/src/App.vue` | `<a-config-provider :theme="themeStore.currentTheme">` |
| `web/src/assets/css/base.css` | `--main-*` 青色系（--main-700 #046a82 为主色）、`--gray-*` 灰色系、`--color-primary-*` 别名 |
| `web/src/assets/css/base.dark.css` | 暗色模式变量覆盖（--main-700 #82c3d6 等） |
| 设计规范 | `docs/develop-guides/design.md` |

### 4.3 Token 映射表（rag-agent → Yuxi）

#### 4.3.1 antd 主题（`web/src/stores/theme.js`）

| antd token | 当前值（Yuxi） | 目标值（rag-agent） |
|---|---|---|
| `colorPrimary` | `#24839b` | `#0b806a` |
| `borderRadius` | `8` | `8`（不变） |

#### 4.3.2 CSS 变量（`base.css` 亮色）

| Yuxi 变量 | 当前值 | 目标值（来自 rag-agent） | 语义 |
|---|---|---|---|
| `--main-1000` | `#01151f` | `#10231f`（品牌深色近似） | 最深品牌色 |
| `--main-900` | `#023944` | `#144a3d` | 深品牌色 |
| `--main-800` | `#035065` | `#0b5f4e` | |
| `--main-700` | `#046a82` | **`#0b806a`** | **主色（对应 --brand-accent）** |
| `--main-600` | `#24839a` | `#1e8f78` | |
| `--main-500` | `#3996ae` | `#3aa084` | 标准 |
| `--main-400` | `#5faec2` | `#5fb59b` | |
| `--main-300` | `#82c3d6` | `#82c7b1` | |
| `--main-200` | `#a3d8e8` | `#a5d9c8` | |
| `--main-100` | `#c4eaf5` | `#c6eade` | |
| `--main-50` | `#e1f6fb` | `#e4f5ef` | |
| `--main-40` | `#eaf3f5` | `#e8f3ef`（对应 --brand-accent-soft） | 品牌柔和底 |
| `--main-bright` | `#0188a6` | `#0f9a7f` | 高亮 |
| `--main-color` | `var(--main-700)` | `var(--main-700)` | 自动跟随 |

#### 4.3.3 灰色系（`base.css`）

| Yuxi 变量 | 当前值 | 目标值（rag-agent） | 语义 |
|---|---|---|---|
| `--gray-1000` | `#151616` | `#1b2421`（--brand-ink） | 主文本 |
| `--gray-600` | `#697070` | `#676767`（--muted） | 次要文本 |
| `--gray-500` | `#979999` | `#909090`（--faint） | 弱化文本 |
| `--gray-300` | `#d7d9d9` | `#d7d7d2`（--line-strong） | 强分隔线 |
| `--gray-200` | `#e4e6e6` | `#e7e7e4`（--line） | 分隔线 |
| `--gray-50` | `#f5f7f7` | `#f7f7f5`（--paper-soft） | 页面底色 |

> 灰色系建议**只替换与语义强相关的几档**（文本、次要、分隔线、底色），其余档位可保持，避免全站灰阶跳变。

#### 4.3.4 语义色（`base.css`）

| 语义 | Yuxi 现有（青色系近似） | 目标值（rag-agent） |
|---|---|---|
| 成功/信息 | `--main-600` | `#0f766e`（--green） |
| 危险 | `--red` 类变量（如 `#d9534f`） | `#b4232c`（--red） |
| 警告 | `--amber` 类变量 | `#9a6700`（--amber） |

#### 4.3.5 阴影与布局

| 项 | 目标值（rag-agent） | Yuxi 落点 |
|---|---|---|
| 大阴影 | `0 18px 48px rgba(24,24,22,.14)` | `base.css` 通用阴影变量 |
| 小阴影 | `0 8px 24px rgba(24,24,22,.08)` | `base.css` 卡片阴影 |
| 侧边栏宽 | `264px` | 布局组件侧栏宽度 |
| 会话区宽 | `820px` | 聊天容器 max-width |
| 顶栏 | 毛玻璃 `rgba(255,255,255,.9)` + `blur(18px)` | 顶部栏样式 |

#### 4.3.6 暗色模式（`base.dark.css`）

- rag-agent 目前以亮色为主，暗色 token 需**按 rag-agent 配色推导**（把亮色映射同步到暗色档位，如 `--main-700` 亮色 `#0b806a` → 暗色 `#1e8f78` 类浅化）。
- 目标：暗色下主色保持墨绿系，避免与亮色割裂。

### 4.4 实施步骤（渐进式，可回退）

```
Step 1  改 antd 主题：web/src/stores/theme.js 的 colorPrimary → #0b806a
        （一行改动，全站按钮/链接/选中态立即换色，先看整体效果）

Step 2  改 base.css 主色系：--main-700 ~ --main-50 整体替换为墨绿系
        （同步 base.dark.css 对应档位）

Step 3  改语义/中性色：灰色系选 5 档 + 成功/危险/警告色对齐

Step 4  布局细节：侧边栏 264px、会话区 820px、顶栏毛玻璃、阴影变量

Step 5  回归验证：亮/暗双模式，检查知识库卡片、图谱视图、对话页、管理后台无样式冲突
```

### 4.5 风险与回退

| 风险 | 应对 |
|---|---|
| 换主色后部分页面（图谱/图表/知识库卡片）对比度不适 | 逐页微调，不一次性大改 |
| 暗色模式与亮色不同步 | Step 2 同步改 `base.dark.css` |
| 灰色系大改导致层次感丢失 | 只替换语义相关档位，保留灰阶梯度 |
| antd 组件默认色（如 success/warning 色）未覆盖 | 在 `theme.js` token 里补 `colorSuccess/colorError/colorWarning` |

**回退方式**：改动集中在主题 Token 与布局/顶栏组件，git 单次提交，可一键 revert。

---

## 五、涉及文件清单

| 项目 | 文件 |
|---|---|
| rag-agent 设计源 | `D:\Workspace\rag-agent\web\src\index.css`、`web/src/main.tsx` |
| Yuxi antd 主题 | `D:\Yuxi\web\src\stores\theme.js`、`web/src/App.vue` |
| Yuxi CSS 变量 | `D:\Yuxi\web\src\assets\css\base.css`、`base.dark.css` |
| Yuxi 布局与顶栏 | `D:\Yuxi\web\src\layouts\AppLayout.vue`、`components\AgentChatComponent.vue`、`components\HeaderComponent.vue`、`components\shared\PageHeader.vue` |
| Yuxi 设计规范 | `D:\Yuxi\docs\develop-guides\design.md` |

### 5.1 钉钉同步（3.1）

| 来源（rag-agent） | 落点（Yuxi） |
|---|---|
| `dingtalk_sync.py`、`dingtalk_directory_service.py`、`dingtalk_directory_db.py`、`dingtalk_client.py` | `yuxi.services.dingtalk_directory_service` + `server/routers/dingtalk_router.py` |
| `pg_schema.py:125-162`（3 张快照表） | **3 张规范化钉钉表**（departments / user_departments / sync_runs）+ `users` ALTER 加 corp-scoped 身份列（`dingtalk_corp_id`/`dingtalk_union_id`/`dingtalk_user_id`）；不把外部 parent/path 直接写成 Yuxi `Department` 自关联 |
| `DirectorySyncPanel.tsx` | 用户/部门管理页同步面板（Vue，5s 轮询 + 30min 超时兜底） |

### 5.2 表单问答对（3.2）

| 来源（rag-agent） | 落点（Yuxi） |
|---|---|
| `qa_pairs_admin.py`、`qa_pair_db.py`、`qa_index.py`、`ragflow_tools.py:51-237` | `repositories/qa_pair_repository.py` + `services/qa_pair_service.py` + `server/routers/qa_pair_router.py` + `knowledge/implementations/milvus.py`（filter_qa_pair_hits） |
| `pg_schema.py:417-475`（3 表：含 revision/publish_jobs） | `models_knowledge.py` `qa_pairs`（**1 表，编辑即发布，砍 revision/publish_jobs**）+ `index_status` 状态机 + Milvus 补偿任务 |
| `agent_service.py:947-965`（控制流短路返原文） | Yuxi agent 编排层短路（QA 命中不进 LLM 生成节点）+ `normalize_markdown_image_urls`（QA 答案图片 URL 改写） |
| `QAPairsPage.tsx` | `DataBaseInfoView.vue` 问答对 Tab + 新面板 |

### 5.3 知识库内容导入（3.3）

| 来源（rag-agent） | 落点（Yuxi） |
|---|---|
| `knowledge-server` PG `knowledge` 库 4 表（含 `embedding_model_id` 外键）+ MinIO | 一次性迁移脚本（`scripts/`）+ Yuxi `knowledge/manager.py` / Milvus / MinIO |
| 图片 URL **四种**写法（含 `/api/v1/internal/knowledge-assets/...`） | 重写为 Yuxi `/minio/public/...` 同源地址 |
| 两张 assets 表（UUID 主键 vs 32hex 主键） | 分别建映射表，按 `active_parse_version` 过滤 |
| ACL 四表 + 审计表（`kb_id` VARCHAR UUID 翻译） | 映射 Yuxi 知识库共享范围 + ResourcePermission |
| 实测存量：qa/docx/pdf 三种 | csv/json/xlsx/pptx/md/txt/html 无存量，导入脚本只需覆盖前三种 |

### 5.4 管理员权限（3.4）

| 来源（rag-agent） | 落点（Yuxi） |
|---|---|
| `knowledge_admin.py`（kb-admins） | ✅ 已具备（`auth_router.py` create_user/update_user/delete_user），仅补 `dingtalk_union_id` 绑定 + 通讯录选人 |

### 5.5 会议室 Skill（3.5）

| 来源（rag-agent） | 落点（Yuxi） |
|---|---|
| `meeting_room_service.py`、`dingtalk_client.py`（7 API）、`bookings.py`、`schemas/bookings.py` | `agents/toolkits/buildin/dingtalk_meeting.py` + `agents/skills/buildin/meeting-room/SKILL.md` + 前端会议室卡片组件（新增结构化 artifact 类型，非复用 present_artifacts） |
| `pg_schema.py:20-59`（2 表） | `models_business.py` `room_bookings`（统一 schedule_id 删 event_id 死列）/ `booking_confirmations`（补过期清理任务） |
| app 级 access_token（`dingtalk_client.py:99-145`） | **新写** Yuxi app 级 token 管理（非复用 `dingtalk_auth_service` 的 OAuth/userAccessToken） |
| `extract_meeting_params`（system prompt 注入完整 ISO 时间） | Yuxi `prompt.py:54-55` 改注入完整 `shanghai_now().isoformat()`（当前只注入日期） |
| `list_meeting_rooms`（不翻页，>100 截断） | 补翻页循环（dingtalk_client 已支持 next_token/has_more） |

---

## 六、Checklist

- [x] 方向 A 已确认（本文档）
- [x] 2026-08-11 审查修订：对照 rag-agent 代码逐条核对 3.1/3.2/3.3/3.5（见文档顶部修订记录）
- [x] 2026-08-11 代码审查：对照已实现代码逐模块审查 + 实测验证（见第七章审查记录）
- [ ] P0：知识库内容导入 dry-run 与显式导入入口（见 3.3）—— **骨架在，但图片 URL 改写/assets 唯一键/ACL 映射/QA 迁移全缺失（见 7.3）**
- [x] P0：钉钉原生 OAuth 免登接入（PC 扫码 + H5 免登）—— 实测可用
- [x] P0：钉钉身份数据基础（corp-scoped 身份列 + 3 张快照表 + 显式 schema migration）
- [x] P0：钉钉目录同步服务（分页、快照事务、跨进程锁、失败回收、周期任务和主表增量投影）
- [x] P0：钉钉用户/部门同步（快照落库、查询、真实拉取和主表增量投影）
- [ ] P1：钉钉会议室预订 Skill（一次用户确认 + app 级 token + 房间翻页 + 相对日期解析）—— **confirm 重查忙闲用错身份（uid≠union_id，见 7.4 :289）+ CANCEL_PARTIAL 无补偿 + 前端卡片半残**
- [ ] P1：管理员权限补钉钉身份绑定（接口继承 superadmin 权限）—— 绑定本地账号有被同步软删风险（见 7.1）
- [x] P2：表单问答对（持久化索引任务 + Milvus 门控匹配 + 控制流短路返原文 + 可重试补偿）
- [x] P2：问题升级/转人工（记录、可选钉钉 webhook 通知、失败状态和统计）
- [x] P2：文档/图片分析（复用 Yuxi OCR/附件权限链路并注册 Skill）
- [x] P2：统计报表与客服前端页面（QA 统计、迁移/通讯录管理页和权限路由）
- [ ] 后续：只读 SQL 安全查询 Skill
- [x] 设计 Token：Step 1 antd 主色
- [x] 设计 Token：Step 2 base.css 主色系（含暗色）—— **暗色 --main-color/--main-bright 硬编码未跟随（见 7.6）**
- [x] 设计 Token：Step 3 语义/中性色
- [x] 设计 Token：Step 4 布局与阴影
- [ ] 设计 Token：Step 5 双模式回归验证 —— **暗色模式有变量割裂（见 7.6），需实测验证**

> 🚨 **阻塞性问题（7.0）**：GPT 代码引入 `Cannot generate a JsonSchema for core_schema.CallableSchema`，导致整个 agent run 失败（git stash 对比已确认是 GPT 引入的）。**在修复此问题前，所有功能都无法使用，必须优先修复。**

---

## 七、已实现代码审查记录（2026-08-11）

> 审查方式：4 个子代理并行静态审查 + 浏览器实测 + git stash 对比验证。
> 审查范围：GPT 已实现的所有改动（41 个已修改文件 + 19 个新建文件）。
> 结论：方向正确，单测全过（16/16），但有 **1 个阻塞性 bug** 和若干偏差需修。

### 7.0 阻塞性问题（必须先修，否则整个 agent 不能用）

#### 🔴 P0-阻塞：agent run 失败 `Cannot generate a JsonSchema for core_schema.CallableSchema`

- **实测确认**：浏览器发消息"域控密码忘了怎么办"，前端报"流式处理失败"，后端 worker 报 `Run failed: Cannot generate a JsonSchema for core_schema.CallableSchema`（`run_worker.py:607`、`manager.py:957`），agent run 直接失败无回答。
- **git stash 对比验证**：stash 掉所有 GPT 改动 + 移走新文件后，基线 agent run 完全正常（发"你好"得到完整回答）。恢复 GPT 代码后复现失败。**确认是 GPT 代码引入的**。
- **可能原因**：GPT 新加的 toolkit（`dingtalk.py`/`qa.py`）或 skill（`meeting-room`/`qa-pairs`）注册时，某个 Pydantic model/args_schema 含 `Callable` 类型字段，导致 LangGraph/Pydantic v2 在运行时为工具生成 JSON schema 失败。`get_graph()` 静态构建不报错（惰性加载时未触发工具 schema 生成），只有在 agent run 时才暴露。
- **定位方向**：逐个禁用 `agents/toolkits/__init__.py` 里的 `dingtalk`、`qa` import，缩小到具体 toolkit；再检查该 toolkit 的 `@tool` 装饰器 args_schema 有无 Callable 字段。
- **影响**：整个智能助手不能对话，所有功能都被阻塞，优先级最高。

### 7.1 钉钉目录同步（3.1）

**红线检查（通过）**：
- ✅ 红线 1：快照替换只作用于快照表，users 走增量（`dingtalk_directory_service.py:346-353` 快照替换 / `:310-316` 增量 is_deleted）
- ✅ 红线 2：全程无 DELETE Department，离职部门快照层 `active=false`（`:347`）
- ✅ 红线 3：主表合并按 `dingtalk_corp_id == corp_id` 隔离，不触碰本地账号（`:275-278,311`）。但标离职 SQL 缺 `dingtalk_union_id IS NOT NULL` 兜底，脏行可能误标（建议补 AND）

**违反规划的问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🔴 P0 | **非根部门名丢失** | `dingtalk_directory_service.py:122` | `"dept_name": "根部门" if dept_id == "1" else dept_id` 把 `_normalize_department` 算出的真实名（如"研发"）丢掉，存 dept_id（如"2"）。前端显示"2"而非"研发"，投影部门 description 也是"钉钉部门：2（/1/2/）" |
| 🔴 P0 | **主部门选择取反** | `dingtalk_directory_service.py:272-273` | `items[0]` 按 dept_id 升序取**最浅根部门**，规划要求取**最深叶子部门**。全文无 depth/leaf 计算 |
| 🟡 | **changed_users 缓存失效完全没做** | `dingtalk_directory_service.py` 全文 | 规划要求"算 symmetric_difference + 按 key 失效 Redis ACL 缓存"，实现里 `changed` 只是个计数，无法驱动按 key 失效。调岗/离职后 ACL 不会实时失效 |
| 🟡 | **departments 加列没做** | `models_business.py:34-53` | 规划修订后要求 departments 加 `dingtalk_dept_id`/`parent_id`/`dept_path`/`dingtalk_active`，实现仍是 `id/name/description/created_at`（GPT 按修订前的正文做的，文档存在内部矛盾需先确认） |
| 🟡 | **定时同步缺失** | `run_worker.py:677-679` | 触发只有"手动 + reap cron"，无定时全量同步 cron。规划 3.1.1"定时任务默认 3600s"未落地 |
| 🟢 | **绑定本地账号被同步软删的风险** | `dingtalk_router.py:125-157` | bind 接口允许把本地 admin 绑到 corp_id+union_id，该 unionId 从钉钉消失时 `_project_users:311` 会把本地账号 is_deleted=1。建议投影软删只作用于"由投影创建的钉钉用户" |
| 🟢 | **孤儿 queued run** | `dingtalk_router.py:66-69` | enqueue 被 `_job_id` 去重返回 None 时，run 行停在 queued 直到 30min 后 reap 标 failed |

**测试覆盖不足**（只有 2 个 happy-path 测试）：无 dept_name 断言、无多部门主部门选择、无本地账号隔离（红线 3）、无 include_children、无 bind/unbind、无 ALTER 迁移路径测试。

### 7.2 表单问答对（3.2）

**与规划一致**：砍 revision/publish_jobs ✅、index_status 状态机 + ARQ 补偿 ✅（实测索引成功）、图片 URL 改写 ✅、不保留 20 短语黑名单 ✅、软删 ✅。

**违反规划的问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🔴 P0 | **filter_qa_pair_hits 完全没移植** | `milvus.py` 全文 | 规划要求移植 rag-agent 版打分门控（24 词停用问词表 + 字符集合重合 + bigram Dice + 0.72 阈值 + 0.10 分差 + 0.82/0.45 信号门），实现里 grep 全零命中。QA 走纯 PG `SequenceMatcher.ratio()` 全表扫（前 2000 条），阈值 0.92，完全绕开 Milvus 和 rag-agent 打分逻辑 |
| 🔴 P0 | **QA 根本不进 Milvus** | `qa_pair_service.py:228` | 规划要求"QA chunk 复用同一 Milvus 集合，chunk 带 source_type=qa_pair"，实现是独立 `qa_pairs` 表 + 内存匹配，QA 永远不进 Milvus。`KnowledgeChunk` 表无 `source_type` 列。架构偏离规划 |
| 🟡 | **字段名不符规划** | `models_qa.py:21` | 规划要求 `answer_markdown`（统一 markdown 体系），实际是 `answer`（中性命名）。实测确认无 `answer_text` 残留 ✅，但命名没体现 markdown 正本语义 |
| 🟡 | **status 用双布尔列而非 published/disabled** | `models_qa.py:26-27` | 规划要求 `status VARCHAR(16) DEFAULT 'published'`（两态），实际用 `published` + `enabled` 两个独立布尔，多出"已发布但禁用"等组合态（规划要砍的 draft 变相回归） |
| 🟡 | **控制流短路无集成测试** | `chat_service.py:938-977` | 短路位置正确（agent stream 之前），形式满足"不进 LLM"。但规划点名的"集成测试断言模型输出 == 答案原文"完全缺失，短路正确性无回归保护 |
| 🟡 | **_sync_asset_refs 未实现** | `models_qa.py:24` | 规划要求保留 rag-agent 的 `_sync_asset_refs`（QA 图片资产同步到 asset_refs 表），实际只用 `image_refs` JSON 列存字符串，无关联表。孤儿清理任务会误删 QA 答案里的图 |
| 🟢 | **前端统计显示"索引完成 0"** | `MigrationAdminView.vue` | 实测：QA index_status=ready（后端确认），但前端统计显示 0。前端读 index_status 逻辑有 bug |
| 🟢 | **publish 与 update 职责重叠** | `qa_pair_router.py:122` vs `:46` | 两个入口都能发布，重复发布会多跳版本号 |

### 7.3 知识库内容导入（3.3）

**与规划一致**：dry-run 预检框架 ✅、manifest 扫描 ✅、sha256 幂等 ✅、superadmin 路由守卫 ✅。

**违反规划的问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🔴 P0 | **图片 URL 四种改写全缺失** | `knowledge_migration_service.py` 全文 | 规划核心要求（含第四种 `/api/v1/internal/knowledge-assets/...`），实现 grep `asset://`/`knowledge-assets` 零命中。导入后回答必丢图 |
| 🔴 P0 | **document_assets 三列唯一键 + active_parse_version 过滤未实现** | 同上 | 实现基于目录文件扫描，无读源库 `document_assets` 表逻辑，无 parse_version 概念。会灌入重复/过期 chunk 和图片 |
| 🔴 P0 | **两张 assets 表 ID 格式不同未处理** | 同上 | 无 assets 映射表，无 UUID vs 32hex 区分处理 |
| 🟡 | **embedding_model_id 一起导出未实现** | `knowledge_migration_service.py:130-149` | dry_run 检查的是目标 KB spec，不从源库读 `embedding_model_id` 外键→`model_configs`。源 KB 用哪个 embedding 模型信息没结构化导出 |
| 🟡 | **QA 问答对迁移路径没接** | `knowledge_migration_service.py` | 规划要求 `chunk_method=qa_pair` 的合成 chunk 落 `qa_pairs` 表，实现只处理普通文件，无 QA 迁移分支 |
| 🟡 | **ACL 映射未实现** | 同上 | manifest 有 `acl` 字段，`import_manifest` 完全不处理 |
| 🟡 | **migration_import 返回 202 但同步执行** | `knowledge_migration_router.py:36,53` | 无 sync_runs 进度表、无异步任务、无重试，单文件失败直接中断整批 |
| 🟢 | **SUPPORTED_EXTENSIONS 含 .csv** | `knowledge_migration_service.py:16` | 实测存量只有 qa/docx/pdf，csv 无独立 parser |

**定位**：当前 `import_manifest` 只能处理"裸文件目录"，无法处理 rag-agent 实际的结构化导出（chunks/图片/ACL/QA）。骨架完整、血肉缺失。

### 7.4 钉钉会议室预订（3.5）

**与规划一致**：方式 2 ask_user_question ✅、app 级 token 新写 ✅（`dingtalk_meeting_service.py:53-72`）、两段式 preview/confirm ✅、房间翻页 ✅、prompt 注入完整时间戳 ✅（`prompt.py:54`）、schedule_id 统一 ✅、过期清理 cron ✅、砍关键词路由 ✅。

**违反规划的问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🔴 P0 | **confirm_booking 重查忙闲用错身份** | `dingtalk_meeting_service.py:289` | `query_room_availability(uid, ...)` 传的是 Yuxi uid，钉钉要的是 unionId。payload 里存了正确的 `union_id`（:264），唯独 :289 漏了。TOCTOU 重查形同虚设，防重复预订检测失效 |
| 🟡 | **CANCEL_PARTIAL 无后台补偿** | `dingtalk_meeting_service.py:404` | 规划要求后台扫描补偿，`run_worker.py:677-680` cron 只有 `cleanup_expired_booking_confirmations`，无 CANCEL_PARTIAL 扫描。同理 CREATING 崩溃后无回收 |
| 🟡 | **前端未识别结构化房间卡片** | `dingtalk.py:69,151` vs `web/src` | 工具返回 `{"type":"meeting_rooms",...}`，前端 grep 零命中。后端发了结构化消息，前端无渲染分支，用户只看到 LLM 文本化列表。卡片交互半残 |
| 🟡 | **幂等键公式不符规划** | `dingtalk_meeting_service.py:293` | 实现是 `sha256(uid+confirm_token)`，规划要求 `sha256(user+room+start+end+token)`。功能等价但跨系统对账困难 |
| 🟡 | **confirmToken TTL 600s 而非 300s** | `dingtalk_meeting_service.py:256` | `timedelta(minutes=10)`，规划要求 300s（5min）。拉长 TOCTOU 窗口 |
| 🟡 | **search_meeting_rooms schema 砍了过滤参数** | `dingtalk.py:16-19` | 只有 start_time/end_time，砍了 title/capacity/building/floor/equipment。service 层不过滤，>100 房间全返回，token 浪费 |
| 🟢 | **validate_time_range 未校验同一天** | `dingtalk_meeting_service.py:196-204` | 23:00→次日 01:00 会被通过，与 rag-agent 不一致 |

**测试覆盖不足**：无 TOCTOU 被抢测试、无 CANCEL_PARTIAL 测试、无 TTL 过期测试、无相对日期集成断言（规划明确要求）。

### 7.5 钉钉 H5 免登

**与规划一致**：`dingtalkAuth.js` 轮询 + dd.ready 兜底 + 5s 超时 ✅、路由守卫前移 ✅、corp-scoped 身份隔离 ✅、OIDC 复用 ✅。

**问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🟡 | **LoginView onMounted 免登在 checkServerHealth 之后** | `LoginView.vue:653-664` | 规划要求"免登在 checkServerHealth 之前触发"，实际顺序是 health → firstRun → 免登。服务端不可达时 health 先耗时间，免登延后。路由守卫前移对了，但 onMounted 内顺序相反 |
| 🟡 | **路由守卫 + onMounted 双重免登最坏 10s 阻塞** | `router/index.js:187-211` | 守卫失败 5s + onMounted 再 5s = 10s 无反馈 |
| 🟢 | **H5 authCode 不校验 corp 归属** | `dingtalk_auth_service.py:307-326` | 若 `DINGTALK_CORP_ID` 配错，H5 免登会把用户绑到错误 corp |
| 🟢 | **前端测试几乎空** | `dingtalkAuth.test.js` | 36 行只测 logout 标记 set/get/clear，JSAPI 轮询/dd.ready/超时/UA 判断全未覆盖 |

### 7.6 设计 Token（4 节）

**与规划一致**：antd colorPrimary #0b806a ✅、语义色补全 ✅、base.css 墨绿系 ✅、灰色系 6 档 ✅、布局 264px/820px ✅、阴影 ✅、顶栏毛玻璃 blur(18px) ✅。

**问题**：

| 级别 | 问题 | 位置 | 说明 |
|---|---|---|---|
| 🟡 | **暗色 `--main-color` 硬编码未跟随** | `base.dark.css:23` | 亮色 `var(--main-700)` 自动跟随，暗色硬编码 `#3aa084`（=`--main-500`）。暗色下 `--main-color` 与 `--main-700` 两套值，视觉割裂 |
| 🟡 | **暗色 `--main-bright` 同样硬编码** | `base.dark.css:24` | 与 `--main-color` 同类问题 |
| 🟡 | **theme.js colorLink 用 CSS 变量可能失效** | `theme.js:19-21` | antd ConfigProvider 的 colorLink 在 JS 层做颜色派生（tinycolor），传 `'var(--main-color)'` 字符串无法被解析，可能导致 antd 链接色派生失效或控制台告警。需实测验证 |
| 🟢 | **灰色系改 6 档略超"5 档"建议** | `base.css:29-43` | 规划说"只替换与语义强相关的几档"，实际改 6 档。但 6 档都来自规划映射表，属忠实执行表，非违规 |

### 7.7 文档内部矛盾（需先确认再改代码）

规划文档存在内部矛盾：顶部修订记录（:5）说"补 departments 加列+保留快照表"，但 3.1.3 正文（:130,:133）说"3 张规范化表，不把钉钉外部树字段塞进 departments"。GPT 按正文做的（3 张表、不加 departments 列），与修订记录冲突。**需确认以哪个为准**：
- 若以修订记录为准：需加 departments 列 + 收敛快照表
- 若以正文为准：需修正修订记录措辞，并补"离职部门在主表如何标记"

### 7.8 审查结论与修复优先级

```
P0-阻塞（必须先修，否则 agent 不能用）：
  1. agent run 失败 CallableSchema（7.0）—— 定位并移除 GPT 新加 toolkit 里的 Callable 字段

P0（功能性缺陷）：
  2. 钉钉同步：非根部门名丢失（7.1 :122）
  3. 钉钉同步：主部门选择取反（7.1 :272-273）
  4. QA：filter_qa_pair_hits + 24 词停用词表完全没移植（7.2）
  5. 知识库导入：图片 URL 四种改写全缺失（7.3）
  6. 知识库导入：document_assets 三列唯一键 + active_parse_version 过滤未实现（7.3）
  7. 知识库导入：两张 assets 表 ID 格式不同未处理（7.3）
  8. 会议室：confirm_booking 重查忙闲用错身份 uid≠union_id（7.4 :289）

P1（偏差/健壮性）：
  9. 钉钉同步：changed_users 缓存失效没做（7.1）
  10. 钉钉同步：定时同步缺失（7.1）
  11. QA：控制流短路无集成测试（7.2）—— 规划点名的高优先级测试
  12. QA：_sync_asset_refs 未实现（7.2）
  13. 知识库导入：embedding_model_id/QA 迁移/ACL 映射未实现（7.3）
  14. 会议室：CANCEL_PARTIAL 无后台补偿（7.4）
  15. 会议室：前端未识别结构化房间卡片（7.4）
  16. 设计 Token：暗色 --main-color/--main-bright 硬编码（7.6）

P2（细节/体验）：
  17. 免登：LoginView onMounted 免登在 checkServerHealth 之后（7.5）
  18. 各模块测试覆盖不足（普遍只 happy path）
  19. 文档内部矛盾需先确认（7.7）
```

## 八、2026-08-11 本轮执行结果

本节为 7.1、7.2 旧审查结论的后续执行记录；涉及钉钉通讯录与表单问答对时，以本节为准。

### 8.1 钉钉通讯录同步（已完成）

- 已修复非根部门名称、最深主部门选择、部门主表外部标识投影和本地手工绑定账号隔离。
- 已实现全量部门树、成员分页、多部门关系、限流重试、失败保留旧快照、重复任务保护、30 分钟失败回收和默认每小时周期同步。
- 同步入口已归入超级管理员“用户管理”，展示配置、状态、部门数、成员数、时间与错误信息。
- 真实环境同步通过：483 个部门、4954 名成员，快照和主表投影数量一致，真实部门名称无哈希占位。
- Yuxi 共享权限按请求直接读取用户当前部门计算，没有 rag-agent 的 Redis ACL 用户缓存，因此 7.1 所述 `changed_users` 缓存失效在当前架构不适用。

### 8.2 表单问答对（已完成）

- 已按知识库补齐搜索、状态筛选、分页、新增、编辑即发布、停用、删除、索引状态和错误展示。
- 已接入持久化发布任务与真实 Milvus 索引；问题和别名参与向量召回，答案不进入向量文本，高置信命中在 Agent 前直接返回 Markdown 原文。
- 已移植噪声词归一化、字符重合和分差门控；低置信或歧义命中回退普通文档检索，不返回 QA 合成块。
- 问答对合成文件已从普通文档列表、目录统计和文件工具中隐藏，并跳过知识图谱抽取；换版、停用和删除会清理旧索引。
- 真实 Milvus 验证通过：新增、别名命中、原文返回、换版、旧版移除、合成文件隐藏和删除清理均符合预期。
- Yuxi 当前没有文档资产引用表和孤儿资产清理任务，因此 7.2 所述 `_sync_asset_refs` 不适用；图片引用继续随 Markdown/`image_refs` 保存。

### 8.3 回归结果

- Docker 内后端定向测试 24 项通过。
- Ruff、前端 ESLint 和 Vite 生产构建通过。
- 已在浏览器验证“用户管理”的钉钉同步卡片和知识库“问答对”页的列表、筛选及新增编辑弹窗。
