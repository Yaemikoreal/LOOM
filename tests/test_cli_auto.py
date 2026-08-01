"""cli/auto 模块测试 - novel auto 命令。"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from opennovel.cli.main import app
from opennovel.storage.yaml_storage import YAMLStorage

runner = CliRunner()


class TestAutoCommandHelp:
    """novel auto --help 测试。"""

    def test_auto_help_contains_keyword(self) -> None:
        """测试 --help 输出包含 '三 Agent' 关键字。"""
        result = runner.invoke(app, ["auto", "--help"])
        assert result.exit_code == 0
        assert "三 Agent" in result.output


class TestAutoCommandNoOutline:
    """novel auto 大纲文件不存在时的错误处理测试。"""

    def test_auto_no_outline_file(self, tmp_path: Path) -> None:
        """测试大纲文件不存在时报错退出。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # 创建 novel.yaml 配置
        config = {
            "version": "1.0.1",
            "model": "gpt-4",
            "creative_direction": "测试创作",
            "target_chapters": 3,
            "words_per_chapter": 3000,
            "outline": "outlines/story.md",
        }
        config_path = project_dir / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        # 不创建 outlines/story.md
        result = runner.invoke(app, ["auto", str(project_dir)])
        assert result.exit_code != 0
        assert "不存在" in result.output

    def test_auto_empty_outline_file(self, tmp_path: Path) -> None:
        """测试大纲文件为空时报错退出。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # 创建 novel.yaml 配置
        config = {
            "version": "1.0.1",
            "model": "gpt-4",
            "creative_direction": "测试创作",
            "target_chapters": 3,
            "words_per_chapter": 3000,
            "outline": "outlines/story.md",
        }
        config_path = project_dir / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        # 创建空的大纲文件
        outline_dir = project_dir / "outlines"
        outline_dir.mkdir()
        outline_path = outline_dir / "story.md"
        outline_path.write_text("", encoding="utf-8")

        result = runner.invoke(app, ["auto", str(project_dir)])
        assert result.exit_code != 0
        assert "为空" in result.output


class TestAutoCommandDryRun:
    """novel auto --dry-run 测试。"""

    def _create_project_structure(self, project_dir: Path) -> None:
        """创建完整的项目结构用于 dry-run 测试。

        Args:
            project_dir: 项目根目录路径
        """
        # 创建 novel.yaml 配置
        config = {
            "version": "1.0.1",
            "model": "gpt-4",
            "creative_direction": "黑暗奇幻风格，注重氛围营造",
            "target_chapters": 2,
            "words_per_chapter": 3000,
            "outline": "outlines/story.md",
        }
        config_path = project_dir / "novel.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        # 创建大纲文件（含 ## 标题分隔的章节）
        outline_dir = project_dir / "outlines"
        outline_dir.mkdir(parents=True, exist_ok=True)
        outline_content = """# 故事大纲

## 第一章：命运的开端

主角在酒馆中接到神秘委托，踏上冒险之旅。

## 第二章：黑暗森林

主角进入被诅咒的森林，遭遇第一场战斗。
"""
        outline_path = outline_dir / "story.md"
        outline_path.write_text(outline_content, encoding="utf-8")

        # 创建角色文件
        storage = YAMLStorage()
        characters_dir = project_dir / "characters"
        characters_dir.mkdir(parents=True, exist_ok=True)
        storage.write_markdown_file(
            characters_dir / "char_001.md",
            {
                "id": "char_001",
                "name": "艾伦",
                "aliases": [],
                "location": None,
                "physical": {"injuries": [], "buffs": [], "debuffs": []},
                "emotional": {
                    "grief": 0.0,
                    "anger": 0.0,
                    "fear": 0.0,
                    "joy": 0.0,
                    "determination": 0.5,
                },
                "inventory": [],
                "knowledge": [],
            },
            "# 艾伦\n\n一位年轻的冒险者。",
        )

        # 创建设定文件
        canon_dir = project_dir / "canon"
        canon_dir.mkdir(parents=True, exist_ok=True)
        storage.write_markdown_file(
            canon_dir / "world_rules.md",
            {"id": "canon_world_rules", "type": "world_rules"},
            "# 世界观设定\n\n这是一个黑暗奇幻世界。",
        )

        # 创建必要的空目录
        (project_dir / "draft").mkdir(parents=True, exist_ok=True)
        (project_dir / "subconscious").mkdir(parents=True, exist_ok=True)
        (project_dir / ".snapshots").mkdir(parents=True, exist_ok=True)

    def test_auto_dry_run_success(self, tmp_path: Path) -> None:
        """测试 dry-run 模式正常解析大纲。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        self._create_project_structure(project_dir)

        result = runner.invoke(app, ["auto", "--dry-run", str(project_dir)])
        assert result.exit_code == 0
        assert "Dry Run" in result.output
        # 验证输出中包含章节解析结果
        assert "ch_" in result.output

    def test_auto_dry_run_shows_chapters(self, tmp_path: Path) -> None:
        """测试 dry-run 模式显示正确的章节数。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        self._create_project_structure(project_dir)

        result = runner.invoke(app, ["auto", "--dry-run", str(project_dir)])
        assert result.exit_code == 0
        # 配置中 target_chapters=2，大纲也有 2 章
        assert "共 2 章" in result.output

    def test_auto_dry_run_with_chapters_override(self, tmp_path: Path) -> None:
        """测试 dry-run 模式使用 --chapters 覆盖章节数。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        self._create_project_structure(project_dir)

        result = runner.invoke(app, ["auto", "--dry-run", "--chapters", "1", str(project_dir)])
        assert result.exit_code == 0
        assert "共 1 章" in result.output

    def test_auto_dry_run_shows_project_info(self, tmp_path: Path) -> None:
        """测试 dry-run 模式显示项目信息。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        self._create_project_structure(project_dir)

        result = runner.invoke(app, ["auto", "--dry-run", str(project_dir)])
        assert result.exit_code == 0
        # 验证显示创作方向
        assert "黑暗奇幻" in result.output
        # 验证显示章节数
        assert "2" in result.output
