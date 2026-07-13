"""搜索管道数据模型 — 分块、FTS5、RRF 融合、重排序。

定义 Chunk 分块模型、ChunkSource 来源枚举，以及搜索管道的结果类型。

详见 docs/adr/0007-hybrid-search-and-reranking-architecture.md。
"""

from enum import Enum

from pydantic import BaseModel, Field


class ChunkSource(str, Enum):
    """分块来源枚举，标记 chunk 的原始文档类型。

    在 ContextAssembler 中据此分配权威层级：
    CANON > STATE_MEMORY (CHARACTER/EVENT) > SUBCONSCIOUS
    """

    CANON = "canon"
    """世界观设定文档，最高权威。"""

    SUBCONSCIOUS = "subconscious"
    """灵感潜意识池，最低权威。"""

    CHARACTER = "character"
    """角色卡片（YAML Frontmatter + Markdown 正文）。"""

    DRAFT = "draft"
    """章节正文。"""


class Chunk(BaseModel):
    """MarkdownChunker 产出的分块单元。

    每个 chunk 拥有全局唯一的 chunk_id，作为三存储（VectorStore / FTS5 / Cross-Encoder）
    之间的关联桥。

    chunk_id 格式：{source}_{doc_stem}_p{chunk_index}
    例：canon_world_rules_p0, draft_ch_001_p3

    确定性生成（不含 hash），支持增量覆盖。
    """

    chunk_id: str = Field(description="全局唯一分块标识")
    source: ChunkSource = Field(description="分块来源类型")
    doc_stem: str = Field(description="源文件名（不含扩展名）")
    chunk_index: int = Field(ge=0, description="文件内分块序号，从 0 开始")
    text: str = Field(description="分块文本内容")
    metadata: dict = Field(default_factory=dict, description="附加元数据（Frontmatter 字段等）")

    model_config = {"frozen": False}


class SearchResult(BaseModel):
    """搜索管道的单条检索结果。"""

    chunk_id: str = Field(description="分块标识")
    text: str = Field(description="分块文本")
    source: ChunkSource = Field(description="来源类型")
    score: float = Field(default=0.0, description="RRF 融合分数")
    rerank_score: float | None = Field(default=None, description="Cross-Encoder 重排序分数（可选）")
    channel: str = Field(default="", description="来源通道：vector / fts5 / event")


class SearchResponse(BaseModel):
    """搜索管道的完整响应。"""

    query: str = Field(description="原始查询文本")
    results: list[SearchResult] = Field(default_factory=list, description="排序后的检索结果")
    total_candidates: int = Field(default=0, description="RRF 融合后的候选总数")
    reranker_used: bool = Field(default=False, description="是否使用了 Cross-Encoder 重排序")
