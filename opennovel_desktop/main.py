"""OpenNovel Desktop 应用启动入口。"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from opennovel_desktop.app_state import AppState
from opennovel_desktop.styles.theme_loader import ThemeLoader
from opennovel_desktop.widgets.main_window import NovelDesktopWindow


def _fix_ssl_certs() -> None:
    """配置 SSL 证书路径，修复 LiteLLM 在 Windows 上的证书验证失败。

    Windows 下 Python 的 ssl 默认不绑定系统证书存储，
    LiteLLM 请求 GitHub raw 内容时可能因 SSL_CERTIFICATE_VERIFY_FAILED 失败。
    此处尝试使用 certifi 提供的 CA 包，无 certifi 时静默跳过。
    """
    with contextlib.suppress(Exception):
        import os  # noqa: PLC0415

        import certifi  # noqa: PLC0415

        ca_path = certifi.where()
        if ca_path:
            os.environ.setdefault("SSL_CERT_FILE", ca_path)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)


# 确保 QApplication 能正确处理高 DPI 屏幕
QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def _get_resources_path() -> Path:
    """返回 resources/ 目录的绝对路径。"""
    return Path(__file__).resolve().parent / "resources"


def _setup_fonts(app: QApplication) -> None:
    """设置全局字体回退链。

    UI 优先 Inter（西文）+ 系统中文回退，编辑器字体在 NovelEditor 中单独设置。
    Windows 下 Qt 默认字体映射不足，显式指定 fallback。
    """
    font = QFont("Inter, Microsoft YaHei, Noto Sans CJK SC, sans-serif")
    font.setPointSize(10)
    app.setFont(font)


def main() -> None:
    """启动 OpenNovel Desktop 应用。"""
    # 修正 stdout 编码防止 Windows GBK 问题（沿用项目约定）
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    # 配置 SSL 证书路径，修复 LiteLLM 在 Windows 上的证书验证失败
    _fix_ssl_certs()

    app = QApplication(sys.argv)
    app.setApplicationName("OpenNovel")
    app.setOrganizationName("OpenNovel")
    app.setApplicationVersion(__import__("opennovel_desktop").__version__)

    # 字体与高 DPI
    _setup_fonts(app)

    # 加载系统主题（自动检测亮/暗）
    res_path = _get_resources_path()
    ThemeLoader.initialize(res_path)
    ThemeLoader.apply_system_theme(app)

    # 初始化 AppState 单例
    AppState.initialize()

    # 初始化日志系统（写入 logs/gui-*.log）
    from opennovel_desktop.utils.log_manager import LogManager  # noqa: PLC0415

    log_dir = _get_resources_path().parent / "logs"
    LogManager.initialize(log_dir)
    # 注册日志关闭钩子：崩溃前刷缓冲区，防止日志丢失
    import atexit  # noqa: PLC0415

    atexit.register(LogManager.instance().shutdown)
    app.aboutToQuit.connect(LogManager.instance().shutdown)
    LogManager.info("OpenNovel Desktop 启动")

    # 首次运行检测 → 显示设置向导
    config_locations = [
        Path.cwd() / ".opennovel.yaml",
        Path.home() / ".opennovel.yaml",
    ]
    is_first_run = not any(p.exists() for p in config_locations)

    if is_first_run:
        from PySide6.QtWidgets import QWizard  # noqa: PLC0415

        from opennovel_desktop.dialogs.setup_wizard import SetupWizard  # noqa: PLC0415

        wizard = SetupWizard()
        if wizard.exec() == QWizard.DialogCode.Accepted:
            wizard.apply_config()

    # 创建并显示主窗口
    window = NovelDesktopWindow(res_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
