"""状态投影器 — 将事件流折叠为角色状态快照。

State Projector 从 EventLog 事件流归约为角色在任意时间点的可信状态快照，
作为 ContextAssembler 的运行时数据源注入创作上下文。

核心操作是"时间轴折叠"：
1. 查询指定角色截至目标章节的所有事件（时间序）
2. 依次应用每个事件变更到空白状态字典
3. 输出该时间点的最终状态快照

设计原则：
- 纯函数式：输入事件列表 → 输出状态快照，无副作用
- <30ms 预期延迟（2500 事件规模）
- 不依赖 LLM
"""

import logging
from pathlib import Path

from opennovel.schemas.state import CharacterStateSnapshot
from opennovel.storage.sqlite import EventStore

logger = logging.getLogger(__name__)


class StateProjector:
    """状态投影器：将 EventLog 事件流折叠为角色状态快照。

    使用方式:
        projector = StateProjector(event_store)
        snapshot = projector.project("char_001", "ch_050")
        print(snapshot.physical)  # {"左臂": "骨折"}
    """

    def __init__(self, event_store: EventStore) -> None:
        """初始化状态投影器。

        Args:
            event_store: EventStore 实例，用于查询事件
        """
        self._event_store = event_store

    def project(
        self,
        character_id: str,
        up_to_chapter: str,
    ) -> CharacterStateSnapshot:
        """折叠事件流，生成角色状态快照。

        Args:
            character_id: 角色 Canonical ID（如 "char_001"）
            up_to_chapter: 截止章节 ID（如 "ch_050"）

        Returns:
            该时间点的状态快照
        """
        events = self._event_store.get_events_up_to(character_id, up_to_chapter)
        state = CharacterStateSnapshot(character_id=character_id)

        for evt in events:
            self._apply_event(state, evt)

        state.chapter_id = up_to_chapter
        state.event_count = len(events)
        return state

    def _apply_event(
        self,
        state: CharacterStateSnapshot,
        event,  # EventLog
    ) -> None:
        """将单个事件折叠进状态快照。

        Args:
            state: 正在构建的状态快照（就地修改）
            event: 事件日志记录
        """
        event_type = event.event_type

        if event_type == "INJURY":
            # 受伤 → 身体部位标记为 injured
            state.physical[event.description] = "injured"

        elif event_type == "HEAL":
            # 康复 → 移除受伤状态
            state.physical.pop(event.description, None)

        elif event_type == "ITEM_GAIN":
            # 获得物品
            if event.description not in state.inventory:
                state.inventory.append(event.description)

        elif event_type == "ITEM_LOSS":
            # 失去物品
            if event.description in state.inventory:
                state.inventory.remove(event.description)

        elif event_type == "EMOTION_SHIFT":
            # 情绪变化 → 更新情绪维度
            state.emotional[event.description] = event.causal_pressure

        elif event_type == "LOCATION_CHANGE":
            # 位置变化
            state.location = event.description

        elif event_type == "RELATIONSHIP_CHANGE":
            # 关系变化
            state.relationships[event.description] = "changed"

        elif event_type == "KNOWLEDGE":
            # 获得知识
            if event.description not in state.knowledge:
                state.knowledge.append(event.description)

        # CUSTOM 类型的事件不做特殊处理，由具体项目扩展

    def format_for_context(self, snapshot: CharacterStateSnapshot) -> str:
        """将状态快照格式化为 Prompt 可读的文本块。

        Args:
            snapshot: 状态快照

        Returns:
            格式化后的文本（用于注入上下文 STATE MEMORY 区块）
        """
        parts: list[str] = []

        if snapshot.physical:
            body = ", ".join(
                f"{k}({v})" for k, v in snapshot.physical.items()
            )
            parts.append(f"  Body: {body}")

        if snapshot.emotional:
            mood = ", ".join(
                f"{k}={v}" for k, v in snapshot.emotional.items()
            )
            parts.append(f"  Mood: {mood}")

        if snapshot.inventory:
            items = ", ".join(snapshot.inventory)
            parts.append(f"  Items: {items}")

        if snapshot.knowledge:
            knowledge = "; ".join(snapshot.knowledge)
            parts.append(f"  Knowledge: {knowledge}")

        if snapshot.location:
            parts.append(f"  Location: {snapshot.location}")

        if snapshot.relationships:
            rels = ", ".join(
                f"{k}({v})" for k, v in snapshot.relationships.items()
            )
            parts.append(f"  Relations: {rels}")

        if not parts:
            return f"  [{snapshot.character_id}] 无已知状态"

        return f"  [{snapshot.character_id}]\n" + "\n".join(parts)

    def format_snapshots(
        self,
        snapshots: list[CharacterStateSnapshot],
    ) -> str:
        """将多个角色状态快照格式化为单一文本块。

        Args:
            snapshots: 状态快照列表

        Returns:
            格式化后的文本（用于注入上下文）
        """
        if not snapshots:
            return ""

        lines = ["## Current Character States"]
        for snap in snapshots:
            formatted = self.format_for_context(snap)
            if formatted:
                lines.append(formatted)
        return "\n".join(lines)
