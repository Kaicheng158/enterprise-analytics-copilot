# UI MVP — Figma 设计交接规范 v1

> 历史交接稿：四页面导航及仅中文要求已被 Workspace v4.1 双语工作台取代。当前以 [v4.1 交接](workspace-v41-frontend.md) 与用户附件为准；保留下文用于既有 Sales Agent API/安全边界对照，不沿用为当前导航。

状态：四个桌面核心页面第一版已完成（用户确认）；完整设计交付尚未验收。接下来完成 Design System、390px 移动端、异常状态和可点击 Prototype。代码基线 Phase 5.7e `263629c`；本轮只同步文档。

设计文件：[Enterprise Analytics Copilot — Figma MVP](https://www.figma.com/design/GxZ8cOgDc4Wj7XUJN1aAJO)。进度来源为用户确认；本轮未检查 Figma 节点或判定设计验收通过。
依据：[审阅整合稿](mvp-figma-design-brief.md)、`frontend/agent/`、`backend/agent_api/{api,models,auth}.py`、`backend/planning/models.py`。API 字段以代码为准；本文不新增/修改 API Contract。

## 1. Figma 文件与范围

现有设计文件沿用下方组织规范；页面组织仍需在后续设计交付中核对：

- `01 MVP — Buildable`：四个核心页面，每页 desktop 1440 与 mobile 390 两个 frame，至少 8 个主 frame。
- `02 Future Concepts`：Dashboard、AI Chat/RAG、Task History、Account/Workspace，全部明显标注 Future / Not available；不连接 MVP 成功流程，不放可点击的假功能入口。
- `03 Components & Tokens`：变量、文字样式、Auto Layout、组件和状态 variants。
- `04 Flows & API Notes`：可点击原型、错误分支、字段映射、开发注释和未决项。

采用中文界面，保留 Task ID 等必要技术名称。仅 light theme；配色为提案，不锁定品牌。使用可编辑文字/组件，不能用扁平截图交付。文档 synthetic 示例可用于原型，必须标注，不能包含真实凭证。

## 2. 四个核心页面

| 页面 | 内容与动作 | 退出/分支 | 数据依据 |
|---|---|---|---|
| Local Access | 产品与本地范围说明、密码式 key 输入、“继续”、可选已知 Task ID 查询入口 | 继续到表单；有 Task ID 才能无提交查询；401 返回凭证输入，403 停止 | key 仅用于 header；无登录/认证探测 API |
| Sales Comparison | 基准季度、比较季度、地区、synthetic 声明、“提交分析” | 202 转状态；本地同季度校验；提交未知保留原请求并提供恢复 | AgentTaskRequest |
| Task Status | Task ID 复制、状态、步数、更新时间、当前参数；手动重新查询 | completed 到结果；failed/needs_reconciliation 停止；网络中断只暂停查询 | AgentTaskResponse |
| Sales Result | 两期销售额、差额、增长率、货币/地区、来源表/数据版本；“新建比较”“查看任务状态” | 新建显式新任务；不提供取消、下载或分享假按钮 | result: SalesGrowthComparison |

四个页面可实现为 `/agent` 内四个视图；不要求新前端路由或后端路由。Local Access 不宣称 key 已验证，不因“继续”自动调用 POST。清除凭证仅清理本地内存，不称为服务端登出，不取消任务。

模式提示区：LOCAL / SYNTHETIC / FAKE 必须来自已核实的部署配置；目前无模式 API，未确认时显示“运行模式未确认”。FAKE 不等于真实 DeepSeek，SYNTHETIC 不等于真实客户数据。模式确认方案属于实现前待决项，不靠模型响应推测。

### 响应式布局

- Desktop 1440：居中 max-width 1120，外边距至少 32，12 列/24 gutter；Local Access 表单 max-width 480；比较表单 8 列、说明 4 列；状态主区 8 列、来源/参数区 4 列；结果卡片 2–4 列。
- Mobile 390：左右 16，单列，卡片纵向排列，主按钮全宽；长 UUID/来源换行并提供独立复制按钮；不可依赖 hover；无横向页面滚动。
- 小于 768 转单列；在 320、768、1024 宽和 200% 缩放复核。键盘弹出时不遮挡表单/错误；长中文不得截掉关键错误。
- 最小点击区域 44×44，焦点顺序遵循阅读顺序；状态用图标+文字，不只用颜色。

## 3. Design Tokens（提议值，视觉审阅后确认）

Figma variables 用语义名绑定，避免每个节点独立硬编码。

| Token | 建议值 / 用途 |
|---|---|
| color/bg/canvas、surface | #F8FAFC、#FFFFFF |
| color/text/primary、secondary | #0F172A、#475569 |
| color/action/primary、hover、on-primary | #1D4ED8、#1E40AF、#FFFFFF |
| color/border/subtle、control | #E2E8F0（装饰）、#64748B（可辨识控件边界） |
| color/focus | #1D4ED8，2px outline + 2px offset |
| color/status/success、warning、danger、info | #166534、#92400E、#B91C1C、#1E40AF |
| color/status/*-surface | #F0FDF4、#FFFBEB、#FEF2F2、#EFF6FF |
| spacing | 4、8、12、16、24、32、48、64 px |
| radius | control 8、card 12、pill 999 px |
| typography | 系统中文 sans-serif；12/18 caption、14/20 label、16/24 body、20/28 section、28/36 title、36/44 metric |
| weight / numeric | 400/500/600；金额 tabular numerals，禁止靠字重区分正负 |
| border / shadow | 1px；overlay 才用轻阴影，卡片优先边框 |
| motion | 120–200ms，reduced-motion 时移除非必要动画 |

普通文字对比目标 ≥4.5:1，大文字/关键 UI 边界 ≥3:1；这是交付检查项，不是已完成 WCAG 认证。需要检查实际颜色组合、disabled 与 focus。不要将红/绿直接等同于财务好坏，差额始终显示符号与数值。

## 4. 组件与变体

| 组件 | 必备 variants/properties |
|---|---|
| AppShell / ModeNotice | desktop/mobile；mode confirmed/unknown；范围与临时数据库提示 |
| LocalKeyInput | empty/filled/focus/error/disabled；仅掩码显示，无回显日志 |
| PeriodSelect / RegionSelect / FieldError | idle/focus/invalid/disabled；label、helper、error 关联 |
| Button | primary/secondary；idle/hover/focus/loading/disabled；提交加载防重复 |
| TaskStatusPill | queued/running/completed/failed/needs_reconciliation；中文标签与原始状态映射 |
| InlineAlert | info/warning/error；标题、可执行动作；401/403/404/409两类/422/429/502/503/network |
| TaskProgress / TaskIdCopy | completed_steps/total_steps；复制成功/失败；无 ETA 或虚构步骤名 |
| MetricCard / SourceMetadata | 正/负/零/不可用；period/currency/source/dataset；不把空值显示为0 |
| ComparisonBarChart（可选） | 两根柱；同量纲、零基线；可读文字表格备选；只画 result 数字 |
| Loading / EmptyState | 提交中、查询中、尚未提交、未找到任务；不要占位假金额 |

源数据为不可信文本，采用安全文本渲染。来源不是可下载链接；没有 source URL 时不生成“查看原文”链接。

## 5. API 到视图的字段映射

| 视图字段/行为 | 真实字段 | 约束 |
|---|---|---|
| 本地 key | `X-Internal-Agent-Key` header | 仅内存；不能进入 URL/storage/日志/Figma |
| 提交身份 | 服务端固定 principal/scope | UI 不传用户/租户/工具授权字段 |
| 任务类型 | body.task | 固定 compare_quarterly_sales |
| 基准/比较季度 | body.baseline_period/comparison_period | 2026-Q1、2026-Q2、2026-Q3；不同值 |
| 地区 | body.region | all/north/south；UI 中文标签、请求保留 enum |
| 恢复提交 | Idempotency-Key header | 同逻辑提交绑定不可变 payload，8–200 字符 |
| 任务标识 | task_id | UUID；GET path，复制但不当成认证凭据 |
| 当前状态 | status | 五种枚举；202/200 不代表 completed |
| 进度 | progress.completed_steps/total_steps | 只计数；不生成步骤内容或 ETA |
| 刷新信息 | updated_at/state_version | 时间带时区；拒绝旧响应覆盖新状态，版本仅在同任务比较 |
| 重复创建提示 | created | false 表示复用任务，不等于失败 |
| 两期金额 | result.baseline_total_sales/comparison_total_sales | Decimal string；对应 result 中季度 |
| 变化 | result.absolute_change/growth_rate_percent | 直接显示服务端计算；不重新算增长率 |
| 口径 | result.region/currency | USD；与 request 对照，不能静默改单位 |
| 来源 | result.source_relation/dataset_version | 原样安全显示；synthetic-sales-db-v1 |
| 任务失败 | error.code/message | failed/reconciliation；仅安全文案，不回显凭证/堆栈 |
| 技术详情（折叠） | response_version/policy_version/runtime_version | 按返回值显示；不伪造 Prompt/工具 trace |

仅使用 POST `/internal/agent/tasks` 与 GET `/internal/agent/tasks/{task_id}`。没有任务列表、取消、认证探测、模式发现、Agent 成本查询 API。完整请求/响应说明见[设计准备文档](mvp-figma-design-brief.md)。

## 6. 异常原型必须覆盖

- 401：重新输入凭证，保留非敏感请求上下文；403：访问受限，不自动重试。
- 404：任务不存在或不可见；说明本地数据可能已清理，不推断他人任务存在。
- 409 idempotency conflict：停止并说明 key 与请求不匹配；409 status changed：仅 GET 有界重查。
- 422：同时支持 `detail` 数组和 `{code,message}`；字段级安全提示，不直接渲染原始 input/header。
- 429：队列忙碌，保留请求与 key；502：计划未被接受；503：按 code 区分功能未开启与服务不可用。
- POST 超时/断网：结果未知，不称任务失败；保留 key/payload，手动恢复同一提交。
- GET 断网/超时：保留 task ID 与最后状态，标明“状态未更新”；手动重查，禁止重新 POST。
- needs_reconciliation：停止；“执行结果需操作员核查”，没有自动重跑按钮。
- 非 JSON/字段异常/不支持响应版本：安全的响应错误，不以缺失金额补零。

## 7. Future Concepts 隔离

| Concept | 真实基础 | 尚缺 |
|---|---|---|
| Dashboard | 局部运行 telemetry | 授权聚合 API、可靠持久指标、生产可用性 |
| AI Chat / RAG | 各自独立 API | 统一身份/网关/产品路由、Agent+RAG 组合、稳定 UI 字段消费契约 |
| Task History | 单任务查询、底层存储 | 授权列表/分页、非 disposable 存储；当前会话 task ID 不等于历史列表 |
| Account / Workspace | 固定内部 key/scope | 正式登录注册、用户管理、多租户、角色权限、会话 |

这些页面即使画概念稿也必须独立、不可从 MVP 主流程误入。不提供上传、账单、模型切换等可用承诺。

## 8. 交付检查与未决项

设计师交付 8 个响应式主 frame、组件 variants、关键异常分支、可点击流程、字段注释；开发者逐项核对，不以视觉截图替代交互契约。

待确认：品牌主色/字体；是否首版包含双柱图；受信运行模式标识的部署注入方式。保守默认：中文 light theme、金额卡片优先、未知模式明确标注。无新接口前，不设计认证成功探测。凭证和请求恢复暂只保留内存，刷新后需重新输入并手动查询 Task ID；跨刷新恢复另行设计，不承诺已有能力。

当前状态：Phase 5.8a 本地实现与交接规范已核对，Figma 原生同步仍为 Pending。
