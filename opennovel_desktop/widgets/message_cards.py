"""MessageCard — 对话消息卡片组件。

三种卡片类型内联在聊天流中：
- ThinkingCard: Agent 思考过程（可折叠 + 流式追加 + 阶段指示器）
- ChapterCard: 创作结果（五维评分 + [接受][修订][重写][存入灵感]）
- DiffCard: Commit 审阅（文件/事件列表 + [确认提交][取消]）
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ── 发送者颜色映射 ──────────────────────────────────────────
SENDER_COLORS: dict[str, str] = {
    "System": "#6B6863",
    "User": "#4A7C5B",
    "Writer": "#5A7C9A",
    "Critic": "#C4913A",
    "Manager": "#8B5CF6",
    "Director": "#B85C4A",
}
SENDER_LABELS: dict[str, str] = {
    "System": "系统",
    "User": "我",
    "Writer": "Writer",
    "Critic": "Critic",
    "Manager": "Manager",
    "Director": "Director",
}
PHASE_NAMES: dict[str, str] = {
    "think": "规划",
    "write": "创作",
    "evaluate": "评估",
    "update": "更新",
}
PHASE_COLORS: dict[str, str] = {
    "pending": "#C8C6C2",
    "running": "#5A7C9A",
    "passed": "#4A7C5B",
    "warning": "#C4913A",
    "failed": "#B85C4A",
}
PHASE_ICONS: dict[str, str] = {
    "pending": "○",
    "running": "●",
    "passed": "✓",
    "warning": "⚠",
    "failed": "✕",
}


# ── 评分条组件 ──────────────────────────────────────────────


class _ScoreBar(QWidget):
    """单维度评分条。标签 + 进度条 + 分数文本。"""

    def __init__(self, label: str, score: int, max_score: int = 20) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        name = QLabel(label)
        name.setFixedWidth(48)
        name.setStyleSheet("font-size: 12px; color: #6B6863;")
        layout.addWidget(name)

        bar = QProgressBar()
        bar.setFixedHeight(8)
        bar.setMaximum(max_score)
        bar.setValue(score)
        bar.setTextVisible(False)
        ratio = score / max_score
        if ratio < 0.5:
            bar.setStyleSheet(
                "QProgressBar { background: #EDE9E4; border-radius: 3px; }"
                "QProgressBar::chunk { background: #B85C4A; border-radius: 3px; }"
            )
        elif ratio < 0.7:
            bar.setStyleSheet(
                "QProgressBar { background: #EDE9E4; border-radius: 3px; }"
                "QProgressBar::chunk { background: #C4913A; border-radius: 3px; }"
            )
        else:
            bar.setStyleSheet(
                "QProgressBar { background: #EDE9E4; border-radius: 3px; }"
                "QProgressBar::chunk { background: #4A7C5B; border-radius: 3px; }"
            )
        layout.addWidget(bar, 1)

        score_label = QLabel(f"{score}/{max_score}")
        score_label.setFixedWidth(40)
        score_label.setStyleSheet("font-size: 12px; color: #6B6863;")
        layout.addWidget(score_label)


# ── 基类 ────────────────────────────────────────────────────


class MessageCard(QFrame):
    """聊天消息卡片基类。

    提供：header（发送者 + 时间戳 + 折叠按钮）、body（子类填充）、
    footer（操作按钮区），以及可选的折叠/展开动画。
    """

    def __init__(
        self,
        card_id: str,
        sender: str,
        sender_color: str = "#6B6863",
        collapsible: bool = False,
    ) -> None:
        super().__init__()
        self._card_id = card_id
        self._sender = sender
        self._sender_color = sender_color
        self._collapsible = collapsible
        self._collapsed = False

        self.setProperty("card_id", card_id)
        self.setObjectName("MessageCard")

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # 左侧颜色条 + 内容区
        content = QFrame()
        content.setStyleSheet(
            f"""
            #MessageCard > QFrame {{
                border-left: 3px solid {sender_color};
                border-radius: 4px;
                background: palette(window);
                margin: 2px 0;
            }}
            """
        )

        inner = QVBoxLayout(content)
        inner.setContentsMargins(8, 4, 8, 4)
        inner.setSpacing(6)

        # Header
        self._header = self._build_header(sender, sender_color)
        inner.addWidget(self._header)

        # Body（子类填充）
        self._body_container = QVBoxLayout()
        self._body_container.setContentsMargins(4, 0, 4, 0)
        inner.addLayout(self._body_container)

        # Footer（子类填充）
        self._footer_container = QVBoxLayout()
        self._footer_container.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(self._footer_container)

        self._main_layout.addWidget(content)

    # ── Header ──────────────────────────────────────────────

    def _build_header(self, sender: str, color: str) -> QWidget:
        header = QFrame()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = SENDER_LABELS.get(sender, sender)
        name = QLabel(f"<b style='color:{color};'>{label}</b>")
        name.setStyleSheet("font-size: 12px;")
        layout.addWidget(name)

        layout.addStretch()

        if self._collapsible:
            self._collapse_btn = QPushButton("▼")
            self._collapse_btn.setFixedSize(20, 20)
            self._collapse_btn.setObjectName("SecondaryButton")
            self._collapse_btn.setStyleSheet("font-size: 10px; padding: 0; min-height: 18px;")
            self._collapse_btn.clicked.connect(self._toggle_collapse)
            layout.addWidget(self._collapse_btn)

        return header

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        # 隐藏/显示 body 和 footer 中的所有子 widget
        for i in range(self._body_container.count()):
            w = self._body_container.itemAt(i)
            if w and w.widget():
                w.widget().setVisible(not self._collapsed)
        for i in range(self._footer_container.count()):
            w = self._footer_container.itemAt(i)
            if w and w.widget():
                w.widget().setVisible(not self._collapsed)
        self._collapse_btn.setText("▶" if self._collapsed else "▼")

    # ── Body / Footer setter ────────────────────────────────

    def _set_body_widget(self, widget: QWidget) -> None:
        self._body_container.addWidget(widget)

    def _set_body_layout(self, layout: QHBoxLayout | QVBoxLayout) -> None:
        self._body_container.addLayout(layout)

    def _set_footer_widget(self, widget: QWidget) -> None:
        self._footer_container.addWidget(widget)

    def _clear_body(self) -> None:
        while self._body_container.count():
            item = self._body_container.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _clear_footer(self) -> None:
        while self._footer_container.count():
            item = self._footer_container.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    @property
    def card_id(self) -> str:
        return self._card_id


# ── 思维链卡片 ──────────────────────────────────────────────


class ThinkingCard(MessageCard):
    """Agent 思考过程卡片（可折叠）。

    布局：
    ┌──────────────────────────────────────────┐
    │ ● Writer   思考中...            [▼]     │
    ├──────────────────────────────────────────┤
    │ 正在分析章节上下文，规划大纲...              │
    │                                          │
    │ ○ 规划  ● 创作  ○ 评估                    │
    └──────────────────────────────────────────┘
    """

    def __init__(self, card_id: str, sender: str = "Writer") -> None:
        color = SENDER_COLORS.get(sender, "#5A7C9A")
        super().__init__(card_id, sender, sender_color=color, collapsible=True)

        # Status 文本（header 副标题）
        self._status_label = QLabel("思考中...")
        self._status_label.setStyleSheet("font-size: 11px; color: #A8A49E;")
        layout = self._header.layout()
        if layout:
            layout.insertWidget(1, self._status_label)

        # Body：流式文本显示区域
        self._body_text = QPlainTextEdit()
        self._body_text.setReadOnly(True)
        self._body_text.setMaximumHeight(200)
        self._body_text.setStyleSheet(
            "font-size: 13px; line-height: 1.6; border: none; background: transparent;"
        )
        mono_font = QFont("monospace", 10)
        mono_font.setFamilies(["JetBrains Mono", "Consolas", "monospace"])
        self._body_text.setFont(mono_font)
        self._set_body_widget(self._body_text)

        # Footer：阶段指示器
        phase_bar = QFrame()
        phase_layout = QHBoxLayout(phase_bar)
        phase_layout.setContentsMargins(0, 2, 0, 2)
        phase_layout.setSpacing(12)

        self._phase_indicators: dict[str, QLabel] = {}
        for pid in ("think", "write", "evaluate"):
            label = QLabel(f"○ {PHASE_NAMES.get(pid, pid)}")
            label.setStyleSheet("font-size: 11px; color: #C8C6C2;")
            phase_layout.addWidget(label)
            self._phase_indicators[pid] = label

        phase_layout.addStretch()
        self._set_footer_widget(phase_bar)

        self._finalized: bool = False

    # ── Streaming API ───────────────────────────────────────

    def append_text(self, chunk: str) -> None:
        """追加流式文本。"""
        if self._finalized:
            return
        cursor = self._body_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self._body_text.setTextCursor(cursor)
        self._body_text.ensureCursorVisible()

    def update_status(self, msg: str) -> None:
        """更新 header 状态文本。"""
        self._status_label.setText(msg)

    def set_phase(self, phase_id: str, status: str) -> None:
        """更新阶段指示器。"""
        indicator = self._phase_indicators.get(phase_id)
        if indicator is None:
            return
        icon = PHASE_ICONS.get(status, "○")
        color = PHASE_COLORS.get(status, "#C8C6C2")
        name = PHASE_NAMES.get(phase_id, phase_id)
        indicator.setText(f"{icon} {name}")
        indicator.setStyleSheet(f"font-size: 11px; color: {color};")

    def finalize(self) -> None:
        """标记思考完成。"""
        self._finalized = True
        self.update_status("完成")
        # 折叠 body 区域以节省空间
        self._body_text.setMaximumHeight(80)


# ── 创作结果卡片 ────────────────────────────────────────────


class ChapterCard(MessageCard):
    """章节创作结果卡片。

    布局：
    ┌──────────────────────────────────────────┐
    │ ● Writer   创作完成             [▼]     │
    ├──────────────────────────────────────────┤
    │ ┌─ 五维评分 ───────────────────────────┐ │
    │ │ 连贯性  ████████░░ 16/20            │ │
    │ │ ...（5 个维度）                      │ │
    │ └─────────────────────────────────────┘ │
    │ 字数: 2,456                              │
    │                                          │
    │ [接受] [修订] [重写] [存入灵感]            │
    └──────────────────────────────────────────┘
    """

    accept_requested = Signal(str)
    revise_requested = Signal(str)
    rewrite_requested = Signal(str)
    stash_requested = Signal(str)

    DIMENSION_NAMES: dict[str, str] = {
        "narration": "叙事",
        "character": "角色",
        "pacing": "节奏",
        "dialogue": "对白",
        "logic": "逻辑",
        "coherence": "连贯性",
        "style": "风格",
        "creativity": "创意",
        "emotion": "情感",
        "thematic": "主题",
    }

    def __init__(self, card_id: str, chapter_id: str, sender: str = "Writer") -> None:
        color = SENDER_COLORS.get(sender, "#5A7C9A")
        super().__init__(card_id, sender, sender_color=color, collapsible=True)
        self._chapter_id = chapter_id

        # 摘要标签
        self._summary_label = QLabel("字数: —")
        self._summary_label.setStyleSheet("font-size: 12px; color: #6B6863; padding: 2px 0;")
        self._set_body_widget(self._summary_label)

        # 评分区
        self._scores_layout = QVBoxLayout()
        self._scores_layout.setContentsMargins(0, 0, 0, 0)
        self._scores_layout.setSpacing(2)
        self._set_body_layout(self._scores_layout)

        # 文本预览
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setMaximumHeight(60)
        self._preview.setStyleSheet(
            "font-size: 12px; color: #A8A49E; font-style: italic; padding: 4px 0;"
        )
        self._preview.hide()
        self._set_body_widget(self._preview)

        # Footer：操作按钮
        btn_bar = QFrame()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(8)

        self._btn_accept = QPushButton("✓ 接受")
        self._btn_accept.setObjectName("AcceptButton")
        self._btn_accept.clicked.connect(lambda: self.accept_requested.emit(self._chapter_id))
        btn_layout.addWidget(self._btn_accept)

        self._btn_revise = QPushButton("修订")
        self._btn_revise.setObjectName("SecondaryButton")
        self._btn_revise.clicked.connect(lambda: self.revise_requested.emit(self._chapter_id))
        btn_layout.addWidget(self._btn_revise)

        self._btn_rewrite = QPushButton("重写")
        self._btn_rewrite.setObjectName("SecondaryButton")
        self._btn_rewrite.clicked.connect(lambda: self.rewrite_requested.emit(self._chapter_id))
        btn_layout.addWidget(self._btn_rewrite)

        self._btn_stash = QPushButton("存入灵感")
        self._btn_stash.setObjectName("SecondaryButton")
        self._btn_stash.clicked.connect(lambda: self.stash_requested.emit(self._chapter_id))
        btn_layout.addWidget(self._btn_stash)

        btn_layout.addStretch()
        self._set_footer_widget(btn_bar)

    # ── 数据填充 ────────────────────────────────────────────

    def set_scores(self, scores: dict[str, int] | dict[str, object], max_score: int = 20) -> None:
        """设置五维评分。scores 可以是 {key: int} 或 {key: {score, max, label}}。"""
        # 清空已有评分条
        while self._scores_layout.count():
            item = self._scores_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for key, val in scores.items():
            if isinstance(val, dict):
                score_val = int(val.get("score", 0))
                max_val = int(val.get("max", max_score))
                label = str(val.get("label", self.DIMENSION_NAMES.get(key, key)))
            elif isinstance(val, (int, float)):
                score_val = int(val)
                max_val = max_score
                label = self.DIMENSION_NAMES.get(key, key)
            else:
                continue

            bar = _ScoreBar(label, score_val, max_val)
            self._scores_layout.addWidget(bar)

    def set_summary(self, word_count: int, score: int = 0) -> None:
        """设置摘要信息。"""
        parts = [f"字数: {word_count:,}"]
        if score > 0:
            parts.append(f"评分: {score}/100")
        self._summary_label.setText("  |  ".join(parts))

    def set_preview(self, text: str) -> None:
        """设置文本预览（前200字）。"""
        preview = text[:200].replace("\n", " ").strip()
        if len(text) > 200:
            preview += "..."
        self._preview.setText(preview)
        self._preview.show()

    @property
    def chapter_id(self) -> str:
        return self._chapter_id


# ── Diff 审阅卡片 ──────────────────────────────────────────


class DiffCard(MessageCard):
    """Commit 审阅卡片。

    布局：
    ┌──────────────────────────────────────────┐
    │ ● System   Commit 审阅          [▼]     │
    ├──────────────────────────────────────────┤
    │ ┌─ 文件变更 ───────────────────────────┐ │
    │ │ draft/ch_002.md                      │ │
    │ │ characters/protagonist.md            │ │
    │ └─────────────────────────────────────┘ │
    │ ┌─ 提取事件 ───────────────────────────┐ │
    │ │ ☑ [CHAR_DEVEL] 主角成长...           │ │
    │ │ ☑ [PLOT_ADV] 剧情推进...             │ │
    │ └─────────────────────────────────────┘ │
    │                                          │
    │ [确认提交 (N 事件)]  [取消]               │
    └──────────────────────────────────────────┘
    """

    events_confirmed = Signal(list)
    cancelled = Signal()

    def __init__(self, card_id: str) -> None:
        super().__init__(card_id, "System", sender_color="#6B6863", collapsible=True)

        # 文件变更区
        files_label = QLabel("<b>文件变更</b>")
        files_label.setStyleSheet("font-size: 12px; padding: 2px 0;")
        self._set_body_widget(files_label)

        self._files_layout = QVBoxLayout()
        self._files_layout.setContentsMargins(4, 0, 0, 4)
        self._files_layout.setSpacing(2)
        self._set_body_layout(self._files_layout)

        # 事件列表区
        events_label = QLabel("<b>提取事件</b>")
        events_label.setStyleSheet("font-size: 12px; padding: 2px 0;")
        self._set_body_widget(events_label)

        self._events_layout = QVBoxLayout()
        self._events_layout.setContentsMargins(4, 0, 0, 4)
        self._events_layout.setSpacing(2)
        self._set_body_layout(self._events_layout)

        self._event_checkboxes: list[QCheckBox] = []

        # Footer：操作按钮
        btn_bar = QFrame()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(8)

        self._btn_confirm = QPushButton("确认提交")
        self._btn_confirm.clicked.connect(self._on_confirm)
        btn_layout.addWidget(self._btn_confirm)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setObjectName("SecondaryButton")
        self._btn_cancel.clicked.connect(lambda: self.cancelled.emit())
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()
        self._set_footer_widget(btn_bar)

        # 救援模式 UI（默认隐藏）
        self._rescue_bar = QFrame()
        self._rescue_bar.setStyleSheet(
            "background: #FFF0EE; border: 1px solid #B85C4A;"
            "border-radius: 4px; padding: 8px; margin: 4px 0;"
        )
        rescue_layout = QHBoxLayout(self._rescue_bar)
        rescue_layout.addWidget(QLabel("<b style='color:#B85C4A;'>⚠ 提取失败</b>"))
        self._btn_retry = QPushButton("重试")
        self._btn_dirty = QPushButton("脏提交")
        self._btn_abort = QPushButton("终止")
        self._btn_retry.setObjectName("SecondaryButton")
        self._btn_dirty.setObjectName("DangerButton")
        self._btn_abort.setObjectName("DangerButton")
        rescue_layout.addWidget(self._btn_retry)
        rescue_layout.addWidget(self._btn_dirty)
        rescue_layout.addWidget(self._btn_abort)
        rescue_layout.addStretch()
        self._rescue_bar.hide()
        self._set_body_widget(self._rescue_bar)

    # ── 数据填充 ────────────────────────────────────────────

    def show_diff(self, diff_data: dict[str, object]) -> None:
        """填充 diff 数据。"""
        # 清空旧内容
        self._clear_files()
        self._clear_events()

        # 文件列表
        files: list[dict[str, object]] = []
        raw_files = diff_data.get("files", diff_data.get("file_changes", []))
        if isinstance(raw_files, list):
            for f in raw_files:
                if isinstance(f, dict):
                    files.append(f)
                elif isinstance(f, str):
                    files.append({"path": f, "changes": ""})

        for f in files:
            path = str(f.get("path", f.get("file", "")))
            changes = str(f.get("changes", f.get("diff", "")))
            label = QLabel(f"  {path}  {changes}")
            label.setStyleSheet("font-size: 12px; color: #6B6863;")
            self._files_layout.addWidget(label)

        # 事件复选框
        events: list[dict[str, object]] = []
        raw_events = diff_data.get("events", [])
        if isinstance(raw_events, list):
            for e in raw_events:
                if isinstance(e, dict):
                    events.append(e)
                elif isinstance(e, str):
                    events.append({"description": e, "event_type": "UNKNOWN"})

        for evt in events:
            etype = str(evt.get("event_type", evt.get("type", "")))
            desc = str(evt.get("description", evt.get("summary", "")))
            text = f"[{etype}] {desc}" if etype else desc
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 12px;")
            self._event_checkboxes.append(cb)
            self._events_layout.addWidget(cb)

        # 更新确认按钮文字
        count = len(self._event_checkboxes)
        self._btn_confirm.setText(f"确认提交 ({count} 事件)")

    def _on_confirm(self) -> None:
        """收集选中事件并发射信号。"""
        selected = [cb.text() for cb in self._event_checkboxes if cb.isChecked()]
        self.events_confirmed.emit(selected)

    def _clear_files(self) -> None:
        while self._files_layout.count():
            item = self._files_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _clear_events(self) -> None:
        self._event_checkboxes.clear()
        while self._events_layout.count():
            item = self._events_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    # ── 救援模式 ──────────────────────────────────────────

    def show_rescue_mode(self) -> None:
        self._rescue_bar.show()

    def exit_rescue_mode(self) -> None:
        self._rescue_bar.hide()
