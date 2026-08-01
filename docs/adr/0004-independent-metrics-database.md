# 0004 — 独立指标数据库

运行遥测数据（Token 用量、评分历史、Agent 调用链）存储在独立的 `.novel.metrics.db` 中，与叙事真相的 EventStore（`.novel.db`）物理隔离。

## 背景

GUI 监控面板需要持久化以下数据：每章每 Agent 的 Token 消耗、Critic 评分趋势、Director 策略指导历史、Agent 调用链耗时。当前 `novel auto` 运行时这些数据都是内存中的临时变量，跑完即丢。

## 决策

1. **存储位置**：独立的 `.novel.metrics.db`（SQLite），与 `.novel.db` 分离。
2. **核心表结构**：
   - `token_usage(agent, chapter, run_id, input_tokens, output_tokens, model, timestamp)`
   - `evaluation_history(chapter, run_id, total_score, dim_plot, dim_char, dim_logic, dim_style, dim_emotion)`
   - `agent_trace(run_id, chapter, agent, action, input_hash, output_hash, duration_ms, timestamp)`
3. **生命周期**：指标数据可独立归档、清理，不影响叙事数据。

## 考虑过的替代方案

- **扩展现有 SQLite（.novel.db）**：技术债。EventStore 是叙事真相（经人工确认的事件），指标是运行遥测（自动采集的元数据），两者语义不同、访问模式不同、生命周期不同。混在一起会导致查询干扰和数据污染。
- **JSONL 日志文件**：降级。不支持聚合查询（如"找出所有评分低于 70 的章节的 Director 指导"），无法直接支撑 GUI 的趋势图表。

## 影响

- 新增一个 SQLite 文件，需在 `.gitignore` 中排除
- GUI 监控面板可直接读取指标数据库，无需解析日志
- 指标数据可独立于叙事数据进行清理和归档
