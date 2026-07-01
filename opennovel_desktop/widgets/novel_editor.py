"""NovelEditor — 轻量 Markdown 编辑器组件。

基于 QPlainTextEdit，集成 MarkdownHighlighter。
预留流式输出、差异高亮、右键 Agent 菜单接口。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

from opennovel_desktop.styles.markdown_highlighter import MarkdownHighlighter
from opennovel_desktop.styles.theme_loader import ThemeLoader


class NovelEditor(QPlainTextEdit):
    """轻量 Markdown 编辑器。

    - 衬线字体正文，模拟纸上写作心流
    - 集成 MarkdownHighlighter
    - append_streaming_text() — Agent 流式输出实时追加
    - highlight_diff() — 行级差异高亮
    - 右键 Agent 操作菜单
    """

    # 用户请求 Agent 操作的信号
    agent_action_requested = Signal(str, str)  # action_name, selected_text

    def __init__(self, parent: QTextEdit | None = None) -> None:
        super().__init__(parent)
        self._highlighter = MarkdownHighlighter(self.document())
        self._streaming_marker: int | None = None  # 流式输出起始位置

        self._setup_editor()
        self._connect_signals()

    # ── 初始化 ────────────────────────────────────────────

    def _setup_editor(self) -> None:
        """配置编辑器字体、行高、边距。"""
        colors = ThemeLoader.current_colors()

        # 衬线正文（设计规范 §3.3）
        editor_font = QFont()
        editor_font.setFamilies(
            [
                "Noto Serif CJK SC",
                "Source Han Serif SC",
                "SimSun",
                "Times New Roman",
                "serif",
            ]
        )
        editor_font.setPointSize(16)
        self.setFont(editor_font)

        # 行高 1.8（通过样式表模拟）
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {colors["editor_bg"]};
                color: {colors["editor_fg"]};
                padding: 24px 32px;
                border: none;
                selection-background-color: {colors["selection_bg"]};
                line-height: 1.8;
            }}
        """)

        # 行号边距（非行号部件，仅通过 padding 留白）
        self.setTabStopDistance(40)

        # 自动换行，适合小说阅读
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        # 不显示垂直滚动条（由 QSplitter 撑满时自然跟随）
        # 保留滚动条但保持 Qt 默认

    def _connect_signals(self) -> None:
        """连接光标/修改状态信号。"""
        self.cursorPositionChanged.connect(self._on_cursor_moved)
        self.textChanged.connect(self._on_text_changed)

    # ── 核心接口 ──────────────────────────────────────────

    def append_streaming_text(self, text: str) -> None:
        """追加流式文本到编辑器末尾（Agent 写作输出）。"""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if self._streaming_marker is None:
            # 首次流式追加：记下起始位置供后续使用
            self._streaming_marker = cursor.position()

        cursor.insertText(text)
        # 自动滚动到底部，让用户看到最新内容
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def finalize_streaming(self) -> None:
        """流式输出结束，清除标记。"""
        self._streaming_marker = None

    def get_selection_range(self) -> tuple[int, int] | None:
        """获取当前选中文本的起止位置。

        Returns:
            (start_pos, end_pos) 或 None（无选中文本）。
        """
        cursor = self.textCursor()
        if cursor.hasSelection():
            return (cursor.selectionStart(), cursor.selectionEnd())
        return None

    def replace_selection_range(self, start: int, end: int, text: str) -> None:
        """替换指定范围的文本。

        Args:
            start: 起始位置（char offset）
            end: 结束位置（char offset）
            text: 替换文本
        """
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def highlight_diff(
        self,
        start_line: int,
        end_line: int,
        color: QColor | str | None = None,
    ) -> None:
        """高亮指定行范围（0-indexed）。用于 Critic 反馈定位。

        Args:
            start_line: 起始行号（0-indexed）
            end_line: 结束行号（含）
            color: 高亮色，None 使用主题强调色
        """
        extra_selections = self.extraSelections()

        if color is None:
            colors = ThemeLoader.current_colors()
            color = QColor(colors["diff_add_bg"])
        elif isinstance(color, str):
            color = QColor(color)

        block = self.document().findBlockByNumber(start_line)
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(color)
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)

        # 扩展到结束行
        end_block = self.document().findBlockByNumber(end_line)
        end_pos = end_block.position() + end_block.length()
        cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)

        selection.cursor = cursor
        extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def clear_highlights(self) -> None:
        """清除所有额外高亮。"""
        self.setExtraSelections([])

    # ── 右键菜单 ──────────────────────────────────────────

    def contextMenuEvent(self, event: object) -> None:  # noqa: N802
        """注入 Agent 操作的右键菜单。"""
        from PySide6.QtGui import QContextMenuEvent  # noqa: PLC0415

        if not isinstance(event, QContextMenuEvent):
            return

        menu = self.createStandardContextMenu()

        # 检查是否有选中文本
        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        selected_text = cursor.selectedText() if has_selection else ""

        if has_selection:
            menu.addSeparator()

            # Agent 即时执行组
            polish_action = menu.addAction("润色")
            polish_action.triggered.connect(
                lambda: self.agent_action_requested.emit("polish", selected_text)
            )

            continue_action = menu.addAction("续写")
            continue_action.triggered.connect(
                lambda: self.agent_action_requested.emit("continue", selected_text)
            )

            expand_action = menu.addAction("扩写")
            expand_action.triggered.connect(
                lambda: self.agent_action_requested.emit("expand", selected_text)
            )

            menu.addSeparator()

            # Agent 输入面板（复杂操作）
            rewrite_menu = menu.addMenu("从反派视角重写")
            rewrite_action = rewrite_menu.addAction("发送到 Agent 面板...")
            rewrite_action.triggered.connect(
                lambda: self.agent_action_requested.emit("rewrite", selected_text)
            )

            critic_action = menu.addAction("发送给 Critic 检查")
            critic_action.triggered.connect(
                lambda: self.agent_action_requested.emit("critic_check", selected_text)
            )

            menu.addSeparator()

            # 工具操作
            query_action = menu.addAction("查询相关知识")
            query_action.triggered.connect(
                lambda: self.agent_action_requested.emit("query_knowledge", selected_text)
            )

            stash_action = menu.addAction("存入灵感碎片池")
            stash_action.triggered.connect(
                lambda: self.agent_action_requested.emit("stash", selected_text)
            )

        menu.exec(event.globalPos())

    # ── 内部回调 ──────────────────────────────────────────

    def _on_cursor_moved(self) -> None:
        """光标移动时更新状态栏的行/列指示。"""
        # 预留：后续通过 AppState 信号更新状态栏
        pass

    def _on_text_changed(self) -> None:
        """文本变更时更新字数统计。"""
        # 预留：后续通过 AppState 信号更新状态栏字数
        pass
