"""schemas 模块测试 - 数据模型校验。"""

import pytest
from pydantic import ValidationError

from opennovel.schemas.character import (
    CharacterFrontmatter,
    EmotionVector,
    PhysicalState,
)
from opennovel.schemas.event import EventCreate, EventLog, EventLogBase, EventType, SnapshotMeta


class TestEventLog:
    """EventLog 数据模型测试。

    注意：SQLModel table=True 模式下 Pydantic field_validator 不自动触发，
    因此校验逻辑的测试放在 TestEventCreate 中（纯 Pydantic 模型）。
    """

    def test_event_log_creation(self) -> None:
        """测试 EventLog 基本创建。"""
        event = EventLog(
            event_id="evt_ch001_001",
            chapter_id="ch_001",
            timestamp="第3天·午后",
            character_id="char_001",
            event_type="INJURY",
            description="左臂被巨剑斩伤",
            causal_pressure=0.9,
        )
        assert event.event_id == "evt_ch001_001"
        assert event.event_type == "INJURY"
        assert event.causal_pressure == 0.9


class TestEventCreate:
    """EventCreate 请求模型测试（纯 Pydantic，校验器正常触发）。"""

    def test_event_create_valid(self) -> None:
        """测试合法的事件创建请求。"""
        event = EventCreate(
            event_id="evt_ch001_001",
            chapter_id="ch_001",
            timestamp="第3天·午后",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="左臂骨折",
            causal_pressure=0.9,
        )
        assert event.event_type == EventType.INJURY

    def test_causal_pressure_validation(self) -> None:
        """测试因果压强范围校验（通过 EventCreate 的 ge/le 约束）。"""
        with pytest.raises(ValidationError):
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="test",
                causal_pressure=1.5,
            )

    def test_causal_pressure_negative(self) -> None:
        """测试因果压强负值校验。"""
        with pytest.raises(ValidationError):
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="test",
                causal_pressure=-0.1,
            )

    def test_event_type_enum(self) -> None:
        """测试事件类型枚举完整性。"""
        expected_types = {
            "INJURY",
            "HEAL",
            "ITEM_GAIN",
            "ITEM_LOSS",
            "KNOWLEDGE",
            "LOCATION_CHANGE",
            "EMOTION_SHIFT",
            "RELATIONSHIP_CHANGE",
            "CUSTOM",
        }
        actual_types = {e.value for e in EventType}
        assert actual_types == expected_types


class TestCharacterFrontmatter:
    """CharacterFrontmatter 数据模型测试。"""

    def test_character_creation(self) -> None:
        """测试角色 Frontmatter 基本创建。"""
        char = CharacterFrontmatter(
            id="char_001",
            name="Alice",
            aliases=["Alys", "The Witch"],
        )
        assert char.id == "char_001"
        assert char.name == "Alice"
        assert len(char.aliases) == 2

    def test_character_id_validation(self) -> None:
        """测试角色 ID 必须使用 char_xxx 格式。"""
        with pytest.raises(ValidationError):
            CharacterFrontmatter(id="alice", name="Alice")

    def test_location_validation(self) -> None:
        """测试地点 ID 必须使用 loc_xxx 格式。"""
        with pytest.raises(ValidationError):
            CharacterFrontmatter(id="char_001", name="Alice", location="tower")

    def test_emotion_vector_defaults(self) -> None:
        """测试 EmotionVector 核心维度默认值。"""
        ev = EmotionVector()
        assert ev.grief == 0.0
        assert ev.joy == 0.0
        assert ev.extras == {}

    def test_emotion_vector_range(self) -> None:
        """测试核心维度值范围校验。"""
        with pytest.raises(ValidationError):
            EmotionVector(grief=1.5)

    def test_emotion_vector_extras(self) -> None:
        """测试自定义情绪维度。"""
        ev = EmotionVector(
            grief=0.3,
            extras={"jealousy": 0.8, "hope": 0.6},
        )
        assert ev.extras["jealousy"] == 0.8
        assert ev.extras["hope"] == 0.6

    def test_emotion_vector_extras_validation(self) -> None:
        """测试自定义情绪超范围校验。"""
        with pytest.raises(ValidationError):
            EmotionVector(grief=0.1, extras={"shame": 1.5})

    def test_physical_state_defaults(self) -> None:
        """测试物理状态默认值。"""
        physical = PhysicalState()
        assert physical.injuries == []
        assert physical.buffs == []


class TestSnapshotMeta:
    """SnapshotMeta 数据模型测试。"""

    def test_delta_files_structure(self) -> None:
        """测试文件级增量快照结构。"""
        snapshot = SnapshotMeta(
            snapshot_id="snap_ch_001_1698765432",
            source_command="commit ch_001",
            timestamp="2023-10-31T10:00:00",
            delta_files={
                "characters/char_001.md": {
                    "fm_before": {"id": "char_001", "physical": []},
                    "fm_after": {"id": "char_001", "physical": ["left_arm_fracture"]},
                },
            },
            delta_sqlite={"event_ids_to_rollback": ["evt_001", "evt_002"]},
        )
        assert snapshot.snapshot_id == "snap_ch_001_1698765432"
        assert snapshot.source_command == "commit ch_001"
        assert "characters/char_001.md" in snapshot.delta_files
        assert snapshot.delta_sqlite["event_ids_to_rollback"] == ["evt_001", "evt_002"]

    def test_delta_files_defaults(self) -> None:
        """测试快照默认值。"""
        snapshot = SnapshotMeta(
            snapshot_id="snap_ch_001",
            source_command="commit ch_001",
            timestamp="2023-10-31T10:00:00",
        )
        assert snapshot.delta_files == {}
        assert snapshot.delta_sqlite == {"event_ids_to_rollback": []}


class TestEventLogBaseValidators:
    """EventLogBase field_validator 测试（直接调用类方法以覆盖 SQLModel 桩代码路径）。"""

    def test_validate_causal_pressure_negative(self) -> None:
        """测试因果压强负值触发 ValueError（覆盖 lines 52-53）。"""
        with pytest.raises(ValueError, match="causal_pressure 必须在"):
            EventLogBase.validate_causal_pressure(-0.1)

    def test_validate_causal_pressure_above_one(self) -> None:
        """测试因果压强超上限触发 ValueError。"""
        with pytest.raises(ValueError, match="causal_pressure 必须在"):
            EventLogBase.validate_causal_pressure(1.5)

    def test_validate_causal_pressure_valid_rounding(self) -> None:
        """测试因果压强合法值正确四舍五入（覆盖 line 54）。"""
        result = EventLogBase.validate_causal_pressure(0.555)
        assert result == 0.56  # round(0.555, 2) = 0.56

    def test_validate_causal_pressure_boundary_zero(self) -> None:
        """测试因果压强下边界值 0.0 合法。"""
        result = EventLogBase.validate_causal_pressure(0.0)
        assert result == 0.0

    def test_validate_causal_pressure_boundary_one(self) -> None:
        """测试因果压强上边界值 1.0 合法。"""
        result = EventLogBase.validate_causal_pressure(1.0)
        assert result == 1.0

    def test_validate_character_id_invalid_prefix(self) -> None:
        """测试角色 ID 前缀不合法触发 ValueError（覆盖 lines 60-61）。"""
        with pytest.raises(ValueError, match="character_id 必须使用 Canonical ID 规范"):
            EventLogBase.validate_character_id("role_001")

    def test_validate_character_id_empty_string(self) -> None:
        """测试空字符串角色 ID 触发 ValueError。"""
        with pytest.raises(ValueError, match="character_id 必须使用 Canonical ID 规范"):
            EventLogBase.validate_character_id("")

    def test_validate_character_id_valid(self) -> None:
        """测试合法角色 ID 正常通过（覆盖 line 62）。"""
        result = EventLogBase.validate_character_id("char_001")
        assert result == "char_001"
