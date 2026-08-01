"""cli/stash 模块测试 - 灵感潜意识池命令。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from opennovel.cli.stash import stash_app

runner = CliRunner()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """创建已初始化的项目目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "subconscious").mkdir()
    return root


class TestStashCommand:
    """novel stash 命令测试。"""

    def test_stash_basic(self, project_root: Path) -> None:
        """测试基本灵感存入。"""
        result = runner.invoke(
            stash_app,
            ["深渊不收我", str(project_root)],
        )

        assert result.exit_code == 0
        assert "已存入" in result.output

        # 验证写入文件
        lines_file = project_root / "subconscious" / "lines.md"
        assert lines_file.exists()
        content = lines_file.read_text(encoding="utf-8")
        assert "深渊不收我" in content

    def test_stash_with_tags(self, project_root: Path) -> None:
        """测试带标签的灵感存入（直接调用函数绕过 CliRunner 解析问题）。"""
        from opennovel.cli.stash import stash

        stash(text="这是一句金句", tags=["金句", "哲学"], path=str(project_root))

        lines_file = project_root / "subconscious" / "lines.md"
        content = lines_file.read_text(encoding="utf-8")
        assert "这是一句金句" in content
        assert "#金句" in content
        assert "#哲学" in content

    def test_stash_uninitialized_project(self, tmp_path: Path) -> None:
        """测试未初始化项目时应报错。"""
        uninitialized = tmp_path / "uninitialized"
        uninitialized.mkdir()

        result = runner.invoke(
            stash_app,
            ["灵感文本", str(uninitialized)],
        )

        assert result.exit_code != 0
        assert "未初始化" in result.output

    def test_stash_appends_to_existing(self, project_root: Path) -> None:
        """测试多次存入追加而非覆盖。"""
        runner.invoke(stash_app, ["第一条灵感", str(project_root)])
        runner.invoke(stash_app, ["第二条灵感", str(project_root)])

        lines_file = project_root / "subconscious" / "lines.md"
        content = lines_file.read_text(encoding="utf-8")
        assert "第一条灵感" in content
        assert "第二条灵感" in content
