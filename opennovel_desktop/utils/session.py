"""SessionManager + AutoSaveManager — 会话持久化与崩溃恢复。

SessionManager: 窗口几何/面板状态 → QSettings INI + 编辑状态 → session.json
AutoSaveManager: 60s QTimer 自动保存编辑器草稿到 .snapshots/autosave/
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtWidgets import QMainWindow

from opennovel_desktop.app_state import AppState


class SessionManager:
    """管理窗口几何、面板状态、打开文件列表的持久化。

    窗口几何通过 QMainWindow.saveGeometry() / restoreGeometry() 保存。
    面板状态通过 QMainWindow.saveState() / restoreState() 保存。
    编辑状态（文件列表、光标位置）单独序列化为 JSON。
    """

    def __init__(self) -> None:
        self._settings = QSettings("OpenNovel", "desktop")

    # ── 窗口几何 ──────────────────────────────────────────

    def save_window_geometry(self, window: QMainWindow) -> None:
        """保存窗口位置、大小、最大化状态。"""
        geometry = window.saveGeometry()
        self._settings.setValue("window/geometry", geometry)

        state = window.saveState()
        self._settings.setValue("window/state", state)

    def restore_window_geometry(self, window: QMainWindow) -> bool:
        """恢复窗口几何。返回是否成功恢复。"""
        geometry = self._settings.value("window/geometry")
        state = self._settings.value("window/state")
        if geometry is not None:
            window.restoreGeometry(geometry)
            if state is not None:
                window.restoreState(state)
            return True
        return False

    # ── 面板状态 ──────────────────────────────────────────

    def save_panel_state(self, app_state: AppState) -> None:
        """保存面板显隐状态。"""
        self._settings.setValue("panels/left_visible", app_state.left_panel_visible)
        self._settings.setValue("panels/right_visible", app_state.right_panel_visible)
        self._settings.setValue("panels/status_visible", app_state.status_bar_visible)
        self._settings.setValue("panels/focus_mode", app_state.focus_mode)

    def restore_panel_state(self, app_state: AppState) -> None:
        """恢复面板显隐状态。"""
        left = self._settings.value("panels/left_visible")
        if left is not None:
            app_state.left_panel_visible = str(left).lower() == "true"
        right = self._settings.value("panels/right_visible")
        if right is not None:
            app_state.right_panel_visible = str(right).lower() == "true"
        status = self._settings.value("panels/status_visible")
        if status is not None:
            app_state.status_bar_visible = str(status).lower() == "true"

    # ── 编辑会话 ──────────────────────────────────────────

    def save_editor_session(self, app_state: AppState, open_files: list[str]) -> None:
        """保存编辑会话（打开的文件列表、当前文件）。"""
        data = {
            "current_project": app_state.current_project,
            "current_file": app_state.current_file,
            "open_files": open_files,
        }
        session_path = self._get_session_path()
        try:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def restore_editor_session(self) -> dict[str, object] | None:
        """恢复上次编辑会话。返回 None 表示无可恢复会话。"""
        session_path = self._get_session_path()
        if not session_path.exists():
            return None
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return None

    # ── 辅助 ──────────────────────────────────────────────

    def save_last_project(self, project_root: str) -> None:
        """保存最后打开的项目路径。"""
        self._settings.setValue("session/last_project", project_root)

    def restore_last_project(self) -> str:
        """恢复最后打开的项目路径。"""
        value = self._settings.value("session/last_project")
        return str(value) if value is not None else ""

    @staticmethod
    def _get_session_path() -> Path:
        """获取 session.json 路径（%APPDATA%/OpenNovel/session.json）。"""
        data_dir = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        return data_dir / "OpenNovel" / "session.json"


class AutoSaveManager:
    """编辑器草稿自动保存（60s 间隔）。

    保存到 .snapshots/autosave/ 目录，与 StateManager 机制复用。
    启动时检测未提交备份并弹出恢复提示。

    需要调用 set_editor_provider() 设置编辑器内容提供者回调，
    否则仅写入占位标记（无法获取编辑器内容时兜底）。
    """

    def __init__(self, app_state: AppState | None = None) -> None:
        self._app_state = app_state or AppState.instance()
        self._timer = QTimer()
        self._timer.setInterval(60_000)  # 60s
        self._timer.timeout.connect(self._autosave)
        self._dirty: bool = False
        self._editor_provider = None  # Callable[[], dict[str, str]]

    def set_editor_provider(self, provider) -> None:
        """设置编辑器内容提供者回调。

        provider() 返回 dict[file_path, content]，每个打开的文件的真实内容。
        """
        self._editor_provider = provider

    def start(self) -> None:
        """启动自动保存定时器。"""
        self._timer.start()

    def stop(self) -> None:
        """停止自动保存定时器。"""
        self._timer.stop()

    def mark_dirty(self) -> None:
        """标记编辑器内容已变更（下次 timeout 时保存）。"""
        self._dirty = True

    def _autosave(self) -> None:
        """执行自动保存：写入编辑器真实内容。"""
        if not self._dirty:
            return
        if not self._app_state.current_project:
            return

        save_dir = Path(self._app_state.current_project) / ".snapshots" / "autosave"
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        # 有提供者时写入真实编辑器内容
        if self._editor_provider:
            contents = self._editor_provider()
            for file_path, content in contents.items():
                autosave_path = save_dir / f"{Path(file_path).name}.autosave.md"
                with contextlib.suppress(OSError):
                    autosave_path.write_text(content, encoding="utf-8")
        else:
            # 无提供者时兜底：仅写时间戳标记
            state = self._app_state
            if state.current_file:
                file_path = Path(state.current_file)
                autosave_path = save_dir / f"{file_path.name}.autosave.md"
                with contextlib.suppress(OSError):
                    autosave_path.write_text(
                        f"<!-- autosave at {__import__('datetime').datetime.now()} -->\n",
                        encoding="utf-8",
                    )

        self._dirty = False

    @staticmethod
    def check_crash_recovery(project_root: str) -> str | None:
        """检测崩溃恢复文件。返回草稿文件路径或 None。"""
        autosave_dir = Path(project_root) / ".snapshots" / "autosave"
        if not autosave_dir.exists():
            return None
        autosave_files = list(autosave_dir.glob("*.autosave.md"))
        if not autosave_files:
            return None
        # 返回最新的文件
        return str(max(autosave_files, key=lambda p: p.stat().st_mtime))

    @staticmethod
    def clear_autosave(project_root: str) -> None:
        """清除崩溃恢复文件。"""
        autosave_dir = Path(project_root) / ".snapshots" / "autosave"
        if autosave_dir.exists():
            for f in autosave_dir.glob("*.autosave.md"):
                with contextlib.suppress(OSError):
                    f.unlink()
