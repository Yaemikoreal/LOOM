"""DiffReviewPanel — Diff 审阅面板。

展示 commit 流程中的文件变更和提取事件。
支持 checkbox 勾选确认、冲突三按钮、Rescue Mode 警告条。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class DiffReviewPanel(QWidget):
    """Diff 审阅面板。"""

    events_confirmed = Signal(list)  # 已确认事件列表
    rollback_requested = Signal(str)  # 回滚到指定 snapshot
    rescue_retry = Signal()
    rescue_dirty = Signal()
    rescue_abort = Signal()

    conflict_keep_mine = Signal(str)  # file_path
    conflict_keep_theirs = Signal(str)
    conflict_manual = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._event_checkboxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Commit 审阅")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        # Rescue Mode 警告区（初始隐藏）
        self._rescue_bar = QFrame()
        self._rescue_bar.setStyleSheet(
            "background: #FFF0EE; border: 1px solid #B85C4A; border-radius: 4px;"
        )
        rescue_layout = QHBoxLayout(self._rescue_bar)

        rescue_label = QLabel("⚠️  Auditor 提取失败")
        rescue_label.setStyleSheet("color: #B85C4A; font-weight: 600;")
        rescue_layout.addWidget(rescue_label, 1)

        btn_retry = QPushButton("重试")
        btn_retry.clicked.connect(self.rescue_retry.emit)
        rescue_layout.addWidget(btn_retry)

        btn_dirty = QPushButton("脏提交")
        btn_dirty.setObjectName("SecondaryButton")
        btn_dirty.clicked.connect(self.rescue_dirty.emit)
        rescue_layout.addWidget(btn_dirty)

        btn_abort = QPushButton("取消")
        btn_abort.setObjectName("DangerButton")
        btn_abort.clicked.connect(self.rescue_abort.emit)
        rescue_layout.addWidget(btn_abort)

        self._rescue_bar.setVisible(False)
        layout.addWidget(self._rescue_bar)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # 文件变更区
        files_label = QLabel("文件变更")
        files_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(files_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._content = QVBoxLayout()
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(4)

        scroll_content = QWidget()
        scroll_content.setLayout(self._content)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 占位
        self._placeholder = QLabel("尚未运行 Commit")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #A8A49E; padding: 40px;")
        self._content.addWidget(self._placeholder)

        # 底部确认按钮
        self._btn_confirm = QPushButton("确认提交 (0 事件)")
        self._btn_confirm.setEnabled(False)
        self._btn_confirm.clicked.connect(self._on_confirm)
        layout.addWidget(self._btn_confirm)

    def show_diff(self, diff_data: dict) -> None:
        """展示 Commit Diff。"""
        self._clear_content()

        # 文件变更列表
        files = diff_data.get("files", []) or diff_data.get("file_changes", [])
        for f in files:
            from pathlib import Path

            name = Path(f).name
            label = QLabel(f"  {name}")
            label.setStyleSheet("padding: 6px 8px;")
            self._content.addWidget(label)

        # 事件列表
        events = diff_data.get("events", [])
        if events:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("margin: 8px 0;")
            self._content.addWidget(sep)

            ev_label = QLabel("提取事件")
            ev_label.setStyleSheet("font-weight: 600; margin-top: 8px;")
            self._content.addWidget(ev_label)

            self._event_checkboxes.clear()
            for ev in events:
                ev_type = ev.get("event_type", ev.get("type", "UNKNOWN"))
                char_id = ev.get("character_ids", ev.get("character_id", ""))
                if isinstance(char_id, list):
                    char_id = ", ".join(char_id)
                desc = ev.get("description", ev.get("desc", ""))
                pressure = ev.get("causal_pressure", ev.get("pressure", ""))

                text = f"{ev_type}  {char_id}  {desc}"
                if pressure:
                    text += f"  ({pressure})"

                cb = QCheckBox(text)
                cb.setChecked(True)  # 默认勾选
                cb.setStyleSheet("font-size: 12px; padding: 4px 0;")
                self._event_checkboxes.append(cb)
                self._content.addWidget(cb)

        count = len(events)
        self._btn_confirm.setText(f"确认提交 ({count} 事件)")
        self._btn_confirm.setEnabled(count > 0)

    def show_conflict(self, file_path: str, mine: str, theirs: str) -> None:
        """展示冲突解决选项。"""
        self._clear_content()

        from pathlib import Path

        name = Path(file_path).name

        label = QLabel(f"⚠️ 冲突: {name}")
        label.setStyleSheet("font-weight: 600; color: #B85C4A;")
        self._content.addWidget(label)

        btn_mine = QPushButton("保留我的")
        btn_mine.clicked.connect(lambda: self.conflict_keep_mine.emit(file_path))
        self._content.addWidget(btn_mine)

        btn_theirs = QPushButton("保留 AI 的")
        btn_theirs.setObjectName("SecondaryButton")
        btn_theirs.clicked.connect(lambda: self.conflict_keep_theirs.emit(file_path))
        self._content.addWidget(btn_theirs)

        btn_manual = QPushButton("手动编辑")
        btn_manual.setObjectName("SecondaryButton")
        btn_manual.clicked.connect(lambda: self.conflict_manual.emit(file_path))
        self._content.addWidget(btn_manual)

    def enter_rescue_mode(self) -> None:
        """进入救援模式（展示红色警告条）。"""
        self._rescue_bar.setVisible(True)
        self._btn_confirm.setEnabled(False)

    def exit_rescue_mode(self) -> None:
        """退出救援模式。"""
        self._rescue_bar.setVisible(False)

    def _on_confirm(self) -> None:
        """收集勾选事件并发射确认信号。"""
        selected = []
        for cb in self._event_checkboxes:
            if cb.isChecked():
                selected.append(cb.text())
        self.events_confirmed.emit(selected)

    def _clear_content(self) -> None:
        while self._content.count():
            item = self._content.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
