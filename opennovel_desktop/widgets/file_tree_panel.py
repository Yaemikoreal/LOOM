"""FileTreePanel — 语义分组文件树导航。

将项目物理目录按语义分组展示：
- 正文 → draft/
- 设定 → canon/ + characters/
- 蓝图 → outlines/ + foreshadowing/ + timeline/
- 灵感 → subconscious/
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from opennovel_desktop.app_state import AppState

# 语义分组定义
SEMANTIC_GROUPS: dict[str, list[str]] = {
    "正文": ["draft"],
    "设定": ["canon", "characters"],
    "蓝图": ["outlines", "foreshadowing", "timeline"],
    "灵感": ["subconscious"],
}


class FileTreePanel(QWidget):
    """语义分组文件树导航面板。"""

    file_selected = Signal(str)  # 选中文件的路径
    file_activated = Signal(str)  # 双击/回车打开文件

    def __init__(self, app_state: AppState | None = None) -> None:
        super().__init__()
        self._app_state = app_state or AppState.instance()
        self._project_root: str = ""

        self._setup_ui()

        # 监听项目切换
        self._app_state.project_changed.connect(self._on_project_changed)

    def _setup_ui(self) -> None:
        """初始化空状态布局。"""
        from PySide6.QtWidgets import QLabel

        self._placeholder = QLabel("未打开项目", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #A8A49E; padding: 40px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._placeholder)

    def _on_project_changed(self, project_root: str) -> None:
        """项目切换时重建文件树。"""
        self._project_root = project_root
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """基于语义分组重建文件树。先清空旧 widget 再重建。"""
        if not self._project_root:
            return
        root = Path(self._project_root)
        if not root.exists():
            return
        layout = self.layout()
        if layout is None:
            return

        self._clear_layout(layout)

        for group_name, dirs in SEMANTIC_GROUPS.items():
            files: list[Path] = []
            for d in dirs:
                dir_path = root / d
                if dir_path.exists():
                    files.extend(sorted(dir_path.glob("*.md")))
            if not files:
                continue

            title = QLabel(f"  {group_name}")
            title.setStyleSheet(
                "font-weight: 600; padding: 4px 0; color: #6B6863; font-size: 12px;"
            )
            layout.addWidget(title)

            for file_path in files:
                btn = QPushButton(file_path.name)
                btn.setFlat(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet("""
                    QPushButton {
                        text-align: left; padding: 4px 12px;
                        border: none; border-radius: 4px; font-size: 13px;
                    }
                    QPushButton:hover { background: palette(midlight); }
                """)
                path_str = str(file_path)
                btn.clicked.connect(lambda checked, p=path_str: self.file_activated.emit(p))
                layout.addWidget(btn)

        layout.addStretch()

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        """递归清空布局中所有 widget。"""
        while layout.count():
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
            elif item and item.layout():
                FileTreePanel._clear_layout(item.layout())

    def refresh(self) -> None:
        """刷新文件树。"""
        self._rebuild_tree()
