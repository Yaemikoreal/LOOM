"""伏笔 Markdown 持久化存储。

以 Markdown 表格格式读写 foreshadowing/foreshadowing.md，
包含完整的伏笔列表和状态追踪表格。

格式示例：
```markdown
# 伏笔与暗线追踪

## 伏笔状态表

| ID | 类型 | 描述 | 埋设章节 | 状态 | 关联角色 | 预计回收 | 备注 |
|----|------|------|----------|------|----------|----------|------|
| F001 | plot | ... | ch_001 | 已埋设 | char_001 | ch_008 | ... |

## 微观伏笔详情

### F001 — 埋设于 ch_001
...（描述、状态历史）
```
"""

import logging
from pathlib import Path

from opennovel.schemas.foreshadowing import (
    ForeshadowItem,
    ForeshadowState,
    ForeshadowStatus,
    ForeshadowType,
)

logger = logging.getLogger(__name__)

# 类型标签映射（中/英 → 枚举值）
_TYPE_LABELS: dict[str, ForeshadowType] = {
    "plot": ForeshadowType.PLOT,
    "情节": ForeshadowType.PLOT,
    "character": ForeshadowType.CHARACTER,
    "角色": ForeshadowType.CHARACTER,
    "theme": ForeshadowType.THEME,
    "主题": ForeshadowType.THEME,
    "world": ForeshadowType.WORLD,
    "世界观": ForeshadowType.WORLD,
}

_STATUS_LABELS: dict[str, ForeshadowStatus] = {
    "buried": ForeshadowStatus.BURIED,
    "已埋设": ForeshadowStatus.BURIED,
    "in_progress": ForeshadowStatus.IN_PROGRESS,
    "推进中": ForeshadowStatus.IN_PROGRESS,
    "closed": ForeshadowStatus.CLOSED,
    "已收束": ForeshadowStatus.CLOSED,
}

_REVERSE_TYPE: dict[ForeshadowType, str] = {
    ForeshadowType.PLOT: "plot",
    ForeshadowType.CHARACTER: "character",
    ForeshadowType.THEME: "theme",
    ForeshadowType.WORLD: "world",
}

_REVERSE_STATUS: dict[ForeshadowStatus, str] = {
    ForeshadowStatus.BURIED: "buried",
    ForeshadowStatus.IN_PROGRESS: "in_progress",
    ForeshadowStatus.CLOSED: "closed",
}

# 中文状态标签（用于生成可读的 Markdown）
_CN_TYPE: dict[ForeshadowType, str] = {
    ForeshadowType.PLOT: "情节",
    ForeshadowType.CHARACTER: "角色",
    ForeshadowType.THEME: "主题",
    ForeshadowType.WORLD: "世界观",
}

_CN_STATUS: dict[ForeshadowStatus, str] = {
    ForeshadowStatus.BURIED: "已埋设",
    ForeshadowStatus.IN_PROGRESS: "推进中",
    ForeshadowStatus.CLOSED: "已收束",
}


class ForeshadowStore:
    """伏笔 Markdown 文件读写。

    管理 foreshadowing/foreshadowing.md 的解析、更新和写入。
    支持原子写入防止文件损坏。
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.file_path = project_root / "foreshadowing" / "foreshadowing.md"

    def load(self) -> ForeshadowState:
        """从 foreshadowing.md 加载伏笔状态。

        文件不存在时返回空状态。

        Returns:
            ForeshadowState 包含所有已有伏笔
        """
        if not self.file_path.exists():
            return ForeshadowState(items=[])

        try:
            text = self.file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取伏笔文件失败: %s", e)
            return ForeshadowState(items=[])

        return self._parse_markdown(text)

    def save(self, state: ForeshadowState) -> None:
        """将伏笔状态写入 foreshadowing.md。

        使用原子写入防止文件损坏。

        Args:
            state: 待写入的完整伏笔状态
        """
        content = self._format_markdown(state)
        self._atomic_write(content)

    def merge_updates(
        self,
        current: ForeshadowState,
        items: list[ForeshadowItem],
    ) -> ForeshadowState:
        """合并新增/更新到现有伏笔状态。

        匹配策略：
        - 已有 foreshadow_id → 更新该条目（覆盖非空字段）
        - 新 foreshadow_id → 追加

        Args:
            current: 现有伏笔状态
            items: Director 输出的最新伏笔列表（完整列表，包含已有和新条目）

        Returns:
            合并后的新状态
        """
        existing = {item.foreshadow_id: item for item in current.items}

        for item in items:
            if item.foreshadow_id in existing:
                # 更新已有条目
                existing[item.foreshadow_id] = item
            else:
                # 追加新条目
                existing[item.foreshadow_id] = item

        return ForeshadowState(items=list(existing.values()))

    def _parse_markdown(self, text: str) -> ForeshadowState:
        """从 Markdown 表格文本解析伏笔列表。

        Args:
            text: 完整 Markdown 文本

        Returns:
            ForeshadowState
        """
        items: list[ForeshadowItem] = []
        lines = text.split("\n")
        in_table = False
        headers: list[str] = []

        for line in lines:
            stripped = line.strip()

            # 检测表格开始
            if stripped.startswith("| ID |"):
                in_table = True
                headers = [
                    h.strip().lower()
                    for h in stripped.strip("|").split("|")
                ]
                continue

            # 跳过表格分隔行
            if in_table and stripped.startswith("|---"):
                continue

            # 解析表格行
            if in_table and stripped.startswith("|") and stripped.endswith("|"):
                if stripped.strip("|").strip() == "":
                    in_table = False
                    continue

                cells = [c.strip() for c in stripped.strip("|").split("|")]
                item = self._parse_table_row(headers, cells)
                if item:
                    items.append(item)
                continue

            # 非表格区域
            if not stripped.startswith("|"):
                in_table = False

        return ForeshadowState(items=items)

    def _parse_table_row(
        self,
        headers: list[str],
        cells: list[str],
    ) -> ForeshadowItem | None:
        """从表格行解析单条伏笔。

        Args:
            headers: 表头列表
            cells: 单元格值列表

        Returns:
            ForeshadowItem 或 None（解析失败时）
        """
        try:
            data: dict[str, str] = {}
            for i, header in enumerate(headers):
                if i < len(cells):
                    data[header] = cells[i]

            fid = data.get("id", "")
            if not fid or fid == "-":
                return None

            type_str = data.get("类型", data.get("type", ""))
            fore_type = _TYPE_LABELS.get(type_str.lower(), ForeshadowType.PLOT)

            status_str = data.get("状态", "")
            fore_status = _STATUS_LABELS.get(status_str.lower(), ForeshadowStatus.BURIED)

            return ForeshadowItem(
                foreshadow_id=fid,
                type=fore_type,
                description=data.get("描述", data.get("description", "")),
                buried_chapter=data.get("埋设章节", data.get("buried_chapter", "")),
                status=fore_status,
                related_character_ids=[
                    c.strip() for c in data.get("关联角色", "").replace("，", ",").split(",") if c.strip()
                ],
                expected_close_chapter=data.get("预计回收", data.get("expected_close_chapter", "")),
                notes=data.get("备注", data.get("notes", "")),
            )
        except (ValueError, KeyError, IndexError) as e:
            logger.warning("解析伏笔表格行失败: %s, cells=%s", e, cells)
            return None

    def _format_markdown(self, state: ForeshadowState) -> str:
        """将伏笔状态格式化为 Markdown。

        Args:
            state: 伏笔状态

        Returns:
            Markdown 文本
        """
        lines = [
            "# 伏笔与暗线追踪\n",
            "> 自动生成。由 Director 在全局分析时更新。\n",
            "> 新增伏笔：`novel foreshadow add` | 列表：`novel foreshadow list`\n",
        ]

        # 状态统计
        total = len(state.items)
        buried = sum(1 for i in state.items if i.status == ForeshadowStatus.BURIED)
        in_progress = sum(1 for i in state.items if i.status == ForeshadowStatus.IN_PROGRESS)
        closed = sum(1 for i in state.items if i.status == ForeshadowStatus.CLOSED)
        lines.append(f"\n**统计**: 共 {total} 条 | 已埋设 {buried} | 推进中 {in_progress} | 已收束 {closed}\n")

        # 伏笔状态表
        lines.append("## 伏笔状态表\n")
        lines.append(
            "| ID | 类型 | 描述 | 埋设章节 | 状态 | 关联角色 | 预计回收 | 备注 |"
        )
        lines.append(
            "|----|------|------|----------|------|----------|----------|------|"
        )

        for item in state.items:
            cn_type = _CN_TYPE.get(item.type, item.type.value)
            cn_status = _CN_STATUS.get(item.status, item.status.value)
            chars = ", ".join(item.related_character_ids) if item.related_character_ids else "-"
            close_ch = item.expected_close_chapter or "-"
            notes = item.notes or "-"
            lines.append(
                f"| {item.foreshadow_id} "
                f"| {cn_type} "
                f"| {item.description} "
                f"| {item.buried_chapter} "
                f"| {cn_status} "
                f"| {chars} "
                f"| {close_ch} "
                f"| {notes} |"
            )

        lines.append("")

        # 每条伏笔详情
        lines.append("## 微观伏笔详情\n")
        for item in state.items:
            cn_type = _CN_TYPE.get(item.type, item.type.value)
            cn_status = _CN_STATUS.get(item.status, item.status.value)
            lines.append(f"### {item.foreshadow_id} — 埋设于 {item.buried_chapter}")
            lines.append(f"- **类型**: {cn_type}")
            lines.append(f"- **描述**: {item.description}")
            lines.append(f"- **状态**: {cn_status}")
            if item.related_character_ids:
                lines.append(f"- **关联角色**: {', '.join(item.related_character_ids)}")
            if item.expected_close_chapter:
                lines.append(f"- **预计回收**: {item.expected_close_chapter}")
            if item.notes:
                lines.append(f"- **备注**: {item.notes}")
            lines.append("")

        return "\n".join(lines)

    def _atomic_write(self, content: str) -> None:
        """原子写入伏笔文件。

        写入到临时文件后 rename，防止断电/崩溃导致文件损坏。
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.file_path.with_suffix(".md.tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self.file_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
