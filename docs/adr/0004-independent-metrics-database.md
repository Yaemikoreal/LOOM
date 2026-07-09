# 0004 — 独立指标数据库（Superseded）

> **状态**: Superseded（已被 roadmap P0 "四层压三层" 决策取代）
> **取代者**: `docs/roadmap.md` 第 2 节 "架构演进方向：四层压三层"
> **实施时间**: 2026-07-09

运行遥测数据（Token 用量、评分历史、Agent 调用链）原存储在独立的 `.novel.metrics.db` 中，与叙事真相的 EventStore（`.novel.db`）物理隔离。**当前已合并到 `.novel.db`**，由 `MetricsStore` 在同一数据库中管理指标表。

## 背景（原始）

GUI 监控面板需要持久化以下数据：每章每 Agent 的 Token 消耗、Critic 评分趋势、Director 策略指导历史、Agent 调用链耗时。当前 `novel auto` 运行时这些数据都是内存中的临时变量，跑完即丢。

## 原始决策

1. **存储位置**：独立的 `.novel.metrics.db`（SQLite），与 `.novel.db` 分离。
2. **核心表结构**：
   - `token_usage(agent, chapter, run_id, input_tokens, output_tokens, model, timestamp)`
   - `evaluation_history(chapter, run_id, total_score, dim_plot, dim_char, dim_logic, dim_style, dim_emotion)`
   - `agent_trace(run_id, chapter, agent, action, input_hash, output_hash, duration_ms, timestamp)`
3. **生命周期**：指标数据可独立归档、清理，不影响叙事数据。

## 变更决策

随着 roadmap 推进，为简化架构、降低维护成本，决定将 Metrics 表合并到 `.novel.db`：

1. **统一数据库路径**: `.novel.db` 同时承载 EventStore 与 MetricsStore 表。
2. **向后兼容**: `MetricsStore` 初始化时检测 `.novel.metrics.db`；若存在则自动迁移数据到 `.novel.db`，迁移成功后重命名为 `.novel.metrics.db.migrated`。
3. **迁移降级**: 迁移失败时静默降级，使用新的空表，不阻塞用户。

## 考虑过的替代方案（原始）

- **扩展现有 SQLite（.novel.db）**：技术债。EventStore 是叙事真相（经人工确认的事件），指标是运行遥测（自动采集的元数据），两者语义不同、访问模式不同、生命周期不同。混在一起会导致查询干扰和数据污染。
- **JSONL 日志文件**：降级。不支持聚合查询（如"找出所有评分低于 70 的章节的 Director 指导"），无法直接支撑 GUI 的趋势图表。

## 当前取舍

- 合并后通过表名前缀和 SQLModel metadata 区分两类数据，查询仍可通过表隔离。
- 减少了一个 SQLite 文件，简化了备份、迁移和快照管理。
- 旧项目首次加载时自动迁移，无需人工干预。

## 影响

- 新创建项目目录下仅存在 `.novel.db`。
- `MetricsStore` 默认使用 `.novel.db`。
- 旧 `.novel.metrics.db` 在成功迁移后被重命名为 `.novel.metrics.db.migrated`。
- 涉及文件：`opennovel/storage/metrics.py`、`opennovel/core/auto_runner.py`、
  `opennovel/core/evaluation_auditor.py`、`opennovel/core/doctor.py`、
  `opennovel/cli/main.py`。
