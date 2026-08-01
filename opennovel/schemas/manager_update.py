"""Manager 状态更新输出模型。

Manager 读取章节正文后，提取角色状态变更和事件记录，
通过 StateManager 应用到 YAML Frontmatter 和 SQLite 事件账本。
"""

from typing import Any

from pydantic import BaseModel, field_validator

from opennovel.schemas.event import EventType


class CharacterUpdate(BaseModel):
    """单个角色的状态更新。"""

    character_id: str
    field: str
    value: Any
    reason: str

    @field_validator("character_id")
    @classmethod
    def validate_character_id(cls, v: str) -> str:
        if not v.startswith("char_"):
            raise ValueError(f"角色 ID 必须以 'char_' 开头，收到: {v}")
        return v


class EventRecord(BaseModel):
    """事件记录，支持因果链字段。"""

    event_id: str
    character_id: str
    event_type: EventType
    description: str
    causal_pressure: float
    timestamp: str
    caused_by: str | None = None
    related_event_ids: list[str] | None = None

    @field_validator("character_id")
    @classmethod
    def validate_character_id(cls, v: str) -> str:
        if not v.startswith("char_"):
            raise ValueError(f"角色 ID 必须以 'char_' 开头，收到: {v}")
        return v

    @field_validator("causal_pressure")
    @classmethod
    def validate_causal_pressure(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"因果压强必须在 0.0-1.0 之间，收到: {v}")
        return v


class ManagerUpdateResult(BaseModel):
    """Manager 输出的完整更新结果。"""

    character_updates: list[CharacterUpdate]
    events: list[EventRecord]
    chapter_summary: str
