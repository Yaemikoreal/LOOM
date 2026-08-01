"""混合检索路由器 - SQL 精确召回 + 向量语义搜索。

双轨并行检索架构（ADR 0006）：
- SQL 路径：从 EventStore 精确查询结构化事实（角色事件、因果链、高压力事件）
- 向量路径：从 VectorStore 语义检索非结构化内容（设定、潜意识）

两条路径独立运行，结果由 ContextAssembler 统一合并注入。

使用方式:
    hybrid = HybridRetriever(project_root, event_store)
    context = hybrid.query_narrative_context("char_001 受伤后", chapter_id="ch_003")
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from opennovel.core.retriever import Retriever
from opennovel.schemas.event import EventLog
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """混合检索结果，包含 SQL 和向量两条路径的输出。"""

    # SQL 精确召回
    character_events: list[EventLog] = field(default_factory=list)
    causal_chain: list[EventLog] = field(default_factory=list)
    high_pressure_events: list[EventLog] = field(default_factory=list)

    # 向量语义检索
    canon_content: str = ""
    subconscious_content: str = ""

    # 格式化后的因果链上下文（供 ContextAssembler 使用）
    causal_chain_context: str = ""


class HybridRetriever:
    """混合检索路由器，统一 SQL 精确召回和向量语义搜索。

    SQL 路径提供精确的结构化事实（"发生了什么"），
    向量路径提供语义关联的非结构化内容（"什么内容相关"）。
    """

    def __init__(
        self,
        project_root: Path,
        event_store: EventStore | None = None,
        retriever: Retriever | None = None,
    ) -> None:
        """初始化混合检索路由器。

        Args:
            project_root: 项目根目录路径
            event_store: 事件账本实例（SQL 路径）
            retriever: 语义检索实例（向量路径）
        """
        self.project_root = project_root
        self.event_store = event_store
        self.retriever = retriever or Retriever(project_root)

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

        Args:
            query_text: 查询文本（用于向量语义搜索）
            chapter_id: 当前章节 ID（用于 SQL 范围过滤）
            character_ids: 关注的角色 ID 列表
            top_k_canon: 设定检索返回条数
            top_k_subconscious: 潜意识检索返回条数
            pressure_threshold: 高压力事件阈值

        Returns:
            RetrievalResult 双轨检索结果
        """
        result = RetrievalResult()

        # ── SQL 精确召回 ──
        if self.event_store:
            try:
                # 高压力事件
                result.high_pressure_events = (
                    self.event_store.get_high_pressure_events(pressure_threshold)
                )

                # 角色相关事件
                if character_ids:
                    for char_id in character_ids:
                        events = self.event_store.get_events_by_character(char_id)
                        result.character_events.extend(events[:5])  # 每角色最多 5 条

                # 因果链上下文
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

    def query_for_writer(self, chapter_id: str, outline_hint: str) -> RetrievalResult:
        """为 Writer Agent 定制的检索策略。

        侧重设定和因果链，确保创作一致性。

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

        侧重因果一致性校验，注入更多事件链上下文。

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
