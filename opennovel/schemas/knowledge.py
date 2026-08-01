"""知识缺口检测数据模型 — Writer 主动检索的协议定义。

Writer 在创作过程中检测到知识缺口时，通过 KnowledgeNeed 向
AutoRunner 申请额外的上下文信息，AutoRunner 通过 ToolRegistry
查询对应的数据源并将结果注入 Writer 的后续调用上下文。

详见 ADR 0006 — Agent Autonomy（Agent 自治）。
"""

from enum import Enum

from pydantic import BaseModel, Field


class KnowledgeSource(str, Enum):
    """知识来源枚举，定义 ToolRegistry 可查询的数据源类型。"""

    CANON = "canon"
    """世界观设定文档，最高权威。"""

    SUBCONSCIOUS = "subconscious"
    """灵感潜意识池，最低权威。"""

    CHARACTER = "character"
    """角色当前状态（Frontmatter）。"""

    EVENT = "event"
    """事件账本中的历史事件。"""


class KnowledgeNeed(BaseModel):
    """Writer 的知识缺口描述。

    Writer 在思考或创作过程中检测到缺少某些关键信息时，
    输出 KnowledgeNeed 列表，AutoRunner 根据 source 路由到
    对应的数据源查询。
    """

    concept: str = Field(
        description="需要查询的概念或关键词，如 '魔法消耗寿命'、'char_001 当前情绪'"
    )
    source: KnowledgeSource = Field(
        description="知识来源类型，决定查询哪个数据源"
    )
    context: str = Field(
        default="",
        description="为什么需要这个知识，用于提高检索精确度",
    )
    character_id: str = Field(
        default="",
        description="CHARACTER 来源时需要指定的角色 ID",
    )


class KnowledgeResult(BaseModel):
    """知识查询结果，包含原始文本和来源元数据。"""

    content: str = Field(description="查询到的知识内容")
    source: KnowledgeSource = Field(description="来源类型")
    concept: str = Field(description="查询的概念")
    relevance: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="相关性评分 0.0~1.0",
    )
