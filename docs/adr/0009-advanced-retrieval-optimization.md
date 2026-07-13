# 0009 — 高级检索优化：动态重排、语义缓存与查询进化

在 ADR 0007 的三通道 + RRF + Cross-Encoder 基础上，增加查询转换、
动态置信度重排和语义缓存三层优化。

## 背景

ADR 0007 实现了基础搜索管道，但存在以下可优化点：

1. **静态重排规则**：`top1 > top2 × 2` 的阈值规则对多义查询（如"剑"可指武器/剑术/铸剑）
   可能过于乐观地跳过重排序，导致次优结果。
2. **查询-文档语义鸿沟**：作者用自然语言描述的查询（"主角性格特点"）与知识库中的
   文档表述（"林远倾向于在危机中保持冷静，但在涉及家人时会冲动"）存在表述差异。
3. **重复检索浪费**：相邻章节的上下文检索高度重叠（如连续几章都涉及同一场景），
   每次重新计算向量和 RRF 融合造成不必要的延迟和成本。

## 决策

### 1. 动态置信度重排（Confidence-Based Reranking）

替换固定的 `top1 > top2 × 2` 规则，引入基于分数分布的重排决策：

```python
def should_rerank(candidates: list[SearchResult]) -> bool:
    """基于分数分布标准差决定是否重排序。"""
    if len(candidates) < 2:
        return False
    scores = [c.score for c in candidates[:5]]
    std = statistics.stdev(scores)
    mean = statistics.mean(scores)
    cv = std / mean if mean > 0 else 0
    # 变异系数 > 0.3 表示分数分散（可能多义），触发重排
    # 变异系数 <= 0.3 表示分数集中，top1 确实优于其他
    return cv > 0.3
```

同时保留原始的阈值快速路径作为性能优化：当 `top1_score > top2_score × 3` 时，
无论变异系数如何都跳过重排（因为 top1 压倒性优势）。

修改文件：`core/reranker.py` — `Reranker.should_skip_rerank()` 方法。

### 2. 查询转换模块（QueryTransformer）

在 SearchPipeline 入口前增加查询转换，支持三种策略：

| 策略 | 适用场景 | 机制 |
|---|---|---|
| **Multi-Query** | 模糊查询 | 将一个查询改写为 3 个不同角度的查询，并行检索后 RRF 融合 |
| **HyDE** (Hypothetical Document Embeddings) | 抽象概念查询 | 让 LLM 生成假想答案，用假想答案的向量检索真实文档 |
| **Query Decomposition** | 复合查询 | 将复杂查询分解为子查询，分别检索后综合 |

策略选择逻辑：
- 查询长度 < 5 字 → Multi-Query（短查询很可能语义不足）
- 查询含抽象概念词（性格/氛围/主题/风格）→ HyDE
- 查询含"和/与/以及/为什么/如何"→ Query Decomposition
- 默认 → 不做转换，直接检索

新增组件：`core/query_transformer.py` — `QueryTransformer` 类。
HyDE 模式使用 Writer 的 think_model（轻量模型）生成假想答案，成本可控。

### 3. 语义缓存层（SemanticCache）

在 SearchPipeline 和 ContextAssembler 之间插入语义缓存：

```python
class SemanticCache:
    """基于语义相似度的检索结果缓存。"""
    
    def get(self, query: str, threshold: float = 0.92) -> SearchResponse | None:
        """查找相似查询的缓存结果。"""
    
    def put(self, query: str, response: SearchResponse) -> None:
        """缓存检索结果。"""
    
    def _compute_similarity(self, q1: str, q2: str) -> float:
        """计算两个查询的语义相似度（使用 BGE-M3 embedding 的余弦相似度）。"""
```

缓存策略：
- 使用 VectorStore 现有 BGE-M3 embedding 对查询文本编码
- 余弦相似度 ≥ 0.92 时命中缓存
- 缓存容量上限 500 条，LRU 淘汰
- 每章完成后清空缓存（新章节的上下文需求通常不同）
- 可选持久化到 `.novel.semantic_cache.db`（SQLite，独立于其他数据库）

新增组件：`core/semantic_cache.py` — `SemanticCache` 类。

### 4. 修复已知缺陷（Code Review P1/P2）

同步修复 code_review_results.md 中与搜索管道相关的已知问题：

- **P2-10**: `_search_vector` 按行分块改为按段落分块（保留上下文完整性）
- **P2-11**: `_search_events` 添加 LIMIT 子句（最多 50 条）
- **P2-12**: `tool_registry.py` 中 SearchPipeline 路径使用实际 RRF/Reranker 分数
- **P2-14**: chunker 将 YAML Frontmatter 字段（id/name/aliases）独立索引

## 考虑过的替代方案

- **全量重排**（每次都跑 Cross-Encoder）：延迟从 0.1ms 飙升至 ~500ms，不可接受。
- **仅用 LLM 做查询改写**：成本高（每次查询多一次 LLM 调用），且对简单查询不必要。
- **Redis 缓存**：引入外部依赖，违背本地优先哲学。SQLite 语义缓存更合适。

## 影响

- 新增 3 个模块：`query_transformer.py`、`semantic_cache.py`，修改 `reranker.py`
- 语义缓存命中时，检索延迟从 ~200ms 降至 ~1ms（仅 embedding 计算）
- HyDE 模式每次额外消耗一次轻量 LLM 调用（约 200 tokens），需在 `novel.yaml` 中
  通过 `retrieval.hyde_enabled` 开关控制
- 查询转换仅对 ContextAssembler 路径启用（Agent 自治路径为保延迟不做转换）

## 依赖

- ADR 0007（SearchPipeline / VectorStore / Reranker）
- ADR 0008（ContextAssembler 重构后的新接口）
