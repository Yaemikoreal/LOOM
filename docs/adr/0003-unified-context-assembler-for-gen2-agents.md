# 0003 — Writer 和 Critic 回归 ContextAssembler 统一上下文管道

Gen2 自主创作系统（Writer/Critic/Manager）绕过了为 Gen1 Actor 设计的 `context_assembler.py`，导致三个隐性上下文孤岛。本 ADR 记录问题根因和修复方向。

## 背景

LOOM 有两代 Agent 系统：

- **Gen1**（交互式）：Actor + Auditor，通过 `novel write` / `novel commit` 驱动。Actor 使用 `context_assembler.py` 组装包含 CANON / STATE MEMORY / SUBCONSCIOUS 的分级上下文，具备三级策略（FRUGAL/STANDARD/PANORAMIC）、权威分级注入和 Token 熔断机制。
- **Gen2**（自主式）：Writer + Critic + Manager，通过 `novel auto` 驱动。Writer 和 Critic 各自接收手动传入的简单参数，完全绕过 `context_assembler.py`。

## 问题

Gen2 的 `AutoRunner` 为 Writer 和 Critic 手动组装上下文，导致以下数据虽已存储但未被消费（"隐性孤岛"）：

1. **SUBCONSCIOUS 向量检索缺失**：Writer 的 `think()` 调用了 `retriever.query_canon()` 但没有调用 `query_subconscious()`。Critic 完全没有 Retriever。
2. **EventStore 时序链缺失**：Writer 只接收 `previous_summary`（文字摘要），不查询 SQLite EventStore 的事件时间线。因果链在生成阶段被忽略。
3. **Causal Pressure 反馈断层**：Manager 计算并存储了事件的因果压力值，但该值未注入 Writer 的 Prompt。Writer 无法感知"剧情张力已高，需要释放"。
4. **Critic 上下文剥夺**：Critic 只接收 `chapter_text`、`chapter_hint`、`outline`，不注入角色当前状态。Critic 无法验证"角色瞬移"、"物品凭空产生"等一致性问题。

## 根因

`context_assembler.py` 是为 Gen1 Actor 的 `write_stream()` 方法设计的，接口绑定在 Actor 的调用链上。Gen2 的 AutoRunner 在组装 Agent 调用时，没有复用这套机制，而是为每个 Agent 手写了简化的上下文拼接逻辑。

这不是"没有共享记忆"——YAML Frontmatter + SQLite + LlamaIndex 双索引作为存储层是完整的。问题是**上下文的消费路径断裂**：数据存了，但没有被正确注入到 Agent 的 Prompt 中。

## 决策

**统一 Writer 和 Critic 的上下文入口，使其回归 ContextAssembler 的分级上下文管道。**

具体措施：

1. **新增 `assemble_context()` 通用入口**：参数化 `task_message`（替代硬编码的 `CONTINUE:`）和 `active_characters`（显式传入或从 chapter_path 提取）。`assemble_actor_context()` 改为 thin wrapper，行为不变。
2. **Writer 上下文增强**：
   - 接入 SUBCONSCIOUS 向量检索（`retriever.query_subconscious()`）
   - 查询 EventStore 近期高压力事件（`get_high_pressure_events(threshold=0.5)`）
   - 通过 `assemble_context()` 注入 CANON + STATE_MEMORY + SUBCONSCIOUS + 因果压力摘要
3. **Critic 上下文增强**：
   - 注入涉及角色的当前状态（通过 ContextAssembler 的 STATE_MEMORY 层）
   - 接入 Retriever（canon + subconscious）用于一致性交叉验证
   - 注入 EventStore 事件链用于因果一致性校验
4. **AutoRunner 重构**：将 Retriever 和 EventStore 注入 Writer/Critic 构造函数，上下文组装由各 Agent 通过 `assemble_context()` 自行完成。

## 考虑过的替代方案

- **分别修复每个 Agent 的上下文**：逐个在 AutoRunner 中为 Writer/Critic 添加缺失的上下文注入。可行但导致上下文组装逻辑分散在多处，维护成本高。拒绝。
- **新建独立的 Gen2ContextAssembler**：与 Gen1 的 context_assembler 完全分离。避免了接口耦合，但导致两套上下文逻辑需要同步维护。拒绝。
- **将 ContextAssembler 作为可组合的上下文 Provider**：每个 Agent 按需声明需要哪些上下文层（CANON、STATE、SUBCONSCIOUS、EVENT_CHAIN 等），ContextAssembler 按声明组装。这是最灵活的方案，但改动较大。作为长期方向保留。

## 影响

- `context_assembler.py` 需要接口扩展，可能引入 `ContextRequest` 描述对象
- `auto_runner.py` 的 `run_chapter()` 需要重构上下文传递逻辑
- Writer 和 Critic 的构造函数需要接收 Retriever 和 EventStore 引用
- 修复后，P2（反馈文本锚定）、P3（盲目变异）、P4（导演 Agent）的收益才能兑现——它们都依赖 Agent 拥有完整的上下文

## 依赖

- P0（快照 + DiffChecker 接入）应先完成，确保上下文修复后的自动流程有安全底座
