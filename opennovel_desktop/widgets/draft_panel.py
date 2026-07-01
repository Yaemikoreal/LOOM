"""DraftPanel — 分栏草稿对比面板。

编辑器交互「分栏对比」模式：左侧原文 | 右侧 Agent 版本。
[接受草稿] 替换原文 / [拒绝] 丢弃草稿。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
)


class DraftPanel(QFrame):
    """并排草稿对比面板。

    ┌─────────────────────────────────────┐
    │ 分栏对比                  [✕] 关闭  │
    ├──────────────────┬──────────────────┤
    │  原文             │  Agent 版本      │
    │  QTextBrowser    │  QTextBrowser    │
    ├──────────────────┴──────────────────┤
    │  [接受草稿]          [拒绝]          │
    └─────────────────────────────────────┘
    """

    draft_accepted = Signal(str)  # accepted text
    draft_rejected = Signal()
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DraftPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 标题栏 ──────────────────────────────────────────
        title_bar = QFrame()
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)

        title = QLabel("<b>分栏对比</b>")
        title.setStyleSheet("font-size: 14px;")
        title_layout.addWidget(title)
        title_layout.addStretch()

        close_btn = QPushButton("✕ 关闭")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setFixedWidth(60)
        close_btn.clicked.connect(self.close_requested.emit)
        close_btn.clicked.connect(self.hide)
        title_layout.addWidget(close_btn)

        layout.addWidget(title_bar)

        # ── 并排视图 ────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        # 左侧：原文
        self._original_view = QTextBrowser()
        self._original_view.setReadOnly(True)
        self._original_view.setStyleSheet(
            "font-size: 13px; line-height: 1.6; border: 1px solid palette(mid);"
            "border-radius: 4px; padding: 8px;"
        )
        splitter.addWidget(self._original_view)

        # 右侧：Agent 版本
        self._draft_view = QTextBrowser()
        self._draft_view.setReadOnly(True)
        self._draft_view.setStyleSheet(
            "font-size: 13px; line-height: 1.6; border: 1px solid palette(mid);"
            "border-radius: 4px; padding: 8px;"
        )
        splitter.addWidget(self._draft_view)
        splitter.setSizes([300, 300])

        layout.addWidget(splitter, 1)

        # ── Footer 按钮 ─────────────────────────────────────
        btn_bar = QFrame()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        self._btn_accept = QPushButton("✓ 接受草稿")
        self._btn_accept.clicked.connect(self._on_accept)
        btn_layout.addWidget(self._btn_accept)

        self._btn_reject = QPushButton("拒绝")
        self._btn_reject.setObjectName("SecondaryButton")
        self._btn_reject.clicked.connect(self._on_reject)
        btn_layout.addWidget(self._btn_reject)

        btn_layout.addStretch()
        layout.addWidget(btn_bar)

        self._draft_text: str = ""

    # ── 公开接口 ──────────────────────────────────────────

    def show_draft(self, original_text: str, draft_text: str) -> None:
        """填充两侧并显示。"""
        self._original_view.setPlainText(original_text)
        self._draft_view.setPlainText(draft_text)
        self._draft_text = draft_text
        self.show()

    def clear_draft(self) -> None:
        """清空并隐藏。"""
        self._original_view.clear()
        self._draft_view.clear()
        self._draft_text = ""
        self.hide()

    # ── 内部 ──────────────────────────────────────────────

    def _on_accept(self) -> None:
        if self._draft_text:
            self.draft_accepted.emit(self._draft_text)
        self.clear_draft()

    def _on_reject(self) -> None:
        self.draft_rejected.emit()
        self.clear_draft()
