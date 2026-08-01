"""因果链系统测试 - Phase 2.1。

测试范围：
- EventLog schema 新增字段（caused_by, related_event_ids）
- EventCreate schema 新增字段
- EventStore 因果链查询方法
- EventRecord schema 新增字段
- ManagerUpdateResult 因果链字段传递
"""

import json

import pytest

from opennovel.schemas.event import EventCreate, EventLog, EventType
from opennovel.schemas.manager_update import EventRecord, ManagerUpdateResult
from opennovel.storage.sqlite import EventStore


# ── Schema 测试 ──────────────────────────────────────────────────────


class TestEventLogCausalFields:
    """EventLog 因果链字段测试。"""

    def test_event_log_with_causal_fields(self, tmp_path):
        """EventLog 支持 caused_by 和 related_event_ids 字段。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
        )
        record = store.add_event(event)
        assert record.caused_by is None
        assert record.related_event_ids is None

    def test_event_log_with_caused_by(self, tmp_path):
        """EventLog 正确存储 caused_by 字段。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        # 先创建前置事件
        event1 = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
        )
        store.add_event(event1)

        # 创建因果后续事件
        event2 = EventCreate(
            event_id="evt_002",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.HEAL,
            description="角色接受治疗",
            causal_pressure=0.6,
            caused_by="evt_001",
        )
        record = store.add_event(event2)
        assert record.caused_by == "evt_001"

    def test_event_log_with_related_ids(self, tmp_path):
        """EventLog 正确存储 related_event_ids 字段。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
            related_event_ids=["evt_002", "evt_003"],
        )
        record = store.add_event(event)
        assert record.related_event_ids == '["evt_002", "evt_003"]'
        assert record.get_related_ids() == ["evt_002", "evt_003"]

    def test_get_related_ids_empty(self):
        """get_related_ids 在无关联事件时返回空列表。"""
        event = EventLog(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type="INJURY",
            description="测试",
            related_event_ids=None,
        )
        assert event.get_related_ids() == []

    def test_get_related_ids_invalid_json(self):
        """get_related_ids 在 JSON 解析失败时返回空列表。"""
        event = EventLog(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type="INJURY",
            description="测试",
            related_event_ids="invalid json",
        )
        assert event.get_related_ids() == []

    def test_set_related_ids(self):
        """set_related_ids 正确序列化列表。"""
        event = EventLog(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type="INJURY",
            description="测试",
        )
        event.set_related_ids(["evt_002", "evt_003"])
        assert event.related_event_ids == '["evt_002", "evt_003"]'
        assert event.get_related_ids() == ["evt_002", "evt_003"]

    def test_set_related_ids_empty(self):
        """set_related_ids 空列表时设为 None。"""
        event = EventLog(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type="INJURY",
            description="测试",
        )
        event.set_related_ids([])
        assert event.related_event_ids is None


class TestEventCreateCausalFields:
    """EventCreate 因果链字段测试。"""

    def test_event_create_with_causal_fields(self):
        """EventCreate 支持 caused_by 和 related_event_ids。"""
        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
            caused_by="evt_000",
            related_event_ids=["evt_002", "evt_003"],
        )
        assert event.caused_by == "evt_000"
        assert event.related_event_ids == ["evt_002", "evt_003"]

    def test_event_create_causal_fields_default_none(self):
        """EventCreate 因果链字段默认为 None。"""
        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
        )
        assert event.caused_by is None
        assert event.related_event_ids is None


class TestEventRecordCausalFields:
    """EventRecord 因果链字段测试。"""

    def test_event_record_with_causal_fields(self):
        """EventRecord 支持 caused_by 和 related_event_ids。"""
        record = EventRecord(
            event_id="evt_001",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
            timestamp="第1天",
            caused_by="evt_000",
            related_event_ids=["evt_002"],
        )
        assert record.caused_by == "evt_000"
        assert record.related_event_ids == ["evt_002"]

    def test_event_record_causal_fields_default_none(self):
        """EventRecord 因果链字段默认为 None。"""
        record = EventRecord(
            event_id="evt_001",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
            timestamp="第1天",
        )
        assert record.caused_by is None
        assert record.related_event_ids is None


# ── EventStore 因果链查询测试 ────────────────────────────────────────


class TestEventStoreCausalChain:
    """EventStore 因果链查询方法测试。"""

    def _create_chain(self, store: EventStore) -> None:
        """创建测试因果链: evt_001 → evt_002 → evt_003。"""
        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天早上",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="角色在战斗中受伤",
                causal_pressure=0.9,
            ),
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_001",
                timestamp="第1天中午",
                character_id="char_001",
                event_type=EventType.HEAL,
                description="队友为角色包扎伤口",
                causal_pressure=0.7,
                caused_by="evt_001",
            ),
            EventCreate(
                event_id="evt_003",
                chapter_id="ch_001",
                timestamp="第1天晚上",
                character_id="char_001",
                event_type=EventType.EMOTION_SHIFT,
                description="角色因伤势产生恐惧",
                causal_pressure=0.6,
                caused_by="evt_002",
            ),
        ]
        store.add_events_batch(events)

    def test_get_causal_chain_basic(self, tmp_path):
        """get_causal_chain 正确追溯因果链。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        chain = store.get_causal_chain("evt_003")
        assert len(chain) == 3
        assert chain[0].event_id == "evt_001"  # 最早在前
        assert chain[1].event_id == "evt_002"
        assert chain[2].event_id == "evt_003"

    def test_get_causal_chain_from_middle(self, tmp_path):
        """从链中间开始追溯。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        chain = store.get_causal_chain("evt_002")
        assert len(chain) == 2
        assert chain[0].event_id == "evt_001"
        assert chain[1].event_id == "evt_002"

    def test_get_causal_chain_from_root(self, tmp_path):
        """从链根节点追溯（只有自身）。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        chain = store.get_causal_chain("evt_001")
        assert len(chain) == 1
        assert chain[0].event_id == "evt_001"

    def test_get_causal_chain_nonexistent(self, tmp_path):
        """不存在的事件返回空链。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        chain = store.get_causal_chain("evt_nonexistent")
        assert len(chain) == 0

    def test_get_causal_chain_max_depth(self, tmp_path):
        """max_depth 限制追溯深度。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        chain = store.get_causal_chain("evt_003", max_depth=2)
        assert len(chain) == 2
        assert chain[0].event_id == "evt_002"
        assert chain[1].event_id == "evt_003"

    def test_get_causal_descendants_basic(self, tmp_path):
        """get_causal_descendants 正确查找因果后继。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        descendants = store.get_causal_descendants("evt_001")
        assert len(descendants) == 2
        event_ids = [e.event_id for e in descendants]
        assert "evt_002" in event_ids
        assert "evt_003" in event_ids

    def test_get_causal_descendants_leaf(self, tmp_path):
        """叶节点没有后继。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)
        self._create_chain(store)

        descendants = store.get_causal_descendants("evt_003")
        assert len(descendants) == 0

    def test_get_causal_descendants_nonexistent(self, tmp_path):
        """不存在的事件返回空后继。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        descendants = store.get_causal_descendants("evt_nonexistent")
        assert len(descendants) == 0

    def test_get_related_events(self, tmp_path):
        """get_related_events 正确查找关联事件。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        # 创建关联事件
        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="角色A受伤",
                causal_pressure=0.8,
                related_event_ids=["evt_002", "evt_003"],
            ),
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_002",
                event_type=EventType.INJURY,
                description="角色B受伤",
                causal_pressure=0.7,
            ),
            EventCreate(
                event_id="evt_003",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_003",
                event_type=EventType.ITEM_LOSS,
                description="角色C丢失物品",
                causal_pressure=0.5,
            ),
        ]
        store.add_events_batch(events)

        related = store.get_related_events("evt_001")
        assert len(related) == 2
        related_ids = [e.event_id for e in related]
        assert "evt_002" in related_ids
        assert "evt_003" in related_ids

    def test_get_related_events_no_relations(self, tmp_path):
        """无关联事件时返回空列表。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.8,
        )
        store.add_event(event)

        related = store.get_related_events("evt_001")
        assert len(related) == 0

    def test_get_related_events_nonexistent(self, tmp_path):
        """不存在的事件返回空关联。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        related = store.get_related_events("evt_nonexistent")
        assert len(related) == 0

    def test_get_events_by_character_and_type(self, tmp_path):
        """按角色+类型精确查询。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="受伤1",
                causal_pressure=0.8,
            ),
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.HEAL,
                description="治疗1",
                causal_pressure=0.6,
            ),
            EventCreate(
                event_id="evt_003",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_002",
                event_type=EventType.INJURY,
                description="受伤2",
                causal_pressure=0.7,
            ),
        ]
        store.add_events_batch(events)

        results = store.get_events_by_character_and_type("char_001", "INJURY")
        assert len(results) == 1
        assert results[0].event_id == "evt_001"

    def test_batch_add_with_causal_fields(self, tmp_path):
        """批量添加事件时正确处理因果链字段。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="受伤",
                causal_pressure=0.8,
            ),
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.HEAL,
                description="治疗",
                causal_pressure=0.6,
                caused_by="evt_001",
                related_event_ids=["evt_001"],
            ),
        ]
        records = store.add_events_batch(events)
        assert len(records) == 2
        assert records[0].caused_by is None
        assert records[1].caused_by == "evt_001"
        assert json.loads(records[1].related_event_ids) == ["evt_001"]

    def test_diamond_causal_graph(self, tmp_path):
        """菱形因果图：A → B, A → C, B → D, C → D。"""
        db_path = tmp_path / "test.db"
        store = EventStore(db_path)

        events = [
            EventCreate(
                event_id="A", chapter_id="ch_001", timestamp="t1",
                character_id="char_001", event_type=EventType.CUSTOM,
                description="A", causal_pressure=0.9,
            ),
            EventCreate(
                event_id="B", chapter_id="ch_001", timestamp="t2",
                character_id="char_001", event_type=EventType.CUSTOM,
                description="B", causal_pressure=0.7, caused_by="A",
            ),
            EventCreate(
                event_id="C", chapter_id="ch_001", timestamp="t2",
                character_id="char_002", event_type=EventType.CUSTOM,
                description="C", causal_pressure=0.7, caused_by="A",
            ),
            EventCreate(
                event_id="D", chapter_id="ch_001", timestamp="t3",
                character_id="char_001", event_type=EventType.CUSTOM,
                description="D", causal_pressure=0.8, caused_by="B",
            ),
        ]
        store.add_events_batch(events)

        # 从 D 追溯到 A（通过 B）
        chain = store.get_causal_chain("D")
        assert len(chain) == 3
        assert [e.event_id for e in chain] == ["A", "B", "D"]

        # A 的后继包含 B, C, D
        descendants = store.get_causal_descendants("A")
        desc_ids = {e.event_id for e in descendants}
        assert desc_ids == {"B", "C", "D"}


# ── ManagerUpdateResult 因果链测试 ──────────────────────────────────


class TestManagerUpdateResultCausalChain:
    """ManagerUpdateResult 因果链字段传递测试。"""

    def test_manager_result_with_causal_events(self):
        """ManagerUpdateResult 正确包含带因果链字段的事件。"""
        result = ManagerUpdateResult(
            character_updates=[],
            events=[
                EventRecord(
                    event_id="evt_001",
                    character_id="char_001",
                    event_type=EventType.INJURY,
                    description="受伤",
                    causal_pressure=0.8,
                    timestamp="第1天",
                ),
                EventRecord(
                    event_id="evt_002",
                    character_id="char_001",
                    event_type=EventType.HEAL,
                    description="治疗",
                    causal_pressure=0.6,
                    timestamp="第1天",
                    caused_by="evt_001",
                    related_event_ids=["evt_001"],
                ),
            ],
            chapter_summary="测试摘要",
        )
        assert result.events[0].caused_by is None
        assert result.events[1].caused_by == "evt_001"
        assert result.events[1].related_event_ids == ["evt_001"]

    def test_manager_result_json_roundtrip(self):
        """ManagerUpdateResult JSON 序列化/反序列化保留因果链字段。"""
        result = ManagerUpdateResult(
            character_updates=[],
            events=[
                EventRecord(
                    event_id="evt_001",
                    character_id="char_001",
                    event_type=EventType.INJURY,
                    description="受伤",
                    causal_pressure=0.8,
                    timestamp="第1天",
                    caused_by="evt_000",
                    related_event_ids=["evt_002"],
                ),
            ],
            chapter_summary="测试",
        )
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        restored = ManagerUpdateResult(**data)
        assert restored.events[0].caused_by == "evt_000"
        assert restored.events[0].related_event_ids == ["evt_002"]
