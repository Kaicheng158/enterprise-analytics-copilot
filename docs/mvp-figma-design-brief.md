# Enterprise Analytics Copilot — 首版 MVP / Figma 设计准备

> 历史设计参考：下文保留销售模板时期的四页面方案。当前 UI 设计基准已更新为 Workspace v4.1，导航与产品定位以 [v4.1 实现与交接](workspace-v41-frontend.md) 为准；下文“UI 尚未开始”等进度仅描述历史阶段。既有 API 映射仍为参考，不代表已实现上传、身份或通用分析。

状态：Figma 文件已创建，Local Access、Sales Comparison、Task Status、Sales Result 四个桌面核心页面第一版已完成（用户确认）；UI 实现尚未开始。

设计文件：[Enterprise Analytics Copilot — Figma MVP](https://www.figma.com/design/GxZ8cOgDc4Wj7XUJN1aAJO)。进度来源为用户确认；本轮未检查 Figma 节点或判定设计验收通过。
来源：用户提供的 `mvp-figma-design-brief-reviewed.md`。文档中的建议作为设计输入，不授权执行其后续开发或付费步骤。本轮范围以用户最新请求为准。

交接入口：[Figma 规范](ui-mvp-figma-handoff.md) · [独立 Frontend/UI 工作流](ui-mvp-workflow.md)。
代码基线：Phase 5.7e，commit `263629c`。本文是产品设计提案；不改变已有 API、功能开关或冻结评估标准。

## 1. 首版定位与边界

首版主流程是**本地、受控的 synthetic sales 两季度比较**：选择季度与地区 → 提交任务 → 查询真实执行状态 → 查看可复算结果与数据来源。目标用户是获准使用本地演示的分析人员，当前没有真实用户账号体系。

不要将它设计成能处理任意企业问题的通用聊天 Agent。真实 Planner Adapter 已存在，但本次产品组合只做 Mock 验证；Fake 演示必须显式标注。RAG 与普通 Chat 是独立能力，没有自动路由或与销售 Agent 合并。

当前 Worker 是队列执行者，由同一服务进程中的 supervisor 管理线程，并非已部署的独立生产 Worker 服务。当前本地 profile 创建 disposable PostgreSQL 数据库，正常关闭会清理；不能承诺永久历史、跨服务重启保留任务或生产 SLA。

## 2. 功能分类（以代码为准）

### A. 已实现且有现成接口可对接

| 功能 | 当前接口/位置 | 设计可用范围与限制 |
|---|---|---|
| 本地销售 Agent 页面 | `/agent`，`frontend/agent/` | 静态 HTML/CSS/JS，当前只支持表单、轮询、文本结果 |
| 提交销售比较任务 | `POST /internal/agent/tasks` | 默认关闭；loopback；共享内部访问密钥；服务端固定身份/scope |
| 状态与结果查询 | `GET /internal/agent/tasks/{task_id}` | queued/running/completed/failed/needs_reconciliation；有进度和来源；不是 SSE |
| 幂等提交 | `Idempotency-Key` header | 同 key+同请求返回原任务；冲突 409；前端重试应复用 key |
| 销售分析结果 | `SalesGrowthComparison` | 两期销售额、差额、增长率、地区、USD、表名和 dataset version；不是 LLM 随意生成数字 |
| 普通结构化问答 | `POST /chat`，主应用 | analytics-v2；不查数据库、不自动调工具；真实调用可能收费 |
| 独立文档 RAG 问答 | `POST /rag/chat`，RAG 应用 | 默认关闭；服务端文档授权；需预先入库、向量化；非用户可选任意资料库 |
| 健康检查 | `GET /health`、`GET /health/db`，主应用 | 不应假设独立 Agent app 同样提供这些路由 |

“API 可对接”不意味着已提供统一网关、统一认证或可公开上线。Agent、Chat、RAG 是不同应用入口；跨入口部署、同源代理和鉴权统一仍需实施。当前页面不提供 Chat/RAG 入口。

### B. 已有后端基础，但 UI 所需 API/产品化尚缺

| 后端基础 | 还需要什么 | 首版处理 |
|---|---|---|
| Execution/Checkpoint/Receipt | 授权任务列表、分页、可公开步骤摘要/审计查询契约 | 只展示现有 progress，不伪造逐步 timeline |
| Lease/CAS/Recovery/Reconciliation | 操作员权限、审核与协调 API、安全操作流程 | `needs_reconciliation` 显示停止并联系操作员；不放“自动重试”按钮 |
| Queue / Worker metrics | 经授权健康/队列/运行指标 API；持久化指标 | 不画实时运维 dashboard 假装已接通 |
| Token/cost telemetry | 按任务查询、持久化账本和授权统计 API | Agent 响应没有费用字段，不显示假费用；Chat 可显示实际估算值 |
| 文档 ingestion/chunking/embedding | 受控上传、文档列表、权限、处理状态、删除与重建 API | 不提供可用的上传/管理页面 |
| Provider abstraction / config | 服务端可选模型能力与授权配置接口、多 provider 实现 | 不给用户 model/temperature/prompt 切换控件 |
| Planner→Worker 数据通路 | 长期非 disposable 存储、独立进程运维与恢复管理 | 标注本地预览限制 |

任务取消、重新执行的完整语义尚未实现；不能把恢复基础等同于已有取消功能。Agent+RAG 组合任务仍是 roadmap 5.8，不能通过前端简单拼接就宣称实现。

### C. 尚未实现（明确排除首版）

- 正式登录、注册、退出会话、找回密码、SSO、用户管理。
- 企业/工作区/团队管理、多租户权限、成员邀请、角色分配和生产 RBAC。
- 用户 API key/BYOK 管理、配额、计费、订阅、成本账单。
- 任意业务提问自动规划、任意 SQL、可写工具、完整 Human-in-the-loop 审批。
- 报告导出/分享、持久化聊天记录、自动图表 API、生产部署与可用性保证。

内部共享 key 验证不是用户登录；固定 scope 检查不是多租户产品。

## 3. 推荐 Figma 页面结构

先制作下列 4 个 frame，可最终合并为一个响应式工作台；路由名称为设计建议，不是新增 API。

| Frame | 内容 | 必须提供的状态 |
|---|---|---|
| 01 本地访问/连接 | 产品名称、synthetic 数据提示、本地访问密钥、受控范围说明 | 未连接、认证失败、功能关闭、服务不可达；不显示“注册” |
| 02 新建销售比较 | 基准期、比较期、地区、提交按钮、任务范围说明 | 默认、同季度错误、提交中、422、429；不放自由文本通用提问框 |
| 03 任务详情 | task ID、实际 status、completed/total steps、更新时间 | queued、running、短暂读取冲突、网络中断、failed、needs_reconciliation |
| 04 分析结果 | 两期金额、绝对变化、增长率、地区/货币、来源表与数据版本 | 成功、零增长、负增长；不可计算时显示服务端停止状态而非编造 0% |

结果可设计金额卡片和由返回数字绘制的简单双柱图；图表属于后续 UI 工作，不是已经实现的能力。只用服务器返回数字，不将模型建议渲染成事实。

文档问答仅作为 **Future Concepts 独立 frame**（不属于首版可开发交付）：query、summary/facts/interpretation/limitations、citations/sources、空证据/功能未开启/失败。先展示设计，不承诺与 Agent 同源或同一鉴权。账号中心、文档管理、计费页不纳入本轮可交付 MVP。

## 4. 用户流程与交互规则

1. 操作员先启动 Fake 本地 profile；页面明确显示演示性质。设计中的运行模式提示目前没有能力发现 API，需要由部署配置注入，不可从模型回答猜测。
2. 用户输入内部访问 key，选择不同季度和地区。服务端身份/scope、Prompt、工具、预算由服务器控制，前端不允许覆盖。
3. 为一次逻辑提交生成并保存一个 idempotency key（保留在当前交互状态）。提交超时或丢失响应时复用该 key 和原始 payload；用户改参数后新建 key。
4. POST 成功后保留 task ID，轮询 GET。提交按钮防重复点击；不要把网络超时描述为执行失败。
5. GET 409 `agent_status_changed` 可以有界重试；409 幂等冲突不可当成同一种错误。429 显示忙碌；不无限自动重提。
6. completed 展示结果与来源；failed 展示安全错误；needs_reconciliation 停止轮询并提示人工检查，不能自动重新执行未知结果。
7. 网络中断保留 task ID，并提供“重新查询”交互；当前页面尚无手动 task ID 查询控件，这属于后续 UI 实现。服务重启后 disposable 任务可能不存在。

当前 UI 每次点击生成新 key，只有约 60 秒有界轮询，没有防双击和可靠的丢响应恢复；上述设计是待实现的交互改进，不声称现状已支持。

无精确剩余时长时仅显示状态和步骤计数；不制造百分比或完成时间。可访问性要求：键盘操作、清晰焦点、字段错误关联、状态 live region、状态不只靠颜色；金额/来源可复制。正文必须安全转义，模型和检索文本不能作为 HTML 执行。

## 5. 现有 Agent API Contract

依据：`backend/agent_api/api.py`、`models.py`、`auth.py` 和 `backend/planning/models.py`。运行实例的 `/openapi.json` 是字段约束核对来源。

### 提交

`POST /internal/agent/tasks` → HTTP 202。
Headers：`Content-Type: application/json`；`X-Internal-Agent-Key: <本地内部凭证>`；`Idempotency-Key: <8–200字符>`，格式 `[A-Za-z0-9][A-Za-z0-9._:-]{7,199}`。

```json
{
  "task": "compare_quarterly_sales",
  "baseline_period": "2026-Q1",
  "comparison_period": "2026-Q2",
  "region": "all"
}
```

task 仅允许上述值；period 仅 `2026-Q1/Q2/Q3`，两期不能相同；region 仅 `all/north/south`。字段必填；额外字段禁止。不能提交 scope、tools、SQL、system prompt、model 或自然语言 question。

### 查询与响应

`GET /internal/agent/tasks/{UUID}` → HTTP 200，携带同一内部认证 header，无请求 body。POST/GET 都返回 `AgentTaskResponse`：

| 字段 | 类型 / 语义 |
|---|---|
| response_version | 固定 `internal-agent-task-v1` |
| task_id | UUID |
| status | queued / running / needs_reconciliation / completed / failed |
| created | boolean 或 null；POST 重复为 false；不要以它判断任务完成 |
| request | 原始严格任务参数 |
| progress | completed_steps 非负；total_steps 1–8；不可据此推断具体步骤名称 |
| result | completed 才有 SalesGrowthComparison，否则 null |
| error | failed/reconciliation 才有 code、message，否则 null |
| state_version | 非负整数，用于状态版本识别，不允许客户端写入 |
| policy_version / runtime_version | 服务端返回的版本字符串，不在 UI 硬编码 |
| updated_at | 带时区时间戳 |

成功 result 示例（已在 Mock Planner + 实际 PostgreSQL 测试验证的 synthetic 全地区数据）：

```json
{
  "baseline_period": "2026-Q1",
  "comparison_period": "2026-Q2",
  "region": "all",
  "currency": "USD",
  "baseline_total_sales": "400.00",
  "comparison_total_sales": "600.00",
  "absolute_change": "200.00",
  "growth_rate_percent": "50.00",
  "source_relation": "analytics.sales_orders",
  "dataset_version": "synthetic-sales-db-v1"
}
```

Decimal 金额/百分比以 JSON string 输出；格式化不能造成二次浮点误算。前端使用服务端计算结果；基准期为零时不要自行补为 0% 或无穷大。Agent response 当前不含自由文本报告、完整 Plan、工具原始输出、Receipt、tokens 或 cost。

### 错误

业务错误通常是 `{"detail":{"code":"…","message":"…"}}`。Pydantic 请求校验 422 的 `detail` 是数组，前端需兼容，不能假设总有 message。

| HTTP | 含义 / UI 行为 |
|---|---|
| 401 | 内部凭证缺失/错误，重新输入，不回显 key |
| 403 | 非 loopback 或无 scope 权限；停止 |
| 404 | 任务不存在或不可见；不泄露其他 scope 是否存在 |
| 409 | `agent_idempotency_conflict` 停止；`agent_status_changed` 有界重查 |
| 422 | 请求字段/UUID/幂等 key 校验失败，定位可修正字段 |
| 429 | `agent_queue_full`，保持原请求，告知容量限制 |
| 502 | `agent_plan_rejected`，不能绕过 Validator 执行 |
| 503 | 功能关闭、runtime/persistence/integrity 不可用，显示安全错误 |

任务级 error.code 为 `agent_execution_failed` 或 `agent_reconciliation_required`。不要把 HTTP 成功等同于任务成功。

## 6. 独立 Chat / RAG Contract（供后续页面参考）

`POST /chat`：body `{"user_message":"…"}`；兼容旧 message 名称但不能同时传两者。response 含 `answer`（summary:string；facts/interpretation/limitations:string[]）、provider/model、usage（prompt_tokens/completion_tokens/total_tokens 等）、latency_ms、finish_reason、prompt_version/SHA、request_id、retry_count、estimated_cost、cost_currency、pricing_version/tier、cost_complete。成本不完整时不能显示为确定账单。普通 Chat 不提供企业数据访问。

`POST /rag/chat`：body `{"query":"…"}`，非空字符串，最长 8000 字符，额外字段禁止。response：contract_version=`rag-response-v1`；answer；citations（field/index/source_label）；sources（仅服务端 context metadata）；generation；token_budget；context_sha256。后三类 dict 尚不是稳定的细粒度 UI 强类型契约；对接前需冻结消费字段，不自行假定 page/document 字段必有。只呈现当前 response 的实际 source，不伪造引用或原文件下载地址。RAG 默认关闭、授权文档 allowlist 默认空。

这两个接口没有与 Agent 统一的用户身份/租户产品层。不能直接开放给公网；本轮不接入真实调用。

## 7. Figma 交付与确认事项

交付应包含：上述主流程 frame、所有关键异常/空/加载状态、字段与真实 API 对照、桌面布局及窄屏适配、可复用状态/金额/来源组件。明确 mock data 标记；不得在设计稿中包含真实凭证、个人路径或敏感业务数据。

建议确认：首版只做销售比较主流程；将 RAG 独立标记为 Future Concepts；不做登录注册假页面；采用中文主界面并保留英文技术标识。确认后才能进入 UI 开发。

下一步依次完成 Design System、390px 移动端、异常状态与可点击 Prototype，再进行 UI/UX 审阅确认。新增 API（任务列表、取消、审计、能力发现等）需独立立项和契约设计；本文不创建任何这些路由，不修改数据库或 frozen artifacts。


---

## 8. 设计审阅补充 v1.1（2026-10-23）

> 本节属于**新增的设计建议和开发验收要求**，不表示已有后端/API 已实现。原文第 1–7 节保留为代码核对基线。不要修改 Phase 2–5 冻结评估资产和历史判定。

### 8.1 双轨页面规划，区分能开发与仅能概念设计

**轨道 A：本轮 API-backed 可实现 MVP**

1. `Local Access`：本地连接及共享内部 key，明确 Fake/Mock/Synthetic 模式；不称为用户登录。
2. `Sales Comparison`：两个不同季度（`2026-Q1/Q2/Q3`）和地区（`all/north/south`）选择；只允许 `compare_quarterly_sales`。
3. `Task Status`：`queued`、`running`、`completed`、`failed`、`needs_reconciliation`；显示 task ID、完成步数、更新时间。
4. `Sales Result`：服务端返回的两期金额、绝对变化、增长率、货币、source relation、dataset version；可选基于返回数值的简单双柱图。

**轨道 B：独立标为 Future / Concept 的 Figma 页面，不纳入本轮可运行功能**

- 综合 Dashboard、自由文本 AI Chat、文档 RAG、Task History、Account / Workspace。
- Chat/RAG 已有**独立**后端接口，但没有统一身份、网关、路由与任务模型；不能直接作为当前 Sales Agent 页面中的可用选项。
- 任务历史目前没有服务端列表 API；若 UI 展示本次会话已有 task ID，必须明确“当前会话”，不能声称持久历史。
- 注册、登录、用户管理、多租户权限、上传文件、导出报告、费用账单仍属于后续功能，Figma 可概念设计但不能伪装已上线。

### 8.2 第一版可复用设计系统

建议 Figma 至少建立下面这些 editable 组件/变体：

- App shell：导航、页面标题、模式提示 `LOCAL / SYNTHETIC / FAKE`（取自受信配置而非模型推断）。
- Form controls：PeriodSelect、RegionSelect、LocalKeyInput、PrimaryButton、FieldError。
- Status：TaskStatusPill（五种服务端状态）、InlineAlert（401/403/404/409/422/429/502/503/网络断开）、Loading、EmptyState。
- Analytics：MetricCard、ComparisonBarChart、SourceMetadata、TaskProgress、TaskIdCopy。
- Layout：desktop 1440 宽与 mobile 390 宽的关键页面；统一 type scale、spacing、radius、border、focus 状态；符合 WCAG AA 可读性与键盘操作要求。
- 优先中文界面，保留 `Agent`、`Checkpoint`、`Task ID` 等必要技术标识；文案不要声称真实客户数据。

不预先锁定某个品牌配色。Figma 先给出一套清晰专业的 light-theme 设计 token，再通过视觉审查确认；避免过早同时做多套主题。

### 8.3 本轮必须修正的前端行为（对照现有 app.js）

1. **单次逻辑提交保持相同幂等键**：一旦生成 `Idempotency-Key`，与不可变请求 payload 绑定；网络中断、提交响应丢失和安全重试使用同一键与同一参数，参数变化才生成新键。
2. **防重复提交**：请求等待期间禁用重复点击；但超时不能宣告任务失败，保留可供恢复的提交状态/查询线索。
3. **轮询策略**：`GET` 409 `agent_status_changed` 做有限重查；`queued/running` 继续；`completed/failed/needs_reconciliation` 停止。网络故障时允许用户手动重新查询，避免无限轮询。
4. **错误映射**：区分 409 幂等冲突与 409 状态冲突；401 凭证错误、403 授权范围、404 任务不可见/已清理、422 字段错误、429 队列满、502 Plan 不可执行、503 系统不可用。
5. **安全显示**：绝不渲染凭证明文，不保存在 localStorage/sessionStorage、URL 或日志；使用 `textContent` 等安全文本渲染；不将 API 错误解释为金额为零。
6. **任务恢复**：提供手动 Task ID 查询；明确 disposable DB 随服务关闭可清理，不能保证跨重启读取。Figma 只设计，不擅自承诺增加服务器历史列表能力。
7. **进度展示**：只显示 `completed_steps/total_steps` 和服务端 status，不虚构完成百分比、ETA 或未提供的逐步追踪。
8. **结果规范**：USD 原始 Decimal 字符串由服务端计算；负增长要清楚显示，基数为 0 时不能前端计算 `Infinity`、`NaN` 或无依据的 `0%`；数据源字段单独可见。

### 8.4 从 Figma 到实现的明确映射

| Frame / 组件 | 现有数据/API | 必须验证的状态 | 首版限制 |
|---|---|---|---|
| Local Access / KeyInput | `X-Internal-Agent-Key` | 401 / 403 / feature off | 内部 key ≠ 用户登录 |
| Compare Form | `POST /internal/agent/tasks` | 202 / 409 / 422 / 429 / 提交超时 | 只支持固定任务参数 |
| Task Status | `GET /internal/agent/tasks/{task_id}` | queued / running / failed / needs_reconciliation / 409 / 404 | 不存在任务列表 API |
| Results / MetricCards | `SalesGrowthComparison` | completed / negative / zero / unavailable | 不展示 Agent 成本或编造说明 |
| Source Metadata | `source_relation`, `dataset_version` | 返回值存在并可复制 | 当前为 synthetic 来源 |
| RAG concept | 独立 `/rag/chat`（未来集成） | disabled / no evidence / cited answer | 非本轮 Agent 主流程 |

### 8.5 Figma 第一轮交付与验收条件

- [ ] 一个命名清晰的 Figma Design 文件；分 `01 MVP — Buildable`、`02 Future Concepts`、`03 Components & Tokens` 页面。
- [ ] 至少 4 个 buildable 主流程 Frame；desktop 和 mobile 的主流程布局；状态组件变体与关键错误/空/加载状态。
- [ ] Frame 与现有 API 字段逐项对应；`Fake / Synthetic` 提示明显，不能误称真实 DeepSeek 或真实企业业务数据。
- [ ] Figma 元素使用可编辑布局和组件，而不是一张扁平截图；统一字体、间距、图表、表单与导航。
- [ ] 不包含真实 API key、访问 token、私人路径/诊断文件；不要求任意 SQL、写工具或模型切换。
- [ ] 独立设计审查一次：页面层级、交互流程、异常状态、宽屏/窄屏、可访问性与文案。
- [ ] **设计审查通过后**，Work 才开始替换 `frontend/agent/` 的样式与交互；保持现有 API、测试和默认关闭 Feature Flags，先本地验证再提交。

### 8.6 顺序与范围控制

1. 先进行 Figma 可交互概念与视觉结构设计；本轮**不要求重写前端**。
2. 然后进行独立 UI 审查和原型调整。
3. 再进行 Phase `UI-MVP-1`：对现有 `frontend/agent/` 做增量实现，重点修复幂等、重复点击和异常体验；新增后端 API 必须先立项并定义契约。
4. 独立开展经预算预注册的真实 DeepSeek 产品链路 smoke；不能把 Fake/Mock 结果当成真实付费端到端验收。
5. 后续正式 Auth、Agent+RAG 组合任务、持久历史和生产 Worker 功能分别进入自己的工作包。


## 9. 实际接口核对补充

Local Access 仅在内存接收共享内部 key，不是登录；当前无无副作用凭证验证接口，也无运行模式发现接口。进入表单只能标记“凭证待验证”，收到受保护的提交/查询响应后才可反映该次请求的认证结果。不得为测试 key 偷偷创建任务。默认关闭、Fake 模式与 synthetic 数据必须由受信部署信息确认，未配置时显示“运行模式未确认”。

本轮仅同步已有 Figma 设计进度，不操作 Figma 文件或修改运行代码。首版四个页面是 UI 视图，不新增后端路由。

当前状态：Phase 5.8a 本地前端已完成收尾审查，Figma 原生同步仍为 Pending。

## Product Vision v2 规划索引

后续页面和能力规划以 [Product Vision v2](product-vision-v2.md)、[Product Roadmap v2](product-roadmap-v2.md) 与 [数据生命周期及项目协作架构初稿](project-data-architecture-v2.md) 为准。本 Brief 保留 Phase 5.8a 已确认的 Auth/Workspace 视觉与 API 边界，不把计划中的上传、项目、真实身份或协作描述为已实现。
