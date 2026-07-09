"""cli/report 模块测试 - novel report 命令。"""

from pathlib import Path

from typer.testing import CliRunner

from opennovel.cli.main import app
from opennovel.storage.metrics import MetricsStore

runner = CliRunner()


class TestReportCommandHelp:
    """novel report --help 测试。"""

    def test_report_help(self) -> None:
        """测试 --help 输出。"""
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
        assert "成本" in result.output


class TestReportCostCommand:
    """novel report --cost 测试。"""

    def test_report_cost_no_db(self, tmp_path: Path) -> None:
        """无指标数据库时报错。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = runner.invoke(app, ["report", "--cost", str(project_dir)])
        assert result.exit_code == 1
        assert "未找到" in result.output

    def test_report_cost_empty(self, tmp_path: Path) -> None:
        """有数据库但无记录时提示。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        db_path = project_dir / ".novel.db"
        with MetricsStore(db_path):
            pass
        result = runner.invoke(app, ["report", "--cost", str(project_dir)])
        assert result.exit_code == 0
        assert "暂无" in result.output

    def test_report_cost_with_data(self, tmp_path: Path) -> None:
        """有 Token 记录时输出成本报告。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        db_path = project_dir / ".novel.db"
        with MetricsStore(db_path) as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 1000, 2000)

        result = runner.invoke(app, ["report", "--cost", str(project_dir)])
        assert result.exit_code == 0
        assert "writer" in result.output
        assert "gpt-4" in result.output
        assert "总估算成本" in result.output
