"""Actor 代理测试 - 沉浸式续写引擎（mock LLM）。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opennovel.agents.actor import Actor
from opennovel.storage.yaml_storage import YAMLStorage


class TestActorInit:
    """Actor 初始化测试。"""

    def test_default_prompt_path(self, tmp_path: Path) -> None:
        """测试默认 prompt 路径指向 prompts/actor.v1.md。"""
        bus = MagicMock()
        ret = MagicMock()
        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        assert actor.prompt_path == tmp_path / "prompts" / "actor.v1.md"

    def test_custom_prompt_path(self, tmp_path: Path) -> None:
        """测试自定义 prompt 路径。"""
        bus = MagicMock()
        ret = MagicMock()
        custom = tmp_path / "custom_prompt.md"
        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path, prompt_path=custom)
        assert actor.prompt_path == custom

    def test_attributes_stored(self, tmp_path: Path) -> None:
        """测试属性正确存储。"""
        bus = MagicMock()
        ret = MagicMock()
        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        assert actor.llm_bus is bus
        assert actor.retriever is ret
        assert actor.project_root == tmp_path


class TestBuildContext:
    """Actor._build_context 上下文组装测试。"""

    def test_build_context_calls_retriever(self, tmp_path: Path) -> None:
        """测试上下文组装调用检索器。"""
        bus = MagicMock()
        ret = MagicMock()
        ret.query_canon.return_value = "魔法设定"
        ret.query_subconscious.return_value = "灵感碎片"

        # 创建项目结构
        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )
        (tmp_path / "prompts" / "actor.v1.md").write_text("你是写作代理。", encoding="utf-8")

        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        messages = actor._build_context(tmp_path / "ch_001.md", "正文内容")

        ret.query_canon.assert_called_once()
        ret.query_subconscious.assert_called_once()
        assert len(messages) >= 1

    def test_build_context_returns_valid_messages(self, tmp_path: Path) -> None:
        """测试返回的消息格式正确。"""
        bus = MagicMock()
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        (tmp_path / "characters").mkdir()
        storage = YAMLStorage()
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )

        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        messages = actor._build_context(tmp_path / "ch_001.md", "正文")

        for msg in messages:
            assert "role" in msg
            assert "content" in msg


class TestWriteSync:
    """Actor.write_sync 同步续写测试。"""

    def test_write_sync_returns_text(self, tmp_path: Path) -> None:
        """测试同步续写返回生成文本。"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "续写的内容。"

        bus = MagicMock()
        bus.chat.return_value = mock_response
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )
        (tmp_path / "prompts" / "actor.v1.md").write_text("你是写作代理。", encoding="utf-8")

        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        result = actor.write_sync(tmp_path / "ch_001.md", "故事开头。")

        assert result == "续写的内容。"
        bus.chat.assert_called_once()

    def test_write_sync_passes_messages_to_llm(self, tmp_path: Path) -> None:
        """测试同步续写将消息列表传给 LLM。"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "生成内容"

        bus = MagicMock()
        bus.chat.return_value = mock_response
        ret = MagicMock()
        ret.query_canon.return_value = "设定"
        ret.query_subconscious.return_value = "灵感"

        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )
        (tmp_path / "prompts" / "actor.v1.md").write_text("你是写作代理。", encoding="utf-8")

        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        actor.write_sync(tmp_path / "ch_001.md", "正文")

        # 验证 chat 被调用且参数是消息列表
        call_args = bus.chat.call_args[0][0]
        assert isinstance(call_args, list)
        assert len(call_args) >= 1
        assert all("role" in m and "content" in m for m in call_args)


class TestWriteStream:
    """Actor.write_stream 流式续写测试。"""

    @pytest.mark.anyio
    async def test_write_stream_yields_chunks(self, tmp_path: Path) -> None:
        """测试流式续写逐块返回内容。"""

        async def mock_aiter():
            yield "第一段"
            yield "第二段"

        bus = MagicMock()
        bus.achat_stream.return_value = mock_aiter()
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        (tmp_path / "characters").mkdir()
        (tmp_path / "prompts").mkdir()
        storage = YAMLStorage()
        storage.write_markdown_file(
            tmp_path / "characters" / "char_001.md",
            {"id": "char_001", "name": "角色"},
            "# 背景",
        )
        storage.write_markdown_file(
            tmp_path / "ch_001.md",
            {"id": "ch_001", "pov": "char_001"},
            "# 第一章",
        )
        (tmp_path / "prompts" / "actor.v1.md").write_text("你是写作代理。", encoding="utf-8")

        actor = Actor(llm_bus=bus, retriever=ret, project_root=tmp_path)
        chunks = []
        async for chunk in actor.write_stream(tmp_path / "ch_001.md", "正文"):
            chunks.append(chunk)

        assert chunks == ["第一段", "第二段"]
