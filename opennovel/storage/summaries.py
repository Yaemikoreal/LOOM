"""历史摘要持久化。

每次 novel commit 或 Manager 更新后，将章节摘要写入 summaries/ 目录。
每个章节一个独立的 Markdown 摘要文件，便于快速回顾前情。

Manager 已经生成 chapter_summary，这里只负责格式化并落盘。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def format_summary(
    chapter_id: str,
    chapter_title: str,
    chapter_summary: str,
    total_score: int = 0,
    word_count: int = 0,
    key_events: list[str] | None = None,
    character_changes: list[str] | None = None,
    mismatches: list[str] | None = None,
) -> str:
    """格式化单章摘要为结构化 Markdown。

    Args:
        chapter_id: 章节 ID
        chapter_title: 章节标题
        chapter_summary: Manager 提取的章节摘要
        total_score: Critic 评分
        word_count: 字数
        key_events: 关键事件列表
        character_changes: 角色变更列表
        mismatches: 一致性问题列表

    Returns:
        格式化的 Markdown 摘要
    """
    lines = [
        f"# {chapter_id}: {chapter_title}\n",
    ]

    # 元数据
    meta_parts = []
    if total_score:
        meta_parts.append(f"评分: {total_score}")
    if word_count:
        meta_parts.append(f"字数: {word_count}")
    if meta_parts:
        lines.append(f"> {' | '.join(meta_parts)}\n")

    # 叙事摘要
    lines.append("## 叙事摘要\n")
    lines.append(chapter_summary)
    lines.append("")

    # 关键事件
    if key_events:
        lines.append("## 关键事件\n")
        for evt in key_events:
            lines.append(f"- {evt}")
        lines.append("")

    # 角色变更
    if character_changes:
        lines.append("## 角色变更\n")
        for change in character_changes:
            lines.append(f"- {change}")
        lines.append("")

    # 一致性问题
    if mismatches:
        lines.append("## 一致性问题\n")
        for m in mismatches:
            lines.append(f"- {m}")
        lines.append("")

    return "\n".join(lines)


def write_summary(
    project_root: Path,
    chapter_id: str,
    chapter_title: str,
    chapter_summary: str,
    total_score: int = 0,
    word_count: int = 0,
    key_events: list[str] | None = None,
    character_changes: list[str] | None = None,
    mismatches: list[str] | None = None,
) -> Path | None:
    """写入章节摘要到 summaries/ 目录。

    Args:
        project_root: 项目根目录
        chapter_id: 章节 ID
        chapter_title: 章节标题
        chapter_summary: Manager 提取的章节摘要
        total_score: Critic 评分
        word_count: 字数
        key_events: 关键事件列表
        character_changes: 角色变更列表
        mismatches: 一致性问题列表

    Returns:
        写入的文件路径，写入失败返回 None
    """
    content = format_summary(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        chapter_summary=chapter_summary,
        total_score=total_score,
        word_count=word_count,
        key_events=key_events,
        character_changes=character_changes,
        mismatches=mismatches,
    )

    summaries_dir = project_root / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    file_path = summaries_dir / f"{chapter_id}.md"

    # 原子写入
    tmp_path = file_path.with_suffix(".md.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(file_path)
        logger.info("摘要已写入: %s", file_path)
        return file_path
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        logger.warning("写入摘要失败: %s", file_path)
        return None
