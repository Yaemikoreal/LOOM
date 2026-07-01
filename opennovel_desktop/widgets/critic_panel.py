"""CriticFeedbackPanel — Critic 评分反馈面板。

顶部五维评分条形图，中部 AnchoredIssue 可勾选列表，
底部操作按钮（要求修订/跳过/全部接受）。
点击 issue → 编辑器滚动到对应行。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class _ScoreBar(QWidget):
    """单维度评分条。"""

    def __init__(self, label: str, score: int, max_score: int = 20) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self._label = QLabel(f"{label}")
        self._label.setFixedWidth(60)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, max_score)
        self._bar.setValue(score)
        self._bar.setFixedHeight(14)
        self._bar.setTextVisible(True)
        self._bar.setFormat(f"{score}/{max_score}")
        # 低分变色
        if score / max_score < 0.6:
            self._bar.setProperty("warning", True)
            self._bar.style().unpolish(self._bar)
            self._bar.style().polish(self._bar)
        layout.addWidget(self._bar, 1)


class CriticFeedbackPanel(QWidget):
    """Critic 反馈面板。"""

    revise_requested = Signal()
    issue_selected = Signal(str, int)  # file_path, line_no
    accept_all = Signal()
    skip = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._issue_checkboxes: list[QCheckBox] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 标题
        title = QLabel("Critic 评估")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        # 评分区（滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        self._scores_layout = QVBoxLayout(scroll_content)
        self._scores_layout.setContentsMargins(0, 0, 0, 0)
        self._scores_layout.setSpacing(4)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 初始提示
        self._placeholder = QLabel("尚未收到评估")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #A8A49E; padding: 40px;")
        self._scores_layout.addWidget(self._placeholder)

        # 底部按钮
        btn_layout = QHBoxLayout()
        self._btn_revise = QPushButton("要求修订")
        self._btn_skip = QPushButton("跳过")
        self._btn_skip.setObjectName("SecondaryButton")
        self._btn_accept = QPushButton("全部接受")
        self._btn_accept.setObjectName("SecondaryButton")

        self._btn_revise.clicked.connect(self.revise_requested.emit)
        self._btn_skip.clicked.connect(self.skip.emit)
        self._btn_accept.clicked.connect(self.accept_all.emit)

        btn_layout.addWidget(self._btn_revise)
        btn_layout.addWidget(self._btn_accept)
        btn_layout.addWidget(self._btn_skip)
        layout.addLayout(btn_layout)

        self._disable_buttons()

    def display_evaluation(self, evaluation: dict) -> None:
        """展示完整评估结果。"""
        # 清除占位
        if hasattr(self, "_placeholder"):
            self._scores_layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            delattr(self, "_placeholder")

        # 清除旧评分条
        self._clear_score_bars()

        # 添加评分条（设计规范 §12b）
        dimensions = evaluation.get("dimensions", {})
        if not dimensions and "scores" in evaluation:
            dimensions = evaluation["scores"]

        for dim_name, score in dimensions.items():
            if isinstance(score, dict):
                score_val = score.get("score", 0)
                max_val = score.get("max", 20)
            elif isinstance(score, (int, float)):
                score_val = int(score)
                max_val = 20
            else:
                continue
            bar = _ScoreBar(dim_name, score_val, max_val)
            self._scores_layout.addWidget(bar)

        # 添加 AnchoredIssue 列表
        issues = evaluation.get("issues", []) or evaluation.get("anchored_issues", [])
        if issues:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #E4E2DD;")
            self._scores_layout.addWidget(sep)

            issues_label = QLabel("问题列表")
            issues_label.setStyleSheet("font-weight: 600; margin-top: 8px;")
            self._scores_layout.addWidget(issues_label)

            self._issue_checkboxes.clear()
            for issue in issues:
                quote = issue.get("quote", "") or issue.get("text", "")
                desc = issue.get("problem", "") or issue.get("description", "")
                severity = issue.get("severity", "minor")

                cb = QCheckBox(f"[{severity.upper()}] {quote[:50]}… — {desc[:60]}…")
                cb.setStyleSheet("font-size: 12px; padding: 4px 0;")
                # 存储定位信息
                loc = issue.get("location_hint", "")
                if loc and isinstance(loc, str) and ":" in loc:
                    parts = loc.split(":")
                    if parts[0]:
                        cb.setProperty("file_path", parts[0])
                    if len(parts) > 1 and parts[1].lstrip("L").isdigit():
                        cb.setProperty("line_no", int(parts[1].lstrip("L")))
                cb.stateChanged.connect(self._on_issue_toggled)
                self._issue_checkboxes.append(cb)
                self._scores_layout.addWidget(cb)

        self._enable_buttons()

    def display_empty(self) -> None:
        """重置为空状态。"""
        self._clear_score_bars()
        if not hasattr(self, "_placeholder"):
            self._placeholder = QLabel("尚未收到评估")
            self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._placeholder.setStyleSheet("color: #A8A49E; padding: 40px;")
            self._scores_layout.addWidget(self._placeholder)
        self._disable_buttons()

    def _on_issue_toggled(self, state: int) -> None:
        """issue checkbox 选中时发射定位信号。"""
        if state != Qt.CheckState.Checked.value:
            return
        cb = self.sender()
        if cb is None:
            return
        file_path = cb.property("file_path")
        line_no = cb.property("line_no")
        if file_path and line_no is not None:
            self.issue_selected.emit(file_path, line_no)

    def _clear_score_bars(self) -> None:
        while self._scores_layout.count():
            item = self._scores_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _enable_buttons(self) -> None:
        self._btn_revise.setEnabled(True)
        self._btn_accept.setEnabled(True)
        self._btn_skip.setEnabled(True)

    def _disable_buttons(self) -> None:
        self._btn_revise.setEnabled(False)
        self._btn_accept.setEnabled(False)
        self._btn_skip.setEnabled(False)
