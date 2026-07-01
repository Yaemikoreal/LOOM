"""InlineDiffWidget — 内联 Diff 控件。

红色删除线原文 + 绿色背景修改文，并排展示。
提供 接受/拒绝 按钮。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)


class InlineDiffWidget(QFrame):
    """内联 Diff 展示控件。"""

    accepted = Signal()
    rejected = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("border: 1px solid #E4E2DD; border-radius: 4px; margin: 8px 0;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 标题
        title = QLabel("Agent 修改建议")
        title.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(title)

        # 并排展示
        diff_row = QHBoxLayout()
        diff_row.setSpacing(8)

        # 原文（红色删除线）
        self._original = QTextBrowser()
        self._original.setStyleSheet(
            "background: #FFEBEE; color: #C62828; font-size: 13px;"
            " text-decoration: line-through; border: 1px solid #FFCDD2;"
            " border-radius: 4px; padding: 8px;"
        )
        self._original.setMaximumHeight(120)
        diff_row.addWidget(self._original, 1)

        # 修改文（绿色背景）
        self._modified = QTextBrowser()
        self._modified.setStyleSheet(
            "background: #E8F5E9; color: #2E7D32; font-size: 13px; "
            "border: 1px solid #C8E6C9; border-radius: 4px; padding: 8px;"
        )
        self._modified.setMaximumHeight(120)
        diff_row.addWidget(self._modified, 1)

        layout.addLayout(diff_row)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._accept_btn = QPushButton("接受")
        self._accept_btn.clicked.connect(self.accepted.emit)
        btn_row.addWidget(self._accept_btn)

        self._reject_btn = QPushButton("拒绝")
        self._reject_btn.setObjectName("SecondaryButton")
        self._reject_btn.clicked.connect(self.rejected.emit)
        btn_row.addWidget(self._reject_btn)

        layout.addLayout(btn_row)

    def show_diff(self, original: str, modified: str) -> None:
        """设置对比内容。"""
        self._original.setPlainText(original)
        self._modified.setPlainText(modified)
