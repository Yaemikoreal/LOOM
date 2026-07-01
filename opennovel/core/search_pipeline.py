"""三通道混合搜索管道 — VectorStore + FTS5 + EventStore → RRF → Cross-Encoder。

管道流程:
    Query
      ├── VectorStore（BGE-M3 语义检索）→ top 15 chunk_ids
      ├── FTS5（unicode61 精确关键词） → top 15 chunk_ids
      └── EventStore（SQL 事件查询） → top 15 chunk_ids (×1.5 权重)
               ↓
      RRF 融合（k=30, EventStore ×1.5）→ top 50
               ↓
      Cross-Encoder（bge-reranker-v2-m3）→ top 5 (可选)
               ↓
      ContextAssembler / Agent

两级精度策略:
- Agent 自治 (ToolRegistry): RRF only, use_reranker=False
- ContextAssembler: 全流程 RRF + Cross-Encoder

使用方式:
    pipeline = SearchPipeline(project_root)
    result = pipeline.search("影渊森林的诅咒", chapter_id="ch_005")

详见 ADR 0007 — 混合语义-关键词检索 + 重排序架构。
"""

import hashlib
import logging
from pathlib import Path
from typing import Any

from opennovel.core.reranker import Reranker
from opennovel.schemas.event import EventLog
from opennovel.schemas.search import Chunk, ChunkSource, RerankedChunk, RetrievalResult
from opennovel.storage.fts5 import Fts5Store
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)

# 各通道 top_k 配置
_DEFAULT_VECTOR_TOP_K = 15
_DEFAULT_FTS5_TOP_K = 15
_DEFAULT_EVENT_TOP_K = 15

# RRF 参数
_RRF_K = 30
_EVENT_WEIGHT = 1.5  # EventStore 通道加权
_VECTOR_WEIGHT = 1.0
_FTS5_WEIGHT = 1.0

# Reranker 参数
_RERANKER_TOP_K = 5


class SearchPipeline:
    """三通道混合搜索管道。

    协同 VectorStore（语义）、FTS5（关键词）、EventStore（事件）三通道，
    经 RRF 融合和可选 Cross-Encoder 重排序后输出最终结果。
    """

    def __init__(
        self,
        project_root: Path,
        event_store: EventStore | None = None,
        retriever: Any | None = None,
        fts5_store: Fts5Store | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        """初始化搜索管道。

        Args:
            project_root: 项目根目录路径
            event_store: EventStore 实例（事件通道）
            retriever: Retriever 实例（向量语义通道，含 VectorStore）
            fts5_store: Fts5Store 实例（关键词通道），不传则自动初始化
            reranker: Reranker 实例，不传则按需创建
        """
        self.project_root = project_root
        self._event_store = event_store
        self._retriever = retriever
        self._fts5_store = fts5_store or Fts5Store(project_root)
        self._reranker = reranker

    # ── 属性访问 ───────────────────────────────────────────────────

    @property
    def fts5_store(self) -> Fts5Store:
        """获取 FTS5 存储实例。"""
        return self._fts5_store

    @property
    def reranker(self) -> Reranker:
        """获取 Reranker 实例（惰性初始化）。"""
        if self._reranker is None:
            self._reranker = Reranker()
        return self._reranker

    # ── 主入口 ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        use_reranker: bool = True,
        top_k: int = 5,
    ) -> RetrievalResult:
        """执行三通道混合搜索。

        Args:
            query: 查询文本
            chapter_id: 当前章节 ID（用于事件通道过滤）
            character_ids: 关注的角色 ID 列表
            use_reranker: 是否启用 Cross-Encoder 重排序
            top_k: 最终返回结果数

        Returns:
            RetrievalResult 管道输出
        """
        if not query or not query.strip():
            return RetrievalResult()

        # ── 三通道并行检索 ──
        vector_chunks = self._search_vector(query, top_k=_DEFAULT_VECTOR_TOP_K)
        fts5_results = self._search_fts5(query, top_k=_DEFAULT_FTS5_TOP_K)
        event_chunks = self._search_events(
            query,
            chapter_id=chapter_id,
            character_ids=character_ids,
            top_k=_DEFAULT_EVENT_TOP_K,
        )

        # ── 收集所有候选 Chunk（去重） ──
        all_chunks: list[Chunk] = []
        seen_ids: set[str] = set()

        for chunk_list, weight in [
            (vector_chunks, _VECTOR_WEIGHT),
            (fts5_results, _FTS5_WEIGHT),
            (event_chunks, _EVENT_WEIGHT),
        ]:
            for chunk in chunk_list:
                # RRF 权重记录在 chunk.score（负值存储权重）
                chunk.score = weight
                if chunk.chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk.chunk_id)
                all_chunks.append(chunk)

        if not all_chunks:
            return RetrievalResult()

        # ── RRF 融合 ──
        rrf_scored = self._rrf_fusion(
            vector_chunks=vector_chunks,
            fts5_chunks=fts5_results,
            event_chunks=event_chunks,
            all_chunks=all_chunks,
        )

        # ── 阈值退出检查：top1 > 2× top2 → 跳过 Reranker ──
        skip_reranker = False
        if use_reranker and len(rrf_scored) >= 2:
            sorted_scores = sorted(rrf_scored.values(), reverse=True)
            if sorted_scores[0] > 2.0 * sorted_scores[1]:
                skip_reranker = True
                logger.debug(
                    "RRF 阈值退出: top1=%.4f, top2=%.4f (>2x)",
                    sorted_scores[0], sorted_scores[1],
                )

        # ── Cross-Encoder 重排序 ──
        if use_reranker and not skip_reranker:
            reranked = self._apply_reranker(query, all_chunks, rrf_scored)
        else:
            # 按 RRF 分数降序取 top_k
            sorted_chunk_ids = sorted(rrf_scored, key=rrf_scored.get, reverse=True)
            reranked = [
                RerankedChunk(
                    chunk=all_chunks[self._find_chunk_index(all_chunks, cid)],
                    rrf_score=rrf_scored[cid],
                    rank=i,
                )
                for i, cid in enumerate(sorted_chunk_ids[:top_k])
            ]

        return RetrievalResult(chunks=reranked)

    def search_for_agent(
        self,
        query: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        top_k: int = 10,
    ) -> RetrievalResult:
        """Agent 自治级别搜索（RRF only，无 Reranker）。

        供 ToolRegistry 使用，适合 Agent 内部的实时知识查询。

        Args:
            query: 查询文本
            chapter_id: 当前章节 ID
            character_ids: 关注的角色 ID 列表
            top_k: 返回结果数

        Returns:
            RetrievalResult
        """
        return self.search(
            query=query,
            chapter_id=chapter_id,
            character_ids=character_ids,
            use_reranker=False,
            top_k=top_k,
        )

    # ── 检索通道 ──────────────────────────────────────────────────

    def _search_vector(self, query: str, top_k: int = 15) -> list[Chunk]:
        """向量语义通道检索。

        Args:
            query: 查询文本
            top_k: 返回数

        Returns:
            Chunk 列表
        """
        if self._retriever is None:
            return []

        try:
            # Retriever 返回文本内容（通常按空行分隔的段落）
            canon_text = self._retriever.query_canon(query, top_k=top_k)
            subconscious_text = self._retriever.query_subconscious(query, top_k=top_k)

            chunks: list[Chunk] = []

            def _add_text(text: str, source: ChunkSource, content_type: str) -> None:
                # 按双换行（段落边界）分割，保留段落完整性
                for para in text.split("\n\n"):
                    para = para.strip()
                    if para:
                        # 跳过纯 Frontmatter 行
                        if para.startswith("---") or para.startswith("title:"):
                            continue
                        chunks.append(
                            Chunk(
                                chunk_id=self._make_content_hash("vector", para),
                                text=para,
                                source=source,
                                metadata={"channel": "vector", "type": content_type},
                            )
                        )

            if canon_text:
                _add_text(canon_text, ChunkSource.CANON, "canon")
            if subconscious_text:
                _add_text(subconscious_text, ChunkSource.SUBCONSCIOUS, "subconscious")

            return chunks

        except Exception as e:
            logger.warning("向量语义检索失败: %s", e)
            return []

    def _search_fts5(self, query: str, top_k: int = 15) -> list[Chunk]:
        """FTS5 关键词通道检索。

        Args:
            query: 查询文本
            top_k: 返回数

        Returns:
            Chunk 列表
        """
        try:
            results = self._fts5_store.search(query, top_k=top_k)
            chunks: list[Chunk] = []
            for r in results:
                src_val = r["source"]
                valid_sources = {s.value for s in ChunkSource}
                source = ChunkSource(src_val) if src_val in valid_sources else ChunkSource.CANON
                chunks.append(
                    Chunk(
                        chunk_id=r["chunk_id"],
                        text=r["text"],
                        source=source,
                        metadata=r.get("metadata", {}),
                    )
                )
            return chunks
        except Exception as e:
            logger.warning("FTS5 检索失败: %s", e)
            return []

    def _search_events(
        self,
        query: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        top_k: int = 15,
    ) -> list[Chunk]:
        """事件通道检索。

        EventStore 查询结果格式化后转为 Chunk。
        按因果压强(causal_pressure)降序排列。

        Args:
            query: 查询文本（用于关键词匹配事件描述）
            chapter_id: 当前章节 ID
            character_ids: 关注的角色 ID
            top_k: 返回数

        Returns:
            Chunk 列表
        """
        if self._event_store is None:
            return []

        try:
            events: list[EventLog] = []

            # 按角色查询
            if character_ids:
                for char_id in character_ids:
                    char_events = self._event_store.get_events_by_character(char_id)
                    events.extend(char_events)

            # 按章节查询
            if chapter_id:
                ch_events = self._event_store.get_events_by_chapter(chapter_id)
                events.extend(ch_events)

            # 高压力事件补充（带 LIMIT 防全表扫描）
            high_events = self._event_store.get_high_pressure_events(threshold=0.3)
            events.extend(high_events[:max(1, top_k // 2)])

            # 去重（按 event_id）
            seen: set[str] = set()
            unique_events: list[EventLog] = []
            for evt in events:
                if evt.event_id not in seen:
                    seen.add(evt.event_id)
                    unique_events.append(evt)

            # 按因果压强降序排列
            unique_events.sort(key=lambda e: e.causal_pressure, reverse=True)

            # 可选：关键词过滤（与 query 文本匹配）
            if query.strip():
                query_lower = query.lower()
                filtered: list[EventLog] = []
                for evt in unique_events:
                    if any(
                        word in evt.description.lower()
                        for word in query_lower.split()
                    ):
                        filtered.append(evt)
                unique_events = filtered

            # 格式化为 Chunk
            chunks: list[Chunk] = []
            for evt in unique_events[:top_k]:
                text = (
                    f"[{evt.chapter_id}] {evt.event_type}: {evt.description} "
                    f"(压强={evt.causal_pressure})"
                )
                chunks.append(
                    Chunk(
                        chunk_id=f"evt_{evt.event_id}",
                        text=text,
                        source=ChunkSource.EVENT,
                        metadata={
                            "event_id": evt.event_id,
                            "chapter_id": evt.chapter_id,
                            "character_id": evt.character_id,
                            "causal_pressure": evt.causal_pressure,
                            "channel": "event",
                        },
                        score=evt.causal_pressure,
                    )
                )

            return chunks

        except Exception as e:
            logger.warning("事件检索失败: %s", e)
            return []

    # ── RRF 融合 ──────────────────────────────────────────────────

    def _rrf_fusion(
        self,
        vector_chunks: list[Chunk],
        fts5_chunks: list[Chunk],
        event_chunks: list[Chunk],
        all_chunks: list[Chunk],
    ) -> dict[str, float]:
        """执行 RRF（Reciprocal Rank Fusion）融合。

        RRF 公式：score(item) = Σ (weight / (k + rank(item, channel)))

        Args:
            vector_chunks: 向量通道结果
            fts5_chunks: FTS5 通道结果
            event_chunks: 事件通道结果
            all_chunks: 所有候选 Chunk

        Returns:
            dict[chunk_id, rrf_score]
        """
        # 构建每个通道的排名映射 {chunk_id: rank}
        vector_rank = {c.chunk_id: i for i, c in enumerate(vector_chunks)}
        fts5_rank = {c.chunk_id: i for i, c in enumerate(fts5_chunks)}
        event_rank = {c.chunk_id: i for i, c in enumerate(event_chunks)}

        # 计算 RRF 分数
        rrf_scores: dict[str, float] = {}
        for chunk in all_chunks:
            cid = chunk.chunk_id
            score = 0.0

            # VectorStore 通道
            if cid in vector_rank:
                score += _VECTOR_WEIGHT / (_RRF_K + vector_rank[cid])

            # FTS5 通道
            if cid in fts5_rank:
                score += _FTS5_WEIGHT / (_RRF_K + fts5_rank[cid])

            # EventStore 通道（×1.5 权重）
            if cid in event_rank:
                score += _EVENT_WEIGHT / (_RRF_K + event_rank[cid])

            if score > 0:
                rrf_scores[cid] = score

        return rrf_scores

    # ── Reranker ──────────────────────────────────────────────────

    def _apply_reranker(
        self,
        query: str,
        all_chunks: list[Chunk],
        rrf_scores: dict[str, float],
        top_k: int = _RERANKER_TOP_K,
    ) -> list[RerankedChunk]:
        """应用 Cross-Encoder 重排序。

        Args:
            query: 查询文本
            all_chunks: 所有候选 Chunk
            rrf_scores: RRF 分数映射
            top_k: 最终返回数

        Returns:
            重排序后的 RerankedChunk 列表
        """
        # 取 RRF top 50 作为 Reranker 输入
        sorted_by_rrf = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:50]
        candidates = [all_chunks[self._find_chunk_index(all_chunks, cid)] for cid in sorted_by_rrf]
        candidate_texts = [c.text for c in candidates]

        if not candidate_texts:
            return []

        # Reranker 评分
        try:
            reranker_scores = self.reranker.rerank(query, candidate_texts, threshold_exit=True)
        except Exception as e:
            logger.warning("Reranker 评分失败，回退 RRF: %s", e)
            reranker_scores = [1.0] * len(candidate_texts)

        # 按 Reranker 分数降序排列
        indexed = list(enumerate(reranker_scores))
        indexed.sort(key=lambda x: x[1], reverse=True)

        reranked: list[RerankedChunk] = []
        for rank, (orig_idx, rerank_score) in enumerate(indexed[:top_k]):
            chunk = candidates[orig_idx]
            cid = chunk.chunk_id
            reranked.append(
                RerankedChunk(
                    chunk=chunk,
                    rrf_score=rrf_scores.get(cid, 0.0),
                    reranker_score=rerank_score,
                    rank=rank,
                )
            )

        return reranked

    # ── 辅助方法 ──────────────────────────────────────────────────

    @staticmethod
    def _make_content_hash(prefix: str, text: str) -> str:
        """根据文本内容生成确定性 hash ID。

        Args:
            prefix: 前缀（如 "vector"、"event"）
            text: 文本内容

        Returns:
            格式: {prefix}_{sha256短值}
        """
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{h}"

    @staticmethod
    def _find_chunk_index(chunks: list[Chunk], chunk_id: str) -> int:
        """在列表中查找指定 chunk_id 的索引。

        Args:
            chunks: Chunk 列表
            chunk_id: 目标 chunk_id

        Returns:
            索引

        Raises:
            ValueError: chunk_id 在列表中不存在
        """
        for i, c in enumerate(chunks):
            if c.chunk_id == chunk_id:
                return i
        raise ValueError(f"chunk_id '{chunk_id}' not found in {len(chunks)} chunks")
