# 0008 — 动态上下文工程：从静态注入到按需调度

将 ContextAssembler 从"一次性全量注入"升级为"注意力预算 + JIT 按需查阅 + 一致性校验"
的三层动态调度系统。

## 背景

当前 ContextAssembler 的工作模式是一次性将所有可能相关的知识（CANON、STATE MEMORY、
SUBCONSCIOUS）注入 LLM 上下文。这带来三个问题：

1. **注意力稀释**：LLM 存在"迷失在中间"效应，上下文越长，中间部分的信息留存率越低。
   注入大量低相关性知识反而降低生成质量。
2. **上下文腐烂**：多章生成后，早期注入的静态信息可能已过时，但仍在上下文中占用
   Token 配额。
3. **数据漂移风险**：多源数据（Markdown/YAML/SQLite/Vector）之间可能产生不一致，
   当前无任何入前校验，错误数据直接喂给 LLM。

## 决策

### 1. 注意力预算管理器（AttentionBudgetManager）

为每个上下文片段打上**重要性标签**和**时效性标签**，在组装时按优先级分配 Token 配额：

```
优先级排序（从高到低）：
① CANON 核心规则（不可违背的世界观，高重要性 + 永久时效）
② 当前章节角色状态（高重要性 + 即时时效）
③ 近期高压力事件链（高重要性 + 短时效）
④ 前章摘要（中重要性 + 中时效）
⑤ SUBCONSCIOUS 灵感碎片（低重要性 + 永久时效）
```

组装策略：高优先级内容置于上下文**开头和结尾**（LLM 注意力最强的位置），
低优先级内容放在中间位置。

新增组件：`core/attention_budget.py` — `AttentionBudgetManager` 类。
配置：通过 `novel.yaml` 的 `context.attention_budget` 字段调整各层级的配额比例。

### 2. JIT（Just-In-Time）按需查阅模式

不预加载所有资料。核心设定（主角核心特质、当前主线目标）预置于上下文，
详细历史事件、配角信息、设定细节放入外部知识库。

Agent 在创作中遇到特定知识缺口时，通过 ToolRegistry 主动触发查询，
即时拉取所需片段。与现有 Agent 自治机制（ADR 0006）集成。

新增：`core/jit_retriever.py` — `JITRetriever`，接收 `KnowledgeNeed[]`，
路由到 SearchPipeline 精确查询，内置结果缓存（同次创作会话内复用）。

### 3. 上下文一致性校验层

在 ContextAssembler 的 STATE MEMORY 注入前增加校验步骤：

- **Canon 冲突检测**：快速比对角色状态与 CANON 规则（复用 CanonChecker）
- **跨源一致性**：比对 YAML Frontmatter、SQLite EventStore、最近正文中的角色状态
  是否一致。不一致时标记差异并触发 Auditor 自纠偏
- **脏标记过滤**：自动跳过 `dirty_flag: extraction_failed` 章节的状态数据

新增：`core/context_validator.py` — `ContextValidator` 类。
校验失败时降级策略：冲突数据降为 `[WARNING] 以下信息可能不一致，请谨慎参考`，
而非直接阻断注入。

### 4. 动态摘要触发

当单章历史超过 Token 阈值（默认 16K）时，自动调用轻量模型生成摘要替代原始长文本：

- 触发点：`assemble_context()` 中检测 `chapter_text` 的 Token 数
- 摘要模型：通过 `novel.yaml` 的 `context.summarizer_model` 配置（默认 Writer 的 think_model）
- 缓存：摘要结果缓存于 `.novel.db` 的 `chapter_summaries` 表（复用现有 summaries 存储）

## 考虑过的替代方案

- **全局固定配额**：简单但无法适应不同章节类型的差异化需求。拒绝。
- **完全 JIT 模式**（完全不预加载）：对常见知识（如主角名）每次查询增加延迟和成本，
  且 LLM 可能在无上下文提示时根本不知道需要查询什么。拒绝。
- **仅做一致性校验不做 JIT**：不能解决注意力稀释问题。拒绝。

## 影响

- `core/context_assembler.py` 需重构为分阶段调度流程
- 新增 3 个模块：`attention_budget.py`、`jit_retriever.py`、`context_validator.py`
- 现有 Agent 调用 `assemble_context()` 的接口向后兼容，新增可选参数
- JIT 模式初期仅对 Writer 启用（Critic/Manager 保持全量注入），逐步推广
- 摘要功能依赖 summaries 存储（已存在），仅加触发逻辑

## 依赖

- ADR 0007（SearchPipeline）— JIT 检索的底层通道
- ADR 0006（Agent Autonomy）— JIT 模式复用 ToolCallParser 协议
