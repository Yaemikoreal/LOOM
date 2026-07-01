"""LogPanel — GUI 日志查看面板。

实时显示 logging 输出，支持级别过滤和自动滚动。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.utils.log_manager import LEVEL_TAGS, LogManager


class LogPanel(QWidget):
    """日志查看面板。右侧上下文标签，按需呼出。"""

    def __init__(self) -> None:
        super().__init__()
        self._paused: bool = False
        self._buffer: list[str] = []
        self._flush_timer = QTimer()
        self._flush_timer.setInterval(300)  # 300ms 批量刷新
        self._flush_timer.timeout.connect(self._flush_buffer)

        self._setup_ui()
        self._connect_logger()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 标题 + 工具栏
        top_row = QHBoxLayout()
        title = QLabel("运行日志")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        top_row.addWidget(title)
        top_row.addStretch()

        # 级别过滤
        self._level_combo = QComboBox()
        self._level_combo.addItems(["INFO", "DEBUG", "WARNING", "ERROR"])
        self._level_combo.setCurrentText("INFO")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        top_row.addWidget(self._level_combo)

        # 自动滚动
        self._scroll_cb = QCheckBox("自动滚动")
        self._scroll_cb.setChecked(True)
        top_row.addWidget(self._scroll_cb)

        # 暂停
        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setObjectName("SecondaryButton")
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        self._pause_btn.setFixedWidth(60)
        top_row.addWidget(self._pause_btn)

        # 清除
        clear_btn = QPushButton("清除")
        clear_btn.setObjectName("SecondaryButton")
        clear_btn.clicked.connect(self._clear_log)
        clear_btn.setFixedWidth(60)
        top_row.addWidget(clear_btn)

        layout.addLayout(top_row)

        # 日志显示区
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)  # 防止内存溢出
        self._log_view.setStyleSheet("""
            QPlainTextEdit {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 12px;
                line-height: 1.4;
                padding: 8px;
                background: palette(window);
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
        """)
        layout.addWidget(self._log_view, 1)

        # 底部状态
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color: #A8A49E; font-size: 11px;")
        layout.addWidget(self._status_label)

        self._initial_msg = True

    def _connect_logger(self) -> None:
        """连接 LogManager 的 Qt Signal。"""
        try:
            mgr = LogManager.instance()
            mgr.signal_bridge.log_received.connect(self._on_log_received)
        except (AssertionError, RuntimeError):
            # LogManager 尚未初始化，延迟连接
            QTimer.singleShot(1000, self._connect_logger)

    def _on_log_received(
        self,
        levelno: int,
        levelname: str,
        logger_name: str,
        message: str,
        timestamp: float,  # noqa: ARG002
    ) -> None:
        """收到新日志条目。"""
        if self._paused:
            return

        # 级别过滤
        current_level = self._level_combo.currentText()
        level_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        min_level = level_map.get(current_level, 1)
        msg_level = level_map.get(levelname, 1)
        if msg_level < min_level:
            return

        # 颜色标签
        tag, color_hex = LEVEL_TAGS.get(levelno, ("", "#A8A49E"))
        colored_line = (
            f'<span style="color:{color_hex};font-weight:600;">[{tag}]</span> '
            f'<span style="color:#6B6863;">{message}</span>'
        )

        self._buffer.append(colored_line)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_buffer(self) -> None:
        """批量刷新日志到界面。"""
        if not self._buffer:
            self._flush_timer.stop()
            return

        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        for line in self._buffer:
            cursor.insertHtml(line + "<br>")

        self._buffer.clear()
        self._flush_timer.stop()

        # 自动滚动
        if self._scroll_cb.isChecked():
            self._log_view.ensureCursorVisible()

        # 更新统计
        block_count = self._log_view.blockCount()
        self._status_label.setText(f"{block_count} 条日志")

        if self._initial_msg:
            self._initial_msg = False
            self._log_view.setPlainText("")

    def _on_level_changed(self, level: str) -> None:
        """日志级别过滤变更。"""
        self._status_label.setText(f"过滤: {level}")

    def _on_pause_toggled(self, paused: bool) -> None:
        """暂停/恢复。"""
        self._paused = paused
        self._pause_btn.setText("继续" if paused else "暂停")

    def _clear_log(self) -> None:
        """清除当前显示。"""
        self._log_view.clear()
        self._buffer.clear()
        self._status_label.setText("已清除")
