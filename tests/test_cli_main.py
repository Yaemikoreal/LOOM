"""cli/main 模块测试 - init、rollback、diff、doctor 命令。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from opennovel.cli.main import app
from opennovel.storage.yaml_storage import YAMLStorage

runner = CliRunner()


class TestInitCommand:
    """novel init 命令测试。"""

    def test_init_creates_directories(self, tmp_path: Path) -> None:
        """测试 init 创建标准目录结构。"""
        project_dir = tmp_path / "my_novel"
        project_dir.mkdir()

        result = runner.invoke(app, ["init", str(project_dir)])

        assert result.exit_code == 0
        assert (project_dir / "canon").is_dir()
        assert (project_dir / "characters").is_dir()
        assert (project_dir / "draft").is_dir()
        assert (project_dir / "outlines").is_dir()
        assert (project_dir / "subconscious").is_dir()
        assert (project_dir / ".snapshots").is_dir()

    def test_init_creates_templates(self, tmp_path: Path) -> None:
        """测试 init 生成模板文件。"""
        project_dir = tmp_path / "my_novel"
        project_dir.mkdir()

        result = runner.invoke(app, ["init", str(project_dir)])

        assert result.exit_code == 0
        assert (project_dir / "characters" / "char_001.md").exists()
        assert (project_dir / "canon" / "world_rules.md").exists()
        assert (project_dir / "draft" / "ch_001.md").exists()
        assert (project_dir / "novel.yaml").exists()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        """测试 init 幂等性（重复运行不报错）。"""
        project_dir = tmp_path / "my_novel"
        project_dir.mkdir()

        runner.invoke(app, ["init", str(project_dir)])
        result = runner.invoke(app, ["init", str(project_dir)])

        assert result.exit_code == 0

    def test_init_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试默认路径（当前目录）。"""
        from opennovel.core.global_config import GlobalConfig

        monkeypatch.chdir(tmp_path)
        # 交互式模式：提供项目名称、模板、模式
        result = runner.invoke(app, ["init"], input="test_novel\nstandard\nquick\n")

        assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output[:200]}"
        # 交互式模式下项目创建在 workspace 目录
        ws_dir = GlobalConfig.load().workspace_dir
        project_dir = ws_dir / "test_novel"
        assert (project_dir / "canon").is_dir(), f"Project not found at {project_dir}"


class TestDiffCommand:
    """novel diff 命令测试。"""

    def test_diff_no_chapters(self, tmp_path: Path) -> None:
        """测试无章节文件时正常退出（直接调用函数）。"""
        from opennovel.core.diff_checker import DiffChecker

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / "characters").mkdir()

        checker = DiffChecker(project_dir)
        mismatches = checker.check_all()
        assert mismatches == []

    def test_diff_nonexistent_chapter(self, tmp_path: Path) -> None:
        """测试指定不存在的章节应报错。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "draft").mkdir()

        result = runner.invoke(app, ["diff", "nonexistent.md", str(project_dir)])

        assert result.exit_code != 0


class TestRollbackCommand:
    """novel rollback 命令测试。"""

    def test_rollback_nonexistent_snapshot(self, tmp_path: Path) -> None:
        """测试回滚不存在的快照。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".snapshots").mkdir()

        result = runner.invoke(app, ["rollback", "snap_nonexistent", str(project_dir)])

        assert result.exit_code == 0
        assert "失败" in result.output or "不存在" in result.output


class TestDiffWithChapter:
    """novel diff 指定章节测试。"""

    def test_diff_with_valid_chapter(self, tmp_path: Path) -> None:
        """测试对有效章节运行 diff。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / "characters").mkdir()

        storage = YAMLStorage()
        storage.write_markdown_file(
            project_dir / "characters" / "char_001.md",
            {
                "id": "char_001",
                "name": "角色",
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
            },
            "# 背景",
        )
        storage.write_markdown_file(
            project_dir / "draft" / "ch_001.md",
            {"id": "ch_001", "pov": "char_001", "active_characters": ["char_001"]},
            "# 第一章\n\n正文内容。",
        )

        result = runner.invoke(app, ["diff", "ch_001.md", str(project_dir)])
        assert result.exit_code == 0

    def test_diff_all_chapters(self, tmp_path: Path) -> None:
        """测试不指定章节时扫描全部。"""
        from opennovel.core.diff_checker import DiffChecker

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / "characters").mkdir()

        # 直接调用 DiffChecker 避免 CliRunner 的 Windows 权限问题
        checker = DiffChecker(project_dir)
        mismatches = checker.check_all()
        assert mismatches == []


class TestDoctorCommand:
    """novel doctor 命令测试。"""

    def test_doctor_empty_project(self, tmp_path: Path) -> None:
        """测试空项目诊断。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "characters").mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / ".snapshots").mkdir()

        result = runner.invoke(app, ["doctor", str(project_dir)])

        assert result.exit_code == 0

    def test_doctor_with_issues(self, tmp_path: Path) -> None:
        """测试有诊断问题时渲染表格。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "characters").mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / ".snapshots").mkdir()

        # 创建孤立角色（没有被任何章节引用）
        storage = YAMLStorage()
        storage.write_markdown_file(
            project_dir / "characters" / "char_001.md",
            {"id": "char_001", "name": "孤立角色"},
            "# 背景",
        )

        result = runner.invoke(app, ["doctor", str(project_dir)])
        assert result.exit_code == 0

    def test_doctor_healthy_project(self, tmp_path: Path) -> None:
        """测试健康项目显示 OK。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "characters").mkdir()
        (project_dir / "draft").mkdir()
        (project_dir / ".snapshots").mkdir()

        result = runner.invoke(app, ["doctor", str(project_dir)])
        assert result.exit_code == 0
        # 空项目无问题，应显示健康
        output = result.output
        has_ok = "健康" in output or "0 个 ERROR" in output
        assert has_ok or "0 个 WARNING" in output
