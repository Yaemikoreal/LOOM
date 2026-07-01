"""EditorTabWidget — 多标签页编辑器容器。

管理多个 NovelEditor 标签页，每个标签页对应一个打开的文件。
支持双击关闭、标签切换、与 AppState 联动。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QTabWidget

from opennovel_desktop.app_state import AppState
from opennovel_desktop.widgets.novel_editor import NovelEditor


class EditorTabWidget(QTabWidget):
    """多标签页编辑器容器。

    每个标签页承载一个 NovelEditor。管理文件打开/切换/关闭，
    同步更新 AppState.current_file。
    """

    file_opened = Signal(str)  # file_path
    file_closed = Signal(str)  # file_path
    editor_focused = Signal(str)  # file_path（标签切换时触发）

    def __init__(self, parent: QTabWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = AppState.instance()

        self.setTabsClosable(True)
        self.setMovable(True)  # 允许拖拽重排标签顺序（VS Code 风格）
        self.setDocumentMode(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)

        self.tabCloseRequested.connect(self._close_tab)
        self.currentChanged.connect(self._on_tab_switched)

        # 标签页原始标题存储（不含 * 标记）
        self._tab_clean_titles: dict[int, str] = {}

        # 初始欢迎页
        self._show_welcome()

    # ── 公开接口 ──────────────────────────────────────────

    def open_file(self, file_path: str) -> NovelEditor | None:
        """打开文件为新标签页或激活已有标签。

        如果文件已在某标签中打开，直接激活该标签。
        否则创建新标签页，加载文件内容。
        """
        # 检查是否已在某标签中打开
        for i in range(self.count()):
            tab_data = self._tab_data(i)
            if tab_data == file_path:
                self.setCurrentIndex(i)
                return self._editor_at(i)

        # 创建新标签
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        editor = NovelEditor()
        editor.setPlainText(content)
        editor.document().setModified(False)

        # 文件名作为标签标题
        title = Path(file_path).name
        self.addTab(editor, title)

        # 文件路径存入 ToolTip（不受索引变化影响）
        idx = self.count() - 1
        self.setTabToolTip(idx, file_path)
        self._tab_clean_titles[idx] = title

        # 监听文档修改状态 → 更新标签页 * 标记
        editor.document().modificationChanged.connect(
            lambda modified, _idx=idx, _title=title: self._on_modification_changed(
                _idx, _title, modified
            )
        )

        self.setCurrentIndex(idx)
        self._app_state.set_current_file(file_path)
        self.file_opened.emit(file_path)

        return editor

    def current_editor(self) -> NovelEditor | None:
        """返回当前活动标签页的编辑器实例。"""
        widget = self.currentWidget()
        if isinstance(widget, NovelEditor):
            return widget
        return None

    def close_current_file(self) -> bool:
        """关闭当前标签页。"""
        idx = self.currentIndex()
        if idx >= 0:
            self._close_tab(idx)
            return True
        return False

    def get_open_files(self) -> list[str]:
        """返回所有打开的文件路径列表。"""
        files: list[str] = []
        for i in range(self.count()):
            path = self.tabToolTip(i)
            if path:
                files.append(path)
        return files

    # ── 内部 ──────────────────────────────────────────────

    def _tab_data(self, index: int) -> str:
        """获取标签关联的文件路径。"""
        return self.tabToolTip(index)

    def _editor_at(self, index: int) -> NovelEditor | None:
        """获取指定索引的编辑器。"""
        widget = self.widget(index)
        if isinstance(widget, NovelEditor):
            return widget
        return None

    def _on_modification_changed(self, index: int, clean_title: str, modified: bool) -> None:
        """文档修改状态变化 → 标签标题加/去 '*' 前缀。"""
        self._tab_clean_titles[index] = clean_title
        self.setTabText(index, f"* {clean_title}" if modified else clean_title)

    def _close_tab(self, index: int) -> None:
        """关闭指定索引的标签页。"""
        file_path = self._tab_data(index)
        self._tab_clean_titles.pop(index, None)
        self.removeTab(index)

        # 关闭后重新索引 _tab_clean_titles
        reindexed: dict[int, str] = {}
        for old_idx, title in self._tab_clean_titles.items():
            new_idx = old_idx if old_idx < index else old_idx - 1
            reindexed[new_idx] = title
        self._tab_clean_titles = reindexed

        if file_path:
            self.file_closed.emit(file_path)

        # 所有标签关闭后显示欢迎页
        if self.count() == 0:
            self._show_welcome()

        # 更新 AppState
        current = self.current_editor()
        if current:
            self._app_state.set_current_file(self._tab_data(self.currentIndex()))
        else:
            self._app_state.set_current_file("")

    def _on_tab_switched(self, index: int) -> None:
        """标签切换时更新 AppState。"""
        if index < 0:
            return
        file_path = self._tab_data(index)
        if file_path:
            self._app_state.set_current_file(file_path)
            self.editor_focused.emit(file_path)

    def _show_welcome(self) -> None:
        """显示欢迎标签页（编辑器区域为空时）。"""
        # 清理已有欢迎页（如果有）
        for i in range(self.count()):
            if self.tabText(i) == "欢迎":
                self.removeTab(i)
                break

        label = QLabel("打开一个文件开始创作")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #A8A49E; font-size: 14px;")
        self.addTab(label, "欢迎")
        self._app_state.set_current_file("")

    def _clear(self) -> None:
        """清空所有标签页（项目关闭时调用）。"""
        self._tab_clean_titles.clear()
        while self.count() > 0:
            self.removeTab(0)
        self._show_welcome()
