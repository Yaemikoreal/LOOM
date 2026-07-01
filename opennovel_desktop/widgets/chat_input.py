"""SmartInputBar — 对话面板底部输入栏。

包含：模式选择器 + 输入框 + 发送按钮 + 预设命令按钮。
Enter 发送，Shift+Enter 换行。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

MODE_META: dict[str, dict[str, str]] = {
    "replace": {"icon": "↔", "label": "替换选区", "tip": "Agent 结果将替换编辑器中的选中文本"},
    "append": {"icon": "↓", "label": "追加到文件", "tip": "Agent 结果追加到当前文件末尾"},
    "split": {"icon": "⇉", "label": "分栏对比", "tip": "Agent 结果在右侧草稿栏中并排对比"},
}


class SmartInputBar(QFrame):
    """底部输入区域。

    ┌──────────────────────────────────────────┐
    │ 模式: 替换选区                             │
    ├──────────────────────────────────────────┤
    │ ┌──────────────────────┐ [发送]          │
    │ │ 输入指令...          │                 │
    │ └──────────────────────┘                 │
    │ [写章节]  [全自动]  [存入灵感]             │
    └──────────────────────────────────────────┘
    """

    execute_requested = Signal(str, dict)  # action_type, params
    mode_changed = Signal(str)  # "replace" | "append" | "split"

    def __init__(self) -> None:
        super().__init__()
        self._mode: str = "replace"
        self.setObjectName("SmartInputBar")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(4)

        # ── 模式指示器 ──────────────────────────────────────
        self._mode_indicator = QLabel()
        self._mode_indicator.setStyleSheet("font-size: 11px; color: #6B6863; padding: 0 4px;")
        self._mode_indicator.hide()
        outer.addWidget(self._mode_indicator)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        mode_row.addStretch()

        self._mode_btns: dict[str, QPushButton] = {}
        for mode_key in ("replace", "append", "split"):
            meta = MODE_META[mode_key]
            btn = QPushButton(f"{meta['icon']} {meta['label']}")
            btn.setFixedSize(90, 22)
            btn.setObjectName("SecondaryButton")
            btn.setStyleSheet("font-size: 10px; padding: 1px 4px; min-height: 20px;")
            btn.setToolTip(meta["tip"])
            btn.clicked.connect(
                lambda _checked=False, m=mode_key: self._set_mode(m)  # type: ignore[misc]
            )
            self._mode_btns[mode_key] = btn
            mode_row.addWidget(btn)

        outer.addLayout(mode_row)

        # ── 输入行 ──────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("输入指令，与 Agent 对话...（Enter 发送，Shift+Enter 换行）")
        self._input.setFixedHeight(60)
        self._input.setStyleSheet(
            "font-size: 13px; border: 1px solid palette(mid);border-radius: 4px; padding: 6px;"
        )
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedWidth(64)
        self._send_btn.clicked.connect(self._send_message)
        input_row.addWidget(self._send_btn)

        outer.addLayout(input_row)

        # ── 预设按钮行 ──────────────────────────────────────
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)

        self._btn_write = self._make_preset("写章节")
        self._btn_auto = self._make_preset("全自动")
        self._btn_stash = self._make_preset("存入灵感")
        preset_row.addWidget(self._btn_write)
        preset_row.addWidget(self._btn_auto)
        preset_row.addWidget(self._btn_stash)
        preset_row.addStretch()

        outer.addLayout(preset_row)

    # ── 预设按钮 ──────────────────────────────────────────

    @staticmethod
    def _make_preset(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("SecondaryButton")
        btn.setFixedHeight(24)
        btn.setStyleSheet("font-size: 11px; padding: 2px 10px; min-height: 20px;")
        return btn

    def _send_preset(self, command: str) -> None:
        """填充输入框并聚焦。"""
        self._input.setPlainText(command)
        self._input.setFocus()
        # 光标移到末尾
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)

    def inject_preset(self, text: str) -> None:
        """公开方法：外部（工具栏）注入预设文本。"""
        self._input.setPlainText(text)
        self._input.setFocus()

    # ── 模式管理 ──────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        """切换编辑器交互模式。"""
        if mode == self._mode:
            return
        self._mode = mode
        for mk, btn in self._mode_btns.items():
            if mk == mode:
                btn.setStyleSheet(
                    "font-size: 10px; padding: 1px 4px; min-height: 20px;"
                    "background: #B85C4A; color: white; border: none;"
                )
            else:
                btn.setStyleSheet(
                    "font-size: 10px; padding: 1px 4px; min-height: 20px;"
                    "background: transparent; border: 1px solid palette(mid);"
                )
        self.mode_changed.emit(mode)

    def get_mode(self) -> str:
        return self._mode

    def set_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
        for btn in self._mode_btns.values():
            btn.setEnabled(enabled)
        self._btn_write.setEnabled(enabled)
        self._btn_auto.setEnabled(enabled)
        self._btn_stash.setEnabled(enabled)
        if enabled:
            self._input.setFocus()

    # ── 发送 ──────────────────────────────────────────────

    def _send_message(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self.execute_requested.emit("chat", {"text": text, "mode": self._mode})

    # ── 键盘事件 ──────────────────────────────────────────

    def eventFilter(self, obj: object, event: object) -> bool:  # noqa: N802
        """Enter 发送，Shift+Enter 换行。"""
        from PySide6.QtCore import QEvent  # noqa: PLC0415

        is_enter = (
            obj is self._input
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Return
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        )
        if is_enter:
            self._send_message()
            return True
        return super().eventFilter(obj, event)
