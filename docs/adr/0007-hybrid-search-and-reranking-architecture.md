# 0007 — 混合语义-关键词检索 + 重排序架构

HybridRetriever 的三通道（向量/FTS5/EventStore）+ RRF 融合 + Cross-Encoder 重排序，替代原有的双通道简单拼接。

## 背景

原有检索架构的问题：

- **双通道简单拼接**：向量语义（BGE-M3）与 SQL 事件（EventStore）各自检索后直接拼入上下文，缺乏融合机制
- **无关键词检索**：角色名、地名等专有名词在向量检索中语义模糊（"影渊森林"可能被召回为"神秘森林"），SQL 通道只存事件不存正文
- **无重排序**：top-k 直接截断，无法对候选结果二次校准
- **无统一分块**：VectorStore 以整个 .md 文件为索引单位，粒度过粗

## 决策

### 三通道架构

```
Query
  ├── VectorStore（BGE-M3 语义检索）→ top 15 chunk_ids
  ├── FTS5（unicode61 精确关键词） → top 15 chunk_ids
  └── EventStore（SQL 事件查询，格式化文本） → top 15 chunk_ids
           ↓
  RRF 融合（k=30, EventStore ×1.5）→ top 50
           ↓
  Cross-Encoder（bge-reranker-v2-m3）→ top 5
           ↓
  ContextAssembler / Agent
```

### FTS5 存储

新建 `.novel.fts5.db`，独立于 EventStore（`.novel.db`）和 MetricsStore（`.novel.metrics.db`），因为搜索索引的生命周期与叙事真相不同（可删除重建）。

双表设计：
- `chunks`：分块元数据 + 原始文本
- `chunks_fts`：FTS5 虚拟表（external content 模式）

分词策略：unicode61（不引入 jieba），中文按单字 tokenize。接受长查询的低召回——FTS5 负责精确匹配，VectorStore 负责语义补位。查询端可加可选停用词过滤。

### 分块策略

新建 `core/chunker.py`，递归 Markdown 分块：

1. `#` 一级标题 → 顶级边界
2. 超 `max_chunk_tokens=512` → `##` 二级标题切割
3. 仍超限 → 空行段落切割
4. 仍超限 → 句末边界切割

确定性 chunk_id 生成（`{source}_{doc_stem}_p{index}`），不含 hash，支持增量覆盖。

### 索引生命周期

FTS5 实时增量更新（commit/stash 时触发），VectorStore 最终一致性（仅 `novel reindex` 全量重建）。脏数据由 Cross-Encoder 精排防线兜底。

### 两级精度

- Agent 自治（ToolRegistry）：RRF 融合，`use_reranker=False`
- ContextAssembler：全流程 RRF + Cross-Encoder

### 集成点

- `SearchPipeline` 新建（`core/search_pipeline.py`）：三通道 + RRF + Reranker 的完整管道
- `HybridRetriever` 改为薄封装，`chunks` 为主字段，旧字段改为 `@property`
- `ContextAssembler` 优先读取 `chunks`，按 source 分配权威层级（方案 C）
- `ToolRegistry` 通过 SearchPipeline 路由（`use_reranker=False`）
- `novel reindex` 新增 CLI 命令

## 考虑过的选项

| 方案 | 否决原因 |
|------|----------|
| Elasticsearch 全文检索引擎 | 太重，不符合本地优先哲学 |
| rank_bm25 纯方案 | 需全量加载到内存，不如 SQLite FTS5 持久化 |
| jieba 预分词 + FTS5 | 增加运维复杂度，unicode61 在小说专有名词场景已够用 |
| VectorStore 增量实时更新 | LlamaIndex 的 `delete_ref_doc()` 有 embedding 残留隐患，修复成本 > 收益 |
| 单一 SQLite DB 合并搜索和事件 | 生命周期不同（搜索可重建，事件不可丢），物理隔离更干净 |

## 后果

### 正面

- 专有名词检索精度大幅提升（FTS5 精确匹配）
- 多通道结果不再简单拼接，RRF + Reranker 保证质量
- Chunk 级索引提供段落粒度检索
- 脏索引策略避免了 VectorStore 增量更新的高风险代码
- 全部网络依赖为 0，同步为 0，纯本地

### 负面

- **Reranker CPU 延迟**：bge-reranker-v2-m3 在 CPU 上推理 50 个候选对约 400-600ms，在交互式场景中可能产生可见卡顿。
  - **缓解 1**：阈值提前退出——RRF 融合后 top1 分数显著高于第二名（>2×）时跳过 Reranker，直接取 RRF top5。日常单义查询中 top1 通常压倒性，仅多义查询（"剑"可能指武器/剑术/铸剑场景）才真正需要 Reranker。
  - **缓解 2**：后台预取（future work）——ContextAssembler 撰写当前章时预检索下一章，GUI 阶段实现。
- **FTS5 停用词误杀风险**：若加查询端停用词滤波，"规则""状态""世界"等小说核心 key 可能被误杀。
  - **缓解**：不引入查询端停用词列表。unicode61 中文单字 tokenize 下，每个字是最小单元，不存在停用词误杀问题。仅过滤标点和空查询（~10 行代码）。若后续发现需过滤，通过 `novel.yaml search.stop_words` 按项目配置。
- **VectorStore 脏数据遗忘**：长时间不执行 `novel reindex`，向量语义滞后于叙事进展（如角色性格已完全转变但旧向量仍存在）。
  - **缓解**：`.novel.fts5.db` 的 `search_meta` 表记录索引版本和重建时间。CLI 入口自动检测：新增 5+ 章或距上次重建超 7 天时提示 `建议执行 novel reindex`。纯提醒不阻塞创作流程。
- 引入 Cross-Encoder（~2.2GB 模型下载，~500MB 内存），无 GPU 用户可设置 `reranker_enabled: false` 降级为纯 RRF
- FTS5 unicode61 对长查询低召回是预期行为，但需要用户理解
- 全量重建（~30s）对高频修改场景需要手动触发
