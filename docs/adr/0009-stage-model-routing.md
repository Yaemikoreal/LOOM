# 0009 — 阶段级模型路由（成本优化器）

同一 Agent 内，不同阶段使用不同模型以控制 Token 成本。Writer 的 think() 阶段使用廉价模型生成大纲，
write() 阶段切换主力模型创作正文，revise() 阶段再切换修订模型。

## 背景

多 Agent 系统中，不同阶段的计算需求差异大：
- **Think 阶段**：生成结构化大纲，需要逻辑推理但输出量小，可用廉价模型
- **Write 阶段**：创作数千字正文，需要创意性和长上下文，需主力模型
- **Revise 阶段**：锚定反馈精准修订，需要严格遵循指令，需主力模型

如果所有阶段都使用同一种模型（默认 `model`），会产生不必要的 Token 成本。
Writer 的 think 阶段占每个章节 Token 消耗的 20-30%，使用廉价模型可显著降低总成本。

## 决策

1. **三层 fallback 路由**：agent-level model override → novel.yaml `model` → `.opennovel.yaml` `default_model`
2. **阶段级配置**：通过 `novel.yaml` 的 `agents.writer` 下新增字段：
   - `think_model`：思考阶段用廉价模型（如 `gpt-4o-mini`）
   - `write_model`：创作阶段用主力模型（如 `gpt-4`）
   - `write_model_climax`：高潮章节专用创作模型（更高创意性）
   - `revise_model`：修订阶段用主力模型
   - `model`：Agent 级默认（如果阶段未设置则继承此值）
3. **运行时路由**：Writer 在每个方法调用时通过 `model=` 参数选择模型

## 考虑过的替代方案

- **全局使用一个模型**：简单但浪费。think 阶段用昂贵的主力模型产生不必要的成本。
- **完全分离独立 Agent**：过度设计。三个阶段共享同一套上下文和状态。
- **动态模型选择**：让 LLM 自己决定用哪个模型。不可靠且无法预测成本。

## 影响

- 每个章节的成本预计下降 15-25%（think 阶段使用廉价模型）
- Writer 的构造函数和所有方法需要接收和传递 model 参数
- `novel.yaml` schema 需要扩展 `agents.writer.think_model` / `write_model` / `revise_model` 字段
- `LoomConfig` 需要解析阶段级 model override

## 参考

- ADR 0005 — 三层变异控制机制（成本优化器的下游设计）
- CONTEXT.md — Stage Model Routing 段
