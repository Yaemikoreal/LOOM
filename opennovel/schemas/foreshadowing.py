"""伏笔追踪数据模型。

Foreshadowing 记录叙事中已埋设但尚未收束的伏笔和暗线。
由 Director 在全局分析时自动检测并更新，零手工作业。

导演（Director）每 3-5 章分析时，基于已写章节的事件因果链
和叙事上下文，自动识别伏笔的状态变化：
- buried：新引入的可能伏笔
- in_progress：伏笔开始推进/有新发展
- closed：伏笔已收束（因果闭环完成）
"""

from enum import Enum

from pydantic import BaseModel, Field


class ForeshadowType(str, Enum):
    """伏笔类型枚举，定义伏笔的叙事功能分类。"""

    PLOT = "plot"
    """情节伏笔：事件、物品、设定回收后推动主线"""

    CHARACTER = "character"
    """角色伏笔：角色背景、动机、关系变化后续会引爆"""

    THEME = "theme"
    """主题伏笔：符号、隐喻、重复母题暗线贯穿全文"""

    WORLD = "world"
    """世界观伏笔：世界规则、历史秘辛、势力布局延迟揭示"""


class ForeshadowStatus(str, Enum):
    """伏笔状态枚举，标识伏笔在叙事中的当前阶段。"""

    BURIED = "buried"
    """已埋设：伏笔首次被引入叙事，尚未展开"""

    IN_PROGRESS = "in_progress"
    """推进中：伏笔被再次提及或部分揭示，正在展开"""

    CLOSED = "closed"
    """已收束：伏笔完成叙事功能，因果闭环或谜底揭晓"""


class ForeshadowItem(BaseModel):
    """单条伏笔记录。

    记录了伏笔的类型、当前状态、埋设/预计回收位置，
    以及关联的角色。所有字段由 Director 自动填充。
    """

    foreshadow_id: str = Field(description="伏笔唯一标识，如 F001")
    type: ForeshadowType = Field(description="伏笔类型")
    description: str = Field(description="伏笔的自然语言描述")
    buried_chapter: str = Field(description="伏笔埋设的章节 ID，如 ch_001")
    status: ForeshadowStatus = Field(description="当前状态")
    related_character_ids: list[str] = Field(
        default_factory=list,
        description="关联的角色 ID 列表",
    )
    expected_close_chapter: str = Field(
        default="",
        description="预计回收的章节区间，如 ch_008-ch_012",
    )
    notes: str = Field(
        default="",
        description="备注，如进度提示、作者观察等",
    )


class ForeshadowUpdate(BaseModel):
    """Director 输出的伏笔更新指令。

    Director 在分析时对比当前叙事状态与已有伏笔表，
    输出新增伏笔和状态变更。AutoRunner 负责合并到持久化文件。
    """

    foreshadow_id: str = Field(description="伏笔 ID，新增时自增生成，更新时匹配已有")
    type: ForeshadowType | None = Field(default=None, description="伏笔类型（新增必需，更新可选）")
    description: str | None = Field(default=None, description="伏笔描述（新增必需，更新可选）")
    status: ForeshadowStatus | None = Field(default=None, description="新状态（None 表示不变）")
    buried_chapter: str | None = Field(default=None, description="埋设章节（新增必需）")
    related_character_ids: list[str] | None = Field(default=None, description="关联角色")
    expected_close_chapter: str | None = Field(default=None, description="预计回收区间")
    notes: str | None = Field(default=None, description="备注更新")


class ForeshadowState(BaseModel):
    """完整伏笔状态，包含现有伏笔列表。

    作为 Director 的输入上下文（已有伏笔）
    和输出容器（新增/更新后合并的结果）。
    """

    items: list[ForeshadowItem] = Field(
        default_factory=list,
        description="当前所有伏笔记录",
    )
