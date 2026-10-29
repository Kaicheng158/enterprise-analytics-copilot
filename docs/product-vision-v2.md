# Product Vision v2 — 数据分析助手 / Analytics Copilot

2026-10-28 · 已确认产品方向的规划整理；实现阶段与技术方案仍待审阅。
本文是新的产品规划基准，不追溯改变任何历史验收、Prompt、SHA、API 或评估标准。旧文档保留。关联：[开发路线](product-roadmap-v2.md) · [数据与协作架构初稿](project-data-architecture-v2.md)。

新增功能进入实现前必须通过 [Feature Value Gate v1](product-roadmap-v2.md#feature-value-gate-v1)：证明减少用户工作量，并明确服务 Investigate、Simulate、Decide & Review 或 Collaborate 主线。

## 产品核心与首要用户

面向需要核对业务数据、解释差异并推进决策的分析人员与业务负责人。主链路是：**上传业务文件 → 解析与验证 → Analysis & Chat → 可复算分析与可视化 → 按问题复杂度输出简短回答或完整报告**。连接数据源为后续入口。Sales Comparison 保留为已实现模板与回归基线，不再定义整个产品。

回答长度不是正确性的替代指标。一个指标或直接比较优先简短回答，附计算口径、来源和必要限制；跨来源调查、多个交付物或用户明确请求时生成完整报告。允许用户选择输出深度；不得因选择完整报告自动扩大授权、计算范围或费用上限。复杂度判定、报告生成与持久化尚未实现。

## 四个产品能力方向

**A Data Detective、B Decision Lab 与 E Decision Follow-through 是目标产品的核心分析—决策闭环；D Project Collaboration 是让该闭环可长期延续的核心协作能力。** 分阶段上线只控制依赖和风险，不取消对任一方向的产品承诺。首版只实现其中受控、可验证的最小范围，未上线部分继续明确标记为 Planned。

| 方向 | 用户价值 | 结果与可信边界 | 发布位置 |
|---|---|---|---|
| A Data Detective | 跨文件核对、异常调查、差异定位 | 对齐键、单位、期间及粒度；展示缺失/重复/未匹配记录、差异与来源；异常不等于错误或因果证明 | 首版先支持明确键的双表核对；通用异常调查后续 |
| B Decision Lab | 比较可控假设下的方案 | 基准数据版本、显式假设、公式、输入范围及敏感性；标记模拟，不冒充预测或因果结论 | 可信计算闭环之后 |
| E Decision Follow-through | 记录行动、目标并对照实际结果复盘 | 区分建议、已批准行动、已记录执行与实测结果；目标单位/期间/来源一致，不能把相关变化归因于行动 | 首版仅保留后续入口规划 |
| D Project Collaboration | 持久化项目、邀请成员、接续分析、版本和分析分支 | 私有默认、项目与文件独立授权、撤销与审计；分支固定数据/口径版本，不自动合并结论 | 单用户项目先行，成员协作后续 |

字母沿用用户确认的方向标签，不增加 C 产品支线。行动记录不授权发送邮件、修改业务系统或执行其他外部副作用。

## 集中澄清交互

先执行允许的确定性检查，再将当前可发现的阻塞项合并为一次询问：必需文件、匹配键、期间、货币、指标定义、缺失值处理等。区分“无法继续的必要项”和“可选改进”，说明每项影响；允许用户跳过可选项。回答绑定文件 revision 与问题版本，已确认的信息不得重复索取。新文件或真实新歧义出现时允许再次集中询问，不能为了少打断而猜测关键口径。未经确认不得执行依赖关键假设的任务。此交互需新的产品级分析请求契约，不向现有严格 Agent API 偷塞 question/file 字段。

## MVP 与 Future Features

**当前可演示，不等于目标 MVP 完成：** Workspace 双语静态 UI、明确 Demo Login/Register、固定样本图表；独立本地 synthetic sales Agent；已验收的受控 Planner/Worker 与独立 Chat/RAG 后端。Workspace 尚未接这些 API。正式用户身份、文件上传、项目、完整报告均未实现。

**目标第一版受控 MVP：** 单一所有者的私有项目；有限 CSV/XLSX 上传、验证和预览；受支持的聚合/比较及有限双表核对；集中澄清；来源可追踪的简答、图表、版本化报告页面；持久任务查询；配额、期限和删除路径。对外接收真实用户数据前必须具备真实身份、项目/资产授权及数据隔离。先在本地使用 synthetic 文件和 Fake/Mock 验证，不能以 Demo 登录替代这个条件。

**后续：** 通用 Data Detective、Decision Lab、行动/目标复盘、成员邀请与版本分支、PDF/DOCX 解析、现有 RAG 的产品集成、报告导出、数据源连接、SSO/企业治理、计费/BYOK。RAG 后端已存在，未完成的是上传文档到项目内问答的完整产品链路。首版不承诺任意 SQL、任意业务问题、真实预测或自动执行业务行动。

正式注册上线后才增加可跳过的首次引导：创建私有项目 → 导入样本或文件 → 核对数据 → 提问。跳过不影响正常使用；不制作 Demo 教程或“演示如何工作”按钮。

## 当前模块核对与复用

| 已有代码 | 可复用 | 必须新增/补齐 |
|---|---|---|
| frontend/workspace/{app,state,i18n}.js、locales、tokens.css | 七项导航、双语、语言隔离、卡片/表单和安全文本渲染 | 安全 transport、真实状态、上传/预览/项目状态；当前 CSP 禁止网络 |
| frontend/agent/；backend/agent_api/{api,models,auth}.py | 固定销售模板 POST/单任务 GET、幂等及服务端 scope 边界 | 产品身份适配、授权列表与项目关联；不把内部共享 key 当用户账户 |
| backend/planning/structured_provider.py、validation.py、deepseek_planner_adapter.py | 严格 Plan、服务端校验与预算 | 新任务类型的独立契约/工具/评估；当前限定季度销售 |
| backend/planning/checkpoint_store.py、worker_runtime.py、task_queue.py、worker_supervisor.py、reconciliation.py | Checkpoint/Receipt、Lease/CAS、幂等、队列及未知结果停止 | 非 disposable 产品部署、项目任务关联、保留策略；不是 Project State |
| backend/tools/registry.py、executor.py、sales_db.py | 授权、固定参数化查询、精确金额与来源 | 受控文件数据集工具、指标口径及版本校验；禁止任意代码/SQL |
| backend/rag/{ingestion,chunking,embedding,retrieval,context,generation}.py | Text/Markdown、向量存储、检索、上下文预算、引用验证 | 浏览器上传、资产权限与删除级联、PDF/DOCX、项目授权集成 |
| backend/config.py、现有 telemetry 与 eval | 配置、估算成本、安全日志和测试方法 | 产品审计、持久 usage/quota、数据集级回归；冻结历史不可改 |

## Figma / Workspace 影响与交接

现有七项导航保留；1440/768/390、EN/中文、文字品牌与批准的 Auth 色板不重做。Figma v5 参考 https://www.figma.com/design/WPjZjK4xZtfRx47ekBjmeh 。本轮未调用 Figma；Auth 原生同步仍待办，不宣称已完成。

| 现有入口 | v2 规划职责 | 新交接状态 |
|---|---|---|
| Overview | 私有项目入口、继续最近分析、上传入口 | 无项目/无权限/来源过期；右上重复“询问 AI 助手”列为待移除，本轮不动代码 |
| Data Library | 项目资产、上传、解析/验证/预览、配额与生命周期 | 隔离检查、解析失败、口径待确认、超额、到期、删除中/已删除；不能显示假成功 |
| Analysis & Chat | 数据选择、批量澄清、分析、Data Detective；以后情景与复盘 | 未提交/澄清中/真实任务状态/证据不足；来源权限变化后阻止执行 |
| Visualizations & Reports | 图表、简答与 Report Studio 的完整报告视图 | Draft/完成/来源失效/模拟；报告需有数据版本、单位、公式与来源 |
| Activity & Tasks | 授权任务历史及真实进度 | 无任务/查询暂停/失败/待协调；不是项目长期记忆 |
| Settings | UI、回答、报告语言；未来保留策略/配额 | 切 UI 不执行 Agent、不改变数据、不覆盖历史报告 |
| My Account + 独立 Login/Register | 现在 Demo，未来真实身份/会话 | 注册/邀请/权限服务未实现；引导仅在真实注册后 |

Project 是跨页面的上下文（未来选择器），暂不增加第八导航。Project Settings 可挂在现有 Settings，成员入口仅在授权能力上线后启用。A/B/E 是分析模式，D 是共享能力，不各自新建顶层导航。

Figma 后续只新增版本化规划画板，保留旧版。每个组件标明“已接 API / Mock / Planned”及数据来源；缺少 API 的加载、共享、恢复、导出不能画成已可执行。设计交付重点是上传→预览→一次澄清→分析→简答/报告、撤销权限、源文件到期及恢复失败状态，而非重画整个 UI。

## 待决定事项

下一实现前固定支持的 CSV 编码/XLSX sheet 与公式策略、最大文件/行数/单元格数、配额与期限。首个受支持指标/双表业务范围需有明确验收数据。真实 Auth 方案、存储后端、部署区域与备份保留期需要单独技术决策；本轮不替用户选择供应商或宣称已满足合规要求。
