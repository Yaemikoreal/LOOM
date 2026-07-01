"""OutlinePanel — 大纲树面板。

解析 outlines/story.md 的 Markdown 标题层级，生成只读 QTreeWidget。
单击跳转编辑器到对应位置，双击打开大纲源文件。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.app_state import AppState


class OutlinePanel(QWidget):
    """大纲树面板。只读标题层级树，单击跳转，双击打开源文件。"""

    navigate_to_line = Signal(str, int)  # file_path, heading_line
    open_source_file = Signal(str)  # file_path

    def __init__(self, app_state: AppState | None = None) -> None:
        super().__init__()
        self._app_state = app_state or AppState.instance()
        self._project_root: str = ""

        self._setup_ui()
        self._app_state.project_changed.connect(self._on_project_changed)

    def _setup_ui(self) -> None:
        """构建大纲树。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setIndentation(20)
        self._tree.setExpandsOnDoubleClick(True)

        # 单击选中，双击展开/折叠并发射信号
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        layout.addWidget(self._tree)

    def _on_project_changed(self, project_root: str) -> None:
        """项目切换时重新加载大纲。"""
        self._project_root = project_root
        self._load_outline()

    def _load_outline(self) -> None:
        """从 outlines/story.md 解析标题层级。"""
        self._tree.clear()

        if not self._project_root:
            return

        outline_file = Path(self._project_root) / "outlines" / "story.md"
        if not outline_file.exists():
            # 尝试其他可能的大纲文件
            outlines_dir = Path(self._project_root) / "outlines"
            if outlines_dir.exists():
                md_files = list(outlines_dir.glob("*.md"))
                if md_files:
                    outline_file = md_files[0]

        if not outline_file.exists():
            label = QLabel("无大纲文件\n在 outlines/ 目录创建 story.md")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #A8A49E; padding: 20px;")
            # 已经有 tree widget，加占位信息到 tree
            item = QTreeWidgetItem(["（无大纲）"])
            self._tree.addTopLevelItem(item)
            return

        try:
            content = outline_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        self._parse_headings(content, str(outline_file))

    def _parse_headings(self, content: str, file_path: str) -> None:
        """从 Markdown 内容解析标题层级并构建树。

        解析 # → 顶层，## → 子级，### → 孙级，以此类推。
        """
        lines = content.split("\n")
        # 栈结构：每个元素是 (level, QTreeWidgetItem)
        stack: list[tuple[int, QTreeWidgetItem]] = []
        root = self._tree.invisibleRootItem()

        for line_no, line in enumerate(lines, start=1):
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if not match:
                continue

            level = len(match.group(1))
            title = match.group(2).strip()
            heading_text = f"{'  ' * (level - 1)}# {title}"

            item = QTreeWidgetItem()
            item.setText(0, heading_text)
            item.setData(0, Qt.ItemDataRole.UserRole, (file_path, line_no))
            # 不同级别不同字体粗细
            font = item.font(0)
            if level <= 2:
                font.setBold(True)
            item.setFont(0, font)

            # 找到正确的父级
            parent_item = root
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                parent_item = stack[-1][1]

            if parent_item is root:
                self._tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)

            stack.append((level, item))

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """单击 → 导航到对应位置。"""
        _ = column
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            file_path, line_no = data
            self.navigate_to_line.emit(file_path, line_no)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """双击 → 打开源文件。"""
        _ = column
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            file_path, _ = data
            self.open_source_file.emit(file_path)
