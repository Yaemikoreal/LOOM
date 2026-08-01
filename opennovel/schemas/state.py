"""状态快照模型 — 角色在某一时间点的完整状态。

State Projector 的产出物：从 EventLog 事件流折叠为角色状态快照。
与 EventLog（"发生了什么事件"）互补——State Snapshot 回答"导致了什么状态"。
"""

from pydantic import BaseModel, Field


class CharacterStateSnapshot(BaseModel):
    """角色在某一时间点的完整状态快照。

    Attributes:
        character_id: 角色 Canonical ID
        physical: 身体状态映射，如 {"左臂": "骨折", "右腿": "健康"}
        emotional: 情绪向量，如 {"grief": 0.8, "anger": 0.3}
        inventory: 当前持有的物品列表
        knowledge: 当前已知的关键信息列表
        location: 当前所在位置（如有 LOCATION_CHANGE 事件）
        relationships: 关系状态，如 {"char_002": "allies"}
        chapter_id: 快照对应的章节位置
        event_count: 用于生成此快照的事件总数
    """

    character_id: str
    physical: dict[str, str] = Field(default_factory=dict)
    emotional: dict[str, float] = Field(default_factory=dict)
    inventory: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    location: str | None = None
    relationships: dict[str, str] = Field(default_factory=dict)
    chapter_id: str = ""
    event_count: int = 0
