"""Writer 思考阶段的结构化输出模型。

Writer 在创作前先进行思考规划，输出 ChapterOutline 作为创作指导。
大纲存入 outlines/ 目录，供后续章节参考和 Critic 评分时对照。
"""

from pydantic import BaseModel, field_validator


class SceneBreakdown(BaseModel):
    """单个场景的分解描述。"""

    scene_id: str
    description: str
    characters_involved: list[str]
    emotional_tone: str
    estimated_words: int

    @field_validator("characters_involved")
    @classmethod
    def validate_character_ids(cls, v: list[str]) -> list[str]:
        for cid in v:
            if not cid.startswith("char_"):
                raise ValueError(f"角色 ID 必须以 'char_' 开头，收到: {cid}")
        return v


class ChapterOutline(BaseModel):
    """Writer 思考阶段输出的章节大纲。"""

    chapter_id: str
    title: str
    summary: str
    scenes: list[SceneBreakdown]
    character_arcs: dict[str, str]
    key_plot_points: list[str]
    narrative_rhythm: str
    target_words: int
    reasoning: str = ""
    """生成本大纲的构思过程与依据。由 Glass-Box Decision 捕获，不参与校验。"""

    @field_validator("chapter_id")
    @classmethod
    def validate_chapter_id(cls, v: str) -> str:
        if not v.startswith("ch_"):
            raise ValueError(f"章节 ID 必须以 'ch_' 开头，收到: {v}")
        return v

    @field_validator("summary")
    @classmethod
    def validate_summary_length(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError(f"章节概要不超过 500 字，当前: {len(v)} 字")
        return v
