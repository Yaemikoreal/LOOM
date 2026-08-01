"""时间线 Markdown 生成器。

从 EventStore 的 SQLite 数据直接生成 timeline/events.md，
纯 SQL → Markdown 转换，零 LLM Token 开销。

格式示例：
```markdown
# 时间线

| 章节 | 时间戳 | 事件 | 角色 | 类型 | 因果压强 | 前置事件 |
|------|--------|------|------|------|----------|----------|
| ch_001 | 第1天 08:00 | ... | char_001 | INJURY | 0.85 | - |

## 因果链摘要

### ch_001
- evt_001: ... → evt_002: ...
```
"""

import logging
from pathlib import Path

from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


def generate_timeline(project_root: Path, event_store: EventStore | None = None) -> str:
    """从 EventStore 生成完整时间线 Markdown。

    Args:
        project_root: 项目根目录
        event_store: 可选，EventStore 实例。不提供时自动创建。

    Returns:
        Markdown 格式的时间线文本
    """
    own_store = False
    if event_store is None:
        db_path = project_root / ".novel.db"
        if not db_path.exists():
            return "# 时间线\n\n尚无事件记录。\n"
        event_store = EventStore(db_path)
        own_store = True

    try:
        all_events = event_store.get_all_events()
        if not all_events:
            return "# 时间线\n\n尚无事件记录。\n"

        lines = [
            "# 时间线\n",
            "> 自动生成。从事件账本（.novel.db）实时转换。\n",
        ]

        # 按章节分组
        chapter_events: dict[str, list] = {}
        for e in all_events:
            chapter_events.setdefault(e.chapter_id, []).append(e)

        # 事件统计
        total_events = len(all_events)
        high_pressure = sum(1 for e in all_events if (e.causal_pressure or 0.5) >= 0.7)
        lines.append(f"**统计**: 共 {total_events} 个事件 | 高因果压事件 {high_pressure}\n")

        # 事件总表
        lines.append("## 事件总表\n")
        lines.append(
            "| 章节 | 时间戳 | 事件 | 角色 | 类型 | 因果压强 | 前置事件 |"
        )
        lines.append(
            "|------|--------|------|------|------|----------|----------|"
        )

        for e in all_events:
            desc = e.description[:60] + "..." if len(e.description) > 60 else e.description
            ts = e.timestamp or "-"
            char_id = e.character_id or "-"
            etype = e.event_type or "-"
            pressure = f"{e.causal_pressure:.2f}" if e.causal_pressure else "-"
            caused_by = e.caused_by or "-"
            lines.append(
                f"| {e.chapter_id} "
                f"| {ts} "
                f"| {desc} "
                f"| {char_id} "
                f"| {etype} "
                f"| {pressure} "
                f"| {caused_by} |"
            )

        lines.append("")

        # 按章节的因果链
        lines.append("## 各章节事件\n")
        for ch_id in sorted(chapter_events.keys()):
            events = chapter_events[ch_id]
            lines.append(f"### {ch_id}\n")
            for e in events:
                desc = e.description[:100] if len(e.description) > 100 else e.description
                lines.append(f"- **{e.event_id}**: {desc}")
                if e.caused_by:
                    lines.append(f"  - 前因: {e.caused_by}")
                if e.causal_pressure and e.causal_pressure >= 0.7:
                    lines.append(f"  - 因果压强: {e.causal_pressure:.2f} (高)")
                elif e.causal_pressure:
                    lines.append(f"  - 因果压强: {e.causal_pressure:.2f}")
                lines.append("")

        return "\n".join(lines)
    finally:
        if own_store:
            event_store.close()


def write_timeline(project_root: Path, event_store: EventStore | None = None) -> None:
    """写入时间线到 timeline/events.md。

    Args:
        project_root: 项目根目录
        event_store: 可选，EventStore 实例
    """
    content = generate_timeline(project_root, event_store)
    timeline_dir = project_root / "timeline"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    file_path = timeline_dir / "events.md"

    # 原子写入
    tmp_path = file_path.with_suffix(".md.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(file_path)
        logger.info("时间线已写入: %s", file_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
