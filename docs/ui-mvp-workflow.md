# 独立 Frontend/UI MVP 工作流

## 当前增量：Workspace v4.1

当前设计源为用户提供的 v4.1 bilingual HTML + handoff。已建立独立 `frontend/workspace/`、语义翻译键与 EN/zh-CN 资源；不替换 `frontend/agent/`。七项导航、Demo 登录注册、Settings 与核心视图基础已实现。详见 [实现/测试/后续交接](workspace-v41-frontend.md)。

后续顺序：v4.1 Figma 原生组件/双语响应式验收 → 安全 Fake API transport 与状态管理 → 单独批准的源文件/报告/身份 API。以下旧阶段表和四页面设计为历史记录；UI-01～UI-10 网络交互问题仍是接入 Agent 前的阻断项，本轮不宣称已修复旧 console。

状态：UI-MVP-0 文档准备完成；UI-MVP-D1 进行中，四个桌面核心页面第一版已完成（用户确认）；UI 开发尚未开始。此工作流与 Phase 5.7f 运维、5.8 Agent+RAG 分开，不把设计工作标记为后端功能完成。

设计文件：[Enterprise Analytics Copilot — Figma MVP](https://www.figma.com/design/GxZ8cOgDc4Wj7XUJN1aAJO)。进度来源为用户确认；本轮未检查 Figma 节点或判定设计验收通过。
## 范围与阶段

| 阶段 | 工作 | 进入下一步的条件 |
|---|---|---|
| UI-MVP-0（已完成） | 审阅稿整合、代码核对、四页面/Token/组件/API 映射、交互修复清单 | 文档检查完成；不创建 Figma 文件或运行代码 |
| UI-MVP-D1（进行中） | 已有四个桌面页面初稿；依次完成 Design System → 390px 移动端 → 异常状态 → 可点击 Prototype | 独立视觉/交互审查，确认范围与待决项 |
| UI-MVP-D2 | 修订并确认设计 | 用户确认后才开始实现 |
| UI-MVP-1 | 增量修改 frontend/agent；优先状态/请求管理，再页面和样式 | Fake/Mock 用例覆盖下述安全与恢复行为；保持 API 和默认关闭 flags |
| UI-MVP-2 | 本地浏览器+隔离 PostgreSQL 集成，宽窄屏/键盘/网络故障检查 | 无付费请求；主流程与异常状态真实可复现 |
| 独立后续验收 | 真实 Planner 产品 smoke | 单独授权、预注册预算；不复用 Mock 结果作真实调用证据 |

新增后端 API 必须独立定义工作包，不为实现视觉稿擅自扩展 schema 或 production migration。正式 Auth、持久历史、Dashboard、Agent+RAG 与 Workspace 均非 UI-MVP-1。

## 已发现的前端差距与实施验收

以下是待实现要求，不是本轮代码改动。

| 编号 | 现状 | 必修行为 / Mock 验收 |
|---|---|---|
| UI-01 幂等 | 每次 submit 都生成 UUID | 逻辑提交创建一次 `{key, immutable payload}`；POST 响应丢失后同 key/body 恢复，返回同 task ID；修改参数仅显式新建请求；未决旧请求不被覆盖 |
| UI-02 重复提交 | 无提交锁/按钮禁用 | 双击/Enter 连续触发只产生一个在途 POST；单一 UI 提交锁；超时保留 pending/unknown，不生成第二 key |
| UI-03 轮询 | 有120次限制，但无 fetch 超时、单轮询所有权 | 每个任务只一个轮询循环；建议 1s 间隔、单 GET 10s 超时、60s 总轮询窗口；实际时间到即暂停，不宣告执行失败；终态停止 |
| UI-04 状态竞争 | 多次提交可能启动并行轮询覆盖页面 | task ID+轮询代次绑定；同任务 state_version 不回退；旧任务/旧响应不能覆盖当前视图；页面离开取消浏览器请求，不声称取消服务端任务 |
| UI-05 GET恢复 | 409重查，其他异常直接结束；无手动查询 | status_changed 最多连续3次重查且受总窗口限制；网络失败暂停并保留最后状态；用户手动 GET 原 ID；404/403停止 |
| UI-06 POST未知 | 所有错误提示“安全停止” | 30s建议客户端超时只停止等待，不等于 Planner/任务取消；不自动 POST 重试；手动重试使用同 key/body，认证纠正也不换 key |
| UI-07 错误 | 主要读取 detail.message，未映射422数组 | 按 HTTP+code 分类；支持422数组；未知响应使用本地安全文案；禁止把网络失败/非法数据显示为0销售额 |
| UI-08 凭证/文本 | 已用 textContent、未使用 storage | 保留这些安全属性；key只内存、密码掩码，不写 URL/storage/日志/复制动作；不展示原始422 input、header或堆栈 |
| UI-09 金额 | 已显示服务端数字 | 保留 Decimal string 精度；零/负值正确；不可用不补0；来源单独显示；图表如采用仅为可视化，不参与业务计算 |
| UI-10 恢复界面 | 无手动 Task ID 查询 | UUID输入校验后GET；不得为验证key自动POST；页面刷新不保证恢复，明确临时DB清理限制 |

时间参数是前端提议，不改变服务器 timeout、预算或 frozen generation config；实现前可基于无付费网络模拟确认参数，不修改后端契约。AbortController 只取消本地等待，不能承诺撤销服务端执行。poll timeout、failed、needs_reconciliation 必须是不同显示状态。

## 非付费验收矩阵

- 正常 POST→queued/running→completed；负增长、零增长的合法 synthetic response fixture；响应缺字段/非法 JSON。
- 双击/键盘重复；POST成功但响应丢失；同 key同body恢复；参数改变不能复用原 key；409幂等冲突不可循环重试。
- GET乱序/旧 state_version；切换任务/离开视图；409暂态；超时、断网后重新查询；轮询达到时间上限；终态不再请求。
- 401重新输入；403/404停止；422字段定位；429保持请求；502/503安全展示；未知结果无自动重执行。
- 注入式文本作为纯文本；凭证不持久化/不进日志；DOM不暴露密钥结果；金额显示不漂移；手动UUID查询不触发POST。
- 1440/390/320宽、200%缩放、键盘焦点/屏幕阅读状态提示；来源和长ID不溢出。
- 最后再跑现有回归与隔离DB HTTP流程。无新增 API、无迁移、无冻结资产修改、flags默认关闭作为差异检查条件。

## 本轮文档核对记录

核对了 frontend/agent/app.js、styles.css 与既有页面；Agent API请求/响应模型、错误码、认证边界；Architecture与Roadmap。仅设计文档发生改动，未重跑409/108应用测试，也不把上轮测试结果宣称为本轮新结果。检查Markdown引用、diff空白及Git变更范围；没有调用模型或创建Figma远端资产。

交接规范：[Figma UI MVP](ui-mvp-figma-handoff.md)。产品边界：[审阅整合稿](mvp-figma-design-brief.md)。

当前状态：Phase 5.8a 本地前端已完成收尾审查，下一阶段尚未开始。

## Product Vision v2 规划索引

Document-first 主链路与开发优先级见 [Product Vision v2](product-vision-v2.md) 和 [Product Roadmap v2](product-roadmap-v2.md)；资产、项目与协作边界见 [架构初稿](project-data-architecture-v2.md)。5.8b 仍只允许安全对接现有销售模板，不能用它宣称文件分析 MVP 已完成。
