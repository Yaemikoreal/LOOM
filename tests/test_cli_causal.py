"""cli/causal 模块测试 - novel causal 命令。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from opennovel.cli.main import app
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.sqlite import EventStore

runner = CliRunner()


@pytest.fixture
def project_with_events(tmp_path: Path) -> Path:
    """创建包含因果链事件的项目目录。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = project_dir / ".novel.db"

    with EventStore(db_path) as store:
        store.add_event(
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="主角被刺伤",
                causal_pressure=0.8,
            )
        )
        store.add_event(
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_002",
                timestamp="第2天",
                character_id="char_001",
                event_type=EventType.KNOWLEDGE,
                description="主角发现伤口感染",
                causal_pressure=0.7,
                caused_by="evt_001",
            )
        )

    return project_dir


class TestCausalCommand:
    """novel causal 命令测试。"""

    def test_causal_help(self) -> None:
        """测试 --help 输出。"""
        result = runner.invoke(app, ["causal", "--help"])
        assert result.exit_code == 0
        assert "因果链" in result.output

    def test_causal_chain_output(self, project_with_events: Path) -> None:
        """查询因果链返回前置事件。"""
        result = runner.invoke(app, ["causal", "--event", "evt_002", str(project_with_events)])
        assert result.exit_code == 0
        assert "evt_001" not in result.output  # 只显示内容，不显示 ID
        assert "主角被刺伤" in result.output

    def test_causal_descendants_output(self, project_with_events: Path) -> None:
        """查询因果后继返回后续事件。"""
        result = runner.invoke(
            app, ["causal", "--event", "evt_001", "--descendants", str(project_with_events)]
        )
        assert result.exit_code == 0
        assert "伤口感染" in result.output

    def test_causal_no_db(self, tmp_path: Path) -> None:
        """无数据库时报错。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = runner.invoke(app, ["causal", "--event", "evt_001", str(project_dir)])
        assert result.exit_code == 1
        assert "不存在" in result.output
