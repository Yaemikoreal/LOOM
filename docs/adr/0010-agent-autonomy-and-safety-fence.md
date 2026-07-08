# 0010 — Agent 自治与安全围栏

Agent 在安全围栏约束内拥有微观自决权：Writer 可在创作中主动检索知识，Critic 可触发局部热修复。
所有自治行为受 SafetyFence 约束——递归深度、Token 预算、超时熔断、Canon 不可违背。

## 背景

固定管线（Writer → Critic → Manager）是确定性的，但存在效率瓶颈：
- Writer 发现设定缺失时只能依赖 ContextAssembler 的静态注入，无法主动查询
- Critic 发现局部硬伤时必须走全量重试，无法触发段落级修复
- 如果让 Agent 完全自治（Agent A 调 Agent B，Agent B 又调 Agent A），容易形成死循环

需要一个中间方案：赋予 Agent 在安全边界内的微观自决权（工具调用），
但必须由硬编码的安全围栏约束，防止行为失控。

## 决策

### 1. 安全围栏（Safety Fence）

四个维度的硬性约束：

| 维度 | 默认值 | 说明 |
|------|--------|------|
| 递归深度 | 3 层 | 工具调用链最大嵌套层数 |
| Token 预算 | 4000/调用 | 单次自治调用的 Token 上限 |
| 超时熔断 | 120s | 单次自治调用的最大等待时间 |
| Canon 不可违背 | 硬性 | 检测到规则违反时阻断 |

实现于 `core/safety_fence.py` 的 `SafetyFence` 类，
通过 `SafetyFenceConfig` 配置，可通过 `novel.yaml` 的 `safety_fence` 字段覆盖或禁用。

### 2. Agent 自治

- **知识缺口检测**：Writer 在 think 后、write 前自动扫描大纲中的角色引用和设定关键词，
  与已注入上下文比对，识别缺失信息点（`KnowledgeNeed[]`）
- **ToolRegistry**：知识查询分发中枢，按 `KnowledgeSource` 路由到对应数据源
  （CANON→Retriever, CHARACTER→YAMLStorage, EVENT→EventStore）
- **局部热修复**（Local Hot-fix）：Critic 发现局部硬伤时，Writer 通过 `hot_fix()` 进行段落级精确修改，
  失败时自动回退到全章 `revise()`

### 3. 混合工具调用模式

双轨架构保证多供应商兼容性：
- **原生通道**：检测到模型支持原生 Tool-Use 时，走 LiteLLM 原生协议
- **通用回退**：对不支持原生 Tool-Use 的模型，使用 `<tool_call>` XML/JSON 混合块解析
- **废弃管道符格式**：旧的 `##TOOL_CALL##|管道符|分隔` 格式已废弃，管道符在参数包含 `|` 时必然解析错误
- **能力探测**：通过 `LLMConfig.supports_native_tool_use` 字段动态切换

### 4. 治理模型 = SafetyFence + AutoRunner

不引入事件总线或泛化 Lifecycle Hooks：
- 权限门控：ToolRegistry.execute() 入口处的 if/else，硬编码在 SafetyFence 内
- 层级化重试：ToolRegistry.execute() 外层的 try-catch（Phase 3 规划）
- 审计日志：execute() 的 finally 块中直接写入（Phase 3 规划）
- 跨组件联动：由 AutoRunner 编排器**显式调用**，所见即所得

设计哲学：**枯燥但确定**（Boring but deterministic）。

## 考虑过的替代方案

- **Agent 完全自治**：风险高。无界递归调用容易形成死循环。
- **纯固定管线（现状）**：简单但低效。无法适应不同章节的信息需求。
- **事件总线 + 泛化 Hook**：执行顺序不可控，多监听者导致竞态，调试困难。拒绝。

## 影响

- 新增 `core/safety_fence.py`、`core/tool_registry.py`、`core/agent_autonomy.py`
- 新增 `schemas/knowledge.py`（KnowledgeNeed/KnowledgeResult 查询协议）
- Writer 构造函数加入 `tool_registry` 和 `safety_fence` 引用
- `novel.yaml` schema 扩展 `safety_fence` 配置节
- AutoRunner 加入条件跳转（高分跳过 Manager）和局部热修复优先逻辑

## 参考

- ADR 0006 — 混合动态路由架构（调度器宏观编排，与本 ADR 互补）
- ADR 0009 — 阶段级模型路由（成本优化器的上游设计）
- CONTEXT.md — Safety Fence / Agent Autonomy / Governance Infrastructure 段
