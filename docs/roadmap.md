# OpenNovel 发展路线图

> 本路线图为 2025-07 grilling session 的共识整理，覆盖架构、功能、性能三个维度。已完成的改动已打勾，待实施项按优先级排序。

## 0. 项目定位（已确认）

- **核心模式**：CLI 驱动的 AI 自动化长篇小说创作
- **人机边界**：Agent 直接生成并写入 `draft/`，人类在所有章节产出后统览审阅；`novel commit` 作为状态固化节点，`novel rollback` 保证可逆
- **目标用户**：文学创作者与 AI 辅助写作爱好者
- **GUI 策略**：当前无 GUI；未来若重做，基于 MCP 协议作为独立项目，不进入主仓库
- **开源策略**：保持开源，MCP 工具供外部 Agent 环境（Cursor / Claude Desktop / Claude Code）调用

## 1. 已完成的近期改动

| 改动 | 文件 | 说明 |
|---|---|---|
| 修正项目理念 | `AGENTS.md` | 从“人类保留最终决策权”改为“AI 自动化输出 + 事后审阅回滚” |
| 删除 V3 GUI | `desktop/`、`opennovel/api/`、`launch_desktop_v3.py`、`novel-desktop-v3.bat`、`docs/gui-visual-design-guide.md` | 已完全移除，CLI 与 MCP 不受影响 |
| 标记 ADR-0008 废弃 | `docs/adr/0008-gui-v3-architecture.md` | 注明 V3 已删除，未来 GUI 需另起新 ADR |
| 嵌入/重排序模型可配置 | `opennovel/core/config.py`、`opennovel/core/retriever.py`、`opennovel/storage/vector.py`、`opennovel/core/reranker.py`、`opennovel/core/search_pipeline.py` | `novel.yaml` 支持 `embedding_model`、`reranker_model`、`reranker_device`，可选 Qwen3-Embedding / Qwen3-Reranker |
| 新增 ADR-0011 | `docs/adr/0011-configurable-embedding-and-reranker-models.md` | 记录模型可配置化决策 |

## 2. 架构演进方向：四层压三层

当前四层：Human / Machine Shadow / Metrics / Semantic。目标压为三层：

```text
Human Layer          → 纯 Markdown（canon/、characters/、draft/）
Machine Shadow       → 单一 SQLite + YAML Frontmatter + 快照
                       ├─ 事件账本
                       ├─ 状态缓存（State Digest）
                       ├─ 运行指标（原 Metrics）
                       ├─ FTS5 元数据
                       └─ 向量索引后端（Semantic 退化为索引能力）
Agent Runtime        → LLM Bus + AutoRunner + Agent 实现
```

### Phase 1：数据库合并（P0）✅

**任务**：将 `.novel.metrics.db` 并入 `.novel.db`

涉及文件：
- `opennovel/storage/sqlite.py`：扩展 EventStore 或新增 MetricsStore
- 所有写入 metrics 的代码点
- `docs/adr/0004-independent-metrics-database.md`：更新为 Superseded 或修订

**决策项**：
- 选项 A：自动迁移旧项目数据
- 选项 B：不迁移，旧 metrics 丢弃，新建空表
- **推荐**：选项 A，但迁移失败时静默降级到 B，避免阻塞用户

**验收标准**：
- 新创建项目只有一个 `.novel.db`
- 旧项目打开时自动迁移或降级
- `pytest tests/test_storage.py` 通过

## 3. 功能安全：Agent 工具权限治理

### Phase 1：Tool Call Permission Table（P0）✅

**任务**：给每个 Agent 定义允许/禁止的工具与白名单。

推荐权限矩阵：

| Agent | 可读 | 可写 | 禁止 |
|---|---|---|---|
| Writer | canon、character、event、subconscious | draft | event_store、canon、characters、删除文件 |
| Critic | draft、canon、character、event | — | 任何写入 |
| Manager | draft | event_store、state_cache | draft、canon、characters |
| Director | evaluation、event、state | — | 任何写入 |

涉及文件：
- `opennovel/core/tool_registry.py`：执行前查权限表
- `opennovel/core/safety_fence.py`：接入权限检查
- `opennovel/agents/*.py`：明确每个 Agent 的允许工具

**验收标准**：
- Writer 调用写入 EventStore 时被拒绝并记录
- 测试覆盖各 Agent 越权场景

### Phase 2：MCP 工具安全边界（P1）

**任务**：限制 MCP `write_chapter` / `auto_create` 的写入范围与确认阈值。

推荐策略：
- 只能写 `draft/` 和 `subconscious/`
- 覆盖已有章节时返回 diff，需要人类确认
- 删除操作一律禁止，只能通过 `novel rollback` 回退

涉及文件：
- `opennovel/mcp_server.py`
- `docs/mcp-integration.md`

## 4. 性能优化：检索与上下文

### Phase 1：三通道检索并行（P1）✅

**任务**：`SearchPipeline.search()` 中 `_search_vector`、`_search_fts5`、`_search_events` 改为线程池并行。

涉及文件：
- `opennovel/core/search_pipeline.py`

**注意**：
- Agent 自治路径（ToolRegistry）可保持串行，避免线程切换开销
- 向量检索和 Cross-Encoder 是 CPU 密集，FTS5/EventStore 是 I/O，并行收益明显

### Phase 2：LLM 输入缓存（P1）✅

**任务**：对重复 LLM 调用加输入 hash 缓存。

优先缓存场景：
- `Retriever.query_canon()` / `query_subconscious()`
- `Critic.evaluate()` 同一章节重复评分
- `Director.analyze()` 在章节数未变时的重复分析

实现：
- 新增 `opennovel/core/llm_cache.py`
- 缓存落盘到 `.novel.cache.db` 或内存 LRU
- key: `(model, prompt_hash, temperature)`

### Phase 3：动态 Chunk 策略（P2）✅

**任务**：按文档类型使用不同 chunk 大小。

| 文档类型 | chunk 大小 | 说明 |
|---|---|---|
| canon/ | 256-384 tokens | 规则条目短 |
| characters/ | 整卡一个 chunk | 需要完整角色信息 |
| draft/ | 512-1024 tokens | 段落级语义 |
| subconscious/ | 128-256 tokens | 灵感碎片短 |

涉及文件：
- `opennovel/core/chunker.py`
- `opennovel/storage/fts5.py`
- `opennovel/storage/vector.py`

### Phase 4：上下文动态衰减（P2）✅

**任务**：`ContextAssembler` 按章节距离动态选择历史正文注入策略。

推荐策略：
```text
最近 3 章：注入全文
第 4-10 章：注入 Manager 生成的章节摘要
10 章以前：只注入 EventStore 关键事件
```

涉及文件：
- `opennovel/core/context_assembler.py`

### Phase 5：200 万字 Benchmark（P2）✅

**任务**：新增 benchmark 脚本，用真实或合成数据测量检索延迟。

```bash
python scripts/benchmark_retrieval.py --novel novels/demo_novel --chapters 100
```

输出指标：
- 三通道检索延迟（串行 vs 并行）
- Cross-Encoder 延迟
- 索引构建时间
- 内存占用

**实现文件**：
- `scripts/benchmark_retrieval.py`：自包含 benchmark 脚本，支持合成数据生成、FTS5/向量/事件索引构建、串行/并行检索延迟测量、Cross-Encoder 延迟测量、内存采样、JSON 输出。
- `tests/test_benchmark_retrieval.py`：参数解析、合成数据生成、结果结构、小数据量集成测试。

**使用示例**：
```bash
python scripts/benchmark_retrieval.py \
  --novel novels/demo_novel \
  --chapters 100 \
  --words-per-chapter 2000 \
  --queries 10 \
  --output benchmark_result.json \
  --overwrite
```

**注意**：
- 默认使用本地 embedding 模型（`local:BAAI/bge-m3`）；若未安装 `sentence-transformers` 或模型加载失败，向量通道会优雅降级并标记 `vector_available=false`。
- Cross-Encoder 依赖 `sentence-transformers`；不可用时延迟记为 0 并标记 `reranker_available=false`。
- 当前 `SearchPipeline` 的 FTS5 通道在多线程下存在 SQLite 连接线程安全问题（见 `Fts5Store`  docstring），benchmark 会如实反映该行为。

**原则**：没有 benchmark 数据，不引入外部向量数据库。

## 5. 流水线稳定性：AutoRunner 断点续跑

### Phase 1：运行日志持久化（P0）✅

**任务**：`novel auto` 将运行状态写入 `logs/run_{run_id}.json`。

示例结构：
```json
{
  "run_id": "run_20250709_001",
  "novel": "demo_novel",
  "completed": ["ch_001", "ch_002", "ch_003"],
  "failed": null,
  "last_chapter": "ch_003",
  "created_at": "2025-07-09T10:00:00"
}
```

涉及文件：
- `opennovel/core/auto_runner.py`

### Phase 2：断点续跑与幂等写入（P0）✅

**任务**：
- `novel auto` 检测到未完成 run 时，提示“从头开始 / 继续 / 回滚到第 N 章”
- 已完成的章节且评分达标时跳过
- 单章重试时先回滚到该章 commit 前的快照

涉及文件：
- `opennovel/core/auto_runner.py`
- `opennovel/core/state_manager.py`

### Phase 3：层级化重试与降级（P0）✅

**任务**：工具/Agent 调用失败时不中断整个流水线。

策略：
```text
失败 → 重试 3 次（指数退避）
    → 降级（跳过工具，Prompt 标注“检索失败”）
    → 继续生成
    → 记录失败到 metrics
    → 连续 3 章失败则暂停等待人类
```

涉及文件：
- `opennovel/core/tool_registry.py`
- `opennovel/core/auto_runner.py`

## 6. 智能增强：Canon 审计与因果分析

### Phase 1：LLM Canon Auditor（P2）✅

**任务**：在 `CanonChecker` 规则校验基础上，增加 LLM 二次审计。

设计：
- 第一层：`CanonChecker` 快速规则匹配（阻断级）
- 第二层：LLM Auditor 审计复杂语义、隐喻、例外场景（只标记，不阻断）
- 输出 `canon_risk_score`，人类审阅时优先展示高风险章节

涉及文件：
- `opennovel/core/canon_checker.py`
- 新增 `opennovel/core/canon_auditor.py`

### Phase 2：SQL 因果链（P1）✅

**任务**：用 SQL 递归查询实现基础因果追溯，降低对 networkx 的依赖。

适用场景：
- “这个伤口是怎么来的？”
- “这个知识从哪获得？”

涉及文件：
- `opennovel/storage/sqlite.py`

### Phase 3：因果图后台分析（P3）✅

**任务**：全局图分析（中心性、社区发现）改为后台任务 + 缓存。

- 实时查询只支持单角色子图和单事件链
- 全局分析在 `novel doctor --causal` 时触发
- 依赖可选 `networkx`（`pip install -e ".[phase2]"`）

涉及文件：
- `opennovel/core/causal_graph.py`
- `opennovel/cli/doctor.py`

## 7. 成本与可观测性

### Phase 1：Token 与成本统计（P1）✅

**任务**：在 `.novel.db` 中完整记录 token_usage，并提供报告命令。

```bash
novel report --cost
```

输出示例：
```text
Writer.think:  12 次 × gpt-4o-mini = ¥0.23
Writer.write:  12 次 × gpt-4       = ¥12.50
Critic.eval:   12 次 × gpt-4o      = ¥3.10
```

涉及文件：
- `opennovel/core/llm.py`
- `opennovel/storage/sqlite.py`
- 新增 `opennovel/cli/report.py`

### Phase 2：Token 预估缓存（P2）

**任务**：缓存 canon/character 文件的 token 数，避免每次重复计算。

涉及文件：
- `opennovel/core/context_assembler.py`

## 8. 快照与磁盘管理

### Phase 1：快照清理策略（P3）✅

**任务**：控制 `.snapshots/` 目录无限增长。

推荐策略：
- 默认保留最近 50 次 commit 或最近 30 天
- 超过阈值时自动归档到 `.snapshots/archive/`
- 单章超过 5 万字时，快照不存全文 diff，只存 hash

涉及文件：
- `opennovel/core/state_manager.py`

## 9. 实施顺序建议

按依赖关系和投入产出比，建议按以下顺序实施：

1. **P0 - Agent 工具权限表**（安全基础，改动面适中）
2. **P0 - AutoRunner 断点续跑 + 层级化重试**（挂机创作必备）
3. **P0 - Metrics DB 合并到 .novel.db**（架构简化，为后续统计打基础）
4. **P1 - 三通道检索并行**（直接降低延迟）
5. **P1 - LLM 输入缓存**（降低成本）
6. **P1 - SQL 因果链**（减少 networkx 依赖）
7. **P1 - Token/成本统计报告**（数据驱动优化）
8. **P2 - 动态 Chunk 策略 + 上下文动态衰减**（支撑长篇）
9. **P2 - LLM Canon Auditor**（提升一致性质量）
10. **P2 - 200 万字 Benchmark**（验证优化效果）
11. **P3 - 因果图后台分析 + 快照清理**（高级能力）

## 10. 验收标准汇总

每一项实施完成后应满足：

- `ruff check opennovel/ tests/` 通过
- `ruff format --check opennovel/ tests/` 通过
- 相关 pytest 测试通过
- CLI 入口 `novel --help` 正常
- MCP 入口 `novel-mcp` 正常
- 如涉及 schema 变更，更新对应 ADR

---

*路线图维护：每次 grilling 或架构决策后，由执行 Agent 更新本文件并同步 `AGENTS.md`。*
