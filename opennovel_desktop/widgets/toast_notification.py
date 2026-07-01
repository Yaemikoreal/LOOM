"""ToastNotification — 非模态 Toast 轻提示。

右下角滑入，3s 自动消失，失败卡死时转为常驻。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton


class ToastNotification(QFrame):
    """非模态 Toast 通知。

    使用方式：
        ToastNotification.show_info("Commit 完成")
        ToastNotification.show_error("Writer 超时", persistent=True)
    """

    _instances: list[ToastNotification] = []

    def __init__(self, message: str, toast_type: str = "info", persistent: bool = False) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)

        # 背景色
        bg_colors = {
            "info": "#4A7C5B",
            "error": "#B85C4A",
            "warning": "#C4913A",
            "success": "#4A7C5B",
        }
        bg = bg_colors.get(toast_type, "#4A7C5B")

        self.setStyleSheet(f"background: {bg}; border-radius: 6px; padding: 12px 16px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        self._label = QLabel(message)
        self._label.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        if not persistent:
            self._close_btn = QPushButton("✕")
            self._close_btn.setStyleSheet("color: #FFFFFF; border: none; font-size: 14px;")
            self._close_btn.setFixedSize(20, 20)
            self._close_btn.clicked.connect(self._dismiss)
            layout.addWidget(self._close_btn)

        self.adjustSize()

        # 定位到右下角
        self._reposition()

        if not persistent:
            QTimer.singleShot(4000, self._dismiss)

        self.show()

        # 淡入动画
        self._fade_in()

    def _fade_in(self) -> None:
        """简单淡入效果。"""
        self.setWindowOpacity(0)
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()

    def _reposition(self) -> None:
        """定位到屏幕右下角。"""
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            offset = 20 + len(ToastNotification._instances) * 60
            self.move(
                geometry.right() - self.width() - 20,
                geometry.bottom() - self.height() - offset,
            )

    def _dismiss(self) -> None:
        """关闭通知。"""
        with contextlib.suppress(Exception):
            self.close()
            self.deleteLater()
        if self in ToastNotification._instances:
            ToastNotification._instances.remove(self)

    @staticmethod
    def show_info(message: str, duration: int = 4000) -> None:
        """显示信息通知（自动消失）。"""
        _ = duration
        t = ToastNotification(message, "info", persistent=False)
        ToastNotification._instances.append(t)

    @staticmethod
    def show_error(message: str, persistent: bool = False) -> None:
        """显示错误通知。persistent=True 时不会自动消失。"""
        t = ToastNotification(message, "error", persistent=persistent)
        ToastNotification._instances.append(t)

    @staticmethod
    def show_success(message: str) -> None:
        """显示成功通知。"""
        t = ToastNotification(message, "success", persistent=False)
        ToastNotification._instances.append(t)

    @staticmethod
    def show_warning(message: str) -> None:
        """显示警告通知。"""
        t = ToastNotification(message, "warning", persistent=False)
        ToastNotification._instances.append(t)
