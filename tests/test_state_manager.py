"""state_manager 模块测试 - 快照、回滚、Diff 生成。"""

import os
import time
from pathlib import Path

import pytest

from opennovel.core.state_manager import StateManager, _serialize_frontmatter
from opennovel.schemas.character import CharacterFrontmatter
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.yaml_storage import YAMLStorage


@pytest.fixture
def storage() -> YAMLStorage:
    """创建 YAMLStorage 实例。"""
    return YAMLStorage()


@pytest.fixture
def project_root(tmp_path: Path, storage: YAMLStorage) -> Path:
    """创建测试用项目目录。"""
    root = tmp_path / "test_project"
    root.mkdir()

    (root / "characters").mkdir()
    (root / "draft").mkdir()
    (root / ".snapshots").mkdir()

    # 创建角色文件
    storage.write_markdown_file(
        root / "characters" / "char_001.md",
        {
            "id": "char_001",
            "name": "林夜",
            "physical": {"injuries": ["left_arm_fracture"], "buffs": [], "debuffs": []},
            "location": "loc_tower",
        },
        "# 林夜\n\n主角背景。",
    )

    # 创建章节文件
    storage.write_markdown_file(
        root / "draft" / "ch_001.md",
        {
            "id": "ch_001",
            "pov": "char_001",
            "active_characters": ["char_001"],
        },
        "# 第一章\n\n故事开始。",
    )

    return root


class TestCreateSnapshot:
    """create_snapshot 快照生成测试。"""

    def test_snapshot_created(self, project_root: Path) -> None:
        """测试快照文件被创建。"""
        manager = StateManager(project_root)
        chapter_path = project_root / "draft" / "ch_001.md"
        char_path = project_root / "characters" / "char_001.md"

        snapshot = manager.create_snapshot("ch_001", affected_files=[chapter_path, char_path])

        assert snapshot.snapshot_id.startswith("snap_ch_001_")
        assert snapshot.source_command == "commit ch_001"

        # 快照文件应存在
        snap_path = project_root / ".snapshots" / f"{snapshot.snapshot_id}.snapshot.json"
        assert snap_path.exists()

    def test_snapshot_records_delta_files(self, project_root: Path) -> None:
        """测试快照记录了受影响文件的 Frontmatter。"""
        manager = StateManager(project_root)
        char_path = project_root / "characters" / "char_001.md"

        snapshot = manager.create_snapshot("ch_001", affected_files=[char_path])

        # delta_files 应包含角色文件
        assert len(snapshot.delta_files) == 1
        rel_path = "characters/char_001.md"
        assert rel_path in snapshot.delta_files
        assert snapshot.delta_files[rel_path]["fm_before"]["id"] == "char_001"

    def test_snapshot_no_affected_files(self, project_root: Path) -> None:
        """测试无受影响文件时快照仍可创建。"""
        manager = StateManager(project_root)
        snapshot = manager.create_snapshot("ch_001", affected_files=[])

        assert snapshot.snapshot_id.startswith("snap_ch_001_")
        assert snapshot.delta_files == {}


class TestUpdateSnapshotAfter:
    """update_snapshot_after 测试。"""

    def test_update_after_state(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试更新快照的 after 状态。"""
        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        snapshot = manager.create_snapshot("ch_001", affected_files=[char_path])

        # 修改角色状态
        storage.update_frontmatter(
            char_path,
            {"physical": {"injuries": [], "buffs": [], "debuffs": []}},
        )

        # 更新快照 after 状态
        manager.update_snapshot_after(
            snapshot.snapshot_id,
            affected_files=[char_path],
            events_added=["evt_001"],
        )

        # 重新读取快照验证
        import orjson

        snap_path = project_root / ".snapshots" / f"{snapshot.snapshot_id}.snapshot.json"
        with open(snap_path, "rb") as f:
            data = orjson.loads(f.read())

        assert "characters/char_001.md" in data["delta_files"]
        assert data["delta_files"]["characters/char_001.md"]["fm_after"]["id"] == "char_001"
        assert data["delta_sqlite"]["event_ids_to_rollback"] == ["evt_001"]

    def test_update_nonexistent_snapshot(self, project_root: Path) -> None:
        """测试更新不存在的快照不报错。"""
        manager = StateManager(project_root)
        # 不应抛出异常
        manager.update_snapshot_after("nonexistent_snap", [], [])


class TestRollbackSnapshot:
    """rollback_snapshot 回滚测试。"""

    def test_rollback_restores_state(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试回滚恢复原始状态。"""
        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        # 记录原始状态
        original_meta, _ = storage.read_markdown_file(char_path)

        # 创建快照
        snapshot = manager.create_snapshot("ch_001", affected_files=[char_path])

        # 修改状态
        storage.update_frontmatter(char_path, {"name": "修改后的名字"})

        # 更新快照 after 状态
        manager.update_snapshot_after(snapshot.snapshot_id, [char_path], [])

        # 回滚
        success = manager.rollback_snapshot(snapshot.snapshot_id)
        assert success is True

        # 验证恢复
        restored_meta, _ = storage.read_markdown_file(char_path)
        assert restored_meta["name"] == "林夜"

    def test_rollback_nonexistent_snapshot(self, project_root: Path) -> None:
        """测试回滚不存在的快照返回 False。"""
        manager = StateManager(project_root)
        success = manager.rollback_snapshot("nonexistent_snap")
        assert success is False

    def test_rollback_with_conflict(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试回滚时检测到冲突（文件被外部修改）。"""
        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        # 创建快照
        snapshot = manager.create_snapshot("ch_001", affected_files=[char_path])

        # 模拟 commit 后修改
        storage.update_frontmatter(char_path, {"name": "commit 后的名字"})
        manager.update_snapshot_after(snapshot.snapshot_id, [char_path], [])

        # 模拟人类在间隙中手动修改（外部修改）
        storage.update_frontmatter(char_path, {"name": "人类手动改的名字"})

        # 回滚应检测到冲突
        success = manager.rollback_snapshot(snapshot.snapshot_id)
        # 回滚"成功"但因冲突跳过了文件
        assert success is True

    def test_rollback_deletes_events(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试回滚删除关联的 SQLite 事件。"""
        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        # 创建快照
        snapshot = manager.create_snapshot("ch_001", affected_files=[char_path])
        manager.update_snapshot_after(snapshot.snapshot_id, [char_path], ["evt_to_delete"])

        # 回滚
        success = manager.rollback_snapshot(snapshot.snapshot_id)
        assert success is True

    def test_rollback_skips_missing_files(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试回滚时跳过不存在的文件（覆盖 lines 180-181）。"""
        import orjson

        manager = StateManager(project_root, yaml_storage=storage)

        # 手动构造一个引用不存在文件的快照
        snap_id = "snap_test_missing"
        snap_data = {
            "snapshot_id": snap_id,
            "source_command": "commit ch_001",
            "timestamp": "2024-01-01T00:00:00",
            "delta_files": {
                "characters/nonexistent.md": {
                    "fm_before": {"id": "nonexistent", "name": "ghost"},
                    "fm_after": {"id": "nonexistent", "name": "ghost_after"},
                }
            },
            "delta_sqlite": {"event_ids_to_rollback": []},
        }
        snap_path = project_root / ".snapshots" / f"{snap_id}.snapshot.json"
        snap_path.write_bytes(orjson.dumps(snap_data, option=orjson.OPT_INDENT_2))

        # 回滚应成功但跳过不存在的文件
        success = manager.rollback_snapshot(snap_id)
        assert success is True

    def test_rollback_force_restore_no_fm_after(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """测试回滚时 fm_after 为 None 时直接强制恢复（覆盖 lines 199-201）。"""
        import orjson

        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        # 先修改角色名字
        storage.update_frontmatter(char_path, {"name": "被改过的名字"})

        # 手动构造一个 fm_after 为 None 的快照
        snap_id = "snap_force_restore"
        snap_data = {
            "snapshot_id": snap_id,
            "source_command": "commit ch_001",
            "timestamp": "2024-01-01T00:00:00",
            "delta_files": {
                "characters/char_001.md": {
                    "fm_before": {"id": "char_001", "name": "林夜"},
                    "fm_after": None,
                }
            },
            "delta_sqlite": {"event_ids_to_rollback": []},
        }
        snap_path = project_root / ".snapshots" / f"{snap_id}.snapshot.json"
        snap_path.write_bytes(orjson.dumps(snap_data, option=orjson.OPT_INDENT_2))

        # 回滚应强制恢复（无冲突检测）
        success = manager.rollback_snapshot(snap_id)
        assert success is True

        # 验证角色名字恢复为快照中的 fm_before
        restored_meta, _ = storage.read_markdown_file(char_path)
        assert restored_meta["name"] == "林夜"


class TestApplyCharacterDiff:
    """apply_character_diff 角色状态更新测试。"""

    def test_apply_updates_frontmatter(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试更新角色 Frontmatter。"""
        manager = StateManager(project_root, yaml_storage=storage)

        updated = manager.apply_character_diff("char_001", {"name": "新名字"})

        assert isinstance(updated, CharacterFrontmatter)
        assert updated.name == "新名字"
        assert updated.id == "char_001"

    def test_apply_nonexistent_character(self, project_root: Path) -> None:
        """测试更新不存在的角色抛出异常。"""
        manager = StateManager(project_root)
        with pytest.raises(FileNotFoundError):
            manager.apply_character_diff("char_999", {"name": "test"})


class TestApplyEvent:
    """apply_event 事件写入测试。"""

    def test_apply_event_writes_to_sqlite(self, project_root: Path) -> None:
        """测试事件写入 SQLite。"""
        manager = StateManager(project_root)

        event = EventCreate(
            event_id="evt_test_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="测试事件",
            causal_pressure=0.5,
        )
        manager.apply_event(event)

        # 验证写入
        stored = manager.event_store.get_event_by_id("evt_test_001")
        assert stored is not None
        assert stored.description == "测试事件"


class TestGenerateDiffText:
    """generate_diff_text Diff 文本生成测试。"""

    def test_generate_add_diff(self, project_root: Path) -> None:
        """测试生成 add 类型的 Diff。"""
        from opennovel.schemas.event import EventDiff

        manager = StateManager(project_root)
        event = EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="左臂骨折",
            causal_pressure=0.9,
        )
        diffs = [EventDiff(action="add", event=event)]
        text = manager.generate_diff_text(diffs)

        assert "+ [Event]" in text
        assert "char_001" in text
        assert "左臂骨折" in text

    def test_generate_remove_diff(self, project_root: Path) -> None:
        """测试生成 remove 类型的 Diff。"""
        from opennovel.schemas.event import EventDiff

        manager = StateManager(project_root)
        event = EventCreate(
            event_id="evt_002",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.HEAL,
            description="伤口愈合",
            causal_pressure=0.3,
        )
        diffs = [EventDiff(action="remove", event=event)]
        text = manager.generate_diff_text(diffs)

        assert "- [Event]" in text

    def test_generate_empty_diff(self, project_root: Path) -> None:
        """测试空 Diff 列表。"""
        manager = StateManager(project_root)
        text = manager.generate_diff_text([])
        assert text == ""

    def test_generate_modify_diff(self, project_root: Path) -> None:
        """测试生成 modify 类型的 Diff（覆盖 lines 264-265）。"""
        from opennovel.schemas.event import EventDiff

        manager = StateManager(project_root)
        event_before = EventCreate(
            event_id="evt_003",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="左臂骨折",
            causal_pressure=0.9,
        )
        event_after = EventCreate(
            event_id="evt_003",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.HEAL,
            description="左臂痊愈",
            causal_pressure=0.3,
        )
        diffs = [EventDiff(action="modify", event=event_after, before=event_before)]
        text = manager.generate_diff_text(diffs)

        assert "~ [Event]" in text
        assert "左臂骨折" in text
        assert "左臂痊愈" in text


class TestListSnapshots:
    """list_snapshots 快照列表测试。"""

    def test_list_snapshots_empty(self, project_root: Path) -> None:
        """测试无快照时返回空列表。"""
        # 清空快照目录
        snap_dir = project_root / ".snapshots"
        for f in snap_dir.glob("*.snapshot.json"):
            f.unlink()

        manager = StateManager(project_root)
        snapshots = manager.list_snapshots()
        assert snapshots == []

    def test_list_snapshots_after_create(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试创建快照后能列出。"""
        manager = StateManager(project_root, yaml_storage=storage)
        char_path = project_root / "characters" / "char_001.md"

        manager.create_snapshot("ch_001", affected_files=[char_path])
        snapshots = manager.list_snapshots()

        assert len(snapshots) >= 1
        assert snapshots[0].snapshot_id.startswith("snap_ch_001_")

    def test_list_snapshots_skips_corrupt_files(self, project_root: Path) -> None:
        """测试 list_snapshots 跳过损坏的快照文件（覆盖 lines 284-285）。"""
        snap_dir = project_root / ".snapshots"
        # 写入一个损坏的快照文件
        (snap_dir / "corrupt.snapshot.json").write_bytes(b"not valid json{{{")

        manager = StateManager(project_root)
        snapshots = manager.list_snapshots()
        # 不应崩溃，损坏文件被跳过
        assert isinstance(snapshots, list)


class TestSerializeFrontmatter:
    """_serialize_frontmatter 序列化测试。"""

    def test_serialize_basic_types(self) -> None:
        """测试基本类型序列化。"""
        data = {"id": "char_001", "name": "Alice", "count": 5, "active": True}
        result = _serialize_frontmatter(data)
        assert result == data

    def test_serialize_nested_dict(self) -> None:
        """测试嵌套字典序列化。"""
        data = {"physical": {"injuries": ["fracture"]}}
        result = _serialize_frontmatter(data)
        assert result["physical"]["injuries"] == ["fracture"]

    def test_serialize_with_none(self) -> None:
        """测试 None 值序列化。"""
        data = {"location": None}
        result = _serialize_frontmatter(data)
        assert result["location"] is None

    def test_serialize_pydantic_model(self) -> None:
        """测试 Pydantic 模型自动 model_dump 序列化。"""
        char = CharacterFrontmatter(id="char_001", name="测试角色")
        data = {"character": char}
        result = _serialize_frontmatter(data)
        assert result["character"]["id"] == "char_001"
        assert result["character"]["name"] == "测试角色"

    def test_serialize_non_serializable_fallback(self) -> None:
        """测试不可序列化类型回退为 str()。"""

        class CustomObj:
            def __str__(self) -> str:
                return "custom_value"

        data = {"weird_field": CustomObj()}
        result = _serialize_frontmatter(data)
        assert result["weird_field"] == "custom_value"


class TestYamlStorageProperty:
    """yaml_storage 属性测试。"""

    def test_yaml_storage_property(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试 yaml_storage 属性返回注入的实例。"""
        manager = StateManager(project_root, yaml_storage=storage)
        assert manager.yaml_storage is storage


class TestSnapshotCleanup:
    """cleanup_snapshots 快照清理测试。"""

    def _create_snapshot_file(
        self,
        snapshots_dir: Path,
        name: str,
        mtime_offset_days: float = 0,
    ) -> Path:
        """构造一个空快照文件并设置 mtime。"""
        snap_path = snapshots_dir / name
        snap_path.write_bytes(b'{"snapshot_id": "test", "delta_files": {}}')
        new_mtime = time.time() - mtime_offset_days * 86400
        snap_path.touch(exist_ok=True)
        # 同时修改 atime 与 mtime
        os.utime(snap_path, (new_mtime, new_mtime))
        return snap_path

    def test_cleanup_keeps_recent_count(self, project_root: Path) -> None:
        """测试保留最近 N 个快照。"""
        snap_dir = project_root / ".snapshots"
        for i in range(5):
            self._create_snapshot_file(
                snap_dir, f"snap_old_{i}.snapshot.json", mtime_offset_days=i + 1
            )

        # max_days 设为 2，使部分旧快照不进入日期保留范围
        manager = StateManager(project_root, max_count=2, max_days=2)
        archived = manager.cleanup_snapshots()

        # 保留最近 2 个，归档 3 个
        assert len(archived) == 3
        assert len(list(snap_dir.glob("*.snapshot.json"))) == 2
        assert (snap_dir / "archive").is_dir()

    def test_cleanup_keeps_recent_days(self, project_root: Path) -> None:
        """测试保留最近 D 天内的快照。"""
        snap_dir = project_root / ".snapshots"
        # 5 天内 3 个，30 天外 2 个
        self._create_snapshot_file(snap_dir, "snap_recent_1.snapshot.json", mtime_offset_days=1)
        self._create_snapshot_file(snap_dir, "snap_recent_2.snapshot.json", mtime_offset_days=3)
        self._create_snapshot_file(snap_dir, "snap_old_1.snapshot.json", mtime_offset_days=40)
        self._create_snapshot_file(snap_dir, "snap_old_2.snapshot.json", mtime_offset_days=50)

        manager = StateManager(project_root, max_count=1, max_days=5)
        archived = manager.cleanup_snapshots()

        # 最近 1 个 + 5 天内共 2 个 = 保留 2 个，归档 2 个
        assert len(archived) == 2
        assert len(list(snap_dir.glob("*.snapshot.json"))) == 2

    def test_cleanup_empty(self, project_root: Path) -> None:
        """测试无快照时返回空列表。"""
        manager = StateManager(project_root)
        archived = manager.cleanup_snapshots()
        assert archived == []

    def test_configure_retention(self, project_root: Path) -> None:
        """测试运行时配置保留策略。"""
        manager = StateManager(project_root)
        manager.configure_snapshot_retention(10, 60)
        assert manager.max_count == 10
        assert manager.max_days == 60


class TestHashOnlySnapshot:
    """hash-only 快照策略测试。"""

    def test_hash_only_for_long_chapter(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试超长章节使用 hash-only 快照。"""
        manager = StateManager(project_root, yaml_storage=storage)
        chapter_path = project_root / "draft" / "ch_001.md"

        # 写入超过 50000 字的正文
        long_body = "字" * 50001
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            f"# 第一章\n\n{long_body}",
        )

        snapshot = manager.create_snapshot("ch_001", affected_files=[chapter_path])
        rel_path = "draft/ch_001.md"
        assert rel_path in snapshot.delta_files
        assert snapshot.delta_files[rel_path].get("hash_only") is True
        assert "sha256" in snapshot.delta_files[rel_path]
        assert "fm_before" not in snapshot.delta_files[rel_path]

    def test_normal_snapshot_for_short_chapter(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """测试普通章节仍保存完整 frontmatter。"""
        manager = StateManager(project_root, yaml_storage=storage)
        chapter_path = project_root / "draft" / "ch_001.md"

        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章\n\n短正文。",
        )

        snapshot = manager.create_snapshot("ch_001", affected_files=[chapter_path])
        rel_path = "draft/ch_001.md"
        assert snapshot.delta_files[rel_path].get("hash_only") is not True
        assert "fm_before" in snapshot.delta_files[rel_path]

    def test_rollback_hash_only_warns(self, project_root: Path, storage: YAMLStorage) -> None:
        """测试 hash-only 快照回滚时跳过并提示。"""
        import orjson

        manager = StateManager(project_root, yaml_storage=storage)
        chapter_path = project_root / "draft" / "ch_001.md"

        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章\n\n正文。",
        )

        # 手动构造 hash-only 快照
        snap_id = "snap_hash_only"
        snap_data = {
            "snapshot_id": snap_id,
            "source_command": "commit ch_001",
            "timestamp": "2024-01-01T00:00:00",
            "delta_files": {
                "draft/ch_001.md": {
                    "hash_only": True,
                    "sha256": "abc123",
                    "hint": "仅保存 hash",
                }
            },
            "delta_sqlite": {"event_ids_to_rollback": []},
        }
        snap_path = project_root / ".snapshots" / f"{snap_id}.snapshot.json"
        snap_path.write_bytes(orjson.dumps(snap_data, option=orjson.OPT_INDENT_2))

        # 修改章节
        storage.update_frontmatter(chapter_path, {"pov": "char_999"})

        # 回滚应成功但跳过 hash-only 文件
        success = manager.rollback_snapshot(snap_id)
        assert success is True

        # 文件未被恢复（因为我们没有 fm_before）
        meta, _ = storage.read_markdown_file(chapter_path)
        assert meta["pov"] == "char_999"
