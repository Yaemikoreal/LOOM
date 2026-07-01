"""ThemeLoader — QSS 主题加载器，亮/暗双主题跟随系统。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile, QTextStream
from PySide6.QtWidgets import QApplication


class ThemeLoader:
    """管理亮/暗主题 QSS 加载与切换。

    使用方式：
        ThemeLoader.initialize(resources_path)
        ThemeLoader.apply_system_theme(app)
        # 手动切换：
        ThemeLoader.set_theme(app, "dark")
    """

    _resources_path: Path | None = None
    _current_theme: str = "light"

    # ── 公开色值（供 QSyntaxHighlighter 等组件读取）──────
    # 亮色
    light: dict[str, str] = {
        "editor_bg": "#FAF9F6",
        "editor_fg": "#2D2A26",
        "selection_bg": "#F3E4E0",
        "heading1": "#B85C4A",
        "heading2": "#B85C4A",
        "heading3": "#6B6863",
        "bold": "#2D2A26",
        "italic": "#6B6863",
        "blockquote": "#5A7C9A",
        "hr": "#D0CEC8",
        "yaml_fm": "#A8A49E",
        "inline_code": "#5A7C9A",
        "link": "#5A7C9A",
        "diff_add_bg": "#E8F5E9",
        "diff_del_bg": "#FFEBEE",
        "search_highlight": "#FFF3D6",
    }
    # 暗色
    dark: dict[str, str] = {
        "editor_bg": "#1E1E20",
        "editor_fg": "#E4E4E7",
        "selection_bg": "#3A2828",
        "heading1": "#C96A58",
        "heading2": "#C96A58",
        "heading3": "#9A9A9E",
        "bold": "#E4E4E7",
        "italic": "#9A9A9E",
        "blockquote": "#6A8CAC",
        "hr": "#4A4A4D",
        "yaml_fm": "#6E6E72",
        "inline_code": "#6A8CAC",
        "link": "#6A8CAC",
        "diff_add_bg": "#2A3A2E",
        "diff_del_bg": "#3A2828",
        "search_highlight": "#3A3420",
    }

    @classmethod
    def initialize(cls, resources_path: Path) -> None:
        """设置 QSS 主题文件所在目录。"""
        cls._resources_path = resources_path / "themes"

    @classmethod
    def current_theme(cls) -> str:
        """返回当前主题名 "light" | "dark"。"""
        return cls._current_theme

    @classmethod
    def current_colors(cls) -> dict[str, str]:
        """返回当前主题的色值表，供高亮器等组件读取。"""
        return cls.light if cls._current_theme == "light" else cls.dark

    @classmethod
    def detect_system_theme(cls) -> str:
        """通过 QStyleHints 检测系统亮/暗模式。

        回退逻辑：无法检测时返回 "light"。
        """
        app = QApplication.instance()
        if app is None:
            return "light"
        try:
            scheme = app.styleHints().colorScheme()
            # Qt 6.5+ 的 Qt::ColorScheme 枚举
            scheme_name = str(scheme)
            if "dark" in scheme_name.lower():
                return "dark"
            return "light"
        except Exception:
            return "light"

    @classmethod
    def _read_qss(cls, filename: str) -> str:
        """从主题目录读取 QSS 文件内容。"""
        if cls._resources_path is None:
            return ""
        path = cls._resources_path / filename
        if not path.exists():
            return ""
        qf = QFile(str(path))
        if not qf.open(QFile.OpenModeFlag.ReadOnly | QFile.OpenModeFlag.Text):
            return ""
        stream = QTextStream(qf)
        content = stream.readAll()
        qf.close()
        return content

    @classmethod
    def apply_system_theme(cls, app: QApplication) -> None:
        """检测系统亮/暗模式并应用对应主题。"""
        theme = cls.detect_system_theme()
        cls._apply_theme(app, theme)

    @classmethod
    def set_theme(cls, app: QApplication, theme: str) -> None:
        """强制切换到指定主题 (light/dark)，并广播到 AppState。"""
        if theme not in ("light", "dark"):
            theme = "light"
        cls._apply_theme(app, theme)

        # 通知 AppState 各面板刷新
        from opennovel_desktop.app_state import AppState  # noqa: PLC0415

        state = AppState.instance()
        state.set_theme(theme)

    @classmethod
    def _apply_theme(cls, app: QApplication, theme: str) -> None:
        """内部：拼接 base.qss + {theme}.qss 并应用到全局。"""
        cls._current_theme = theme
        base = cls._read_qss("base.qss")
        theme_qss = cls._read_qss(f"{theme}.qss")
        combined = f"{base}\n{theme_qss}"
        app.setStyleSheet(combined)

    @classmethod
    def reload_qss(cls, app: QApplication) -> None:
        """热加载当前主题（开发/高级功能 Ctrl+Shift+R）。"""
        cls._apply_theme(app, cls._current_theme)
