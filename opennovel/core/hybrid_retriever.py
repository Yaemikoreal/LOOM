"""混合检索路由器 - SQL 精确召回 + 向量语义搜索 + FTS5 关键词检索。

三轨并行检索架构（ADR 0006 + ADR 0007）：
- SQL 路径：从 EventStore 精确查询结构化事实（角色事件、因果链、高压力事件）
- 向量路径：从 VectorStore 语义检索非结构化内容（设定、潜意识）
- FTS5 路径：从 Fts5Store 关键词检索（专有名词、角色名、地名）

三条路径独立运行，通过 SearchPipeline 的 RRF 融合统一输出。
HybridRetriever 是 SearchPipeline 的上层封装，保持向后兼容的 API。

使用方式:
    hybrid = HybridRetriever(project_root, event_store)
    context = hybrid.query_narrative_context("char_001 受伤后", chapter_id="ch_003")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from opennovel.core.retriever import Retriever
from opennovel.schemas.event import EventLog
from opennovel.schemas.search import ChunkSource
from opennovel.storage.sqlite import EventStore

if TYPE_CHECKING:
    from opennovel.core.search_pipeline import SearchPipeline
    from opennovel.storage.fts5 import Fts5Store

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """混合检索结果，包含 SQL、向量、FTS5 三条路径的输出。"""

    # SQL 精确召回
    character_events: list[EventLog] = field(default_factory=list)
    causal_chain: list[EventLog] = field(default_factory=list)
    high_pressure_events: list[EventLog] = field(default_factory=list)

    # 向量语义检索 + FTS5 关键词（合并）
    canon_content: str = ""
    subconscious_content: str = ""
    fts5_content: str = ""

    # 格式化后的因果链上下文（供 ContextAssembler 使用）
    causal_chain_context: str = ""


class HybridRetriever:
    """混合检索路由器，统一 SQL + 向量 + FTS5 三轨检索。

    SQL 路径提供精确的结构化事实（"发生了什么"），
    向量路径提供语义关联的非结构化内容（"什么内容相关"），
    FTS5 路径提供专有名词的精确匹配（"谁/哪里"）。

    ADR 0007: 内部通过 SearchPipeline 实现三通道 RRF 融合，
    保持向后兼容的 RetrievalResult API。
    """

    def __init__(
        self,
        project_root: Path,
        event_store: EventStore | None = None,
        retriever: Retriever | None = None,
        fts5_store: Fts5Store | None = None,
        search_pipeline: SearchPipeline | None = None,
    ) -> None:
        """初始化混合检索路由器。

        Args:
            project_root: 项目根目录路径
            event_store: 事件账本实例（SQL 路径）
            retriever: 语义检索实例（向量路径）
            fts5_store: FTS5 全文索引实例（关键词路径）
            search_pipeline: SearchPipeline 实例（可选，自动创建）
        """
        self.project_root = project_root
        self.event_store = event_store
        self.retriever = retriever or Retriever(project_root)
        self.fts5_store = fts5_store

        # 延迟创建 SearchPipeline（避免循环导入，仅在需要时加载）
        self._search_pipeline = search_pipeline
        self._fts5_available: bool | None = None

    def _ensure_fts5(self) -> bool:
        """确保 FTS5 存储可用（延迟初始化）。"""
        if self._fts5_available is not None:
            return self._fts5_available

        if self.fts5_store is not None:
            self._fts5_available = True
            return True

        try:
            from opennovel.storage.fts5 import Fts5Store

            db_path = self.project_root / ".novel.fts5.db"
            if db_path.exists():
                self.fts5_store = Fts5Store(self.project_root, db_path)
                self._fts5_available = True
                return True
            else:
                self._fts5_available = False
                return False
        except Exception as e:
            logger.debug("FTS5 不可用: %s", e)
            self._fts5_available = False
            return False

    def _get_search_pipeline(self) -> SearchPipeline | None:
        """获取或创建 SearchPipeline 实例。"""
        if self._search_pipeline is not None:
            return self._search_pipeline

        if not self._ensure_fts5():
            return None

        try:
            from opennovel.core.search_pipeline import SearchPipeline

            # 尝试从 Retriever 获取底层 VectorStore 实例，使三通道 RRF 完整
            vector_store = None
            if self.retriever is not None:
                vector_store = getattr(self.retriever, "_canon_store", None)

            self._search_pipeline = SearchPipeline(
                self.project_root,
                vector_store=vector_store,
                fts5_store=self.fts5_store,
                event_store=self.event_store,
            )
        except Exception as e:
            logger.debug("SearchPipeline 创建失败: %s", e)
            return None

        return self._search_pipeline

    def query_narrative_context(
        self,
        query_text: str,
        chapter_id: str = "",
        character_ids: list[str] | None = None,
        top_k_canon: int = 3,
        top_k_subconscious: int = 2,
        pressure_threshold: float = 0.5,
    ) -> RetrievalResult:
        """执行混合检索，返回三轨结果。

        Args:
            query_text: 查询文本（用于向量语义搜索和 FTS5）
            chapter_id: 当前章节 ID（用于 SQL 范围过滤）
            character_ids: 关注的角色 ID 列表
            top_k_canon: 设定检索返回条数
            top_k_subconscious: 潜意识检索返回条数
            pressure_threshold: 高压力事件阈值

        Returns:
            RetrievalResult 三轨检索结果
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

        # ── FTS5 关键词检索（优先）──
        fts5_content_parts: list[str] = []
        if self._ensure_fts5() and self.fts5_store is not None:
            try:
                fts5_results = self.fts5_store.search(query_text, top_k=5)
                for r in fts5_results:
                    fts5_content_parts.append(
                        f"[{r['source']}/{r['doc_stem']}] {r['text'][:300]}"
                    )
                if fts5_content_parts:
                    result.fts5_content = "\n---\n".join(fts5_content_parts)
            except Exception as e:
                logger.debug("FTS5 检索失败: %s", e)

        # ── SearchPipeline 统一检索（合并向量 + FTS5）──
        pipeline = self._get_search_pipeline()
        if pipeline is not None:
            try:
                response = pipeline.search(
                    query_text,
                    top_k=max(top_k_canon, top_k_subconscious) + 2,
                    use_reranker=False,  # Agent 自治路径不跑重排序
                )
                # 按来源分类合并到 canon/subconscious 字段
                canon_parts: list[str] = []
                sub_parts: list[str] = []
                for r in response.results:
                    if r.source == ChunkSource.CANON and len(canon_parts) < top_k_canon:
                        canon_parts.append(r.text[:500])
                    elif r.source == ChunkSource.SUBCONSCIOUS and len(sub_parts) < top_k_subconscious:
                        sub_parts.append(r.text[:500])

                if canon_parts:
                    result.canon_content = "\n---\n".join(canon_parts)
                if sub_parts:
                    result.subconscious_content = "\n---\n".join(sub_parts)

            except Exception as e:
                logger.debug("SearchPipeline 检索失败，回退到传统双通道: %s", e)

        # ── 传统向量语义检索（补充回退：填满未被 SearchPipeline 覆盖的字段）──
        try:
            if not result.canon_content:
                result.canon_content = self.retriever.query_canon(
                    query_text[:500], top_k=top_k_canon
                )
            if not result.subconscious_content:
                result.subconscious_content = self.retriever.query_subconscious(
                    query_text[:500], top_k=top_k_subconscious
                )
        except Exception as e:
            logger.warning("向量检索失败: %s", e)

        # 合并 FTS5 内容到 canon_content（向前兼容）
        if result.fts5_content and not result.canon_content:
            result.canon_content = result.fts5_content

        return result

    def _build_causal_chain_context(
        self, events: list[EventLog], limit: int = 10
    ) -> str:
        """将事件列表格式化为因果链上下文文本。"""
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

    def query_for_writer(self, chapter_id: str, outline_hint: str) -> RetrievalResult:
        """为 Writer Agent 定制的检索策略。

        侧重设定和因果链，确保创作一致性。
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

        侧重因果一致性校验，注入更多事件链上下文。
        """
        return self.query_narrative_context(
            query_text=chapter_text[:1000],
            chapter_id=chapter_id,
            top_k_canon=3,
            top_k_subconscious=1,
            pressure_threshold=0.3,
        )
