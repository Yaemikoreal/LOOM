"""搜索管道数据模型 — 分块、检索、重排序的统一协议定义。

定义了三通道混合检索（向量/FTS5/EventStore）中使用的所有数据模型：
- ChunkSource: 分块的权威层级来源
- Chunk: 分块数据单元
- RerankedChunk: 经 Cross-Encoder 重排序后的分块
- RetrievalResult: 搜索管道的最终输出

详见 ADR 0007 — 混合语义-关键词检索 + 重排序架构。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChunkSource(str, Enum):
    """分块的权威层级，决定在 ContextAssembler 中的注入优先级。

    遵循权威分级铁律：
        CANON > STATE_MEMORY > SUBCONSCIOUS
    """

    CANON = "canon"
    """世界观设定文档，最高权威。"""

    CHARACTER = "character"
    """角色当前状态（Frontmatter），属于 STATE_MEMORY 层。"""

    EVENT = "event"
    """事件账本中的历史事件，属于 STATE_MEMORY 层。"""

    SUBCONSCIOUS = "subconscious"
    """灵感潜意识池，最低权威。"""

    DRAFT = "draft"
    """章节正文，用于交叉检索。"""


@dataclass
class Chunk:
    """分块数据单元，是三通道检索的统一数据载体。

    Attributes:
        chunk_id: 全局唯一分块 ID，格式 {source}_{doc_stem}_p{index}
        text: 分块文本内容
        source: 权威层级来源
        metadata: 来源元数据，如 chapter_id, character_id, doc_path 等
        score: 通道内原始分数（RRF 计算前保留）
    """

    chunk_id: str
    text: str
    source: ChunkSource
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def __hash__(self) -> int:
        return hash(self.chunk_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Chunk):
            return NotImplemented
        return self.chunk_id == other.chunk_id


@dataclass
class RerankedChunk:
    """经 Cross-Encoder 重排序后的分块。

    包含 RRF 融合分数和 Cross-Encoder 重排分数的对比，用于 Debug 和调优。
    """

    chunk: Chunk
    rrf_score: float = 0.0
    reranker_score: float = 0.0
    rank: int = 0


@dataclass
class RetrievalResult:
    """搜索管道的最终输出结果。

    chunks 为主字段（三通道 RRF 融合 + 可选 Reranker 重排后的结果）。
    原有的旧字段（character_events, causal_chain 等）在过渡期保留为 @property。

    使用方式:
        result = pipeline.search("影渊森林", chapter_id="ch_003")
        for rc in result.chunks:
            print(f"[{rc.rank}] {rc.chunk.text[:50]} (score={rc.reranker_score:.3f})")
    """

    chunks: list[RerankedChunk] = field(default_factory=list)

    @property
    def has_results(self) -> bool:
        """是否有检索结果。"""
        return len(self.chunks) > 0

    @property
    def top_chunks(self) -> list[RerankedChunk]:
        """获取前 3 个结果。"""
        return self.chunks[:3]

    def format_for_context(self, max_chars: int = 2000) -> str:
        """将检索结果格式化为上下文注入文本。

        Args:
            max_chars: 最大字符数

        Returns:
            格式化文本，每条结果前标注来源和分数
        """
        parts: list[str] = []
        total = 0
        for rc in self.chunks:
            label = f"[{rc.chunk.source.value}]"
            snippet = rc.chunk.text.strip()
            entry = f"{label} (score={rc.reranker_score:.3f})\n{snippet}\n"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
        return "\n".join(parts)
