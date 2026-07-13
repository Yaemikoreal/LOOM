"""JIT（Just-In-Time）按需检索引擎。

不预加载所有资料到上下文。核心设定预置于上下文，
详细历史事件、配角信息、设定细节放入外部知识库。
Agent 遇到知识缺口时通过 ToolRegistry 主动触发查询，即时拉取所需片段。

与 ADR 0006 Agent Autonomy 复用 ToolCallParser 协议。
与 ADR 0007 SearchPipeline 共享底层检索通道。

详见 docs/adr/0008-dynamic-context-engineering.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult
from opennovel.schemas.search import ChunkSource

logger = logging.getLogger(__name__)

# 同次创作会话内的检索结果缓存
_MAX_CACHE_SIZE = 100


class JITRetriever:
    """JIT 按需检索引擎。

    接收 KnowledgeNeed 列表，路由到 SearchPipeline 精确查询，
    内置会话级缓存（同次创作会话内复用结果）。

    使用方式:
        jit = JITRetriever(search_pipeline, tool_registry)
        results = jit.retrieve(knowledge_needs)
    """

    def __init__(
        self,
        search_pipeline=None,
        tool_registry=None,
        project_root: Path | None = None,
    ) -> None:
        """初始化 JIT 检索器。

        Args:
            search_pipeline: SearchPipeline 实例
            tool_registry: ToolRegistry 实例（回退路径）
            project_root: 项目根目录
        """
        self._pipeline = search_pipeline
        self._tool_registry = tool_registry
        self.project_root = project_root
        self._cache: dict[str, list[KnowledgeResult]] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def retrieve(
        self,
        needs: list[KnowledgeNeed],
        top_k: int = 3,
        use_reranker: bool = False,
    ) -> list[KnowledgeResult]:
        """按需检索知识，填充知识缺口。

        查询策略：
        - CANON / SUBCONSCIOUS / CHARACTER → SearchPipeline（快速通道）
        - EVENT → ToolRegistry（结构化查询）
        - 结果缓存于会话内，相同查询直接复用

        Args:
            needs: 知识需求列表
            top_k: 每个查询的返回结果数
            use_reranker: 是否启用 Cross-Encoder 重排

        Returns:
            KnowledgeResult 列表
        """
        results: list[KnowledgeResult] = []

        for need in needs:
            cache_key = f"{need.source.value}:{need.concept}:{need.context[:100]}"

            # 检查缓存
            if cache_key in self._cache:
                self._cache_hits += 1
                results.extend(self._cache[cache_key])
                continue

            self._cache_misses += 1

            # 执行检索
            query = need.concept
            if need.context:
                query = f"{need.concept} {need.context}"

            retrieved = self._retrieve_single(need, query, top_k, use_reranker)
            self._cache[cache_key] = retrieved
            results.extend(retrieved)

            # 缓存上限保护
            if len(self._cache) > _MAX_CACHE_SIZE:
                # 删除最早的条目
                first_key = next(iter(self._cache))
                del self._cache[first_key]

        return results

    def _retrieve_single(
        self,
        need: KnowledgeNeed,
        query: str,
        top_k: int,
        use_reranker: bool,
    ) -> list[KnowledgeResult]:
        """执行单个知识需求的检索。

        Args:
            need: 知识需求
            query: 搜索查询文本
            top_k: 结果数
            use_reranker: 是否重排

        Returns:
            KnowledgeResult 列表
        """
        results: list[KnowledgeResult] = []

        # 优先使用 SearchPipeline
        if self._pipeline is not None:
            try:
                source_filter = _source_to_chunk_source(need.source.value)
                response = self._pipeline.search(
                    query,
                    top_k=top_k,
                    use_reranker=use_reranker,
                    source_filter=source_filter,
                )
                for r in response.results:
                    results.append(
                        KnowledgeResult(
                            content=r.text[:800],
                            source=need.source,
                            concept=need.concept,
                            relevance=r.rerank_score or r.score or 0.8,
                        )
                    )
                if results:
                    return results
            except Exception as e:
                logger.debug("SearchPipeline 检索失败 (%s): %s", need.concept, e)

        # 回退到 ToolRegistry
        if self._tool_registry is not None:
            try:
                return self._tool_registry.query([need])
            except Exception as e:
                logger.debug("ToolRegistry 检索失败 (%s): %s", need.concept, e)

        return results

    def clear_cache(self) -> None:
        """清空会话缓存（每章开始时调用）。"""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def cache_stats(self) -> dict[str, int]:
        """缓存统计信息。"""
        return {
            "size": len(self._cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": (
                self._cache_hits / max(self._cache_hits + self._cache_misses, 1)
            ),
        }


def _source_to_chunk_source(source_value: str) -> ChunkSource | None:
    """将 KnowledgeSource 字符串映射到 ChunkSource 枚举。

    Args:
        source_value: 知识来源字符串

    Returns:
        ChunkSource 枚举值或 None
    """
    mapping = {
        "canon": ChunkSource.CANON,
        "subconscious": ChunkSource.SUBCONSCIOUS,
        "character": ChunkSource.CHARACTER,
        "event": ChunkSource.DRAFT,  # 事件存储在章节上下文中
    }
    return mapping.get(source_value)
