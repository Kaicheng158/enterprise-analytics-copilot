# Product Roadmap v2 — 阶段、依赖与验收

2026-10-28 · 规划草案。方向见 [Product Vision v2](product-vision-v2.md)，数据与协作见 [架构初稿](project-data-architecture-v2.md)。不覆盖 [原 Roadmap](roadmap.md) 的阶段验收历史。

## 历史与当前基线

Phase 0–2 已完成；Phase 3 为 RAG 首轮本地验收，重复运行稳定性未确立；Phase 4 Tool Calling 完成本地 gate；Phase 5.1–5.7e 已有 bounded Planner、Durable Runtime、受保护本地任务 API、队列/Worker 与 synthetic sales 产品演示。它们不是生产级多用户平台。

Phase 5.8a Workspace 双语 UI 与 Auth 细节已完成本地收尾审查；Figma 原生 Auth 同步仍待办。5.8b 已完成默认关闭的本地 Fake/Mock Workspace bridge、隔离数据库和浏览器验收；它仍只支持固定 synthetic sales 模板，不是 document-first MVP。旧表将 Phase 5 写为 LangGraph “未开始”是早期技术路线，不能用于描述当前 Bounded Planning 进度。LangGraph 仍未引入；Evaluation 已在各阶段实现，不应再把早期“Evaluation 未开始”解释为无测试基础。

为避免旧编号冲突，下面用 PV2-xx 表示新的产品工作包，不重编号历史 Phase，也不把它们误标为已完成。

## 执行顺序与阶段依赖

| 阶段 / 优先级 | 交付与范围 | 依赖 / 完成条件 |
|---|---|---|
| 5.8a 收尾 / P0（完成） | 已归档本地视觉、双语、响应式、可访问性与 Demo 安全边界；后续授权时移除 Overview 重复 CTA | 本地审查通过；Figma 原生同步继续单独标记 Pending |
| 5.8b / P0（完成） | Workspace 安全 Fake/Mock bridge；沿用固定 sales 模板与既有 API | 双击仅一次提交、同 key/body 恢复、有界单轮询、乱序防护、终态停止、错误脱敏、locale 不触发任务；loopback/同源边界、隔离 DB 与真实浏览器验收通过；保持默认关闭且不公开启用 |
| PV2-01 / P0（本地完成） | 文件/项目产品契约与威胁边界；版本化 CSV/XLSX 解析验证 foundation | synthetic/local 验收完成；不改变旧 API、不接 LLM；真实上传仍关闭 |
| PV2-02 / P0（本地完成，生产 Hold） | 私有资产持久化、隔离区、配额/期限、删除与恢复；本地可靠性验收 | PV2-01；隔离 migration 与故障矩阵通过；对象存储/KMS/容量/备份/监控仍阻断生产 |
| PV2-03a / P0（本地实现） | localhost 真实账号、凭证与 Session foundation | 独立默认关闭服务；Argon2id、HttpOnly/CSRF、过期/撤销/限流与持久重启；非公网认证 |
| PV2-03b / P0（本地实现，对外仍阻断） | 单所有者私有项目、资产授权、项目状态与任务关联 | 本地跨用户负例、撤销传播、任务/资产逐路径授权与审计通过；无公开 API、协作角色或生产部署，禁止用 Demo 或固定内部 scope 接真实数据 |
| PV2-04 / P0（04a 本地实现，04b–e 未实现） | 文件分析主闭环：04a Product Facade/契约→04b 授权上传/预览→04c 受控计算→04d Durable Agent→04e 图表/简答/版本化报告 | 5.8b+01–03；04a 仅验证身份、项目、ready Revision 和集中澄清，创建零任务；后续须独立验收精确计算、口径一致、来源可复现、无证据不编造及撤权传播 |
| PV2-05 / P1，MVP收束 | Data Detective 有限双表核对、持久历史及端到端验收 | 04；显式匹配键/粒度、重复/未匹配/差异解释、每项结论可定位行/列；安全/数值硬条件全过，独立质量与稳定性评估 |
| PV2-06 / P1 | Decision Lab：版本化假设和确定性情景计算 | 04可信计算/报告；基准与模拟分开、合法输入范围、零/负/边界、敏感性；不承诺预测/因果 |
| PV2-07 / P1 | Decision Follow-through：行动、目标、实际结果复盘 | 03项目+04证据；目标单位/期间/来源、负责人、手动记录与验证区分；不执行外部行动 |
| PV2-08 / P1 | Project Collaboration：邀请、资源级共享、版本/分支与接续 | 03身份授权+02生命周期+04结果版本；邀请过期/撤销、权限差集、敏感报告继承、并发冲突与恢复不得复活旧权限 |
| PV2-09 / P2 | PDF/DOCX、现有 RAG 产品接入、可追踪报告导出 | 02–04授权/来源/生命周期；解析/OCR质量、文档注入、跨项目隔离、源删除→向量/缓存失效；导出防公式/HTML注入 |
| PV2-10 / P2 | 连接数据源、部署运维与企业治理 | 前述各自验收；最小只读连接、secret 管理、备份恢复演练、SLO/负载/成本/审计；SSO、BYOK/计费另立工作包 |

依赖主线：5.8a → 5.8b；PV2-01 → 02 → 03a → 03b；两条汇合 → 04 → 05。06/07/08 在依赖满足后可分别推进，不要求一次全实现。09 不阻塞 CSV/XLSX MVP。对外发布始终依赖真实身份与授权、生命周期、隔离、运维验收；本地通过不自动解锁公网。

## Feature Value Gate v1

所有新增产品功能或工作包在进入实现前都必须留下可核验的 Gate 记录。候选功能必须说明它服务的主线：**Investigate**（Data Detective）、**Simulate**（Decision Lab）、**Decide & Review**（回答、报告与 Follow-through）或 **Collaborate**（Project Collaboration），并证明相对当前流程减少用户完成真实任务所需的工作量。仅写“AI 驱动”、增加页面或增加技术组件不构成价值证据。

A+B+E 是目标产品的核心分析—决策闭环，D 是长期核心协作能力。分阶段上线只表示依赖和风险控制，不取消这些承诺。基础设施候选必须指出它具体解锁哪段用户工作流、避免何种用户错误或人工操作；“为以后打基础”本身不能通过 Gate。

至少预注册一个主要工作量指标及目标，并保留安全、事实、授权、可访问性和成本边界。可选指标包括完成任务的主动步骤或时间、重复输入次数、被打断的澄清轮数、失败后的恢复步骤、人工复算/查源时间、错误或返工率。目标必须在实现与验收前固定，不能看到结果后调低。

| 决策 | 判定条件 | 后续动作 |
|---|---|---|
| **Approve** | 用户、任务和主线明确；有当前基线；有可测的工作量下降目标与代表性测试；依赖、数据、权限、安全和失败恢复边界明确；不重复已有能力 | 进入对应 Roadmap 工作包；实现后按预注册指标验收 |
| **Hold** | 方向可能有效，但缺少基线、目标、API/数据契约、权限设计、负责人、依赖或验证样本 | 不进入实现；补齐缺口后重新评审 |
| **Reject** | 不服务四条主线，不能减少工作量，重复已有能力，只增加装饰/技术新颖性，或违反可信计算、最小权限、数据生命周期与安全边界 | 不列入 Active Roadmap；保留理由避免重复提案 |

完成验收必须比较 baseline 与 candidate，并至少记录任务完成率、主要工作量指标、澄清/恢复次数和错误/返工。安全、事实正确性、授权、可访问性及预算硬条件不能用平均体验分抵消。若未达到工作量目标，结果为 Hold（有明确可修复缺口）或 Reject（价值假设不成立）；不得因功能已经开发而自动 Approve。

Gate 记录字段：feature/work-package ID、目标主线、用户与任务、baseline、价值假设、指标与预注册目标、代表性场景/证据、是否与现有能力重复、开发与运维成本、运行/模型成本、安全与隐私代价、依赖和风险、决策/理由/负责人/日期，以及实现后的实际结果。Hold/Reject 项不得被 UI 或文档描述为已支持功能。

准入流程：提案→记录 baseline/代价/预注册目标→Approve/Hold/Reject→仅 Approve 进入 Active Roadmap→隔离环境实现→按原指标验收→记录实际收益与剩余代价。验收不达标时回到 Hold 或 Reject，不能因为代码已完成而跳过价值复核。

## MVP / Future release 边界与验收

- 内部可用增量：5.8b 只证明现有销售模板可以从新 UI 安全调用；不是 document-first MVP。
- Document-first MVP：01–05。上传验证、私有项目、明确受支持分析、图表、简答/页面报告、有限双表核对、真实任务历史、生命周期必须连通。下载导出与团队协作不列为首版必需。
- 独立后续版本：06情景、07复盘、08协作、09非结构化/RAG/导出、10连接器/企业扩展。高级异常调查在05基础上逐类增加，不宣称通用支持。
- 每阶段先 Fake/Mock 与隔离数据库，预注册新测试再验收。既有 frozen suite/rubric/evidence 只读；新增任务建立新版本，不能为过关修改历史。
- 文件/权限/数值/引用/任务安全为硬性条件；任务完成度、澄清次数、图表可读性、报告相关性单独记录。新产品 gate 的质量阈值与稳定性轮数在执行前确定，不沿用历史85%就宣称普适。
- 真实模型验收另行授权与预算，不属于此次文档工作。默认关闭 flags；无未经批准生产迁移。

## 推荐下一开发阶段

下一阶段为 **PV2-01 文件/项目产品契约与 CSV/XLSX 确定性解析验证 foundation**。5.8b 仅用现有销售模板验证提交、状态查询与来源展示；PV2-01 先做文件契约、容量/类型/公式/日期/单位边界和 synthetic 确定性解析测试，不扩大 Planner 能力或重做 UI。新上传/项目 API 必须独立评审，不能复用旧四字段请求假装接受文件分析。

不把技术选型（LangGraph）、UI大改或持续 Prompt 调优设为产品前置条件。先复用已完成执行控制；只有明确缺口和验收收益时再引入依赖。


## PV2-01 implementation checkpoint — 2026-10-29

Local CSV/XLSX parsing foundation implemented, awaiting user review; no public upload or downstream analysis enabled. Independent typed contracts, guarded parsing, source/normalized/revision identity and content-safe preview are covered by synthetic tests. See [preregistered Feature Value Gate, actual workflow metrics and limits](pv2-01-file-parsing.md). PV2-02 storage/lifecycle and PV2-03 identity/authorization remain required before customer intake. All existing flags and historical acceptance evidence are unchanged.


## PV2-01 final review — 2026-11-13

PV2-01 local deterministic parsing foundation is complete; the preceding recommendation/checkpoint is historical. The next planned work package is PV2-02 project-scoped persistence and lifecycle, **not implemented in this change**. Repeatable revision identities are not database-backed deduplication. No real upload, customer-data intake or Agent file analysis is enabled. Feature Value Gate approval remains limited to the synthetic local foundation; customer/public intake stays Hold.

## PV2-02a local implementation checkpoint — 2026-11-13

PV2-02a private persistence foundation has completed local review and is merged. It adds versioned Project/Asset/AssetRevision/DatasetRevision metadata, opaque private local blobs, project-wide immutable duplicate identity, atomic logical-byte quota reservation and explicit reconciliation. The feature remains default off and trusted-synthetic-only; no route, real identity, production migration or customer upload exists.

PV2-02 is deliberately split. PV2-02b now supplies the local lifecycle, stale-operation reconciliation, expiry, physical deletion and staging/orphan cleanup foundation described below. Production storage/security, backup semantics, derived-object cleanup, scheduled operations and user restore remain PV2-02c or later work. PV2-03 authorization still blocks customer intake. See [PV2-02a design/evidence](pv2-02a-private-dataset-persistence.md).

## PV2-02b local implementation checkpoint — 2026-11-15

PV2-02b now has a local, default-off trusted-synthetic lifecycle implementation: deterministic expiry, immediate read blocking, marked/resumable deletion, lease/fence protection, exactly-once logical quota release, stale StorageOperation reconciliation and reference-checked orphan/staging cleanup. Migration 008 and all lifecycle migrations run only in disposable databases; no HTTP route, scheduler, customer file or application-database migration is enabled. See [design, value metrics and limitations](pv2-02b-dataset-lifecycle.md).

The next storage work is **PV2-02c / Hold**: production object storage and encryption, physical capacity enforcement, scheduled/observable maintenance, backups and deletion tombstones, derived-data cleanup and restore drills. User recycle-bin restoration is not implemented; after physical deletion this phase offers operational resume only. PV2-03 real identity/authorization remains a separate external-release blocker.

## PV2-02c local reliability acceptance — 2026-11-15

PV2-02c completed a bounded local reliability review rather than building production cloud storage. A frozen 20-case fault matrix passed against synthetic data and disposable PostgreSQL, including database/finalization failure, process crash, disk failure, concurrent upload/delete, GC/write exclusion, lease takeover with stale-worker fencing, repeated reconciliation, immutable revision identity, project isolation, read barriers and quota-once settlement. The existing Durable Agent lease test was made deterministic without extending its wait or weakening assertions. See [acceptance report, production gaps and PV2-03 scope](pv2-02c-local-storage-reliability.md).

Decision: the PV2-01/02 local foundation is sufficient to start **PV2-03 local identity and project/asset authorization development**. Production capability and real-customer upload remain **Hold**. Release blockers include production object storage, encryption/KMS, malware quarantine, physical-capacity protection, scheduled/observable lifecycle maintenance, backup/tombstone recovery, derived-data deletion propagation and multi-host consistency. Passing the local suite does not authorize a production migration or public route.

## PV2-03a local authentication checkpoint — 2026-11-15

PV2-03a now has a separate, default-off localhost authentication foundation: strict User/Credential/Session contracts, Argon2id password hashing, opaque server sessions, HttpOnly/SameSite cookies, same-origin and CSRF controls, expiry/revocation, bounded login failure limiting and content-free audit events. Migration 009 is validated only in disposable PostgreSQL. The approved Workspace Login/Register design can use the service only when its independent local flag is explicitly enabled; otherwise the prior non-reading Demo behavior remains unchanged. Successful local registration/login targets My Workspace directly, without an Organization page.

This checkpoint does not claim production authentication or customer onboarding. Email ownership, recovery, MFA, HTTPS deployment, centralized abuse controls, production secrets/monitoring and all real Project/Asset authorization remain Hold. PV2-03b must bind a server-created principal to a single-owner private Project and enforce cross-user project/asset/task denial before any real upload can be enabled. See [PV2-03a design, value gate and limitations](pv2-03a-local-authentication.md).

## PV2-03b local authorization checkpoint — 2026-11-17

PV2-03b adds an append-only migration 010 and a default-off server authorization service. A live PV2-03a session can create one private Project owner relationship; project, active asset and bound durable task paths then require that same active owner. Product task scope is server-derived, wrong-project binding fails, and all negative cases use the same content-free denial. Owner revocation increments the authorization version, revokes task bindings, stops queue claim/heartbeat and blocks later Checkpoint/Receipt publication through a database guard. Metadata-only audit records capture allowed, denied and revoked decisions without credentials or business content.

This local result does not enable customer intake. There is no public project/upload/task facade, collaboration grant, ownership transfer, production RLS/role design, authorization cache strategy or deployed monitoring. PV2-02c production storage blockers and PV2-03a production identity blockers remain Hold. See [PV2-03b design, value gate and limitations](pv2-03b-project-authorization.md).

## PV2-04a local Product Facade checkpoint — 2026-11-18

PV2-04a now composes the existing localhost Auth Session and single-owner Project Authorization in one independent, default-off, same-origin Product Facade. A live user can create/list/read private Project metadata and validate a strict `dataset-analysis-task-v1` request against exact active/ready Asset and Dataset revisions. Missing measure/comparison/time requirements are returned in one centralized clarification bundle. The browser cannot send principal, scope, SQL, Tool name, authorization version or trusted-local authority.

The facade deliberately creates zero Durable executions and exposes no file upload, preview rows, private blobs, computation, model, chart or report generation. Migrations remain 001–010 and are exercised only in disposable PostgreSQL. PV2-04b must add the authorized parser/persistence upload boundary; 04c owns deterministic Dataset operations; 04d owns atomic task binding/Durable execution; 04e owns result, chart and report delivery. See [design, Feature Value Gate, tests and limits](pv2-04a-local-product-facade.md).

Local verification passed 469 offline and 159 isolated PostgreSQL tests on the final complete runs. One existing short-Lease crash-recovery test showed a single timing failure in the first database run. It already used the deterministic database-expiry correction from PV2-02c, so this was not the former fixed-sleep defect. The unchanged test then passed 20/20 targeted repetitions and the full rerun without changing runtime code or assertions; the deliberately one-second test Lease remains a recorded stability risk rather than evidence of product readiness.

## PV2-04b authorized Project and Dataset upload checkpoint — 2026-11-19

PV2-04b now provides a separate, default-off local v2 Product Facade for an authenticated single owner to create an idempotently named private Project, submit a bounded CSV/XLSX file with an explicit schema, persist one immutable private revision and read a finite value-redacted preview. Revision-level source filenames remain accurate when equivalent CSV/XLSX content creates separate revisions. The redacted preview is the current safety default; a policy-aware preview of an authorized user's actual values remains a future versioned requirement. The existing Workspace, synthetic Bridge, `/chat`, `/rag/chat`, Agent APIs and PV2-04a analysis validation remain unchanged.

All Project/Dataset writes require a raw `Idempotency-Key`; only an HMAC key hash and canonical request hash are stored. Same-key changes conflict, concurrent repeats create one result, duplicate file bytes do not consume quota twice, and interrupted publication remains invisible until reconciliation. The authorization write fence serializes owner revocation against storage publication. A browser cannot supply Principal, scope, authorization version, `trusted_local`, SQL, Tool or storage reference.

Feature Value Gate v1 decision: **Approve as necessary infrastructure** for Investigate and as a prerequisite for Simulate, Decide & Review and future Collaborate. Accepted engineering proxies are one complete authenticated browser path, zero client authority fields, zero additional duplicate quota, one same-key result, zero interrupted ready publications and zero cross-scope successes. These do not establish real user time saved; a usability baseline remains required.

Local verification passed 475 offline and 171 isolated PostgreSQL tests, including 12 PV2-04b database cases and a real synthetic localhost browser upload/preview. Migration 011 ran only in disposable databases. Customer upload and production capability remain **Hold** because object storage, KMS, malware quarantine, physical capacity, backup/tombstone handling, monitoring/scheduling, derived-data deletion and multi-host consistency are not present. See [design, evidence and PV2-04c conditions](pv2-04b-authorized-dataset-upload.md).
