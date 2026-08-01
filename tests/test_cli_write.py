"""CLI write 命令测试 - Actor 交互式写作循环。"""

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from opennovel.storage.yaml_storage import YAMLStorage


@pytest.fixture
def write_project(tmp_path: Path) -> dict:
    """创建 write 测试所需的项目结构。"""
    project_root = tmp_path
    (project_root / "draft").mkdir()
    (project_root / "characters").mkdir()
    (project_root / "prompts").mkdir()

    storage = YAMLStorage()
    storage.write_markdown_file(
        project_root / "characters" / "char_001.md",
        {"id": "char_001", "name": "角色"},
        "# 背景",
    )
    chapter_path = project_root / "draft" / "ch_001.md"
    storage.write_markdown_file(
        chapter_path,
        {"id": "ch_001", "pov": "char_001"},
        "# 第一章\n\n故事开头。",
    )
    return {"project_root": project_root, "chapter_path": chapter_path}


def _patch_write_components(
    mock_llm: MagicMock,
    mock_retriever: MagicMock,
    mock_actor: MagicMock | None = None,
):
    """创建 write 组件的 patch 上下文管理器。"""
    patches = [
        patch("opennovel.cli.write.LLMBus", return_value=mock_llm),
        patch("opennovel.cli.write.Retriever", return_value=mock_retriever),
    ]
    if mock_actor is not None:
        patches.append(patch("opennovel.agents.actor.Actor", return_value=mock_actor))
    return contextlib.ExitStack()  # placeholder, actual usage below


class TestWriteCommand:
    """write 命令基本功能测试。"""

    def test_chapter_not_found(self, write_project: dict) -> None:
        """测试章节不存在时退出。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        with pytest.raises(click.exceptions.Exit):
            write(chapter="nonexistent.md", path=str(root), model="test")

    @patch("builtins.input", side_effect=[":q"])
    def test_quit_immediately(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试输入 :q 立即退出。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

    @patch("builtins.input", side_effect=[KeyboardInterrupt])
    def test_keyboard_interrupt_exits(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试 Ctrl+C 退出写作模式。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

    @patch("builtins.input", side_effect=["", ":q"])
    def test_empty_triggers_write(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试空行触发 Actor 续写。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        mock_actor = MagicMock()
        mock_actor.write_sync.return_value = "续写的内容。"

        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            patch("opennovel.agents.actor.Actor", return_value=mock_actor),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

        mock_actor.write_sync.assert_called_once()

    @patch("builtins.input", side_effect=["", ":q"])
    def test_empty_generated_text(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试 Actor 返回空文本时不追加。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        mock_actor = MagicMock()
        mock_actor.write_sync.return_value = ""

        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            patch("opennovel.agents.actor.Actor", return_value=mock_actor),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

    @patch("builtins.input", side_effect=["", ":q"])
    def test_write_error_handled(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试续写出错时捕获异常。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        mock_actor = MagicMock()
        mock_actor.write_sync.side_effect = RuntimeError("LLM 调用失败")

        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            patch("opennovel.agents.actor.Actor", return_value=mock_actor),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

    @patch("builtins.input", side_effect=["非空输入", ":q"])
    def test_non_empty_input_no_trigger(self, mock_input: MagicMock, write_project: dict) -> None:
        """测试非空输入不触发续写。"""
        from opennovel.cli.write import write

        root = write_project["project_root"]
        mock_actor = MagicMock()

        with (
            patch("opennovel.cli.write.LLMBus", return_value=MagicMock()),
            patch("opennovel.cli.write.Retriever", return_value=MagicMock()),
            patch("opennovel.agents.actor.Actor", return_value=mock_actor),
            contextlib.suppress(SystemExit),
        ):
            write(chapter="ch_001.md", path=str(root), model="test")

        mock_actor.write_sync.assert_not_called()
