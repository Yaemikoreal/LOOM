"""storage 模块测试 - SQLite 事件账本。"""

from pathlib import Path

import pytest

from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.sqlite import EventStore


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    """创建测试用事件存储实例。"""
    db_path = tmp_path / "test.db"
    return EventStore(db_path)


@pytest.fixture
def sample_event() -> EventCreate:
    """创建测试用事件。"""
    return EventCreate(
        event_id="evt_ch001_001",
        chapter_id="ch_001",
        timestamp="第3天·午后",
        character_id="char_001",
        event_type=EventType.INJURY,
        description="左臂被巨剑斩伤",
        causal_pressure=0.9,
    )


class TestEventStore:
    """EventStore 测试。"""

    def test_add_and_get_event(self, event_store: EventStore, sample_event: EventCreate) -> None:
        """测试添加和查询事件。"""
        record = event_store.add_event(sample_event)
        assert record.event_id == "evt_ch001_001"

        fetched = event_store.get_event_by_id("evt_ch001_001")
        assert fetched is not None
        assert fetched.description == "左臂被巨剑斩伤"

    def test_get_events_by_chapter(
        self, event_store: EventStore, sample_event: EventCreate
    ) -> None:
        """测试按章节查询事件。"""
        event_store.add_event(sample_event)
        events = event_store.get_events_by_chapter("ch_001")
        assert len(events) == 1

    def test_get_events_by_character(
        self, event_store: EventStore, sample_event: EventCreate
    ) -> None:
        """测试按角色查询事件。"""
        event_store.add_event(sample_event)
        events = event_store.get_events_by_character("char_001")
        assert len(events) == 1

    def test_get_events_by_type(self, event_store: EventStore, sample_event: EventCreate) -> None:
        """测试按类型查询事件。"""
        event_store.add_event(sample_event)
        events = event_store.get_events_by_type("INJURY")
        assert len(events) == 1

    def test_batch_add_events(self, event_store: EventStore) -> None:
        """测试批量添加事件。"""
        events = [
            EventCreate(
                event_id=f"evt_ch001_{i:03d}",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.ITEM_GAIN,
                description=f"获得物品{i}",
                causal_pressure=0.5,
            )
            for i in range(3)
        ]
        records = event_store.add_events_batch(events)
        assert len(records) == 3

    def test_delete_events(self, event_store: EventStore, sample_event: EventCreate) -> None:
        """测试删除事件。"""
        event_store.add_event(sample_event)
        deleted = event_store.delete_events_by_ids(["evt_ch001_001"])
        assert deleted == 1

        fetched = event_store.get_event_by_id("evt_ch001_001")
        assert fetched is None

    def test_high_pressure_events(self, event_store: EventStore) -> None:
        """测试高因果压强事件查询。"""
        high_event = EventCreate(
            event_id="evt_high",
            chapter_id="ch_001",
            timestamp="第5天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="致命伤",
            causal_pressure=0.9,
        )
        low_event = EventCreate(
            event_id="evt_low",
            chapter_id="ch_001",
            timestamp="第5天",
            character_id="char_001",
            event_type=EventType.KNOWLEDGE,
            description="得知天气变化",
            causal_pressure=0.2,
        )
        event_store.add_events_batch([high_event, low_event])

        high_pressure = event_store.get_high_pressure_events(threshold=0.7)
        assert len(high_pressure) == 1
        assert high_pressure[0].event_id == "evt_high"
