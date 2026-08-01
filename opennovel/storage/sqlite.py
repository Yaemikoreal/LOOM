"""SQLite 事件账本存储适配层。

基于 SQLModel 实现 SQLite 的事件日志持久化：
- 事件的增删改查
- 按章节/角色/类型的多维查询
- 因果压强排序
- 因果链 DAG 追溯（Phase 2.1）
"""

import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from opennovel.schemas.event import EventCreate, EventLog

logger = logging.getLogger(__name__)


class EventStore:
    """SQLite 事件账本存储，管理叙事事件的全局因果日志。

    所有事件通过 Canonical ID 关联角色/地点/物品，
    支持跨章节溯源和 Phase 2 的因果推演。

    使用方式:
        with EventStore(db_path) as store:
            store.add_event(event)
    """

    def __init__(self, db_path: Path) -> None:
        """初始化事件存储。

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._create_tables()

    def close(self) -> None:
        """关闭数据库引擎，释放连接。"""
        self._engine.dispose()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _create_tables(self) -> None:
        """创建数据库表结构。"""
        SQLModel.metadata.create_all(self._engine)

    def add_event(self, event: EventCreate) -> EventLog:
        """添加一条事件记录到账本。

        Args:
            event: 经过校验的事件创建请求

        Returns:
            写入数据库的 EventLog 记录
        """
        import json

        record = EventLog(
            event_id=event.event_id,
            chapter_id=event.chapter_id,
            timestamp=event.timestamp,
            character_id=event.character_id,
            event_type=event.event_type.value,
            description=event.description,
            causal_pressure=event.causal_pressure,
            caused_by=event.caused_by,
            related_event_ids=json.dumps(event.related_event_ids)
            if event.related_event_ids
            else None,
            created_at=datetime.now().isoformat(),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        logger.info("事件已入库: %s (%s)", event.event_id, event.event_type)
        return record

    def add_events_batch(self, events: list[EventCreate]) -> list[EventLog]:
        """批量添加事件记录。

        Args:
            events: 事件创建请求列表

        Returns:
            写入数据库的 EventLog 记录列表
        """
        import json

        records: list[EventLog] = []
        with Session(self._engine) as session:
            for event in events:
                record = EventLog(
                    event_id=event.event_id,
                    chapter_id=event.chapter_id,
                    timestamp=event.timestamp,
                    character_id=event.character_id,
                    event_type=event.event_type.value,
                    description=event.description,
                    causal_pressure=event.causal_pressure,
                    caused_by=event.caused_by,
                    related_event_ids=json.dumps(event.related_event_ids)
                    if event.related_event_ids
                    else None,
                    created_at=datetime.now().isoformat(),
                )
                session.add(record)
                records.append(record)
            session.commit()
            for record in records:
                session.refresh(record)
        logger.info("批量入库 %d 条事件", len(records))
        return records

    def get_event_by_id(self, event_id: str) -> EventLog | None:
        """根据事件 ID 查询单条事件。

        Args:
            event_id: 事件唯一标识

        Returns:
            事件记录，若不存在则返回 None
        """
        with Session(self._engine) as session:
            statement = select(EventLog).where(EventLog.event_id == event_id)
            return session.exec(statement).first()

    def get_events_by_chapter(self, chapter_id: str) -> list[EventLog]:
        """查询指定章节的所有事件。

        Args:
            chapter_id: 章节 ID

        Returns:
            事件记录列表
        """
        with Session(self._engine) as session:
            statement = (
                select(EventLog).where(EventLog.chapter_id == chapter_id).order_by(EventLog.id)
            )
            return list(session.exec(statement).all())

    def get_events_by_character(self, character_id: str) -> list[EventLog]:
        """查询指定角色的所有事件。

        Args:
            character_id: 角色 Canonical ID

        Returns:
            事件记录列表，按因果压强降序排列
        """
        with Session(self._engine) as session:
            statement = (
                select(EventLog)
                .where(EventLog.character_id == character_id)
                .order_by(EventLog.causal_pressure.desc())
            )
            return list(session.exec(statement).all())

    def get_events_by_type(self, event_type: str) -> list[EventLog]:
        """查询指定类型的所有事件。

        Args:
            event_type: 事件类型，如 INJURY, HEAL 等

        Returns:
            事件记录列表
        """
        with Session(self._engine) as session:
            statement = select(EventLog).where(EventLog.event_type == event_type)
            return list(session.exec(statement).all())

    def get_high_pressure_events(self, threshold: float = 0.7) -> list[EventLog]:
        """查询因果压强高于阈值的事件，用于一致性校验。

        Args:
            threshold: 因果压强阈值，默认 0.7

        Returns:
            高因果压强事件列表
        """
        with Session(self._engine) as session:
            statement = (
                select(EventLog)
                .where(EventLog.causal_pressure >= threshold)
                .order_by(EventLog.causal_pressure.desc())
            )
            return list(session.exec(statement).all())

    def delete_events_by_ids(self, event_ids: list[str]) -> int:
        """根据事件 ID 列表删除事件记录（用于 rollback）。

        Args:
            event_ids: 要删除的事件 ID 列表

        Returns:
            实际删除的记录数
        """
        deleted_count = 0
        with Session(self._engine) as session:
            for eid in event_ids:
                statement = select(EventLog).where(EventLog.event_id == eid)
                record = session.exec(statement).first()
                if record:
                    session.delete(record)
                    deleted_count += 1
            session.commit()
        logger.info("已删除 %d 条事件记录", deleted_count)
        return deleted_count

    def get_all_events(self) -> list[EventLog]:
        """查询所有事件记录。

        Returns:
            全部事件记录列表
        """
        with Session(self._engine) as session:
            statement = select(EventLog).order_by(EventLog.id)
            return list(session.exec(statement).all())

    def get_events_up_to(
        self,
        character_id: str,
        chapter_id: str,
    ) -> list[EventLog]:
        """查询指定角色截至某章节的所有事件（时间序）。

        用于 State Projector 的时间轴折叠操作：
        获取角色在某个章节之前的所有事件，按时间序排列，
        以便重放事件流归约为状态快照。

        Args:
            character_id: 角色 Canonical ID
            chapter_id: 截止章节 ID（含该章节）

        Returns:
            事件列表，按 chapter_id 升序排列
        """
        with Session(self._engine) as session:
            statement = (
                select(EventLog)
                .where(
                    EventLog.character_id == character_id,
                    EventLog.chapter_id <= chapter_id,
                )
                .order_by(EventLog.chapter_id.asc())
            )
            return list(session.exec(statement).all())

    # ── 因果链 DAG 查询 (Phase 2.1) ──────────────────────────────────

    def get_causal_chain(
        self, event_id: str, max_depth: int = 10
    ) -> list[EventLog]:
        """沿 caused_by 边向上追溯因果链。

        从指定事件出发，递归查找其前置因果事件，直到链顶或达到最大深度。
        返回的列表按因果顺序排列（最早事件在前）。

        Args:
            event_id: 起始事件 ID
            max_depth: 最大追溯深度，防止环路导致无限递归

        Returns:
            因果链上的事件列表（按时间顺序，最早在前）
        """
        chain: list[EventLog] = []
        visited: set[str] = set()
        current_id: str | None = event_id

        while current_id and current_id not in visited and len(chain) < max_depth:
            visited.add(current_id)
            event = self.get_event_by_id(current_id)
            if event is None:
                break
            chain.append(event)
            current_id = event.caused_by

        # 反转使最早事件在前
        chain.reverse()
        return chain

    def get_causal_descendants(
        self, event_id: str, max_depth: int = 10
    ) -> list[EventLog]:
        """沿 caused_by 边向下查找因果后继。

        从指定事件出发，查找所有以它为 caused_by 的后续事件，
        递归展开形成因果树的子分支。

        Args:
            event_id: 起始事件 ID
            max_depth: 最大查找深度

        Returns:
            因果后继事件列表（按因果层级广度优先排列）
        """
        result: list[EventLog] = []
        frontier = [event_id]
        visited: set[str] = {event_id}

        for _ in range(max_depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            with Session(self._engine) as session:
                for fid in frontier:
                    statement = select(EventLog).where(EventLog.caused_by == fid)
                    children = list(session.exec(statement).all())
                    for child in children:
                        if child.event_id not in visited:
                            visited.add(child.event_id)
                            result.append(child)
                            next_frontier.append(child.event_id)
            frontier = next_frontier

        return result

    def get_related_events(self, event_id: str) -> list[EventLog]:
        """查询与指定事件关联的事件（非因果关系）。

        通过 related_event_ids JSON 字段查找叙事上相关但无直接因果关系的事件。

        Args:
            event_id: 起始事件 ID

        Returns:
            关联事件列表
        """
        event = self.get_event_by_id(event_id)
        if event is None:
            return []

        related_ids = event.get_related_ids()
        if not related_ids:
            return []

        related_events: list[EventLog] = []
        for rid in related_ids:
            related = self.get_event_by_id(rid)
            if related is not None:
                related_events.append(related)
        return related_events

    def get_events_by_character_and_type(
        self, character_id: str, event_type: str
    ) -> list[EventLog]:
        """按角色 + 事件类型精确查询。

        用于混合检索中的 SQL 精确召回路径。

        Args:
            character_id: 角色 Canonical ID
            event_type: 事件类型

        Returns:
            匹配的事件列表，按因果压强降序
        """
        with Session(self._engine) as session:
            statement = (
                select(EventLog)
                .where(
                    EventLog.character_id == character_id,
                    EventLog.event_type == event_type,
                )
                .order_by(EventLog.causal_pressure.desc())
            )
            return list(session.exec(statement).all())
