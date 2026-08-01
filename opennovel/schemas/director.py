"""Director Agent 输出模型。

Director 分析已完成章节的全局叙事状态，
输出策略指导用于调整后续章节的创作方向，
并可提议大纲调整和伏笔检测结果。
"""

from enum import Enum

from pydantic import BaseModel, Field

from opennovel.schemas.foreshadowing import ForeshadowItem


class SchedulingAction(str, Enum):
    """调度动作枚举，定义 Director 可提议的大纲修改类型。"""

    INSERT = "insert"
    """在指定位置前插入一个补充章节。"""

    SKIP = "skip"
    """跳过计划中的某个章节（如发现其内容已被前文覆盖）。"""

    MERGE = "merge"
    """将两个章节合并为一章（如内容密度不足）。"""


class SchedulingProposal(BaseModel):
    """Director 的章节调度提议。

    用于动态调整剩余章节的大纲结构，需用户确认后执行。
    """

    action: SchedulingAction = Field(description="调度动作")
    target_chapter_id: str = Field(
        description="目标章节 ID。INSERT 表示在此章节前插入，SKIP/MERGE 表示操作此章节"
    )
    rationale: str = Field(description="提议理由，如「张力曲线连续三章平坦，需要插入一个高潮章节」")
    new_chapter_hint: str = Field(
        default="",
        description="INSERT 动作的补充章节大纲提示",
    )
    merge_with: str = Field(
        default="",
        description="MERGE 动作的合并目标章节 ID",
    )


class DirectorAnalysis(BaseModel):
    """Director Agent 的分析结果。

    用于注入下一章的创作策略，并可提议大纲结构调整和伏笔追踪。
    """

    pacing_assessment: str
    """节奏评估：如 "过快" / "过慢" / "适中" / "先慢后快"。"""

    tension_curve: str
    """张力曲线描述：如 "持续上升" / "起伏" / "下降" / "堆积过高"。"""

    character_arc_status: dict[str, str]
    """每个角色的弧线状态：如 {"char_001": "成长中，尚未达到转折点"}。"""

    strategic_guidance: str
    """对下一章的策略指导（将注入 chapter_hint）。"""

    creative_direction_adjustment: str = ""
    """创作方向调整（将合并到 Writer 的 creative_direction）。可选。"""

    warnings: list[str] = []
    """警告列表：如 ["连续3章评分下降", "因果压力堆积过高"]。"""

    scheduling_proposals: list[SchedulingProposal] = []
    """章节调度提议列表。Director 在发现节奏/张力/内容密度问题时，
    可提议插入补充章节、跳过无必要的章节或合并内容稀疏的章节。
    AutoRunner 将按顺序尝试执行这些提议（需用户确认）。"""

    foreshadowing_items: list[ForeshadowItem] = []
    """伏笔检测结果。Director 基于因果链和叙事上下文，
    自动识别新伏笔并更新已有伏笔的状态。
    AutoRunner 将合并到 foreshadowing/foreshadowing.md。"""
