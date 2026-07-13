# 0011 — 成本效率系统：弹性调度、智能模型路由与性能自适应

建立基于章节重要性的弹性资源调度、内容感知模型路由和本地资源自适应降级机制，
在保证创作质量的前提下大幅降低 Token 消耗和计算延迟。

## 背景

长篇小说生成涉及海量 LLM 调用，成本是核心瓶颈：

1. **统一资源分配**：当前所有章节享受相同的 Token 预算和模型配置，但高潮章节
   和日常过渡章节对质量的要求完全不同。
2. **模型选择静态**：当前模型路由仅在 Agent 级别和阶段级别，缺乏基于内容复杂度
   的动态决策。
3. **批处理不充分**：当前仅 Manager 更新可延迟批处理，更多非实时操作
   （Metrics 记录、FTS5 更新、快照创建）仍在关键路径上。
4. **无资源感知**：当前不检测系统资源状态（CPU/内存/GPU），向量检索和
   因果图计算可能撑爆低配机器。

## 决策

### 1. 弹性资源调度（PriorityScheduler）

基于章节类型的差异化资源分配：

| 章节类型 | Token 预算倍率 | 模型优先级 | Critic 投票 | Director |
|---------|-------------|----------|-----------|---------|
| CLIMAX | ×2.0 | 主力模型 | 3 模型投票 | 强制前置 |
| ROUTINE | ×1.0 | 默认模型 | 单模型 | 每 5 章 |
| TRANSITION | ×0.6 | 轻量模型 | 单模型 | 跳过 |

动态预算调整：当 `Director.analyze()` 检测到叙事张力上升趋势时，自动提升后续
章节的资源等级（TRANSITION → ROUTINE，ROUTINE → CLIMAX）。

配置：通过 `novel.yaml` 的 `scheduler` 字段自定义各类型的资源倍率。

新增组件：`core/priority_scheduler.py` — `PriorityScheduler` 类。

### 2. 内容感知模型路由（ContentAwareRouter）

在 LLMBus 中实现智能模型路由，基于内容特征动态选择模型：

**路由决策因子**：
- 章节类型（CLIMAX 走高性能模型）
- 任务复杂度预估（通过轻量分类器分析 task_message 的复杂度分数）
- 当前 Token 预算余量
- 历史模型表现（该模型在此类任务上的平均评分）

**复杂度预估**（基于启发式规则，不调用 LLM）：
```python
def estimate_complexity(task_message: str) -> float:
    """
    复杂度分数 0.0~1.0，基于：
    - 角色数量（每多一个 +0.1）
    - 是否有动作描写关键词 +0.15
    - 是否有情感描写关键词 +0.1
    - 是否需要多线叙事 +0.2
    - 文本长度 / 预算 +0.1
    """
```

复杂度 ≥ 0.7 → 主力模型；0.4-0.7 → 默认模型；< 0.4 → 轻量模型。

修改文件：`core/llm.py` — `LLMBus` 新增 `route_by_complexity()` 方法。

### 3. 延迟批处理扩展（LazyBatchProcessor）

将非实时操作从关键路径移除，延迟到章节完成后批处理：

**当前关键路径** → **优化后**：

| 操作 | 当前位置 | 优化后位置 |
|------|---------|-----------|
| Manager 状态更新 | 每章同步（高分可跳过） | 批处理（保持现状） |
| FTS5 增量更新 | commit 时同步 | 章节完成后批处理 |
| Metrics 记录 | 每个 Agent 调用同步 | 内存缓冲 → 章末批量写入 |
| 快照创建 | 每章前同步 | 保持同步（回滚需要一致性） |
| 向量索引更新 | novel reindex 手动 | 章末自动增量（可选） |

实现：`LazyBatchProcessor` 维护内存中的操作队列，章末 `flush()` 批量执行。
FTS5 和 Metrics 的批量写入使用 SQLite 事务包裹，性能提升显著。

新增组件：`core/lazy_batch.py` — `LazyBatchProcessor` 类。

### 4. 资源感知降级（ResourceAwareDegrader）

检测系统资源状况，自动调整计算强度：

```python
class ResourceAwareDegrader:
    """系统资源感知降级器。"""
    
    def get_profile(self) -> str:
        """返回当前资源档位：HIGH / MEDIUM / LOW / MINIMAL"""
    
    def adjust_retrieval_top_k(self, base: int) -> int:
        """根据资源档位调整检索 top_k"""
    
    def should_skip_reranker(self) -> bool:
        """资源不足时自动跳过 Cross-Encoder"""
    
    def should_use_frugal_strategy(self) -> bool:
        """资源极低时强制使用 FRUGAL 策略"""
```

检测指标：可用内存百分比、CPU 使用率、是否有 GPU、是否为电池供电（笔记本）。

配置：通过 `novel.yaml` 的 `performance` 字段控制降级档位：
- `auto`（默认）：自动检测
- `high`：禁用降级
- `medium`：中等降级
- `low`：激进降级
- `minimal`：最小化计算

新增组件：`core/resource_aware.py` — `ResourceAwareDegrader` 类。

### 5. Prompt Cache 集成

利用 LiteLLM 的 Prompt Caching 支持（Anthropic Claude 等模型），
缓存稳定不变的系统提示和 CANON 设定前缀：

- 缓存内容：Agent 人格 Prompt（`prompts/*.v1.md`）+ CANON 核心规则
- 缓存策略：前缀匹配（前 N tokens 不变即可命中缓存）
- 预期收益：节省 50-64% 的输入 Token 成本（基于 Anthropic 官方数据）

修改文件：`core/llm.py` — `LLMBus.chat()` 增加 `cached_prefix` 参数。

## 考虑过的替代方案

- **完全手动模型选择**：维护负担重，作者需要在 20+ 模型间选择。拒绝。
- **仅基于模型价格路由**：价格与质量不一定正相关，可能导致高潮章节质量下降。拒绝。
- **全量异步批处理**（包括快照）：快照必须在写前完成以保证回滚一致性，不可异步。

## 影响

- 新增 3 个模块：`priority_scheduler.py`、`lazy_batch.py`、`resource_aware.py`
- 修改 `core/llm.py`（复杂度路由 + Prompt Cache）
- TRANSITION 章节预期节省 40-60% Token（轻量模型 + 低预算 + 跳过 Director）
- 批处理优化：FTS5 写入延迟从关键路径移除，Metrics 写入合并为单事务
- 低配机器自动降级：禁用 Reranker、降低 top_k、强制 FRUGAL 策略

## 依赖

- ADR 0002（三级上下文策略）— FRUGAL 策略在资源降级时强制使用
- ADR 0004（MetricsStore）— 模型表现数据来源
- ADR 0006（章节类型检测）— 弹性调度的分类依据
- ADR 0007（SearchPipeline / Reranker）— 可降级的组件
