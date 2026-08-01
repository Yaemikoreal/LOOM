# 0002 — 三级上下文策略引擎替代固定 Token 预算

根据模型的实际上下文窗口自动映射到三种上下文组装策略（FRUGAL / STANDARD / PANORAMIC），废弃硬编码的 8000 Token 天花板。

## 背景

原设计硬编码 `TOTAL_TOKEN_BUDGET = 8000`，适用于 GPT-4 等 8K 窗口模型。但随着 DeepSeek V4（1M）、Gemini 1.5 Pro（1M+）等超长上下文模型的出现，强行施加 8K 上限严重浪费模型能力且限制叙事连贯性。

## 决策

1. **策略映射**：`LLMBus` 初始化时通过 LiteLLM 获取模型的 `max_tokens`，自动映射到三级策略：
   - **FRUGAL** (< 32K)：保持原有 RAG + 摘要逻辑，`soft_budget = 0.7 × max_window`
   - **STANDARD** (32K–128K)：均衡型，当前章全量 + 前文摘要
   - **PANORAMIC** (> 128K)：全景沉浸，全量设定注入 + 全量潜意识 + 最近 N 章倒序全量正文
2. **全景软限**：即使模型支持 1M，`soft_budget = 128K`，防止延迟失控（首字响应时间）和注意力漫游。
3. **组装器变体**：`context_assembler.py` 根据 `LLMBus.strategy` 选择不同的组装路径，全景模式下 RAG 降级、全量正文升空。

## 考虑过的替代方案

- **全局固定预算**：简单但浪费超长上下文能力。拒绝。
- **仅按比例放大数字**：如设为窗口的 70%，但全景模式需要改变组装逻辑（全量灌注 vs RAG），不仅仅是数字调整。拒绝。

## 影响

- `LLMBus` 需要 LiteLLM `get_model_info()` 支持（备选：人工映射表）
- `context_assembler.py` 需要重构为策略路由模式
- 全景模式下 `novel write` 的延迟和成本需在实际使用中调优 128K 软限
- 全景模式下 `canon/` 和 `subconscious/` 的 MD 文件大小不宜过大（建议单文件 < 16K）
