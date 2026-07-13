"""统一搜索管道 — 三通道 + RRF 融合 + Cross-Encoder 重排序。

架构：
  Query
    ├── VectorStore（BGE-M3 语义检索）→ top 15 chunk_ids
    ├── FTS5（unicode61 精确关键词）   → top 15 chunk_ids
    └── EventStore（SQL 事件查询）     → top 15 chunk_ids
             ↓
    RRF 融合（k=30, EventStore ×1.5）  → top 50 候选
             ↓
    Cross-Encoder（bge-reranker-v2-m3）→ top 5 精排
             ↓
    ContextAssembler / Agent

两级精度策略：
- Agent 自治（ToolRegistry）：RRF 融合，use_reranker=False，延迟 <10ms
- ContextAssembler：完整管道 RRF + Cross-Encoder，延迟 ~200ms

详见 docs/adr/0007-hybrid-search-and-reranking-architecture.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

from opennovel.core.chunker import MarkdownChunker
from opennovel.core.query_transformer import QueryTransformer
from opennovel.core.reranker import Reranker
from opennovel.core.semantic_cache import SemanticCache
from opennovel.schemas.search import Chunk, ChunkSource, SearchResponse, SearchResult

logger = logging.getLogger(__name__)

# ── RRF 融合常量 ──────────────────────────────────────────────────────
RRF_K = 30
RRF_EVENT_WEIGHT = 1.5
RRF_DEFAULT_WEIGHT = 1.0
RRF_TOP_CANDIDATES = 50
CHANNEL_TOP_K = 15

# ── 搜索管道的最大精排结果数 ─────────────────────────────────────────
FINAL_TOP_K = 5


def rrf_fusion(
    channel_results: dict[str, list[SearchResult]],
    k: int = RRF_K,
    event_weight: float = RRF_EVENT_WEIGHT,
    default_weight: float = RRF_DEFAULT_WEIGHT,
) -> list[SearchResult]:
    """多通道检索结果的排名融合（Reciprocal Rank Fusion）。

    公式：score(d) = Σ w_channel / (k + rank_channel(d))

    不依赖原始分数，仅使用排名。对文本去重（相同 text 合并为同一结果）。

    Args:
        channel_results: {channel_name: [SearchResult, ...]} 各通道的排序结果
        k: RRF 平滑常数（默认 30）
        event_weight: EventStore 通道的权重倍率
        default_weight: 其他通道的默认权重

    Returns:
        RRF 融合后的结果列表，按 RRF 分数降序排列，最多 50 条
    """
    # {text_hash: (result, rrf_score)}
    merged: dict[str, tuple[SearchResult, float]] = {}

    for channel_name, results in channel_results.items():
        weight = event_weight if channel_name == "event" else default_weight

        for rank, result in enumerate(results, start=1):
            rrf = weight / (k + rank)

            # 用文本内容作为去重键（简单哈希前 200 字符）
            text_key = result.text[:200].strip()

            if text_key in merged:
                existing_result, existing_score = merged[text_key]
                # 合并：累加 RRF 分数并写回 result.score
                accumulated = existing_score + rrf
                existing_result.score = accumulated
                merged[text_key] = (existing_result, accumulated)
                # 标记为多通道来源
                if channel_name not in existing_result.channel:
                    existing_result.channel += f"+{channel_name}"
            else:
                result.score = rrf
                result.channel = channel_name
                merged[text_key] = (result, rrf)

    # 按 RRF 分数降序排列
    sorted_results = sorted(merged.values(), key=lambda x: x[1], reverse=True)
    results = [r for r, _ in sorted_results[:RRF_TOP_CANDIDATES]]

    logger.debug(
        "RRF 融合: %d 个通道 → %d 个唯一候选 → top %d",
        len(channel_results),
        len(merged),
        len(results),
    )
    return results


class SearchPipeline:
    """统一搜索管道，编排三通道检索 + RRF 融合 + Cross-Encoder 重排序。

    使用方式:
        pipeline = SearchPipeline(project_root, vector_store, fts5_store, event_store)
        response = pipeline.search("影渊森林的魔法规则")
        pipeline.rebuild_index()
    """

    def __init__(
        self,
        project_root: Path,
        vector_store=None,
        fts5_store=None,
        event_store=None,
        reranker: Reranker | None = None,
    ) -> None:
        """初始化搜索管道。

        Args:
            project_root: 项目根目录
            vector_store: VectorStore 实例（语义通道）
            fts5_store: Fts5Store 实例（关键词通道）
            event_store: EventStore 实例（事件通道）
            reranker: Reranker 实例（可选，默认创建）
        """
        self.project_root = project_root
        self.vector_store = vector_store
        self.fts5_store = fts5_store
        self.event_store = event_store
        self.reranker = reranker or Reranker()

        # ADR 0009: 查询转换器 + 语义缓存
        self.query_transformer = QueryTransformer()
        self.semantic_cache = SemanticCache(project_root)

    def search(
        self,
        query: str,
        top_k: int = FINAL_TOP_K,
        use_reranker: bool = True,
        source_filter: ChunkSource | None = None,
    ) -> SearchResponse:
        """执行完整搜索管道：三通道 → RRF → 可选重排序。

        Args:
            query: 查询文本
            top_k: 返回结果数量
            use_reranker: 是否启用 Cross-Encoder 重排序
            source_filter: 可选的来源过滤

        Returns:
            SearchResponse 包含精排后的结果
        """
        if not query.strip():
            return SearchResponse(query=query, results=[], total_candidates=0)

        # ADR 0009: 语义缓存 — 命中时跳过全管道
        if use_reranker:
            cached = self.semantic_cache.get(query)
            if cached is not None:
                return cached

        # ADR 0009: 查询转换 — 模糊/抽象/复合查询增强
        transformed = self.query_transformer.transform(query)
        search_queries = transformed.variants if transformed.strategy != "none" else [query]
        if transformed.strategy != "none":
            logger.debug("查询转换: %s → %s (%s)", query[:50], transformed.strategy, len(search_queries))

        # ── 1. 三通道并行检索（对每个查询变体）──
        all_channel_results: dict[str, list[SearchResult]] = {}
        for q in search_queries[:3]:  # 最多 3 个变体，防止延迟失控
            # 向量语义检索
            if self.vector_store is not None:
                try:
                    vec_texts = self.vector_store.query(q, top_k=CHANNEL_TOP_K)
                    if vec_texts:
                        existing = all_channel_results.get("vector", [])
                        existing.extend([
                            SearchResult(
                                chunk_id=f"vector_{len(existing) + i}",
                                text=t,
                                source=ChunkSource.CANON,
                                score=0.0,
                                channel="vector",
                            )
                            for i, t in enumerate(vec_texts)
                        ])
                        all_channel_results["vector"] = existing
                except Exception as e:
                    logger.debug("向量检索通道失败: %s", e)

            # FTS5 关键词检索
            if self.fts5_store is not None:
                try:
                    fts5_results = self.fts5_store.search(
                        q, top_k=CHANNEL_TOP_K, source_filter=source_filter,
                    )
                    if fts5_results:
                        existing = all_channel_results.get("fts5", [])
                        existing.extend([
                            SearchResult(
                                chunk_id=r["chunk_id"],
                                text=r["text"],
                                source=ChunkSource(r["source"]),
                                score=0.0,
                                channel="fts5",
                            )
                            for r in fts5_results
                        ])
                        all_channel_results["fts5"] = existing
                except Exception as e:
                    logger.debug("FTS5 检索通道失败: %s", e)

            # EventStore 事件查询
            if self.event_store is not None:
                try:
                    event_results = self._search_events(q)
                    if event_results:
                        existing = all_channel_results.get("event", [])
                        existing.extend(event_results)
                        all_channel_results["event"] = existing
                except Exception as e:
                    logger.debug("EventStore 检索通道失败: %s", e)

        # ── 2. RRF 融合 ──
        if not all_channel_results:
            return SearchResponse(query=query, results=[], total_candidates=0)

        candidates = rrf_fusion(all_channel_results)
        total_candidates = len(candidates)

        # ── 3. Cross-Encoder 重排序 ──
        reranker_used = False
        if use_reranker and len(candidates) > 1:
            reranker_used = True
            candidates = self.reranker.rerank(query, candidates)

        # 截断到 top_k
        final_results = candidates[:top_k]

        response = SearchResponse(
            query=query,
            results=final_results,
            total_candidates=total_candidates,
            reranker_used=reranker_used,
        )

        # ADR 0009: 缓存本次检索结果（仅完整管道路径）
        if use_reranker and len(final_results) > 0:
            self.semantic_cache.put(query, response)

        return response

    def _search_events(self, query: str) -> list[SearchResult]:
        """从 EventStore 搜索相关事件并格式化为 SearchResult。

        目前策略：搜索所有高压力事件 + 关键词匹配，
        格式化为可检索的文本片段。

        Args:
            query: 查询文本

        Returns:
            SearchResult 列表
        """
        results: list[SearchResult] = []

        # 获取高压力事件（阈值 0.3，较宽松）
        try:
            high_events = self.event_store.get_high_pressure_events(threshold=0.3)
        except AttributeError:
            # EventStore 可能没有该方法
            return results

        # 简单关键词匹配筛选
        query_lower = query.lower()
        for evt in high_events[:CHANNEL_TOP_K * 2]:
            desc_lower = evt.description.lower()
            event_type_lower = str(evt.event_type).lower()

            # 检查是否匹配查询词
            if any(term in desc_lower or term in event_type_lower
                   for term in query_lower.split()):
                event_text = (
                    f"[事件 {evt.event_id}] {evt.event_type}: {evt.description}"
                    f"（因果压强: {evt.causal_pressure}）"
                )
                results.append(
                    SearchResult(
                        chunk_id=evt.event_id,
                        text=event_text,
                        source=ChunkSource.DRAFT,
                        score=0.0,
                        channel="event",
                    )
                )

        return results[:CHANNEL_TOP_K]

    # ── 索引生命周期 ──────────────────────────────────────────────────

    def rebuild_index(
        self,
        canon_dir: Path | None = None,
        characters_dir: Path | None = None,
        subconscious_dir: Path | None = None,
        draft_dir: Path | None = None,
    ) -> int:
        """全量重建搜索索引。

        1. 清空 FTS5 索引
        2. 对四个源目录重新分块
        3. 批量写入 FTS5
        4. VectorStore 全量重建（调用方负责）

        Args:
            canon_dir: canon/ 目录路径
            characters_dir: characters/ 目录路径
            subconscious_dir: subconscious/ 目录路径
            draft_dir: draft/ 目录路径

        Returns:
            写入的总 chunk 数
        """
        chunker = MarkdownChunker(self.project_root)
        grouped = chunker.chunk_project_sources(
            canon_dir=canon_dir,
            characters_dir=characters_dir,
            subconscious_dir=subconscious_dir,
            draft_dir=draft_dir,
        )

        if not grouped:
            logger.warning("未找到可索引的文件")
            return 0

        # 清空旧索引
        if self.fts5_store is not None:
            self.fts5_store.clear_all()

        # 写入所有 chunk
        total = 0
        if self.fts5_store is not None:
            for source, chunks in grouped.items():
                count = self.fts5_store.insert_chunks(chunks)
                total += count
                logger.info("索引 %s: %d 个 chunk", source.value, count)

                # 将 chunk 文本也添加到向量索引
                if self.vector_store is not None:
                    for chunk in chunks:
                        try:
                            self.vector_store.add_document(
                                chunk.text,
                                metadata={
                                    "chunk_id": chunk.chunk_id,
                                    "source": chunk.source.value,
                                    "doc_stem": chunk.doc_stem,
                                },
                            )
                        except Exception as e:
                            logger.debug("向量索引添加失败: %s (%s)", chunk.chunk_id, e)

            self.fts5_store.record_rebuild()

        logger.info("全量索引重建完成: %d 个 chunk", total)
        return total

    def incremental_update(self, chunks: list[Chunk]) -> int:
        """增量更新索引——写入新增或修改的 chunk。

        FTS5 实时增量更新（INSERT OR REPLACE），VectorStore 最终一致性。

        Args:
            chunks: 需要更新的 Chunk 列表

        Returns:
            更新的 chunk 数
        """
        if not chunks:
            return 0

        count = 0
        if self.fts5_store is not None:
            count = self.fts5_store.insert_chunks(chunks)

        logger.info("增量索引更新: %d 个 chunk", count)
        return count
