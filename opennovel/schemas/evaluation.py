"""Critic 评分输出模型。

Critic 对 Writer 产出的章节进行五维百分制评分。
80 分以上为合格，90 分以上为优秀。
"""

from pydantic import BaseModel, field_validator


class DimensionScore(BaseModel):
    """单个维度的评分。"""

    dimension: str
    score: int
    comment: str

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if not 0 <= v <= 20:
            raise ValueError(f"维度分数必须在 0-20 之间，收到: {v}")
        return v


class AnchoredIssue(BaseModel):
    """带文本定位的评审问题。

    与纯文本 issue 不同，AnchoredIssue 通过 quote 字段锚定到章节原文的具体位置，
    让 Writer 的 revise() 能精确定位修改位置。
    """

    dimension: str
    """所属评分维度，如 "情节逻辑"、"角色一致性"。"""

    severity: str
    """严重程度：critical（硬伤，必须修改）/ major（明显问题）/ minor（建议优化）。"""

    quote: str
    """原文引用（20-50 字），作为定位锚点。"""

    problem: str
    """问题描述。"""

    suggestion: str
    """修改建议。"""

    location_hint: str = ""
    """人类可读的位置提示，如 "第 3 段"、"场景 2 开头"。"""

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"critical", "major", "minor"}
        if v not in allowed:
            raise ValueError(f"severity 必须是 {allowed} 之一，收到: {v}")
        return v

    @field_validator("quote")
    @classmethod
    def validate_quote(cls, v: str) -> str:
        if not v or len(v.strip()) < 5:
            raise ValueError("quote 必须包含至少 5 个字符的原文引用")
        return v.strip()


class ChapterEvaluation(BaseModel):
    """Critic 输出的章节评分结果。"""

    total_score: int
    dimensions: list[DimensionScore]
    summary: str
    issues: list[str]
    suggestions: list[str]
    anchored_issues: list[AnchoredIssue] = []
    critique_reasoning: str = ""
    """评分过程的推理依据。由 Glass-Box Decision 捕获。"""

    @field_validator("total_score")
    @classmethod
    def validate_total_score(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"总分必须在 0-100 之间，收到: {v}")
        return v

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions_count(cls, v: list[DimensionScore]) -> list[DimensionScore]:
        if len(v) != 5:
            raise ValueError(f"必须包含 5 个维度评分，收到: {len(v)}")
        return v

    @property
    def is_pass(self) -> bool:
        """是否合格 (>=80 分)。"""
        return self.total_score >= 80

    @property
    def is_excellent(self) -> bool:
        """是否优秀 (>=90 分)。"""
        return self.total_score >= 90

    @property
    def has_anchored_issues(self) -> bool:
        """是否包含锚定问题。"""
        return len(self.anchored_issues) > 0
