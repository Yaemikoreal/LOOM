"""ChatPanel — 右侧主面板：Agent 对话 + 消息卡片流。

取代旧的 QTabWidget 标签页，成为右侧唯一主面板。
所有消息使用 MessageCard 类型内联展示。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.widgets.chat_input import SmartInputBar
from opennovel_desktop.widgets.message_cards import (
    SENDER_COLORS,
    ChapterCard,
    DiffCard,
    MessageCard,
    ThinkingCard,
)


class ChatPanel(QWidget):
    """右侧主对话面板。

    布局：
    ┌─────────────────────────────────────┐
    │ Agent 对话            [⚙] [清除]    │
    ├─────────────────────────────────────┤
    │ 消息卡片列表（QScrollArea）          │
    │  ┌─ ThinkingCard ────────────────┐ │
    │  └───────────────────────────────┘ │
    │  ┌─ ChapterCard ─────────────────┐ │
    │  └───────────────────────────────┘ │
    ├─────────────────────────────────────┤
    │ SmartInputBar                        │
    └─────────────────────────────────────┘
    """

    # MainWindow 绑定信号
    execute_requested = Signal(str, dict)  # action_type, params
    card_action_triggered = Signal(str, str, dict)  # card_id, action_name, params
    editor_mode_changed = Signal(str)  # "replace" | "append" | "split"

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ChatPanel")

        self._card_counter: int = 0
        self._active_thinking_id: str | None = None
        self._cards: dict[str, MessageCard] = {}  # card_id → card

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 标题栏 ──────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setObjectName("ChatTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("<b>Agent 对话</b>")
        title.setStyleSheet("font-size: 14px;")
        title_layout.addWidget(title)
        title_layout.addStretch()

        self._clear_btn = QPushButton("清除")
        self._clear_btn.setObjectName("SecondaryButton")
        self._clear_btn.setFixedWidth(50)
        self._clear_btn.clicked.connect(self.clear_messages)
        title_layout.addWidget(self._clear_btn)

        outer.addWidget(title_bar)

        # ── 消息区域 ────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._messages_widget = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_widget)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)
        self._messages_layout.setSpacing(6)
        self._messages_layout.addStretch()

        scroll.setWidget(self._messages_widget)
        self._scroll = scroll
        outer.addWidget(scroll, 1)

        # ── 输入栏 ──────────────────────────────────────────
        self._input_bar = SmartInputBar()
        self._input_bar.execute_requested.connect(self.execute_requested)
        self._input_bar.mode_changed.connect(self.editor_mode_changed)

        # 预设按钮连接
        self._input_bar._btn_write.clicked.connect(lambda: self._input_bar._send_preset("/write "))
        self._input_bar._btn_auto.clicked.connect(
            lambda: self._input_bar._send_preset("/auto 按大纲自动创作全部章节")
        )
        self._input_bar._btn_stash.clicked.connect(lambda: self._input_bar._send_preset("/stash "))

        outer.addWidget(self._input_bar)

        # 欢迎消息
        self.show_system_message(
            "你好！我是你的 AI 创作助手。\n"
            "告诉我你想写什么，我会帮你完成。\n\n"
            "试试这些命令：\n"
            "• /write 创作一个新章节\n"
            "• /revise 修改当前选中的文字\n"
            "• /evaluate 评价当前章节质量",
            "info",
        )

    # ── 公开 API — 消息 ────────────────────────────────────

    def show_user_message(self, text: str) -> None:
        """显示用户消息气泡。"""
        card_id = f"user_{self._card_counter}"
        self._card_counter += 1
        card = MessageCard(card_id, "User", sender_color=SENDER_COLORS["User"])
        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px; line-height: 1.5;")
        card._set_body_widget(body)
        self._add_card(card_id, card)

    def show_system_message(self, text: str, msg_type: str = "info") -> None:
        """显示系统通知消息。"""
        card_id = f"sys_{self._card_counter}"
        self._card_counter += 1
        color = {"error": "#B85C4A", "info": "#6B6863", "status": "#A8A49E"}.get(
            msg_type, "#6B6863"
        )
        card = MessageCard(card_id, "System", sender_color=color)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; line-height: 1.5; color: {color};")
        card._set_body_widget(body)
        self._add_card(card_id, card)

    def show_error(self, sender: str, error_msg: str) -> None:
        """显示错误消息。"""
        card_id = f"err_{self._card_counter}"
        self._card_counter += 1
        color = SENDER_COLORS.get(sender, "#B85C4A")
        card = MessageCard(card_id, sender, sender_color=color)
        body = QLabel(f"⚠ {error_msg}")
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px; color: #B85C4A; font-weight: 500;")
        card._set_body_widget(body)
        self._add_card(card_id, card)

    # ── 公开 API — Thinking card ───────────────────────────

    def show_thinking(self, sender: str, status: str = "思考中...") -> str:
        """创建思维链卡片并返回 card_id。"""
        card_id = f"think_{self._card_counter}"
        self._card_counter += 1
        card = ThinkingCard(card_id, sender)
        card.update_status(status)
        self._active_thinking_id = card_id
        self._add_card(card_id, card)
        return card_id

    def append_stream(self, card_id: str, chunk: str) -> None:
        """向思维链卡片追加流式文本。"""
        card = self._cards.get(card_id)
        if isinstance(card, ThinkingCard):
            card.append_text(chunk)
            self._scroll_to_bottom()

    def update_status(self, card_id: str, msg: str) -> None:
        """更新思维卡片状态文本。"""
        card = self._cards.get(card_id)
        if isinstance(card, ThinkingCard):
            card.update_status(msg)

    def set_phase(self, card_id: str, phase_id: str, status: str) -> None:
        """更新思维卡片阶段指示器。"""
        card = self._cards.get(card_id)
        if isinstance(card, ThinkingCard):
            card.set_phase(phase_id, status)

    def finalize_thinking(self, card_id: str) -> None:
        """标记思维卡片完成。"""
        card = self._cards.get(card_id)
        if isinstance(card, ThinkingCard):
            card.finalize()
        if self._active_thinking_id == card_id:
            self._active_thinking_id = None

    # ── 公开 API — Chapter card ────────────────────────────

    def show_chapter_result(
        self,
        chapter_id: str,
        result: dict[str, object] | None = None,
    ) -> str:
        """创建创作结果卡片并返回 card_id。

        Args:
            chapter_id: 章节 ID
            result: AgentWorker.finished 返回的 dict
        """
        card_id = f"chapter_{self._card_counter}"
        self._card_counter += 1

        card = ChapterCard(card_id, chapter_id)

        if result:
            evaluation = result.get("evaluation", {})
            if isinstance(evaluation, dict):
                scores: dict[str, object] = {}
                for key in ("dimensions", "scores"):
                    val = evaluation.get(key)
                    if isinstance(val, dict):
                        scores = val
                        break
                if scores:
                    card.set_scores(scores)

                score_val = int(evaluation.get("score", result.get("score", 0)))
            else:
                score_val = int(result.get("score", 0))

            wc = int(result.get("word_count", 0))
            card.set_summary(wc, score_val)

        # 连接信号到统一转发器
        card.accept_requested.connect(
            lambda cid=chapter_id: self.card_action_triggered.emit(
                card_id, "accept", {"chapter_id": cid}
            )
        )
        card.revise_requested.connect(
            lambda cid=chapter_id: self.card_action_triggered.emit(
                card_id, "revise", {"chapter_id": cid}
            )
        )
        card.rewrite_requested.connect(
            lambda cid=chapter_id: self.card_action_triggered.emit(
                card_id, "rewrite", {"chapter_id": cid}
            )
        )
        card.stash_requested.connect(
            lambda cid=chapter_id: self.card_action_triggered.emit(
                card_id, "stash", {"chapter_id": cid}
            )
        )

        self._add_card(card_id, card)
        return card_id

    # ── 公开 API — Diff card ───────────────────────────────

    def show_diff(self, diff_data: dict[str, object]) -> str:
        """创建 Diff 审阅卡片并返回 card_id。"""
        card_id = f"diff_{self._card_counter}"
        self._card_counter += 1

        card = DiffCard(card_id)
        card.show_diff(diff_data)
        card.events_confirmed.connect(
            lambda events: self.card_action_triggered.emit(
                card_id, "confirm_diff", {"events": events}
            )
        )
        card.cancelled.connect(lambda: self.card_action_triggered.emit(card_id, "cancel_diff", {}))

        # 救援模式连接（通过 card_action_triggered 转发）
        card._btn_retry.clicked.connect(
            lambda: self.card_action_triggered.emit(card_id, "rescue_retry", {})
        )
        card._btn_dirty.clicked.connect(
            lambda: self.card_action_triggered.emit(card_id, "rescue_dirty", {})
        )
        card._btn_abort.clicked.connect(
            lambda: self.card_action_triggered.emit(card_id, "rescue_abort", {})
        )

        self._add_card(card_id, card)
        return card_id

    # ── 公开 API — 清空 / 状态 ──────────────────────────────

    def clear_messages(self) -> None:
        """清除所有消息卡片。"""
        self._cards.clear()
        self._active_thinking_id = None
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def set_input_enabled(self, enabled: bool) -> None:
        self._input_bar.set_enabled(enabled)

    def get_editor_mode(self) -> str:
        return self._input_bar.get_mode()

    def inject_preset(self, text: str) -> None:
        """工具栏注入预设文本到输入框。"""
        self._input_bar.inject_preset(text)

    # ── 内部 — 卡片管理 ────────────────────────────────────

    def _add_card(self, card_id: str, card: MessageCard) -> None:
        """插入卡片到消息列表末尾（stretch 之前）。"""
        self._cards[card_id] = card
        self._messages_layout.insertWidget(self._messages_layout.count() - 1, card)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """滚动到消息列表底部。"""
        vsb = self._scroll.verticalScrollBar()
        vsb.setValue(vsb.maximum())
