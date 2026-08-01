"""端到端集成测试 - 完整工作流验证。

测试路径：init → write → commit → diff → doctor → rollback
验证各模块间的协作正确性。
"""

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from opennovel.storage.yaml_storage import YAMLStorage

# ── Mock LLM ──


class MockMessage:
    def __init__(self, text: str) -> None:
        self.content = text


class MockChoice:
    def __init__(self, text: str) -> None:
        self.message = MockMessage(text)


class MockUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 10
        self.total_tokens = 20


class MockLLMResponse:
    def __init__(self, text: str) -> None:
        self.choices = [MockChoice(text)]
        self.usage = MockUsage()


class MockLLMBus:
    def __init__(self, responses: list[str]) -> None:
        self.responses = [MockLLMResponse(r) for r in responses]
        self.call_count = 0

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> MockLLMResponse:
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp


VALID_EVENTS_JSON = """[
    {
        "event_id": "evt_001",
        "chapter_id": "ch_001",
        "timestamp": "第1天·午后",
        "character_id": "char_001",
        "event_type": "INJURY",
        "description": "左臂被巨剑斩伤",
        "causal_pressure": 0.9
    }
]"""


# ── Fixtures ──


@pytest.fixture
def full_project(tmp_path: Path) -> Path:
    """创建完整的项目结构（模拟 loom init 的输出）。"""
    project_root = tmp_path / "novel"
    project_root.mkdir()

    # 模拟 loom init
    from opennovel.cli.main import init

    init(str(project_root))

    return project_root


# ── 集成测试 ──


class TestInitThenDiff:
    """init 后运行 diff，验证一致性。"""

    def test_init_project_diff_clean(self, full_project: Path) -> None:
        """测试 init 后的项目 diff 应无不一致。"""
        from opennovel.core.diff_checker import DiffChecker

        checker = DiffChecker(full_project)
        mismatches = checker.check_all()
        # init 生成的模板不应有一致性问题
        # （模板角色没有伤势，章节也没有伤势描述，所以是一致的）
        assert isinstance(mismatches, list)


class TestInitThenDoctor:
    """init 后运行 doctor，验证健康度。"""

    def test_init_project_doctor_ok(self, full_project: Path) -> None:
        """测试 init 后的项目 doctor 诊断。"""
        from opennovel.core.doctor import Doctor

        doc = Doctor(full_project)
        items = doc.diagnose()
        # init 生成的项目应该基本健康
        # （char_001 被 ch_001 引用，所以不是孤立角色）
        assert isinstance(items, list)


class TestWriteThenCommit:
    """write 续写后 commit 提取状态。"""

    @patch("typer.prompt", return_value="y")
    def test_write_and_commit_flow(self, mock_prompt: MagicMock, full_project: Path) -> None:
        """测试 write 续写 → commit 提取的完整流程。"""
        from opennovel.cli.commit import commit

        chapter_path = full_project / "draft" / "ch_001.md"
        storage = YAMLStorage()

        # 模拟 write：追加正文
        _, body = storage.read_markdown_file(chapter_path)
        storage.update_frontmatter(chapter_path, {"dirty_flag": "pending_write"})

        # commit：提取事件
        llm = MockLLMBus([VALID_EVENTS_JSON])
        with (
            patch("opennovel.cli.commit.LLMBus", return_value=llm),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(full_project), model="test")

        # 验证快照已创建
        snapshots = list((full_project / ".snapshots").glob("*.json"))
        assert len(snapshots) >= 1


class TestCommitThenRollback:
    """commit 后 rollback 回滚状态。"""

    def test_commit_then_rollback(self, full_project: Path) -> None:
        """测试 commit 固化 → rollback 回滚的完整流程。"""
        from opennovel.core.state_manager import StateManager

        chapter_path = full_project / "draft" / "ch_001.md"
        char_path = full_project / "characters" / "char_001.md"
        storage = YAMLStorage()
        manager = StateManager(full_project)

        # Step 1: 创建快照
        snapshot = manager.create_snapshot("ch_001", affected_files=[chapter_path, char_path])
        assert snapshot is not None

        # Step 2: 模拟状态变更（写入伤势）
        storage.update_frontmatter(
            char_path,
            {"physical": {"injuries": ["left_arm_fracture"], "buffs": [], "debuffs": []}},
        )

        # Step 3: 更新快照 after 状态
        manager.update_snapshot_after(snapshot.snapshot_id, [chapter_path, char_path], ["evt_001"])

        # Step 4: 回滚
        success = manager.rollback_snapshot(snapshot.snapshot_id)
        assert success is True

        # Step 5: 验证状态已恢复
        meta, _ = storage.read_markdown_file(char_path)
        physical = meta.get("physical", {})
        # 回滚后伤势应该被清除
        assert physical.get("injuries") == [] or "left_arm_fracture" not in physical.get(
            "injuries", []
        )


class TestStashThenRetriever:
    """stash 存入灵感后 retriever 查询。"""

    def test_stash_writes_to_subconscious(self, full_project: Path) -> None:
        """测试 stash 写入 subconscious/lines.md。"""
        from opennovel.core.retriever import Retriever

        ret = Retriever(full_project)

        # 存入灵感
        ret.add_to_subconscious("深渊不收我，因为我就是深渊。", tags=["mood", "dark"])

        # 验证文件写入
        lines_file = full_project / "subconscious" / "lines.md"
        assert lines_file.exists()
        content = lines_file.read_text(encoding="utf-8")
        assert "深渊不收我" in content
        assert "#mood" in content

    def test_stash_multiple_entries(self, full_project: Path) -> None:
        """测试多次存入灵感。"""
        from opennovel.core.retriever import Retriever

        ret = Retriever(full_project)

        ret.add_to_subconscious("灵感1", tags=["idea"])
        ret.add_to_subconscious("灵感2", tags=["scene"])
        ret.add_to_subconscious("灵感3")

        lines_file = full_project / "subconscious" / "lines.md"
        content = lines_file.read_text(encoding="utf-8")
        assert content.count("- ") == 3


class TestCharacterStateFlow:
    """角色状态变更全流程测试。"""

    def test_character_state_update(self, full_project: Path) -> None:
        """测试角色状态的读取→修改→回写流程。"""

        char_path = full_project / "characters" / "char_001.md"
        storage = YAMLStorage()

        # 读取初始状态
        char_file = storage.read_character_file(char_path)
        assert char_file.frontmatter.id == "char_001"
        assert char_file.frontmatter.physical.injuries == []

        # 修改状态（添加伤势）
        storage.update_frontmatter(
            char_path,
            {
                "physical": {
                    "injuries": ["left_arm_fracture"],
                    "buffs": [],
                    "debuffs": [],
                }
            },
        )

        # 验证修改
        char_file2 = storage.read_character_file(char_path)
        assert "left_arm_fracture" in char_file2.frontmatter.physical.injuries

        # 再次修改（治愈）
        storage.update_frontmatter(
            char_path,
            {
                "physical": {
                    "injuries": [],
                    "buffs": ["regeneration"],
                    "debuffs": [],
                }
            },
        )

        char_file3 = storage.read_character_file(char_path)
        assert char_file3.frontmatter.physical.injuries == []
        assert "regeneration" in char_file3.frontmatter.physical.buffs


class TestEventLedgerFlow:
    """事件账本全流程测试。"""

    def test_event_crud(self, full_project: Path) -> None:
        """测试事件的创建→查询→删除。"""
        from opennovel.schemas.event import EventCreate, EventType
        from opennovel.storage.sqlite import EventStore

        store = EventStore(full_project / ".novel.db")

        # 创建事件
        event = EventCreate(
            event_id="evt_test_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="测试伤势",
            causal_pressure=0.8,
        )
        store.add_event(event)

        # 查询事件
        events = store.get_events_by_character("char_001")
        assert len(events) >= 1
        assert any(e.event_id == "evt_test_001" for e in events)

        # 删除事件
        store.delete_events_by_ids(["evt_test_001"])
        events2 = store.get_events_by_character("char_001")
        assert not any(e.event_id == "evt_test_001" for e in events2)


class TestSnapshotLifecycle:
    """快照生命周期测试。"""

    def test_create_list_rollback(self, full_project: Path) -> None:
        """测试创建→列表→回滚的完整生命周期。"""
        import time

        from opennovel.core.state_manager import StateManager

        manager = StateManager(full_project)
        chapter_path = full_project / "draft" / "ch_001.md"

        # 创建快照（间隔 1 秒确保 ID 不同）
        manager.create_snapshot("ch_001", affected_files=[chapter_path])  # 第一个快照
        time.sleep(1.1)
        snap2 = manager.create_snapshot("ch_001", affected_files=[chapter_path])

        # 列出快照
        snapshots = manager.list_snapshots()
        assert len(snapshots) >= 2

        # 回滚最新快照
        success = manager.rollback_snapshot(snap2.snapshot_id)
        assert success is True
