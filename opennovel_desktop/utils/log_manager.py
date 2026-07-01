"""LogManager — GUI 日志管理器。

捕获 Python logging 输出，写入文件并转发到 GUI 面板。
遵循项目已有的 `logging.getLogger(__name__)` 模式。
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# 日志级别 → 标签颜色映射（供 LogPanel 使用）
LEVEL_TAGS: dict[int, tuple[str, str]] = {
    logging.DEBUG: ("DEBUG", "#A8A49E"),
    logging.INFO: ("INFO", "#4A7C5B"),
    logging.WARNING: ("WARNING", "#C4913A"),
    logging.ERROR: ("ERROR", "#B85C4A"),
    logging.CRITICAL: ("CRITICAL", "#B85C4A"),
}


class _QtLogHandler(logging.Handler):
    """将 logging 记录转发为 Qt Signal 的 Handler。

    连接到 LogPanel 后实时显示。
    """

    def __init__(self, signal_target: _LogSignalBridge) -> None:
        super().__init__()
        self._target = signal_target
        self.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        """线程安全地转发到主线程。"""
        msg = self.format(record)
        with contextlib.suppress(RuntimeError):
            self._target.log_received.emit(
                record.levelno,
                record.levelname,
                record.name,
                msg,
                record.created,
            )


class _LogSignalBridge(QObject):
    """跨线程 Signal 桥。LogPanel 通过此 Signal 接收日志。"""

    log_received = Signal(int, str, str, str, float)


# 单例实例
_manager: LogManager | None = None


class LogManager:
    """GUI 日志管理器。

    初始化后：
    - 日志写入 `logs/gui-YYYY-MM-DD.log`
    - 日志同时转发到 LogPanel（如有连接）
    - 自动捕获 opennovel 核心模块的日志

    使用方式：
        LogManager.initialize(project_root)
        logger = LogManager.get_logger(__name__)
        logger.info("事件记录")
    """

    def __init__(self, log_dir: str | Path) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Qt 桥接器
        self._signal_bridge = _LogSignalBridge()

        # 根 logger 配置
        self._root_logger = logging.getLogger("opennovel")
        self._root_logger.setLevel(logging.DEBUG)

        # 移除已有的 Handler（避免重复）
        self._root_logger.handlers.clear()

        # Handler 1: 文件（滚动，最大 5MB，保留 3 份）
        log_file = self._log_dir / f"gui-{datetime.now():%Y-%m-%d}.log"
        file_handler = RotatingFileHandler(
            str(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        self._root_logger.addHandler(file_handler)

        # Handler 2: Qt Signal 转发
        qt_handler = _QtLogHandler(self._signal_bridge)
        qt_handler.setLevel(logging.INFO)
        self._root_logger.addHandler(qt_handler)

        # Handler 3: 控制台（仅在开发模式）
        if os.environ.get("OPENNOVEL_DEBUG"):
            console = logging.StreamHandler()
            console.setLevel(logging.DEBUG)
            console.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
            self._root_logger.addHandler(console)

        self.info(f"日志系统初始化: {log_file}")

    # ── 公开接口 ──────────────────────────────────────────

    @property
    def signal_bridge(self) -> _LogSignalBridge:
        """LogPanel 连接此信号接收实时日志。"""
        return self._signal_bridge

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def set_debug_mode(self, enabled: bool) -> None:
        """动态切换 DEBUG 级别。"""
        level = logging.DEBUG if enabled else logging.INFO
        for handler in self._root_logger.handlers:
            handler.setLevel(level)
        self.info(f"调试日志: {'开启' if enabled else '关闭'}")

    def get_recent_logs(self, max_lines: int = 500) -> list[str]:
        """从当前日志文件读取最近的日志行。"""
        log_file = self._log_dir / f"gui-{datetime.now():%Y-%m-%d}.log"
        if not log_file.exists():
            return ["[日志文件不存在]"]
        try:
            lines = log_file.read_text(encoding="utf-8").strip().split("\n")
            return lines[-max_lines:]
        except (OSError, UnicodeDecodeError):
            return ["[读取日志失败]"]

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """获取指定名称的 logger，前缀自动补全 opennovel.desktop。"""
        return logging.getLogger(f"opennovel.desktop.{name}")

    @staticmethod
    def info(msg: str) -> None:
        logging.getLogger("opennovel.desktop").info(msg)

    @staticmethod
    def warning(msg: str) -> None:
        logging.getLogger("opennovel.desktop").warning(msg)

    @staticmethod
    def error(msg: str) -> None:
        logging.getLogger("opennovel.desktop").error(msg)

    @staticmethod
    def debug(msg: str) -> None:
        logging.getLogger("opennovel.desktop").debug(msg)

    def shutdown(self) -> None:
        """刷日志缓冲区并关闭 logging 系统。

        应在进程退出前调用，防止崩溃时最后几条日志丢失。
        """
        for handler in self._root_logger.handlers:
            handler.flush()
        logging.shutdown()

    # ── 单例管理 ──────────────────────────────────────────

    @staticmethod
    def initialize(log_dir: str | Path) -> LogManager:
        """全局初始化（只执行一次）。"""
        global _manager
        if _manager is None:
            _manager = LogManager(log_dir)
        return _manager

    @staticmethod
    def instance() -> LogManager:
        """获取已初始化的单例。"""
        global _manager
        assert _manager is not None, "LogManager 未初始化，请先调用 LogManager.initialize()"
        return _manager
