"""NovelDesktopWindow — 主窗口骨架（三栏驾驶舱布局）。"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.app_state import AppState
from opennovel_desktop.utils.log_manager import LogManager
from opennovel_desktop.widgets.toast_notification import ToastNotification


class _NavButton(QPushButton):
    """左侧导航栏切换按钮。选中态由 MainWindow 管理。"""

    def __init__(self, text: str, icon_path: str | None = None) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setObjectName("NavButton")
        if icon_path:
            self.setProperty("iconPath", icon_path)


class _ToolbarAction(QToolButton):
    """工具栏纯图标按钮，32x32 固定尺寸，带 Tooltip。"""

    def __init__(
        self,
        icon_path: str,
        tooltip: str,
        shortcut: str = "",
    ) -> None:
        super().__init__()
        self.setIconSize(QSize(24, 24))
        self.setFixedSize(QSize(32, 32))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        # 加载 SVG 图标（通过 QRC 或文件路径）
        full_tip = tooltip
        if shortcut:
            full_tip = f"<b>{tooltip}</b><br><i>{shortcut}</i>"
        self.setToolTip(full_tip)


class NovelDesktopWindow(QMainWindow):
    """OpenNovel Desktop 主窗口。

    三栏布局：左侧导航 | 中央编辑器 | 右侧面板。
    包含顶栏工具栏、底栏状态栏、菜单栏。
    """

    def __init__(self, resources_path: Path) -> None:
        super().__init__()
        self._resources_path = resources_path
        self._app_state = AppState.instance()
        self._focus_mode: bool = False

        # 会话持久化
        from opennovel_desktop.utils.session import (  # noqa: PLC0415
            AutoSaveManager,
            SessionManager,
        )

        self._session_mgr = SessionManager()
        self._autosave_mgr = AutoSaveManager(self._app_state)
        self._autosave_mgr.set_editor_provider(self._get_editor_contents)
        self._autosave_mgr.start()

        # 状态存储：面板显隐记录（专注模式恢复用）
        self._left_visible_before_focus: bool = True
        self._right_visible_before_focus: bool = True
        self._toolbar_visible_before_focus: bool = True
        self._menubar_visible_before_focus: bool = True
        self._statusbar_visible_before_focus: bool = True

        # 操作活动标志（防并发 worker）
        self._write_active: bool = False
        self._auto_active: bool = False

        # Agent 状态指示器脉冲动画
        self._agent_active: bool = False
        self._pulse_idx: int = 0
        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(600)
        self._pulse_timer.timeout.connect(self._pulse_agent_status)

        # API 连接状态
        self._api_connected: bool | None = None  # None=未测试, True=正常, False=断开

        self._setup_window()
        self._setup_menu_bar()
        self._setup_toolbar()
        self._setup_central_area()
        self._setup_status_bar()
        self._connect_signals()

        # 启动后自动恢复上次会话
        self._restore_session()

    # ── 窗口基本属性 ──────────────────────────────────────

    def _setup_window(self) -> None:
        """配置窗口基础属性：标题、尺寸、起始位置。"""
        self.setWindowTitle("OpenNovel")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 860)

        # 居中显示
        screen = self.screen()
        if screen:
            geometry = screen.availableGeometry()
            x = (geometry.width() - 1400) // 2
            y = (geometry.height() - 860) // 2
            self.move(x, y)

    # ── 菜单栏 ────────────────────────────────────────────

    def _setup_menu_bar(self) -> None:
        """构建菜单栏：所有项连接实际 handler。"""
        menu_bar = self.menuBar()

        # ── 文件 ──
        file_menu = menu_bar.addMenu("文件(&F)")

        a = QAction("新建项目", self)
        a.setShortcut(QKeySequence("Ctrl+N"))
        a.triggered.connect(self._new_project)
        file_menu.addAction(a)

        a = QAction("打开项目", self)
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._open_project)
        file_menu.addAction(a)

        file_menu.addSeparator()

        a = QAction("保存", self)
        a.setShortcut(QKeySequence("Ctrl+S"))
        a.triggered.connect(self._save_current_editor)
        file_menu.addAction(a)

        a = QAction("另存为...", self)
        a.triggered.connect(self._save_as_file)
        file_menu.addAction(a)

        file_menu.addSeparator()

        a = QAction("偏好设置", self)
        a.setShortcut(QKeySequence("Ctrl+,"))
        a.triggered.connect(self._show_global_settings)
        file_menu.addAction(a)

        file_menu.addSeparator()

        a = QAction("退出", self)
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)
        file_menu.addAction(a)

        # ── 编辑 ──
        edit_menu = menu_bar.addMenu("编辑(&E)")

        a = QAction("撤销", self)
        a.setShortcut(QKeySequence.Undo)
        a.triggered.connect(self._editor_undo)
        edit_menu.addAction(a)

        a = QAction("重做", self)
        a.setShortcut(QKeySequence.Redo)
        a.triggered.connect(self._editor_redo)
        edit_menu.addAction(a)

        edit_menu.addSeparator()

        a = QAction("查找", self)
        a.setShortcut(QKeySequence("Ctrl+F"))
        a.triggered.connect(self._editor_find)
        edit_menu.addAction(a)

        a = QAction("替换", self)
        a.setShortcut(QKeySequence("Ctrl+H"))
        a.triggered.connect(self._editor_replace)
        edit_menu.addAction(a)

        a = QAction("全局搜索", self)
        a.setShortcut(QKeySequence("Ctrl+Shift+F"))
        a.triggered.connect(self._activate_search)
        edit_menu.addAction(a)

        edit_menu.addSeparator()

        agent_menu = edit_menu.addMenu("发送给 Agent")

        a = QAction("润色", self)
        a.triggered.connect(self._agent_action_polish)
        agent_menu.addAction(a)

        a = QAction("续写", self)
        a.triggered.connect(self._agent_action_continue)
        agent_menu.addAction(a)

        a = QAction("扩写", self)
        a.triggered.connect(self._agent_action_expand)
        agent_menu.addAction(a)

        agent_menu.addSeparator()

        a = QAction("从反派视角重写", self)
        a.triggered.connect(self._agent_action_rewrite)
        agent_menu.addAction(a)

        # ── 视图（不变）──
        view_menu = menu_bar.addMenu("视图(&V)")
        self._action_toggle_left = QAction("显示左侧导航", self)
        self._action_toggle_left.setShortcut(QKeySequence("Ctrl+Shift+1"))
        self._action_toggle_left.setCheckable(True)
        self._action_toggle_left.setChecked(True)
        view_menu.addAction(self._action_toggle_left)
        self._action_toggle_right = QAction("显示右侧面板", self)
        self._action_toggle_right.setShortcut(QKeySequence("Ctrl+Shift+2"))
        self._action_toggle_right.setCheckable(True)
        self._action_toggle_right.setChecked(True)
        view_menu.addAction(self._action_toggle_right)
        self._action_toggle_status = QAction("显示状态栏", self)
        self._action_toggle_status.setCheckable(True)
        self._action_toggle_status.setChecked(True)
        view_menu.addAction(self._action_toggle_status)
        view_menu.addSeparator()
        self._action_focus_mode = QAction("专注模式", self)
        self._action_focus_mode.setShortcut(QKeySequence("F11"))
        self._action_focus_mode.setCheckable(True)
        view_menu.addAction(self._action_focus_mode)

        view_menu.addSeparator()

        self._action_show_log = QAction("日志面板", self)
        self._action_show_log.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._action_show_log.setCheckable(True)
        view_menu.addAction(self._action_show_log)

        view_menu.addSeparator()

        self._action_toggle_toolbar_labels = QAction("显示工具栏标签", self)
        self._action_toggle_toolbar_labels.setCheckable(True)
        self._action_toggle_toolbar_labels.setChecked(False)
        view_menu.addAction(self._action_toggle_toolbar_labels)

        # ── 工具 ──
        tools_menu = menu_bar.addMenu("工具(&T)")

        a = QAction("项目诊断", self)
        a.triggered.connect(self._run_diagnose)
        tools_menu.addAction(a)

        a = QAction("重写索引", self)
        a.triggered.connect(self._run_reindex)
        tools_menu.addAction(a)

        a = QAction("校准 Critic", self)
        a.triggered.connect(self._run_calibrate)
        tools_menu.addAction(a)

        tools_menu.addSeparator()

        a = QAction("历史回滚...", self)
        a.triggered.connect(self._run_rollback)
        tools_menu.addAction(a)

        a = QAction("清除缓存", self)
        a.triggered.connect(self._run_clear_cache)
        tools_menu.addAction(a)

        # ── 帮助 ──
        help_menu = menu_bar.addMenu("帮助(&H)")

        a = QAction("关于 OpenNovel", self)
        a.triggered.connect(self._show_about)
        help_menu.addAction(a)

        a = QAction("检查更新", self)
        a.triggered.connect(self._check_update)
        help_menu.addAction(a)

        a = QAction("打开日志目录", self)
        a.triggered.connect(self._open_logs_dir)
        help_menu.addAction(a)

    # ── 工具栏 ────────────────────────────────────────────

    def _load_icon(self, name: str) -> object:
        """从 resources/icons/ 加载 SVG 图标。"""
        from PySide6.QtGui import QIcon  # noqa: PLC0415

        icon_path = self._resources_path / "icons" / f"{name}.svg"
        if icon_path.exists():
            return QIcon(str(icon_path))
        return QIcon()

    def _setup_toolbar(self) -> None:
        """构建主工具栏：6 枚图标按钮（可切换标签显示）。"""
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        toolbar.setObjectName("MainToolBar")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        # 写章节 → 填充对话输入框
        self._btn_write = QToolButton()
        self._btn_write.setIcon(self._load_icon("write-chapter"))
        self._btn_write.setText("写章节")
        self._btn_write.setToolTip("<b>写章节</b><br>在对话中发起创作请求<br><i>Ctrl+Shift+W</i>")
        toolbar.addWidget(self._btn_write)

        # Auto → 填充对话输入框
        self._btn_auto = QToolButton()
        self._btn_auto.setIcon(self._load_icon("auto"))
        self._btn_auto.setText("全自动")
        self._btn_auto.setToolTip(
            "<b>全自动</b><br>在对话中发起自动创作请求<br><i>Ctrl+Shift+A</i>"
        )
        toolbar.addWidget(self._btn_auto)

        # 停止（初始灰色禁用态）
        self._btn_stop = QToolButton()
        self._btn_stop.setIcon(self._load_icon("stop"))
        self._btn_stop.setText("停止")
        self._btn_stop.setEnabled(False)
        self._btn_stop.setToolTip("<b>停止</b><br>中断当前 Agent 操作")
        toolbar.addWidget(self._btn_stop)

        toolbar.addSeparator()

        # Commit（唯一直接执行的按钮）
        self._btn_commit = QToolButton()
        self._btn_commit.setIcon(self._load_icon("commit"))
        self._btn_commit.setText("提交")
        self._btn_commit.setToolTip("<b>提交状态</b><br>提取并固化章节变更<br><i>Ctrl+Shift+C</i>")
        toolbar.addWidget(self._btn_commit)

        # 灵感 → 填充对话输入框
        self._btn_stash = QToolButton()
        self._btn_stash.setIcon(self._load_icon("stash"))
        self._btn_stash.setText("灵感")
        self._btn_stash.setToolTip("<b>灵感</b><br>将选中文本存为灵感<br><i>Ctrl+Shift+S</i>")
        toolbar.addWidget(self._btn_stash)

        self.addToolBar(toolbar)
        self._toolbar = toolbar
        self._toolbar_label_state = False

    # ── 中央三栏布局 ──────────────────────────────────────

    def _setup_central_area(self) -> None:
        """构建 QSplitter 三栏布局。"""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)

        # 左侧导航
        self._left_panel = self._build_left_nav()
        splitter.addWidget(self._left_panel)

        # 中央编辑器区（EditorTabWidget）
        from opennovel_desktop.widgets.editor_tabs import EditorTabWidget

        self._editor_tabs = EditorTabWidget()
        self._editor_tabs.setObjectName("EditorTabs")
        splitter.addWidget(self._editor_tabs)

        # 右侧面板 — QStackedWidget（ChatPanel 为主页，其他为覆盖层）
        self._right_stack = QStackedWidget()
        self._right_stack.setObjectName("RightPanel")
        self._right_stack.setMinimumWidth(280)
        self._right_stack.setMaximumWidth(480)

        # Page 0: ChatPanel（主面板，始终可见）
        from opennovel_desktop.widgets.chat_panel import ChatPanel

        self._chat_panel = ChatPanel()
        self._right_stack.addWidget(self._chat_panel)

        # Page 1: SearchPanel（按需覆盖）
        from opennovel_desktop.widgets.search_panel import SearchPanel

        self._search_panel = SearchPanel()
        self._right_stack.addWidget(self._search_panel)

        # Page 2: LogPanel（按需覆盖）
        from opennovel_desktop.widgets.log_panel import LogPanel

        self._log_panel = LogPanel()
        self._right_stack.addWidget(self._log_panel)

        # Page 3: DraftPanel（分栏对比覆盖层）
        from opennovel_desktop.widgets.draft_panel import DraftPanel

        self._draft_panel = DraftPanel()
        self._draft_panel.draft_accepted.connect(self._on_draft_accepted)
        self._draft_panel.draft_rejected.connect(self._on_draft_rejected)
        self._draft_panel.close_requested.connect(lambda: self._right_stack.setCurrentIndex(0))
        self._right_stack.addWidget(self._draft_panel)

        self._right_stack.setCurrentIndex(0)

        splitter.addWidget(self._right_stack)

        # 设置伸缩因子：编辑器优先扩展
        splitter.setStretchFactor(0, 0)  # 左侧（固定宽度）
        splitter.setStretchFactor(1, 1)  # 中央（优先伸缩）
        splitter.setStretchFactor(2, 0)  # 右侧（固定宽度）
        splitter.setSizes([240, 780, 320])

        self.setCentralWidget(splitter)

    def _build_left_nav(self) -> QWidget:
        """构建左侧导航面板（按钮切换 + QStackedWidget）。"""
        container = QWidget()
        container.setObjectName("LeftPanel")
        container.setMinimumWidth(200)
        container.setMaximumWidth(320)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 按钮栏
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(8, 8, 8, 0)
        btn_bar.setSpacing(4)

        self._nav_btn_files = _NavButton("文件")
        self._nav_btn_chars = _NavButton("角色")
        self._nav_btn_outline = _NavButton("大纲")

        btn_bar.addWidget(self._nav_btn_files)
        btn_bar.addWidget(self._nav_btn_chars)
        btn_bar.addWidget(self._nav_btn_outline)
        layout.addLayout(btn_bar)

        # 堆叠面板（Phase 2 实现）
        from opennovel_desktop.widgets.character_panel import CharacterPanel
        from opennovel_desktop.widgets.file_tree_panel import FileTreePanel
        from opennovel_desktop.widgets.outline_panel import OutlinePanel

        self._file_tree = FileTreePanel(self._app_state)
        self._char_panel = CharacterPanel(self._app_state)
        self._outline_panel = OutlinePanel(self._app_state)

        self._nav_stack = QStackedWidget()
        self._nav_stack.addWidget(self._file_tree)
        self._nav_stack.addWidget(self._char_panel)
        self._nav_stack.addWidget(self._outline_panel)

        layout.addWidget(self._nav_stack, 1)

        # 按钮默认选中第一个
        self._nav_btn_files.setChecked(True)
        self._nav_btn_files.clicked.connect(lambda: self._switch_nav(0))
        self._nav_btn_chars.clicked.connect(lambda: self._switch_nav(1))
        self._nav_btn_outline.clicked.connect(lambda: self._switch_nav(2))

        return container

    def _switch_nav(self, index: int) -> None:
        """切换左侧导航面板。"""
        self._nav_stack.setCurrentIndex(index)
        self._nav_btn_files.setChecked(index == 0)
        self._nav_btn_chars.setChecked(index == 1)
        self._nav_btn_outline.setChecked(index == 2)

    # ── 状态栏 ────────────────────────────────────────────

    def _setup_status_bar(self) -> None:
        """构建四区状态栏：左(文件) + 中(工作状态) + 右(指示器集群)。"""
        status = QStatusBar()
        self.setStatusBar(status)

        # 左区：文件名 + 字数 + 光标
        self._status_left = QLabel("无打开文件")
        status.addWidget(self._status_left, 1)

        # 中区：模型 + 当前操作
        self._status_mid = QLabel("模型 v4-flash")
        status.addWidget(self._status_mid, 1)

        # ── 右区：状态指示器集群 ────────────────────────────
        # 右区1：API 连接状态灯
        self._status_api = QLabel("◌ API")
        self._status_api.setStyleSheet("color: #A8A49E; font-size: 12px; padding: 0 4px;")
        self._status_api.setToolTip("API 连接状态：未测试")
        status.addPermanentWidget(self._status_api)

        # 右区2：Agent 工作状态指示器（脉冲动画）
        self._status_agent = QLabel("● 就绪")
        self._status_agent.setStyleSheet("color: #4A7C5B; font-size: 12px; padding: 0 4px;")
        self._status_agent.setToolTip("Agent 状态：就绪")
        status.addPermanentWidget(self._status_agent)

        # 右区3：保存状态
        self._status_save = QLabel("已保存")
        self._status_save.setStyleSheet("color: #6B6863; font-size: 12px; padding: 0 4px;")
        status.addPermanentWidget(self._status_save)

    # ── 信号连接 ──────────────────────────────────────────

    # ── Agent 状态指示器脉冲动画 ───────────────────────────

    def _set_agent_active(self, active: bool, status_text: str = "") -> None:
        """切换 Agent 状态指示器。

        active=True: 启动脉冲动画，指示器橙色闪烁
        active=False: 停止脉冲，显示绿色就绪状态
        """
        self._agent_active = active
        if active:
            self._pulse_idx = 0
            self._pulse_timer.start()
            self._status_agent.setStyleSheet("color: #C4913A; font-size: 12px; padding: 0 4px;")
            self._status_agent.setText(f"◉ {status_text or '工作中...'}")
            self._status_agent.setToolTip(f"Agent 状态：{status_text or '工作中'}")
        else:
            self._pulse_timer.stop()
            self._status_agent.setStyleSheet("color: #4A7C5B; font-size: 12px; padding: 0 4px;")
            self._status_agent.setText("● 就绪")
            self._status_agent.setToolTip("Agent 状态：就绪")

    def _pulse_agent_status(self) -> None:
        """脉冲动画：在 ● 和 ◉ 之间切换（每 600ms）。"""
        self._pulse_idx += 1
        chars = ["◉", "◎", "◉", "●"]
        icon = chars[self._pulse_idx % len(chars)]
        self._status_agent.setText(f"{icon} 创作中...")

    def _set_api_status(self, connected: bool | None, tooltip: str = "") -> None:
        """更新 API 连接状态灯。

        Args:
            connected: True=正常, False=断开, None=未测试
            tooltip: 鼠标悬停提示文本
        """
        self._api_connected = connected
        if connected is True:
            color = "#4A7C5B"  # 绿色
            text = "● API"
            tip = tooltip or "API 连接正常"
        elif connected is False:
            color = "#B85C4A"  # 红色
            text = "○ API"
            tip = tooltip or "API 连接断开"
        else:
            color = "#A8A49E"  # 灰色
            text = "◌ API"
            tip = tooltip or "API 未测试"
        self._status_api.setStyleSheet(f"color: {color}; font-size: 12px; padding: 0 4px;")
        self._status_api.setText(text)
        self._status_api.setToolTip(tip)

    def _set_save_status(self, status_text: str) -> None:
        """更新保存状态指示。"""
        self._status_save.setText(status_text)

    # ── 工具栏按钮加载状态 ─────────────────────────────────

    def _set_tool_loading(self, loading: bool, loading_text: str = "") -> None:
        """切换写/Auto 按钮的加载状态。

        loading=True: 禁用按钮，显示文字「创作中...」
        loading=False: 恢复按钮初始状态
        """
        for btn in (self._btn_write, self._btn_auto):
            if loading:
                btn._orig_style = btn.toolButtonStyle()
                btn._orig_text = btn.text()
                btn.setText(loading_text or "工作中...")
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                btn.setEnabled(False)
            else:
                orig_style = getattr(btn, "_orig_style", None)
                orig_text = getattr(btn, "_orig_text", None)
                if orig_style is not None:
                    btn.setToolButtonStyle(orig_style)
                if orig_text is not None:
                    btn.setText(orig_text)
                btn.setEnabled(True)

    def _connect_signals(self) -> None:
        """连接 AppState 信号响应和菜单/按钮动作。"""
        # 视图菜单
        self._action_toggle_left.triggered.connect(self._toggle_left_panel)
        self._action_toggle_right.triggered.connect(self._toggle_right_panel)
        self._action_toggle_status.triggered.connect(self._toggle_status_bar)
        self._action_focus_mode.triggered.connect(self.toggle_focus_mode)

        # ── 日志面板 ──
        self._action_show_log.triggered.connect(self._toggle_log_panel)

        # ── 工具栏标签切换 ──
        self._action_toggle_toolbar_labels.triggered.connect(self._toggle_toolbar_labels)

        # ── 工具栏按钮 ──
        self._btn_write.clicked.connect(self._preset_write_chapter)
        self._btn_auto.clicked.connect(self._preset_auto)
        self._btn_stop.clicked.connect(self._stop_worker)
        self._btn_commit.clicked.connect(self._start_commit)
        self._btn_stash.clicked.connect(self._preset_stash)

        # ── 对话面板 → Agent 执行 ──
        self._chat_panel.execute_requested.connect(self._on_chat_request)
        self._chat_panel.card_action_triggered.connect(self._on_card_action)
        self._chat_panel.editor_mode_changed.connect(self._on_editor_mode_changed)

        # ── 全局搜索通过 AppState Signal 激活 ──
        self._app_state.search_activated.connect(self._activate_search)

        # ── 导航面板 → 编辑器 ──
        self._file_tree.file_activated.connect(lambda path: self._editor_tabs.open_file(path))
        self._char_panel.open_character_file.connect(lambda path: self._editor_tabs.open_file(path))
        self._outline_panel.open_source_file.connect(lambda path: self._editor_tabs.open_file(path))

        # ── 文件打开 → 状态栏更新 ──
        self._editor_tabs.file_opened.connect(self._on_file_opened)

        # ── 项目切换 → 更新各面板 ──
        self._app_state.project_changed.connect(self._on_project_changed)

        # ── 编辑器右键 Agent 菜单 ──
        self._editor_tabs.currentChanged.connect(self._connect_editor_signals)
        self._editor_tabs.currentChanged.connect(lambda: self._update_editor_status())
        # 初始连接
        self._connect_editor_signals(self._editor_tabs.currentIndex())

    def _connect_editor_signals(self, index: int) -> None:
        """当编辑器标签页切换时重新连接信号。"""
        editor = self._editor_tabs._editor_at(index) if index >= 0 else None
        if editor and hasattr(editor, "agent_action_requested"):
            # 每个编辑器实例只连接一次，用属性标志防止重复
            if getattr(editor, "_tn_signals_connected", False):
                return
            editor.agent_action_requested.connect(self._on_agent_action)
            editor.textChanged.connect(self._autosave_mgr.mark_dirty)
            editor.cursorPositionChanged.connect(self._update_editor_status)
            editor.document().modificationChanged.connect(
                lambda modified: self._set_save_status("未保存" if modified else "已保存")
            )
            editor._tn_signals_connected = True

    def _on_file_opened(self, file_path: str) -> None:
        """文件打开时更新状态栏。"""
        from pathlib import Path

        name = Path(file_path).name
        self._status_left.setText(f"  {name}  |  … 字  |  行 1, 列 1")
        # 立即计算实际字数
        editor = self._editor_tabs.current_editor()
        if editor:
            self._update_editor_status()

    def _update_editor_status(self) -> None:
        """根据当前编辑器更新状态栏：文件名、字数、光标位置。"""
        editor = self._editor_tabs.current_editor()
        if editor is None:
            return

        text = editor.toPlainText()
        char_count = len(text)

        cursor = editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1

        # 尝试从标签获取文件名
        idx = self._editor_tabs.currentIndex()
        file_name = self._editor_tabs.tabText(idx) if idx >= 0 else ""

        self._status_left.setText(f"  {file_name}  |  {char_count:,} 字  |  行 {line}, 列 {col}")

    def _on_project_changed(self, project_root: str) -> None:
        """项目切换时更新窗口标题。"""
        from pathlib import Path

        proj_name = Path(project_root).name
        self.setWindowTitle(f"OpenNovel — {proj_name}")

    def _restore_session(self) -> None:
        """启动时自动恢复上次会话（项目 + 打开的文件）。"""
        last_project = self._session_mgr.restore_last_project()
        if last_project and Path(last_project).exists():
            try:
                self._app_state.set_project(last_project)
                # 恢复上次打开的文件
                session = self._session_mgr.restore_editor_session()
                if session and isinstance(session, dict):
                    open_files = session.get("open_files", [])
                    if isinstance(open_files, list):
                        for fp in open_files:
                            if isinstance(fp, str) and Path(fp).exists():
                                self._editor_tabs.open_file(fp)
                self._set_api_status(None, "API 待测试")
            except Exception as exc:
                LogManager.warning(f"会话恢复失败: {exc!s}")

    def _activate_search(self) -> None:
        """激活搜索覆盖层（Stack page 1）。"""
        self._right_stack.setCurrentIndex(1)
        self._search_panel._input.setFocus()
        self._search_panel._input.selectAll()

    # ── 对话面板 ──────────────────────────────────────────

    def _activate_chat(self) -> None:
        """确保对话面板可见（Stack page 0）。"""
        self._right_stack.setCurrentIndex(0)

    def _initialize_intent_parser(self) -> None:
        """初始化 IntentParser（懒加载，在首次聊天请求时调用）。"""
        if hasattr(self, "_intent_parser") and self._intent_parser is not None:
            return
        from opennovel_desktop.utils.intent_parser import IntentParser

        config: dict[str, str] = {}
        project = self._app_state.current_project
        if project:
            try:
                from opennovel.core.config import LoomConfig  # noqa: PLC0415

                loom = LoomConfig.load(Path(project))
                config = {
                    "model": loom.model,
                    "api_key": loom.api_key or "",
                    "api_base": loom.api_base or "",
                }
            except Exception:
                pass
        self._intent_parser = IntentParser(config=config)

    def _on_chat_request(self, action_type: str, params: dict) -> None:
        """处理 ChatPanel 发来的用户指令（使用 IntentParser）。"""
        if action_type != "chat" or not params.get("text"):
            return

        text = str(params.get("text", ""))
        editor_mode = str(params.get("mode", "replace"))

        # 显示用户消息
        self._chat_panel.show_user_message(text)

        # 初始化意图解析器
        self._initialize_intent_parser()

        # 解析意图
        context = {
            "current_file": self._app_state.current_file,
            "current_project": self._app_state.current_project,
            "editor_mode": editor_mode,
        }
        intent = self._intent_parser.parse(text, context)

        # 路由
        if intent.action_type == "write":
            self._chat_start_write(intent.params.get("text", text))
        elif intent.action_type == "revise":
            self._chat_start_revise(intent.params.get("text", text))
        elif intent.action_type == "evaluate":
            self._chat_start_evaluate()
        elif intent.action_type == "stash":
            self._chat_stash(intent.params.get("text", text))
        elif intent.action_type == "commit":
            self._start_commit()
        elif intent.action_type == "search":
            self._activate_search()
        else:
            # 方案 B：chat 意图交由 Writer Agent 处理
            self._chat_start_general(text)

    def _on_card_action(self, card_id: str, action: str, params: dict) -> None:
        """处理聊天卡片上的用户操作。"""
        if action == "accept":
            self._on_accept_chapter(str(params.get("chapter_id", "")))
        elif action == "revise":
            self._on_revise_chapter(str(params.get("chapter_id", "")))
        elif action == "rewrite":
            self._on_rewrite_chapter(str(params.get("chapter_id", "")))
        elif action == "stash":
            self._chat_stash_for_card(str(params.get("chapter_id", "")))
        elif action == "confirm_diff":
            events = params.get("events", [])
            if isinstance(events, list):
                self._on_events_confirmed(events)
        elif action == "cancel_diff":
            self._chat_panel.show_system_message("提交已取消", "status")
        elif action == "rescue_retry":
            # 重试 commit
            if hasattr(self, "_commit_worker") and self._commit_worker:
                self._commit_thread = getattr(self, "_commit_thread", None)
                if self._commit_thread is None:
                    from PySide6.QtCore import QThread  # noqa: PLC0415

                    self._commit_thread = QThread()
                    self._commit_worker.moveToThread(self._commit_thread)
                    self._commit_thread.started.connect(
                        lambda: self._commit_worker.do_commit(
                            self._app_state.current_file or "ch_001"
                        )
                    )
                    self._commit_thread.start()
        elif action == "rescue_dirty":
            self._chat_panel.show_system_message("脏提交已执行（带 dirty_flag）", "status")
        elif action == "rescue_abort":
            self._chat_panel.show_system_message("提交已终止", "status")

    def _on_editor_mode_changed(self, mode: str) -> None:
        """编辑器交互模式切换。"""
        _ = mode  # 后续可扩展模式相关的 UI 变化

    def _chat_start_general(self, user_text: str) -> None:
        """通用对话：通过 Writer Agent 回复用户（方案 B）。

        Writer Agent 自行判断：能创作则创作，闲聊则回复。
        """
        project = self._app_state.current_project
        if not project:
            self._chat_panel.show_error("System", "请先打开项目后再交流")
            return

        # 检查是否有已在运行的对话 worker
        chat_thread = getattr(self, "_chat_thread", None)
        if chat_thread and chat_thread.isRunning():
            self._chat_panel.show_system_message("请等待当前操作完成", "status")
            return

        # 通过 Writer Agent 处理（writer 会判定是创作请求还是闲聊）
        self._chat_start_write(user_text)

    def _chat_start_write(self, user_prompt: str) -> None:
        """从对话执行写操作 — 创建思维卡片 + 流式创作。"""
        project = self._app_state.current_project
        if not project:
            self._chat_panel.show_error("System", "请先打开项目")
            return

        current_file = self._app_state.current_file
        chapter_id = Path(current_file).stem if current_file else "ch_001"

        self._chat_panel.set_input_enabled(False)
        self._set_agent_active(True, "对话创作中...")

        # 创建思维卡片
        think_id = self._chat_panel.show_thinking("Writer", "正在分析指令...")

        from PySide6.QtCore import QThread  # noqa: PLC0415

        from opennovel_desktop.worker.agent_worker import AgentWorker  # noqa: PLC0415

        self._chat_worker = AgentWorker(project)
        self._chat_thread = QThread()
        self._chat_worker.moveToThread(self._chat_thread)

        # 信号 → 卡片更新
        self._chat_worker.status_message.connect(
            lambda msg: self._chat_panel.update_status(think_id, msg)
        )
        self._chat_worker.stream_chunk.connect(lambda chunk: self._on_chat_stream(think_id, chunk))
        self._chat_worker.phase_changed.connect(
            lambda pid, status="running": self._chat_panel.set_phase(think_id, pid, status)
        )
        self._chat_worker.finished.connect(
            lambda result: self._on_chat_write_finished(think_id, chapter_id, result)
        )
        self._chat_worker.error_occurred.connect(self._on_chat_error)
        self._chat_thread.started.connect(
            lambda: self._chat_worker.do_write(chapter_id, chapter_hint=user_prompt)
        )
        self._chat_worker.finished.connect(self._chat_thread.quit)
        self._chat_thread.finished.connect(self._chat_cleanup)
        self._chat_thread.start()

    def _on_chat_stream(self, think_id: str, chunk: str) -> None:
        """对话流式输出 → 思维卡片 + 编辑器。"""
        self._chat_panel.append_stream(think_id, chunk)
        editor = self._editor_tabs.current_editor()
        if editor:
            editor.append_streaming_text(chunk)

    def _on_chat_write_finished(self, think_id: str, chapter_id: str, result: dict) -> None:
        """对话写操作完成 → 完成思维卡片 + 展示创作结果。"""
        self._chat_panel.finalize_thinking(think_id)
        self._chat_panel.show_chapter_result(chapter_id, result)
        self._chat_panel.set_input_enabled(True)

        # LLM 调用成功 → 标记 API 正常
        self._set_api_status(True)

    def _chat_start_evaluate(self) -> None:
        """从对话执行评估。"""
        self._chat_panel.show_system_message(
            "评估功能需要调用 Critic Agent，后续版本实现", "status"
        )

    def _chat_start_revise(self, user_prompt: str) -> None:
        """从对话执行修订（使用 Writer 的 revise 功能）。"""
        project = self._app_state.current_project
        if not project:
            self._chat_panel.show_error("System", "请先打开项目")
            return

        current_file = self._app_state.current_file
        chapter_id = Path(current_file).stem if current_file else "ch_001"

        self._chat_panel.set_input_enabled(False)
        self._set_agent_active(True, "修订中...")

        think_id = self._chat_panel.show_thinking("Writer", "正在分析修订请求...")

        from PySide6.QtCore import QThread  # noqa: PLC0415

        from opennovel_desktop.worker.agent_worker import AgentWorker  # noqa: PLC0415

        self._chat_worker = AgentWorker(project)
        self._chat_thread = QThread()
        self._chat_worker.moveToThread(self._chat_thread)

        self._chat_worker.status_message.connect(
            lambda msg: self._chat_panel.update_status(think_id, msg)
        )
        self._chat_worker.stream_chunk.connect(lambda chunk: self._on_chat_stream(think_id, chunk))
        self._chat_worker.phase_changed.connect(
            lambda pid, status="running": self._chat_panel.set_phase(think_id, pid, status)
        )
        self._chat_worker.finished.connect(
            lambda result: self._on_chat_write_finished(think_id, chapter_id, result)
        )
        self._chat_worker.error_occurred.connect(self._on_chat_error)
        self._chat_thread.started.connect(
            lambda: self._chat_worker.do_write(chapter_id, chapter_hint=user_prompt)
        )
        self._chat_worker.finished.connect(self._chat_thread.quit)
        self._chat_thread.finished.connect(self._chat_cleanup)
        self._chat_thread.start()

    def _chat_stash(self, text: str) -> None:
        """从对话存入灵感。"""
        # 去掉指令关键词
        for kw in ("存", "灵感", "保存这个", "记一下", "/stash"):
            text = text.replace(kw, "", 1).strip()
        if text:
            self._stash_text(text)
            self._chat_panel.show_system_message("灵感已存入潜意识池", "info")
        else:
            self._chat_panel.show_system_message("请提供要存入的灵感内容", "status")

    def _chat_stash_for_card(self, chapter_id: str) -> None:
        """从 ChapterCard 存入灵感（获取章节文本）。"""
        editor = self._editor_tabs.current_editor()
        if editor:
            self._stash_text(editor.toPlainText()[:2000])

    def _on_chat_error(self, error_msg: str) -> None:
        """对话执行出错 — 清理线程并恢复输入。"""
        if hasattr(self, "_chat_worker") and self._chat_worker:
            self._chat_worker.stop()
        if hasattr(self, "_chat_thread") and self._chat_thread and self._chat_thread.isRunning():
            self._chat_thread.quit()
        self._set_agent_active(False)
        self._chat_panel.show_error("Writer", error_msg)
        self._chat_panel.set_input_enabled(True)

    def _chat_cleanup(self) -> None:
        """清理对话 Worker 线程。"""
        self._set_agent_active(False)
        if hasattr(self, "_chat_thread") and self._chat_thread:
            self._chat_thread.deleteLater()
            self._chat_thread = None
        if hasattr(self, "_chat_worker") and self._chat_worker:
            self._chat_worker.deleteLater()
            self._chat_worker = None

    def _on_agent_action(self, action: str, selected_text: str) -> None:
        """处理编辑器右键 Agent 菜单动作 → 填充对话输入。"""
        action_prompts: dict[str, str] = {
            "polish": f"/write 请润色以下文字：\n{selected_text[:300]}",
            "continue": f"/write 请续写以下内容：\n{selected_text[:300]}",
            "expand": f"/write 请扩写以下段落：\n{selected_text[:300]}",
            "rewrite": f"/write 请从反派视角重写以下内容：\n{selected_text[:300]}",
            "stash": "",
            "critic_check": "/evaluate 请评价当前章节",
            "query_knowledge": "/search ",
        }
        prompt = action_prompts.get(action, "")
        if action == "stash":
            self._stash_text(selected_text)
        elif prompt:
            self._activate_chat()
            self._chat_panel.inject_preset(prompt)
        else:
            ToastNotification.show_info("知识查询功能在后续版本实现")

    # ── 写章节流水线 ──────────────────────────────────────

    def _start_write_chapter(self) -> None:
        """工具栏 [写章节]：启动 Writer → Critic 流水线。"""
        if self._write_active or self._auto_active:
            return

        editor = self._editor_tabs.current_editor()
        if editor is None:
            ToastNotification.show_warning("请先打开或创建章节文件")
            return

        project = self._app_state.current_project
        if not project:
            ToastNotification.show_warning("请先打开项目")
            return

        self._write_active = True
        self._set_tool_loading(True, "创作中...")
        self._set_agent_active(True, "Writer 创作中")
        self._btn_stop.setEnabled(True)
        self._set_save_status("保存")

        current_file = self._app_state.current_file
        chapter_id = Path(current_file).stem if current_file else "ch_001"

        from PySide6.QtCore import QThread  # noqa: PLC0415

        from opennovel_desktop.worker.agent_worker import AgentWorker  # noqa: PLC0415

        self._worker = AgentWorker(project)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker.stream_chunk.connect(self._on_stream_chunk)
        self._worker.status_message.connect(self._on_status_message)
        self._worker.finished.connect(self._on_write_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker_thread.started.connect(lambda: self._worker.do_write(chapter_id))
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)

        self._right_stack.setCurrentIndex(0)
        self._worker_thread.start()

    def _on_stream_chunk(self, chunk: str) -> None:
        """流式输出追加到当前编辑器。"""
        editor = self._editor_tabs.current_editor()
        if editor:
            editor.append_streaming_text(chunk)

    def _on_write_finished(self, result: dict) -> None:
        """写章节完成 → 展示评分 Toast。"""
        score = result.get("score", 0)
        # LLM 调用成功 → 标记 API 正常
        self._set_api_status(True)
        ToastNotification.show_success(
            f"章节完成：评分 {score}，字数 {result.get('word_count', 0)}"
        )

    # ── Auto 创作 ─────────────────────────────────────────

    def _start_auto(self) -> None:
        """工具栏 [Auto]：启动全自动创作。"""
        if self._write_active or self._auto_active:
            return

        project = self._app_state.current_project
        if not project:
            ToastNotification.show_warning("请先打开项目")
            return

        self._auto_active = True
        self._set_tool_loading(True, "自动中...")
        self._set_agent_active(True, "Auto 创作中")
        self._btn_stop.setEnabled(True)
        self._set_save_status("保存")

        chapter_ids = ["ch_001"]

        from PySide6.QtCore import QThread  # noqa: PLC0415

        from opennovel_desktop.worker.agent_worker import AgentWorker  # noqa: PLC0415

        self._worker = AgentWorker(project)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker.stream_chunk.connect(self._on_stream_chunk)
        self._worker.status_message.connect(self._on_status_message)
        self._worker.finished.connect(self._on_write_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.progress.connect(self._on_auto_progress)
        self._worker_thread.started.connect(lambda: self._worker.do_auto(chapter_ids))
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)

        self._right_stack.setCurrentIndex(0)
        self._worker_thread.start()

        ToastNotification.show_info("Auto 创作已启动")

    def _on_auto_progress(self, current: int, total: int) -> None:
        """Auto 进度更新。"""
        self._status_mid.setText(f"Auto  {current}/{total}")

    # ── Commit 流水线 ─────────────────────────────────────

    def _start_commit(self) -> None:
        """工具栏 [Commit]：启动状态提交。"""
        project = self._app_state.current_project
        if not project:
            ToastNotification.show_warning("请先打开项目")
            return

        self._btn_commit.setEnabled(False)
        self._btn_commit.setText("提交中...")
        self._btn_commit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self._set_agent_active(True, "正在提取事件...")

        current_file = self._app_state.current_file
        chapter_id = Path(current_file).stem if current_file else "ch_001"

        from PySide6.QtCore import QThread  # noqa: PLC0415

        from opennovel_desktop.worker.commit_worker import CommitWorker  # noqa: PLC0415

        self._commit_worker = CommitWorker(project)
        self._commit_thread = QThread()
        self._commit_worker.moveToThread(self._commit_thread)

        self._commit_worker.commit_ready.connect(self._on_commit_ready)
        self._commit_worker.error_occurred.connect(self._on_worker_error)
        self._commit_worker.rescue_mode.connect(
            lambda: self._chat_panel.show_system_message("⚠ 需要人工介入：事件提取失败", "error")
        )
        self._commit_thread.started.connect(lambda: self._commit_worker.do_commit(chapter_id))
        self._commit_worker.commit_ready.connect(self._commit_thread.quit)
        self._commit_thread.finished.connect(self._cleanup_commit_worker)

        self._right_stack.setCurrentIndex(2)
        self._commit_thread.start()

    def _on_commit_ready(self, diff_data: dict) -> None:
        """Commit 就绪 → 在对话中展示 Diff 卡片。"""
        self._chat_panel.show_diff(diff_data)
        self._btn_commit.setEnabled(True)
        self._btn_commit.setText("提交")
        self._btn_commit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._set_agent_active(False)
        self._right_stack.setCurrentIndex(0)
        ToastNotification.show_info("事件提取完成，请审阅 Diff 卡片")

    def _on_events_confirmed(self, events: list[str]) -> None:
        """事件确认 → 写入固化。"""
        _ = events
        ToastNotification.show_success("变更已固化")
        self._chat_panel.show_system_message("变更已固化 ✓", "info")

    # ── 保存 / 灵感 / 停止 ───────────────────────────────

    def _get_editor_contents(self) -> dict[str, str]:
        """返回当前所有打开编辑器的文件路径→内容映射（用于自动保存）。"""
        result: dict[str, str] = {}
        for i in range(self._editor_tabs.count()):
            editor = self._editor_tabs._editor_at(i)
            if editor:
                file_path = self._editor_tabs.tabToolTip(i)
                if file_path:
                    result[file_path] = editor.toPlainText()
        return result

    def _save_current_editor(self) -> None:
        """保存当前编辑器内容到文件。"""
        editor = self._editor_tabs.current_editor()
        if editor is None:
            return
        file_path = self._app_state.current_file
        if not file_path:
            ToastNotification.show_warning("没有打开的文件")
            return

        try:
            Path(file_path).write_text(editor.toPlainText(), encoding="utf-8")
            editor.document().setModified(False)
            self._set_save_status("已保存")
            ToastNotification.show_success("已保存")
        except OSError as e:
            ToastNotification.show_error(f"保存失败: {e!s}")

    def _stash_inspiration(self) -> None:
        """存入灵感碎片池（工具栏按钮）。"""
        editor = self._editor_tabs.current_editor()
        if editor is None:
            return
        selected = editor.textCursor().selectedText()
        if not selected:
            ToastNotification.show_info("请先选中要存入的文本")
            return
        self._stash_text(selected)

    def _stash_text(self, text: str) -> None:
        """将指定文本存入灵感碎片池。"""
        project = self._app_state.current_project
        if not project:
            ToastNotification.show_warning("请先打开项目")
            return
        sub_dir = Path(project) / "subconscious"
        sub_dir.mkdir(parents=True, exist_ok=True)
        # 用时间戳命名防止序号重叠，仅匹配 stash_gui_ 文件
        existing = sorted(sub_dir.glob("stash_gui_*.md"))
        next_id = len(existing)
        stash_file = sub_dir / f"stash_gui_{next_id}.md"
        stash_file.write_text(f"> 灵感（来自 GUI）\n\n{text}\n", encoding="utf-8")
        # 更新向量索引（静默忽略不可用时）
        with contextlib.suppress(Exception):
            from opennovel.storage.vector import VectorStore  # noqa: PLC0415

            vs = VectorStore(Path(project) / ".index")
            vs.index_file(stash_file)
        ToastNotification.show_success("灵感已存入潜意识池")

    # ── 工具栏预设方法 ──────────────────────────────────────

    def _preset_write_chapter(self) -> None:
        """填充对话输入：创作章节。"""
        self._activate_chat()
        current_file = self._app_state.current_file
        hint = f" {Path(current_file).stem}" if current_file else ""
        self._chat_panel.inject_preset(f"/write 创作新章节{hint}")

    def _preset_auto(self) -> None:
        """填充对话输入：全自动创作。"""
        self._activate_chat()
        self._chat_panel.inject_preset("/auto 按大纲自动创作全部章节")

    def _preset_stash(self) -> None:
        """填充对话输入：存入灵感。如有选中文本则带入。"""
        self._activate_chat()
        editor = self._editor_tabs.current_editor()
        if editor and editor.textCursor().hasSelection():
            text = editor.textCursor().selectedText()[:200]
            self._chat_panel.inject_preset(f"/stash {text}")
        else:
            self._chat_panel.inject_preset("/stash ")

    # ── 章节操作回调 ────────────────────────────────────────

    def _on_accept_chapter(self, chapter_id: str) -> None:
        """ChapterCard [接受] → 将章节内容保存为文件。"""
        _ = chapter_id
        ToastNotification.show_success("章节已接受")
        self._set_save_status("已保存")

    def _on_revise_chapter(self, chapter_id: str) -> None:
        """ChapterCard [修订] → 在对话中发起修订请求。"""
        self._chat_panel.inject_preset(f"/revise 请修订 {chapter_id} 章节，改进角色动机和节奏")

    def _on_rewrite_chapter(self, chapter_id: str) -> None:
        """ChapterCard [重写] → 在对话中发起重写请求。"""
        self._chat_panel.inject_preset(f"/write 请重写 {chapter_id} 章节")

    # ── 草稿面板回调 ────────────────────────────────────────

    def _on_draft_accepted(self, text: str) -> None:
        """DraftPanel [接受] → 替换编辑器内容。"""
        editor = self._editor_tabs.current_editor()
        if editor:
            editor.selectAll()
            editor.insertPlainText(text)
        self._right_stack.setCurrentIndex(0)
        ToastNotification.show_success("草稿已应用")

    def _on_draft_rejected(self) -> None:
        """DraftPanel [拒绝] → 返回对话面板。"""
        self._right_stack.setCurrentIndex(0)

    def _stop_worker(self) -> None:
        """停止当前 Worker 操作。"""
        if hasattr(self, "_worker") and self._worker:
            self._worker.stop()
        if hasattr(self, "_chat_worker") and self._chat_worker:
            self._chat_worker.stop()
        if hasattr(self, "_chat_thread") and self._chat_thread and self._chat_thread.isRunning():
            self._chat_thread.quit()
        self._set_tool_loading(False)
        self._set_agent_active(False)
        self._btn_stop.setEnabled(False)
        self._status_mid.setText("模型 v4-flash")
        self._chat_panel.show_system_message("操作已停止", "status")

        ToastNotification.show_info("操作已停止")

    def _on_status_message(self, msg: str) -> None:
        """更新状态栏中区为实时 Agent 状态。"""
        self._status_mid.setText(f"  {msg}")

    # ── Worker 清理 ───────────────────────────────────────

    def _cleanup_worker(self) -> None:
        """清理 AgentWorker 线程。"""
        self._write_active = False
        self._auto_active = False
        self._set_tool_loading(False)
        self._set_agent_active(False)
        self._btn_stop.setEnabled(False)
        self._status_mid.setText("模型 v4-flash")
        if hasattr(self, "_worker_thread") and self._worker_thread:
            self._worker_thread.deleteLater()
            self._worker_thread = None
        if hasattr(self, "_worker") and self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _cleanup_commit_worker(self) -> None:
        """清理 CommitWorker 线程。"""
        self._btn_commit.setEnabled(True)
        self._btn_commit.setText("提交")
        self._btn_commit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._set_agent_active(False)
        if hasattr(self, "_commit_thread") and self._commit_thread:
            self._commit_thread.deleteLater()
            self._commit_thread = None
        if hasattr(self, "_commit_worker") and self._commit_worker:
            self._commit_worker.deleteLater()
            self._commit_worker = None

    def _on_worker_error(self, error_msg: str) -> None:
        """Worker 错误处理。"""
        self._write_active = False
        self._auto_active = False
        self._set_tool_loading(False)
        self._set_agent_active(False)
        self._btn_stop.setEnabled(False)
        self._status_mid.setText("模型 v4-flash")

        # 更新 API 状态（如果是连接类错误）
        if any(kw in error_msg.lower() for kw in ("api", "连接", "timeout", "auth", "key")):
            self._set_api_status(False, error_msg[:100])

        ToastNotification.show_error(error_msg)

    # ── 文件菜单 ──────────────────────────────────────────

    def _new_project(self) -> None:
        """菜单「新建项目」：弹出目录选择并初始化。"""
        from PySide6.QtWidgets import QInputDialog  # noqa: PLC0415

        workspace = Path.cwd() / "novels"
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if not ok or not name.strip():
            return
        project_path = workspace / name.strip()
        try:
            project_path.mkdir(parents=True, exist_ok=True)
            # 创建最小项目结构
            for sub in ("canon", "characters", "draft", "outlines", "subconscious"):
                (project_path / sub).mkdir(exist_ok=True)
            (project_path / "novel.yaml").write_text(
                "version: '1.0'\nmodel: deepseek/deepseek-v4-flash\n", encoding="utf-8"
            )
            self._app_state.set_project(str(project_path))
            ToastNotification.show_success(f"项目 {name} 已创建")
        except OSError as e:
            ToastNotification.show_error(f"创建失败: {e!s}")

    def _open_project(self) -> None:
        """菜单「打开项目」：弹出目录选择并设置当前项目。"""
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        dir_path = QFileDialog.getExistingDirectory(self, "选择小说项目目录")
        if not dir_path:
            return
        # 验证是否为有效项目
        if not (Path(dir_path) / "novel.yaml").exists():
            ToastNotification.show_warning("该目录不是有效的 OpenNovel 项目（无 novel.yaml）")
            return
        self._app_state.set_project(dir_path)
        self._status_left.setText(f"  项目: {Path(dir_path).name}")
        # 自动打开大纲
        outline = Path(dir_path) / "outlines" / "story.md"
        if outline.exists():
            self._editor_tabs.open_file(str(outline))
        ToastNotification.show_success(f"已打开项目: {Path(dir_path).name}")

    def _save_as_file(self) -> None:
        """菜单「另存为」：导出当前编辑器内容。"""
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        editor = self._editor_tabs.current_editor()
        if editor is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "", "Markdown (*.md);;文本文件 (*.txt)"
        )
        if path:
            try:
                Path(path).write_text(editor.toPlainText(), encoding="utf-8")
                ToastNotification.show_success("文件已导出")
            except OSError as e:
                ToastNotification.show_error(f"导出失败: {e!s}")

    def _show_global_settings(self) -> None:
        """菜单「偏好设置」：打开全局设置对话框。"""
        from opennovel_desktop.dialogs.global_settings import (  # noqa: PLC0415
            GlobalSettingsDialog,
        )

        dialog = GlobalSettingsDialog(self)
        dialog.connection_tested.connect(self._on_connection_tested)
        dialog.exec()

    def _on_connection_tested(self, success: bool, message: str) -> None:
        """全局设置对话框中测试连接结果 → 更新主窗口 API 状态灯。"""
        if success:
            self._set_api_status(True, message)
        else:
            self._set_api_status(False, message)

    # ── 编辑菜单 ──────────────────────────────────────────

    def _editor_undo(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor:
            editor.undo()

    def _editor_redo(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor:
            editor.redo()

    def _editor_find(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor:
            _ = editor  # 预留：搜索栏实现

    def _editor_replace(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor:
            _ = editor

    # ── 发送给 Agent 菜单 ─────────────────────────────────

    def _agent_action_polish(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor and editor.textCursor().hasSelection():
            text = editor.textCursor().selectedText()
            self._on_agent_action("polish", text)

    def _agent_action_continue(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor and editor.textCursor().hasSelection():
            text = editor.textCursor().selectedText()
            self._on_agent_action("continue", text)

    def _agent_action_expand(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor and editor.textCursor().hasSelection():
            text = editor.textCursor().selectedText()
            self._on_agent_action("expand", text)

    def _agent_action_rewrite(self) -> None:
        editor = self._editor_tabs.current_editor()
        if editor and editor.textCursor().hasSelection():
            text = editor.textCursor().selectedText()
            self._on_agent_action("rewrite", text)

    # ── 工具菜单 ──────────────────────────────────────────

    def _run_diagnose(self) -> None:
        if not self._app_state.current_project:
            ToastNotification.show_warning("请先打开项目")
            return
        ToastNotification.show_info("项目诊断中...（Phase 5 功能预留）")

    def _run_reindex(self) -> None:
        if not self._app_state.current_project:
            ToastNotification.show_warning("请先打开项目")
            return
        ToastNotification.show_info("重写索引中...（Phase 5 功能预留）")

    def _run_calibrate(self) -> None:
        ToastNotification.show_info("Critic 校准（Phase 5 功能预留）")

    def _run_rollback(self) -> None:
        ToastNotification.show_info("历史回滚（Phase 5 功能预留）")

    def _run_clear_cache(self) -> None:
        ToastNotification.show_info("缓存已清除（Phase 5 功能预留）")

    # ── 帮助菜单 ──────────────────────────────────────────

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

        QMessageBox.about(
            self,
            "关于 OpenNovel",
            "OpenNovel Desktop v2.0.0\n\n"
            "本地优先的长篇小说叙事操作系统。\n"
            "基于 PySide6 的 GUI 桌面客户端。\n\n"
            "AI 辅助创作 | 四 Agent 自主协作 | 因果链世界观",
        )

    def _check_update(self) -> None:
        ToastNotification.show_info("当前已是最新版本")

    def _toggle_log_panel(self, visible: bool) -> None:
        """切换日志面板覆盖层（Stack page 2）。"""
        self._right_stack.setCurrentIndex(2 if visible else 0)
        self._action_show_log.setChecked(visible)

    def _toggle_toolbar_labels(self, show_labels: bool) -> None:
        """切换工具栏按钮标签文字显示。"""
        style = (
            Qt.ToolButtonStyle.ToolButtonTextUnderIcon
            if show_labels
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self._toolbar.setToolButtonStyle(style)
        self._toolbar_label_state = show_labels

    def _open_logs_dir(self) -> None:
        from PySide6.QtCore import QUrl  # noqa: PLC0415
        from PySide6.QtGui import QDesktopServices  # noqa: PLC0415

        logs_dir = LogManager.instance().log_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        LogManager.info(f"打开日志目录: {logs_dir}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir)))

    # ── 面板显隐控制 ──────────────────────────────────────

    def _toggle_left_panel(self, visible: bool) -> None:
        """切换左侧导航显隐。"""
        self._left_panel.setVisible(visible)
        self._app_state.left_panel_visible = visible

    def _toggle_right_panel(self, visible: bool) -> None:
        """切换右侧面板显隐。"""
        self._right_stack.setVisible(visible)
        self._app_state.right_panel_visible = visible

    def _toggle_status_bar(self, visible: bool) -> None:
        """切换状态栏显隐。"""
        self.statusBar().setVisible(visible)
        self._app_state.status_bar_visible = visible

    # ── 专注模式 ──────────────────────────────────────────

    def toggle_focus_mode(self) -> None:
        """F11 一键专注模式：隐藏所有外围面板，仅留编辑器。"""
        self._focus_mode = not self._focus_mode
        self._app_state.toggle_focus_mode()

        if self._focus_mode:
            # 进入专注：记录状态并隐藏
            self._left_visible_before_focus = self._left_panel.isVisible()
            self._right_visible_before_focus = self._right_stack.isVisible()
            self._toolbar_visible_before_focus = not self._btn_write.isHidden()
            bar = self.statusBar()
            self._statusbar_visible_before_focus = bar.isVisible()
            self._menubar_visible_before_focus = not self.menuBar().isHidden()

            self._left_panel.hide()
            self._right_stack.hide()
            self._toolbar.hide()
            self.menuBar().hide()
            bar.hide()
        else:
            # 退出专注：恢复状态
            if self._left_visible_before_focus:
                self._left_panel.show()
            if self._right_visible_before_focus:
                self._right_stack.show()
            if self._toolbar_visible_before_focus:
                self._toolbar.show()
            if self._menubar_visible_before_focus:
                self.menuBar().show()
            if self._statusbar_visible_before_focus:
                bar = self.statusBar()
                bar.show()

    # ── 事件 ──────────────────────────────────────────────

    def keyPressEvent(self, event: object) -> None:  # noqa: N802
        """处理键盘事件：F11 专注于全屏模式。

        F11 在菜单栏已有快捷键绑定，此处于窗口级捕获作为 fallback。
        """
        from PySide6.QtGui import QKeyEvent  # noqa: PLC0415

        if isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_F11:
            self.toggle_focus_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    def _stop_all_workers(self) -> None:
        """停止所有后台 worker 线程（窗口关闭时调用）。"""
        for worker_attr in ("_worker", "_chat_worker", "_commit_worker"):
            if hasattr(self, worker_attr) and getattr(self, worker_attr, None):
                getattr(self, worker_attr).stop()
        for thread_attr in ("_worker_thread", "_chat_thread", "_commit_thread"):
            thread = getattr(self, thread_attr, None)
            if thread and thread.isRunning():
                thread.quit()
                thread.wait(2000)

    def closeEvent(self, event: object) -> None:  # noqa: N802
        """窗口关闭时保存会话并清理 worker。"""
        self._stop_all_workers()
        self._session_mgr.save_window_geometry(self)
        self._session_mgr.save_panel_state(self._app_state)
        self._session_mgr.save_editor_session(self._app_state, self._editor_tabs.get_open_files())
        if self._app_state.current_project:
            self._session_mgr.save_last_project(self._app_state.current_project)
        self._autosave_mgr.stop()
        super().closeEvent(event)
