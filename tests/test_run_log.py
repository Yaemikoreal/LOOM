"""运行日志与断点续跑测试。"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.core.auto_runner import AutoRunner, RunLog, _RunLogManager
from opennovel.core.config import LoomConfig


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """创建最小项目结构。"""
    root = tmp_path / "project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    (root / "canon").mkdir()
    (root / "subconscious").mkdir()
    (root / "outlines").mkdir()
    (root / "novel.yaml").write_text("model: test\n", encoding="utf-8")
    (root / "outlines" / "story.md").write_text(
        "## 第一章\n\n第一章提示。\n\n## 第二章\n\n第二章提示。\n",
        encoding="utf-8",
    )
    return root


class TestRunLog:
    """RunLog 数据类测试。"""

    def test_to_dict(self) -> None:
        """测试序列化。"""
        log = RunLog(
            run_id="run_20250709_001",
            novel="demo",
            completed=["ch_001"],
            failed=[],
            last_chapter="ch_001",
            status="running",
            created_at="2025-07-09T10:00:00",
            updated_at="2025-07-09T10:00:01",
        )
        data = log.to_dict()
        assert data["run_id"] == "run_20250709_001"
        assert data["completed"] == ["ch_001"]
        assert data["status"] == "running"

    def test_from_dict(self) -> None:
        """测试反序列化。"""
        data = {
            "run_id": "run_20250709_001",
            "novel": "demo",
            "completed": ["ch_001", "ch_002"],
            "failed": ["ch_003"],
            "last_chapter": "ch_002",
            "status": "paused",
            "created_at": "2025-07-09T10:00:00",
            "updated_at": "2025-07-09T10:00:02",
        }
        log = RunLog.from_dict(data)
        assert log.run_id == "run_20250709_001"
        assert log.completed == ["ch_001", "ch_002"]
        assert log.failed == ["ch_003"]
        assert log.status == "paused"


class TestRunLogManager:
    """_RunLogManager 测试。"""

    def test_start_run_creates_file(self, project_dir: Path) -> None:
        """测试 start_run 创建日志文件。"""
        manager = _RunLogManager(project_dir)
        log = manager.start_run("demo_novel")

        log_path = project_dir / "logs" / f"run_{log.run_id}.json"
        assert log_path.exists()
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["novel"] == "demo_novel"
        assert data["status"] == "running"
        assert data["completed"] == []

    def test_update_run_persists(self, project_dir: Path) -> None:
        """测试 update_run 持久化更新。"""
        manager = _RunLogManager(project_dir)
        log = manager.start_run("demo_novel")
        manager.update_run(
            completed=["ch_001", "ch_002"],
            failed=["ch_003"],
            last_chapter="ch_002",
        )

        log_path = project_dir / "logs" / f"run_{log.run_id}.json"
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["completed"] == ["ch_001", "ch_002"]
        assert data["failed"] == ["ch_003"]
        assert data["last_chapter"] == "ch_002"
        assert "updated_at" in data

    def test_finish_run_updates_status(self, project_dir: Path) -> None:
        """测试 finish_run 更新状态。"""
        manager = _RunLogManager(project_dir)
        log = manager.start_run("demo_novel")
        manager.finish_run(status="completed")

        log_path = project_dir / "logs" / f"run_{log.run_id}.json"
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["status"] == "completed"

    def test_load_latest_unfinished(self, project_dir: Path) -> None:
        """测试加载最近未完成的运行日志。"""
        manager = _RunLogManager(project_dir)
        manager.start_run("demo_novel")
        manager.update_run(completed=["ch_001"], last_chapter="ch_001")

        loaded = manager.load_latest_unfinished()
        assert loaded is not None
        assert loaded.novel == "demo_novel"
        assert loaded.completed == ["ch_001"]

    def test_load_latest_unfinished_skips_completed(self, project_dir: Path) -> None:
        """测试已完成的运行日志不会被加载。"""
        manager = _RunLogManager(project_dir)
        manager.start_run("demo_novel")
        manager.finish_run(status="completed")

        loaded = manager.load_latest_unfinished()
        assert loaded is None


class TestAutoRunnerResume:
    """AutoRunner 断点续跑测试（使用 mock LLM）。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_creates_run_log(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        project_dir: Path,
    ) -> None:
        """测试 run() 创建运行日志文件。"""
        config = LoomConfig()
        config.target_chapters = 2
        runner = AutoRunner(project_root=project_dir, config=config)

        outline_text = (project_dir / "outlines" / "story.md").read_text(encoding="utf-8")
        # 使用 mock 跳过实际 LLM 调用：让 run_chapter 抛出异常，验证日志仍被创建
        with patch.object(runner, "run_chapter", side_effect=Exception("mock failure")):
            runner.run(outline_text)

        log_files = list((project_dir / "logs").glob("run_*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text(encoding="utf-8"))
        assert data["status"] == "failed"
        assert "ch_001" in data["failed"]
        assert data["completed"] == []

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_resume_uses_latest_unfinished(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        project_dir: Path,
    ) -> None:
        """测试 resume() 加载最近未完成的日志并跳过已完成章节。"""
        config = LoomConfig()
        config.target_chapters = 2
        runner = AutoRunner(project_root=project_dir, config=config)

        # 预置一个未完成的运行日志
        manager = _RunLogManager(project_dir)
        manager.start_run(project_dir.name)
        manager.update_run(completed=["ch_001"], last_chapter="ch_001")

        outline_text = (project_dir / "outlines" / "story.md").read_text(encoding="utf-8")

        # 记录 run 被调用时的 start_from_chapter 参数
        start_from: list[str | None] = []

        def fake_run(outline: str, start_from_chapter: str | None = None) -> MagicMock:
            start_from.append(start_from_chapter)
            report = MagicMock()
            report.failed_chapters = 0
            report.total_chapters = 1
            return report

        with patch.object(runner, "run", side_effect=fake_run):
            runner.resume(outline_text)

        assert start_from == ["ch_002"]

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_consecutive_failures_pause_run(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        project_dir: Path,
    ) -> None:
        """测试连续 3 章失败时暂停运行。"""
        config = LoomConfig()
        config.target_chapters = 3
        runner = AutoRunner(project_root=project_dir, config=config)

        # 写入 3 章大纲
        outline_path = project_dir / "outlines" / "story.md"
        outline_path.write_text(
            "## 第一章\n\n第一章提示。\n\n## 第二章\n\n第二章提示。\n\n## 第三章\n\n第三章提示。\n",
            encoding="utf-8",
        )
        outline_text = outline_path.read_text(encoding="utf-8")

        with (
            patch.object(runner, "run_chapter", side_effect=Exception("mock failure")),
            pytest.raises(RuntimeError, match="连续 3 章创作失败"),
        ):
            runner.run(outline_text)

        log_files = list((project_dir / "logs").glob("run_*.json"))
        data = json.loads(log_files[0].read_text(encoding="utf-8"))
        assert data["status"] == "paused"
        assert data["failed"] == ["ch_001", "ch_002", "ch_003"]
