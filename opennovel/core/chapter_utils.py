"""章节工具函数 — 章节类型检测与分析。

提供与章节分类相关的纯函数工具：
- ChapterType: 章节类型枚举
- detect_chapter_type(): 根据大纲提示检测章节类型

设计原则：纯函数，不依赖 Agent、Runner 或存储层。
所有函数输入 → 输出可预测，便于测试和复用。
"""

from enum import Enum


class ChapterType(str, Enum):
    """章节类型枚举，用于调度器路由决策。

    - CLIMAX: 高潮/转折/决战，强制运行 Director
    - TRANSITION: 过渡/日常/平静，跳过 Director
    - ROUTINE: 普通推进，每 N 章运行一次 Director
    """

    CLIMAX = "climax"
    TRANSITION = "transition"
    ROUTINE = "routine"


def detect_chapter_type(chapter_hint: str) -> ChapterType:
    """根据大纲提示检测章节类型。

    高潮关键词触发 CLIMAX 类型，过渡关键词触发 TRANSITION 类型，
    否则为 ROUTINE 类型。

    Args:
        chapter_hint: 大纲中本章的描述文本

    Returns:
        章节类型枚举
    """
    hint_lower = chapter_hint.lower()

    climax_keywords = ["转折", "高潮", "climax", "决战", "大结局", "finale", "对决"]
    if any(kw in hint_lower for kw in climax_keywords):
        return ChapterType.CLIMAX

    transition_keywords = ["过渡", "日常", "平静", "transition", "日常篇", "休整"]
    if any(kw in hint_lower for kw in transition_keywords):
        return ChapterType.TRANSITION

    return ChapterType.ROUTINE
