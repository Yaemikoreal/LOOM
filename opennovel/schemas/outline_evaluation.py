"""大纲评审输出模型。

Critic 对 Writer 的大纲方案进行三维评估（情节逻辑/角色一致性/节奏设计），
用于盲目变异流程中的多方案预审与选择。
"""

from pydantic import BaseModel, field_validator


class OutlineDimensionScore(BaseModel):
    """大纲评审的单维度评分。"""

    dimension: str
    score: int
    comment: str

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if not 0 <= v <= 20:
            raise ValueError(f"维度分数必须在 0-20 之间，收到: {v}")
        return v


class OutlineEvaluation(BaseModel):
    """大纲评审结果。

    与 ChapterEvaluation 的区别：
    - 3 维度（情节逻辑/角色一致性/节奏设计）而非 5 维度
    - 文笔质量和情感表达无法从大纲评估
    - 用于多方案预审选择，不用于章节合格判定
    """

    total_score: int
    dimensions: list[OutlineDimensionScore]
    summary: str
    issues: list[str]
    suggestions: list[str]

    @field_validator("total_score")
    @classmethod
    def validate_total_score(cls, v: int) -> int:
        if not 0 <= v <= 60:
            raise ValueError(f"总分必须在 0-60 之间（3 维度 x 20 分），收到: {v}")
        return v

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions_count(
        cls, v: list[OutlineDimensionScore]
    ) -> list[OutlineDimensionScore]:
        if len(v) != 3:
            raise ValueError(f"必须包含 3 个维度评分，收到: {len(v)}")
        return v

    @property
    def is_pass(self) -> bool:
        """是否合格 (>=48 分，即 80%)。"""
        return self.total_score >= 48

    @property
    def is_excellent(self) -> bool:
        """是否优秀 (>=54 分，即 90%)。"""
        return self.total_score >= 54
