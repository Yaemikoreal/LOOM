"""SearchPanel — 全局搜索面板。

Ctrl+Shift+F 唤出。精确/语义双模式切换，
canon/draft/subconscious 筛选，结果列表可点击跳转或插入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SearchPanel(QWidget):
    """全局搜索面板。"""

    navigate_to = Signal(str, int)  # file_path, line_no
    insert_text = Signal(str)  # text to insert at cursor

    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 标题
        title = QLabel("全局搜索")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        # 搜索框 + 模式切换
        search_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("搜索项目内容…")
        self._input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._input, 1)

        self._mode_btn = QToolButton()
        self._mode_btn.setText("语义")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setToolTip("切换精确/语义搜索模式")
        self._mode_btn.toggled.connect(self._on_mode_toggle)
        search_row.addWidget(self._mode_btn)

        self._search_btn = QPushButton("搜索")
        self._search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self._search_btn)

        layout.addLayout(search_row)

        # 筛选区
        filter_layout = QHBoxLayout()
        self._filter_canon = QCheckBox("设定")
        self._filter_canon.setChecked(True)
        self._filter_draft = QCheckBox("正文")
        self._filter_draft.setChecked(True)
        self._filter_sub = QCheckBox("灵感")
        self._filter_sub.setChecked(True)
        filter_layout.addWidget(self._filter_canon)
        filter_layout.addWidget(self._filter_draft)
        filter_layout.addWidget(self._filter_sub)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # 结果区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._results = QVBoxLayout()
        self._results.setContentsMargins(0, 0, 0, 0)
        self._results.setSpacing(4)

        self._placeholder = QLabel("输入关键词开始搜索")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #A8A49E; padding: 40px;")
        self._results.addWidget(self._placeholder)

        result_widget = QWidget()
        result_widget.setLayout(self._results)
        scroll.setWidget(result_widget)
        layout.addWidget(scroll, 1)

    def _do_search(self) -> None:
        """执行搜索。"""
        query = self._input.text().strip()
        if not query:
            return
        self._clear_results()

        # 收集筛选
        sources = []
        if self._filter_canon.isChecked():
            sources.append("canon")
        if self._filter_draft.isChecked():
            sources.append("draft")
        if self._filter_sub.isChecked():
            sources.append("subconscious")

        is_exact = self._mode_btn.isChecked()

        # 调用 SearchPipeline
        try:
            from opennovel.core.search_pipeline import SearchPipeline  # noqa: PLC0415
            from opennovel_desktop.app_state import AppState  # noqa: PLC0415

            state = AppState.instance()
            if state.current_project:
                pipeline = SearchPipeline(Path(state.current_project))
                results = pipeline.search(
                    query,
                    sources=sources or None,
                    use_reranker=not is_exact,
                )
                self._display_results(results)
            else:
                self._add_result_item("未打开项目", 0, "请先打开一个小说项目")
        except ImportError:
            self._add_result_item("搜索不可用", 0, "opennovel.core.search_pipeline 未安装")
        except Exception as e:
            self._add_result_item("搜索出错", 0, str(e))

    def _display_results(self, results: list) -> None:
        """展示搜索结果。"""
        if not results:
            self._add_result_item("无结果", 0, "未找到匹配内容")
            return

        for r in results:
            title = r.get("title", "") or r.get("chunk_id", "")
            source = r.get("source", "")
            score = r.get("score", 0) or r.get("relevance", 0)
            snippet = r.get("text", "") or r.get("content", "")
            line_no = r.get("line_no", 0) or 0

            self._add_result_item(
                title,
                score,
                f"[{source}] {snippet[:100]}…",
                line_no=line_no,
            )

    def _add_result_item(
        self, title: str, score: float | int, snippet: str, line_no: int = 0
    ) -> None:
        """添加单个结果项。"""
        from PySide6.QtWidgets import QPushButton  # noqa: PLC0415

        if hasattr(self, "_placeholder"):
            self._results.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            delattr(self, "_placeholder")

        frame = QFrame()
        frame.setStyleSheet(
            "border: 1px solid #E4E2DD; border-radius: 4px; padding: 8px; margin: 2px 0;"
        )
        f_layout = QVBoxLayout(frame)
        f_layout.setContentsMargins(8, 6, 8, 6)
        f_layout.setSpacing(2)

        title_row = QHBoxLayout()
        t = QLabel(f"<b>{title}</b>")
        t.setStyleSheet("font-size: 13px;")
        title_row.addWidget(t, 1)

        if score:
            s = QLabel(f"{score:.0%}" if isinstance(score, float) else str(score))
            s.setStyleSheet("color: #A8A49E; font-size: 11px;")
            title_row.addWidget(s)

        insert_btn = QPushButton("➕")
        insert_btn.setFixedSize(24, 24)
        insert_btn.setFlat(True)
        snippet_text = snippet
        insert_btn.clicked.connect(lambda: self.insert_text.emit(snippet_text))
        title_row.addWidget(insert_btn)

        f_layout.addLayout(title_row)

        snippet_label = QLabel(snippet)
        snippet_label.setStyleSheet("color: #6B6863; font-size: 12px;")
        snippet_label.setWordWrap(True)
        f_layout.addWidget(snippet_label)

        # 点击跳转
        frame.mousePressEvent = lambda e, t=title, ln=line_no: self.navigate_to.emit(t, ln)  # type: ignore[method-assign]

        self._results.addWidget(frame)

    def _on_mode_toggle(self, checked: bool) -> None:
        """切换精确/语义模式。"""
        self._mode_btn.setText("精确" if checked else "语义")

    def _clear_results(self) -> None:
        while self._results.count():
            item = self._results.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
