"""PipelinePanel — 流水线视图面板。

按阶段显示 Agent 创作进度，带颜色编码状态指示器。
点击阶段可展开推理链详情。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# 阶段颜色映射（设计规范 §2.3）
PHASE_COLORS: dict[str, str] = {
    "pending": "#C8C6C2",
    "running": "#5A7C9A",
    "passed": "#4A7C5B",
    "warning": "#C4913A",
    "failed": "#B85C4A",
}

PHASE_LABELS: dict[str, str] = {
    "think": "思考规划",
    "write": "创作正文",
    "evaluate": "评估评分",
    "update": "状态更新",
}


class _PhaseIndicator(QWidget):
    """单个阶段指示器。"""

    def __init__(self, phase_id: str) -> None:
        super().__init__()
        self._phase_id = phase_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(16)
        layout.addWidget(self._dot)

        self._label = QLabel(PHASE_LABELS.get(phase_id, phase_id))
        self._label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._label, 1)

        self._time = QLabel("")
        self._time.setStyleSheet("color: #A8A49E; font-size: 11px;")
        layout.addWidget(self._time)

        self._detail_btn = QPushButton("▼")
        self._detail_btn.setFixedSize(20, 20)
        self._detail_btn.setFlat(True)
        self._detail_btn.setVisible(False)
        layout.addWidget(self._detail_btn)

        self._detail_area = QTextBrowser()
        self._detail_area.setVisible(False)
        self._detail_area.setMaximumHeight(120)
        self._detail_area.setStyleSheet("font-size: 12px; border: none; background: transparent;")
        layout.addWidget(self._detail_area)

        self.set_status("pending")

    def set_status(self, status: str) -> None:
        """更新阶段状态颜色。"""
        color = PHASE_COLORS.get(status, "#C8C6C2")
        self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self._label.setStyleSheet(
            f"font-size: 13px; color: {'#2D2A26' if status == 'running' else '#6B6863'};"
            f"font-weight: {'600' if status == 'running' else '400'};"
        )

    def set_detail(self, text: str) -> None:
        """设置推理链详情。"""
        if text:
            self._detail_btn.setVisible(True)
            self._detail_area.setPlainText(text)
        else:
            self._detail_btn.setVisible(False)

    def toggle_detail(self) -> None:
        """展开/折叠详情。"""
        self._detail_area.setVisible(not self._detail_area.isVisible())
        self._detail_btn.setText("▲" if self._detail_area.isVisible() else "▼")


class PipelinePanel(QWidget):
    """流水线视图面板。"""

    def __init__(self) -> None:
        super().__init__()
        self._phases: dict[str, _PhaseIndicator] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("创作流水线")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._phase_list = QVBoxLayout()
        self._phase_list.setContentsMargins(0, 0, 0, 0)
        self._phase_list.setSpacing(2)

        scroll_content = QWidget()
        scroll_content.setLayout(self._phase_list)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 初始化四个阶段
        for pid in ("think", "write", "evaluate", "update"):
            indicator = _PhaseIndicator(pid)
            self._phases[pid] = indicator
            self._phase_list.addWidget(indicator)

        # 初始状态
        self._status_label = QLabel("就绪  |  等待触发")
        self._status_label.setStyleSheet("color: #A8A49E; font-size: 12px; padding: 4px 0;")
        self._phase_list.addWidget(self._status_label)

    def update_phase(self, phase_id: str, status: str = "running") -> None:
        """更新阶段状态。status: pending/running/passed/warning/failed。"""
        indicator = self._phases.get(phase_id)
        if indicator:
            indicator.set_status(status)
            status_text = {
                "pending": "等待中",
                "running": "运行中",
                "passed": "完成",
                "warning": "需注意",
                "failed": "失败",
            }.get(status, status)
            self._status_label.setText(f"{PHASE_LABELS.get(phase_id, phase_id)}: {status_text}")

    def show_reasoning(self, phase_id: str, reasoning: str) -> None:
        """显示推理链详情。"""
        indicator = self._phases.get(phase_id)
        if indicator:
            indicator.set_detail(reasoning)

    def reset(self) -> None:
        """重置所有阶段为待定。"""
        for indicator in self._phases.values():
            indicator.set_status("pending")
        self._status_label.setText("就绪  |  等待触发")
