"""CLI commit 命令集成测试 - 状态审阅与固化流程。"""

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from opennovel.agents.auditor import AuditorAbortError, ExtractionResult
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.yaml_storage import YAMLStorage

# ── Mock 对象 ──


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


# ── Fixtures ──


@pytest.fixture
def commit_project(tmp_path: Path) -> dict:
    """创建 commit 测试所需的完整项目结构。"""
    project_root = tmp_path
    (project_root / "draft").mkdir()
    (project_root / "characters").mkdir()
    (project_root / "prompts").mkdir()
    (project_root / ".snapshots").mkdir()

    storage = YAMLStorage()

    # 角色文件
    storage.write_markdown_file(
        project_root / "characters" / "char_001.md",
        {
            "id": "char_001",
            "name": "主角",
            "physical": {"injuries": [], "buffs": [], "debuffs": []},
        },
        "# 主角背景",
    )

    # 章节文件
    chapter_path = project_root / "draft" / "ch_001.md"
    storage.write_markdown_file(
        chapter_path,
        {
            "id": "ch_001",
            "pov": "char_001",
            "active_characters": ["char_001"],
        },
        "# 第一章\n\n主角在塔中醒来。",
    )

    # Prompt 文件
    (project_root / "prompts" / "auditor.v1.md").write_text("你是审稿官。", encoding="utf-8")

    return {"project_root": project_root, "chapter_path": chapter_path, "storage": storage}


VALID_EVENTS_JSON = """[
    {
        "event_id": "evt_001",
        "chapter_id": "ch_001",
        "timestamp": "第1天",
        "character_id": "char_001",
        "event_type": "INJURY",
        "description": "左臂受伤",
        "causal_pressure": 0.9
    }
]"""


# ── commit 流程测试 ──


class TestCommitFlow:
    """commit 核心流程测试（mock LLM + mock prompt）。"""

    @patch("typer.prompt", return_value="y")
    def test_successful_commit(self, mock_prompt: MagicMock, commit_project: dict) -> None:
        """测试完整 commit 流程：快照→提取→Diff→确认→固化。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]
        llm = MockLLMBus([VALID_EVENTS_JSON])

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=llm),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")

        # 验证快照已创建
        snapshots = list((root / ".snapshots").glob("*.json"))
        assert len(snapshots) >= 1

    @patch("typer.prompt", return_value="n")
    def test_user_cancels_commit(self, mock_prompt: MagicMock, commit_project: dict) -> None:
        """测试用户在审阅步骤选择取消。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]
        llm = MockLLMBus([VALID_EVENTS_JSON])

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=llm),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")

        # 快照应该已创建（Step 1 在取消前执行）
        snapshots = list((root / ".snapshots").glob("*.json"))
        assert len(snapshots) >= 1

    def test_chapter_not_found(self, commit_project: dict) -> None:
        """测试章节文件不存在时退出。"""
        import click

        from opennovel.cli.commit import commit

        root = commit_project["project_root"]

        with pytest.raises(click.exceptions.Exit):
            commit(chapter="nonexistent.md", path=str(root), model="test")

    def test_auditor_abort(self, commit_project: dict) -> None:
        """测试 Auditor 触发 abort 时正常退出。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]
        mock_llm = MagicMock()

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=mock_llm),
            patch(
                "opennovel.agents.auditor.Auditor.extract_events_with_retry",
                side_effect=AuditorAbortError("abort"),
            ),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")

    def test_dirty_result(self, commit_project: dict) -> None:
        """测试脏提交结果时正常退出。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]
        mock_llm = MagicMock()
        dirty_result = ExtractionResult(events=[], success=False, dirty=True, error="failed")

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=mock_llm),
            patch(
                "opennovel.agents.auditor.Auditor.extract_events_with_retry",
                return_value=dirty_result,
            ),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")

    def test_no_events_detected(self, commit_project: dict) -> None:
        """测试未检测到事件时正常退出。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]
        mock_llm = MagicMock()
        empty_result = ExtractionResult(events=[], success=True)

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=mock_llm),
            patch(
                "opennovel.agents.auditor.Auditor.extract_events_with_retry",
                return_value=empty_result,
            ),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")


class TestCommitStep5Writeback:
    """Step 5 写入固化测试。"""

    @patch("typer.prompt", return_value="y")
    def test_events_written_to_storage(self, mock_prompt: MagicMock, commit_project: dict) -> None:
        """测试确认后事件被写入状态。"""
        from opennovel.cli.commit import commit

        root = commit_project["project_root"]

        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="受伤",
                causal_pressure=0.9,
            ),
        ]
        extraction_result = ExtractionResult(events=events, success=True)

        mock_llm = MagicMock()
        mock_auditor = MagicMock()
        mock_auditor.extract_events_with_retry.return_value = extraction_result
        mock_auditor.generate_diffs.return_value = []
        mock_auditor.apply_confirmed_events.return_value = ["evt_001"]

        with (
            patch("opennovel.cli.commit.LLMBus", return_value=mock_llm),
            patch("opennovel.agents.auditor.Auditor", return_value=mock_auditor),
            contextlib.suppress(SystemExit),
        ):
            commit(chapter="ch_001.md", path=str(root), model="test")

        mock_auditor.apply_confirmed_events.assert_called_once()
        called_events = mock_auditor.apply_confirmed_events.call_args[0][0]
        assert len(called_events) == 1
        assert called_events[0].event_id == "evt_001"


class TestCommitSnapshotCreation:
    """快照创建测试。"""

    def test_snapshot_includes_affected_files(self, commit_project: dict) -> None:
        """测试快照包含章节和活跃角色文件。"""
        from opennovel.core.state_manager import StateManager

        root = commit_project["project_root"]
        manager = StateManager(root)

        chapter_path = root / "draft" / "ch_001.md"
        char_path = root / "characters" / "char_001.md"

        snapshot = manager.create_snapshot("ch_001", affected_files=[chapter_path, char_path])
        assert snapshot is not None
        assert "ch_001" in snapshot.snapshot_id
        assert len(snapshot.delta_files) >= 1
