"""混合检索路由器（薄封装） — ADR 0007 兼容层。

内部委托给 SearchPipeline 执行三通道（向量/FTS5/EventStore）+ RRF + Reranker 搜索。
保留旧接口（query_narrative_context, query_for_writer, query_for_critic）确保
Gen1 Actor/Agent 兼容性。

新接口:
    retriever = HybridRetriever(project_root, pipeline=pipeline)
    result = retriever.search("影渊森林")  # 返回 RetrievalResult（ADR 0007）

旧接口（保留兼容）:
    result = retriever.query_narrative_context("影渊森林")  # 返回旧式 RetrievalResult

详见 ADR 0007 — 混合语义-关键词检索 + 重排序架构。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opennovel.core.retriever import Retriever
from opennovel.schemas.event import EventLog
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """混合检索结果（旧式兼容层）。

    保留了原有的字段（character_events, causal_chain, canon_content, subconscious_content），
    同时新增 chunks 字段承载 SearchPipeline 的输出。

    新代码应优先使用 RetrivalResult.chunks。
    旧字段已标记为 @deprecated，通过 @property 保持兼容。
    """

    # ── ADR 0007 新字段 ──
    chunks: list = field(default_factory=list)

    # ── 旧字段（保留兼容） ──
    character_events: list[EventLog] = field(default_factory=list)
    causal_chain: list[EventLog] = field(default_factory=list)
    high_pressure_events: list[EventLog] = field(default_factory=list)
    canon_content: str = ""
    subconscious_content: str = ""
    causal_chain_context: str = ""


class HybridRetriever:
    """混合检索路由器（薄封装版）。

    内部委托给 SearchPipeline 执行三通道搜索。
    保留旧接口签名，用于 Gen1 Actor 和 AutoRunner 的兼容调用。
    """

    def __init__(
        self,
        project_root: Path,
        event_store: EventStore | None = None,
        retriever: Retriever | None = None,
        search_pipeline: Any | None = None,
    ) -> None:
        """初始化混合检索路由器。

        Args:
            project_root: 项目根目录路径
            event_store: 事件账本实例
            retriever: 语义检索实例
            search_pipeline: SearchPipeline 实例（ADR 0007），
                             提供时优先使用
        """
        self.project_root = project_root
        self.event_store = event_store
        self.retriever = retriever or Retriever(project_root)
        self._pipeline = search_pipeline

    # ── ADR 0007 新接口 ───────────────────────────────────────────

    def search(
        self,
        query_text: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        use_reranker: bool = True,
        top_k: int = 5,
    ) -> "Any":
        """通过 SearchPipeline 执行三通道混合搜索。

        Args:
            query_text: 查询文本
            chapter_id: 当前章节 ID
            character_ids: 关注的角色 ID
            use_reranker: 是否启用 Cross-Encoder
            top_k: 返回结果数

        Returns:
            RetrievalResult（opennovel.schemas.search.RetrievalResult）
        """
        if self._pipeline is not None:
            return self._pipeline.search(
                query=query_text,
                chapter_id=chapter_id,
                character_ids=character_ids,
                use_reranker=use_reranker,
                top_k=top_k,
            )

        # 无 pipeline 时回退到旧实现
        from opennovel.schemas.search import (
            Chunk, ChunkSource, RerankedChunk, RetrievalResult as NewRetrievalResult,
        )
        result = self.query_narrative_context(
            query_text, chapter_id=chapter_id, character_ids=character_ids,
        )
        chunks: list[RerankedChunk] = []
        rank = 0
        if result.canon_content:
            chunks.append(RerankedChunk(
                chunk=Chunk(
                    chunk_id="canon_legacy_p0000",
                    text=result.canon_content,
                    source=ChunkSource.CANON,
                ),
                rrf_score=1.0, rank=rank,
            ))
            rank += 1
        if result.subconscious_content:
            chunks.append(RerankedChunk(
                chunk=Chunk(
                    chunk_id="subconscious_legacy_p0000",
                    text=result.subconscious_content,
                    source=ChunkSource.SUBCONSCIOUS,
                ),
                rrf_score=0.5, rank=rank,
            ))
            rank += 1
        if result.causal_chain_context:
            chunks.append(RerankedChunk(
                chunk=Chunk(
                    chunk_id="events_legacy_p0000",
                    text=result.causal_chain_context,
                    source=ChunkSource.EVENT,
                ),
                rrf_score=0.3, rank=rank,
            ))
        return NewRetrievalResult(chunks=chunks)

    # ── 旧接口（保留 Gen1 兼容性） ────────────────────────────────

    def query_narrative_context(
        self,
        query_text: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        top_k_canon: int = 3,
        top_k_subconscious: int = 2,
        pressure_threshold: float = 0.5,
    ) -> RetrievalResult:
        """执行混合检索，返回双轨结果。

        Note: 这是旧式兼容接口。新代码请使用 .search()。

        Args:
            query_text: 查询文本
            chapter_id: 当前章节 ID
            character_ids: 关注的角色 ID 列表
            top_k_canon: 设定检索返回条数
            top_k_subconscious: 潜意识检索返回条数
            pressure_threshold: 高压力事件阈值

        Returns:
            RetrievalResult 旧式结果
        """
        result = RetrievalResult()

        # ── SQL 精确召回 ──
        if self.event_store:
            try:
                result.high_pressure_events = (
                    self.event_store.get_high_pressure_events(pressure_threshold)
                )
                if character_ids:
                    for char_id in character_ids:
                        events = self.event_store.get_events_by_character(char_id)
                        result.character_events.extend(events[:5])
                result.causal_chain_context = self._build_causal_chain_context(
                    result.high_pressure_events
                )
            except Exception as e:
                logger.warning("SQL 检索失败: %s", e)

        # ── 向量语义检索 ──
        try:
            result.canon_content = self.retriever.query_canon(
                query_text[:500], top_k=top_k_canon
            )
            result.subconscious_content = self.retriever.query_subconscious(
                query_text[:500], top_k=top_k_subconscious
            )
        except Exception as e:
            logger.warning("向量检索失败: %s", e)

        return result

    def query_for_writer(self, chapter_id: str, outline_hint: str) -> RetrievalResult:
        """为 Writer Agent 定制的检索策略。

        Args:
            chapter_id: 章节 ID
            outline_hint: 大纲提示

        Returns:
            RetrievalResult
        """
        return self.query_narrative_context(
            query_text=outline_hint,
            chapter_id=chapter_id,
            top_k_canon=5,
            top_k_subconscious=2,
            pressure_threshold=0.4,
        )

    def query_for_critic(self, chapter_id: str, chapter_text: str) -> RetrievalResult:
        """为 Critic Agent 定制的检索策略。

        Args:
            chapter_id: 章节 ID
            chapter_text: 章节正文

        Returns:
            RetrievalResult
        """
        return self.query_narrative_context(
            query_text=chapter_text[:1000],
            chapter_id=chapter_id,
            top_k_canon=3,
            top_k_subconscious=1,
            pressure_threshold=0.3,
        )

    def _build_causal_chain_context(
        self, events: list[EventLog], limit: int = 10
    ) -> str:
        """将事件列表格式化为因果链上下文文本。

        Args:
            events: 事件列表
            limit: 最大事件数

        Returns:
            格式化的因果链文本
        """
        if not events:
            return ""

        lines = []
        for evt in events[:limit]:
            chain_info = ""
            if evt.caused_by:
                chain_info = f" ← 由 {evt.caused_by} 引起"
            related = evt.get_related_ids()
            if related:
                chain_info += f" [关联: {', '.join(related)}]"
            lines.append(
                f"- [{evt.event_id}] {evt.event_type}: {evt.description} "
                f"(压强={evt.causal_pressure}){chain_info}"
            )
        return "\n".join(lines)
