# 0010 — 多代理架构增强：内部 LLM 混合、信用分配与动态角色

在现有四代理固定管线基础上，引入 Agent 内部的"提议-综合"LLM 混合机制、
多 Critic 投票、历史信用分配学习和事件溯源调试能力。

## 背景

当前四代理系统（Writer → Critic → Manager → Director）采用固定角色 + 单模型模式：

1. **单模型瓶颈**：每个 Agent 只使用一个 LLM，创意多样性和逻辑严谨性不可兼得。
2. **评分偏倚**：单一 Critic 评分可能存在系统性偏倚（如对特定文风的偏好），
   无人验证评审质量。
3. **无法自我改进**：系统从历史运行中无法自动学习——每次失败的 retry 循环
   事后未分析根因，同样的错误可能重复发生。
4. **调试黑盒**：Agent 间的状态传递是隐式的，出错时难以追溯根因。

## 决策

### 1. 内部 LLM 混合机制（Proposal-Synthesis）

为 Writer/Critic/Director 提供配置多模型的选项，采用"提议-综合"结构：

```
Writer 内部流程（Proposal-Synthesis 模式）：
  ┌─────────────┐
  │ 创意生成模型  │ → 方案 A（大胆、富有想象力）
  │ (GPT-4o)    │
  ├─────────────┤
  │ 逻辑结构模型  │ → 方案 B（结构严谨、因果完整）
  │ (Claude)    │
  ├─────────────┤
  │ 细节描写模型  │ → 方案 C（细腻、感官丰富）
  │ (DeepSeek)  │
  └─────────────┘
         ↓
  ┌─────────────┐
  │ 综合仲裁模型  │ → 取各方案精华，融合为最终输出
  │ (主力模型)   │
  └─────────────┘
```

Critic 的多模型投票：重要章节（CLIMAX/Director 标记的高张力章节）使用 3 个模型
并行评分，取中位数作为最终分数，降低单一模型偏见。

配置方式：通过 `novel.yaml` 的 `agents.<agent>.models` 字段配置多模型列表。
未配置时保持现有单模型行为（向后兼容）。

新增组件：`core/multi_model_orchestrator.py` — `MultiModelOrchestrator` 类。

### 2. 信用分配与历史学习（CreditAssignment）

基于 MetricsStore 的历史运行数据，自动分析 Agent 表现并优化配置：

**信用分配算法**：
- 成功标准：Critic 评分 ≥ 80 且无 critical 级 AnchoredIssue
- 失败标准：Critic 评分 < 60 或 3 次以上 retry
- 归因逻辑：通过因果图追溯失败链（Writer 大纲不完整 → 正文偏离 → 低分），
  识别需要改进的 Agent 环节

**自动优化**：
- 当某模型在特定章节类型（CLIMAX/TRANSITION/ROUTINE）连续 3 次低分时，
  自动建议切换到备选模型
- 当某 strategy 策略（FRUGAL/STANDARD/PANORAMIC）导致连续失败时，
  自动调整策略选择偏好
- 优化建议以 `novel doctor --optimize` 报告形式呈现，不自动执行（人工决策）

新增组件：`core/credit_assignment.py` — `CreditAssigner` 类。

### 3. 事件溯源调试模式（Event Sourcing）

为所有 Agent 操作记录不可变事件流，便于调试和回滚：

```
事件类型：
- agent.call.start  {agent, action, input_hash, timestamp}
- agent.call.end    {agent, action, output_hash, duration_ms, token_usage}
- agent.call.error  {agent, action, error_type, error_message, traceback_hash}
- pipeline.retry    {chapter_id, attempt, reason, strategy}
- pipeline.skip     {chapter_id, agent, reason}
- snapshot.create   {chapter_id, files[], event_count}
```

事件存储于 MetricsStore 的 `agent_events` 新表，按 `trace_id` 关联。
所有事件写入是 append-only（不可变），不修改已有记录。

`novel doctor --trace <trace_id>` 命令可回放完整的事件流，
展示每个 Agent 的输入/输出/决策链路（与现有 Glass-Box Decision Making 原则一致）。

新增：MetricsStore 的 `agent_events` 表 + `novel doctor --trace` 增强。

### 4. Agent 状态可视化基础

为 GUI 驾驶舱（CONTEXT.md 设计中）提供数据基础：

- Pipeline View 展示实时 Agent 状态（排队/运行/完成/失败）
- Reasoning Panel 展示推理链（已实现 reasoning JSON 存储）
- Score Trend 展示评分趋势图（已实现 evaluation_history 表）
- Agent Event Timeline 展示事件时间线（本 ADR 新加的 agent_events 表）

此 ADR 仅实现数据层基础，GUI 渲染留待桌面端开发阶段。

## 考虑过的替代方案

- **Agent 完全动态拓扑**（HALO 风格）：根据任务动态创建/销毁 Agent。过于复杂，
  OpenNovel 的四代理模型已覆盖核心创作流程，动态拓扑收益有限。拒绝。
- **全自动优化**（自动切换模型/策略）：风险高，可能在作者不知情时改变创作风格。
  改为"建议-人工批准"模式。
- **完全重写 Agent 框架**：破坏性太大。本方案在现有 Agent 基础上增量增强。

## 影响

- `agents/writer.py`、`agents/critic.py` 新增多模型模式（可选，默认关闭）
- `core/auto_runner.py` 集成 `CreditAssigner` 记录每次运行结果
- `storage/metrics.py` 新增 `agent_events` 表
- `novel doctor --optimize` 和 `--trace` 命令增强
- Token 成本：多模型模式下单次 Writer 调用可能消耗 3-4 倍 Token（仅在作者显式
  配置多模型时触发，CLIMAX 章节默认启用多 Critic 投票）

## 依赖

- ADR 0004（MetricsStore）— 信用分配和事件溯源的数据底座
- ADR 0006（Agent Autonomy）— 共用 SafetyFence 约束机制
