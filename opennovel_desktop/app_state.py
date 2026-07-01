"""AppState — 应用级状态管理层（QObject 单例 + Signal 广播）。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppState(QObject):
    """应用级状态单例，各面板通过 Signal/Slot 订阅状态变更。

    使用 AppState.initialize() 创建一次，之后通过 AppState.instance() 获取。
    """

    _instance: AppState | None = None

    # ── Signals ──────────────────────────────────────────────
    project_changed = Signal(str)  # project_root 路径
    project_closed = Signal()
    file_changed = Signal(str)  # current_file 路径
    file_saved = Signal(str)  # 已保存的文件路径
    agent_status_changed = Signal(str, str)  # agent_name, status
    scores_updated = Signal(list)  # list[Score dict]
    pipeline_phase_changed = Signal(str, str)  # phase_id, status
    theme_changed = Signal(str)  # "light" | "dark"
    search_activated = Signal()
    settings_changed = Signal(dict)  # 配置变更广播

    def __init__(self) -> None:
        super().__init__()
        # ── 项目状态 ──
        self.current_project: str = ""
        self.current_file: str = ""
        self.project_summary: dict[str, object] = {}

        # ── Agent 状态 ──
        self.agent_status: dict[str, str] = {}
        self.recent_scores: list[dict[str, object]] = []

        # ── UI 状态 ──
        self.left_panel_visible: bool = True
        self.right_panel_visible: bool = True
        self.status_bar_visible: bool = True
        self.focus_mode: bool = False

    # ── 单例管理 ──────────────────────────────────────────

    @classmethod
    def initialize(cls) -> AppState:
        """创建或返回 AppState 单例。必须在 QApplication 创建后调用。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def instance(cls) -> AppState:
        """获取已初始化的单例。initialize() 未调用时断言失败。"""
        assert cls._instance is not None, "AppState 未初始化，请先调用 AppState.initialize()"
        return cls._instance

    # ── 状态更新辅助 ─────────────────────────────────────

    def set_project(self, project_root: str) -> None:
        """切换当前项目，广播 project_changed。"""
        self.current_project = project_root
        self.current_file = ""
        self.project_summary = {}
        self.recent_scores = []
        self.project_changed.emit(project_root)

    def close_project(self) -> None:
        """关闭当前项目，清空状态。"""
        self.current_project = ""
        self.current_file = ""
        self.project_summary = {}
        self.recent_scores = []
        self.project_closed.emit()

    def set_current_file(self, file_path: str) -> None:
        """切换当前编辑文件，广播 file_changed。"""
        self.current_file = file_path
        self.file_changed.emit(file_path)

    def set_agent_status(self, agent_name: str, status: str) -> None:
        """更新 Agent 运行状态，广播 agent_status_changed。"""
        self.agent_status[agent_name] = status
        self.agent_status_changed.emit(agent_name, status)

    def set_scores(self, scores: list[dict[str, object]]) -> None:
        """更新最近评分列表，广播 scores_updated。"""
        self.recent_scores = scores
        self.scores_updated.emit(scores)

    def set_theme(self, theme: str) -> None:
        """主题切换广播。ThemeLoader 调用此方法通知各面板刷新。"""
        self.theme_changed.emit(theme)

    def toggle_focus_mode(self) -> None:
        """切换专注模式状态。"""
        self.focus_mode = not self.focus_mode
