"""LOOM MCP Server 测试。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.mcp_server import (
    _handle_auto_create,
    _handle_get_status,
    _handle_init_project,
    _handle_write_chapter,
    list_tools,
)


class TestListTools:
    """list_tools 测试。"""

    _EXPECTED_TOOLS = {
        "init_project", "get_status", "write_chapter", "auto_create",
        "commit", "stash", "diff", "doctor", "foreshadow",
    }

    @pytest.mark.anyio
    async def test_returns_nine_tools(self):
        tools = await list_tools()
        assert len(tools) == 9

    @pytest.mark.anyio
    async def test_tool_names(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert names == self._EXPECTED_TOOLS

    @pytest.mark.anyio
    async def test_tools_have_schemas(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.inputSchema is not None
            assert "type" in tool.inputSchema


class TestInitProject:
    """init_project tool 测试。"""

    @pytest.mark.anyio
    async def test_init_creates_directories(self, tmp_path):
        result = await _handle_init_project({"path": str(tmp_path)})
        assert "项目初始化完成" in result
        for dir_name in ["canon", "characters", "draft", "outlines", "subconscious", ".snapshots"]:
            assert (tmp_path / dir_name).is_dir()

    @pytest.mark.anyio
    async def test_init_creates_files(self, tmp_path):
        await _handle_init_project({"path": str(tmp_path)})
        assert (tmp_path / "characters" / "char_001.md").exists()
        assert (tmp_path / "canon" / "world_rules.md").exists()
        assert (tmp_path / "draft" / "ch_001.md").exists()
        assert (tmp_path / "novel.yaml").exists()

    @pytest.mark.anyio
    async def test_init_idempotent(self, tmp_path):
        """重复初始化不会覆盖已有文件。"""
        await _handle_init_project({"path": str(tmp_path)})
        # 修改角色文件
        char_path = tmp_path / "characters" / "char_001.md"
        char_path.write_text("custom content", encoding="utf-8")
        # 再次初始化
        await _handle_init_project({"path": str(tmp_path)})
        assert char_path.read_text(encoding="utf-8") == "custom content"


class TestGetStatus:
    """get_status tool 测试。"""

    @pytest.mark.anyio
    async def test_status_empty_project(self, tmp_path):
        """未初始化的项目返回基本信息。"""
        result = await _handle_get_status({"path": str(tmp_path)})
        assert "配置" in result

    @pytest.mark.anyio
    async def test_status_with_project(self, tmp_path):
        """已初始化的项目返回完整状态。"""
        await _handle_init_project({"path": str(tmp_path)})
        result = await _handle_get_status({"path": str(tmp_path)})
        assert "角色" in result
        assert "章节" in result
        assert "char_001" in result


class TestWriteChapter:
    """write_chapter tool 测试。"""

    @pytest.mark.anyio
    async def test_write_chapter_requires_id(self, tmp_path):
        """缺少 chapter_id 时返回错误。"""
        result = await _handle_write_chapter({"path": str(tmp_path)})
        assert "错误" in result or "error" in result.lower()

    @pytest.mark.anyio
    @patch("opennovel.mcp_server.Writer")
    @patch("opennovel.mcp_server.Critic")
    @patch("opennovel.mcp_server.LLMBus")
    @patch("opennovel.mcp_server.Retriever")
    async def test_write_chapter_success(
        self, mock_retriever, mock_bus, mock_critic_cls, mock_writer_cls, tmp_path
    ):
        """成功创作章节。"""
        from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
        from opennovel.schemas.outline import ChapterOutline, SceneBreakdown

        # 初始化项目
        await _handle_init_project({"path": str(tmp_path)})

        # Mock Writer
        mock_outline = ChapterOutline(
            chapter_id="ch_001",
            title="测试章节",
            summary="测试概要",
            scenes=[
                SceneBreakdown(
                    scene_id="s1",
                    description="测试场景",
                    characters_involved=["char_001"],
                    emotional_tone="平静",
                    estimated_words=1000,
                )
            ],
            character_arcs={"char_001": "从平静到紧张"},
            key_plot_points=["关键事件"],
            narrative_rhythm="前松后紧",
            target_words=1000,
        )
        mock_writer = MagicMock()
        mock_writer.think.return_value = mock_outline
        mock_writer.write.return_value = "这是测试章节的正文内容。" * 50
        mock_writer_cls.return_value = mock_writer

        # Mock Critic
        mock_evaluation = ChapterEvaluation(
            total_score=85,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=18, comment="不错"),
                DimensionScore(dimension="情节逻辑", score=17, comment="通顺"),
                DimensionScore(dimension="角色一致性", score=17, comment="一致"),
                DimensionScore(dimension="节奏把控", score=16, comment="良好"),
                DimensionScore(dimension="情感表达", score=17, comment="到位"),
            ],
            summary="总体合格",
            issues=[],
            suggestions=[],
        )
        mock_critic = MagicMock()
        mock_critic.evaluate.return_value = mock_evaluation
        mock_critic_cls.return_value = mock_critic

        result = await _handle_write_chapter(
            {
                "path": str(tmp_path),
                "chapter_id": "ch_001",
                "chapter_hint": "测试提示",
            }
        )

        data = json.loads(result)
        assert data["chapter_id"] == "ch_001"
        assert data["score"] == 85
        assert data["is_pass"] is True
        assert data["word_count"] > 0


class TestAutoCreate:
    """auto_create tool 测试。"""

    @pytest.mark.anyio
    async def test_auto_create_no_outline(self, tmp_path):
        """大纲文件不存在时返回错误。"""
        await _handle_init_project({"path": str(tmp_path)})
        result = await _handle_auto_create({"path": str(tmp_path)})
        assert "错误" in result or "不存在" in result

    @pytest.mark.anyio
    @patch("opennovel.mcp_server.AutoRunner")
    async def test_auto_create_success(self, mock_runner_cls, tmp_path):
        """成功执行创作循环。"""
        from opennovel.core.auto_runner import ChapterResult, RunReport
        from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
        from opennovel.schemas.outline import ChapterOutline, SceneBreakdown

        # 初始化项目和大纲
        await _handle_init_project({"path": str(tmp_path)})
        outline_path = tmp_path / "outlines" / "story.md"
        outline_path.write_text("## 第一章：测试\n测试内容\n", encoding="utf-8")

        # Mock AutoRunner
        mock_outline = ChapterOutline(
            chapter_id="ch_001",
            title="测试",
            summary="概要",
            scenes=[
                SceneBreakdown(
                    scene_id="s1",
                    description="场景",
                    characters_involved=["char_001"],
                    emotional_tone="平静",
                    estimated_words=1000,
                )
            ],
            character_arcs={},
            key_plot_points=[],
            narrative_rhythm="平稳",
            target_words=1000,
        )
        mock_eval = ChapterEvaluation(
            total_score=85,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=18, comment=""),
                DimensionScore(dimension="情节逻辑", score=17, comment=""),
                DimensionScore(dimension="角色一致性", score=17, comment=""),
                DimensionScore(dimension="节奏把控", score=16, comment=""),
                DimensionScore(dimension="情感表达", score=17, comment=""),
            ],
            summary="合格",
            issues=[],
            suggestions=[],
        )
        mock_report = RunReport(
            chapters=[
                ChapterResult(
                    chapter_id="ch_001",
                    outline=mock_outline,
                    chapter_text="正文",
                    evaluation=mock_eval,
                    retry_count=0,
                    manager_summary="摘要",
                    word_count=100,
                )
            ],
            total_chapters=1,
            successful_chapters=1,
            failed_chapters=0,
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_report
        mock_runner_cls.return_value = mock_runner

        result = await _handle_auto_create({"path": str(tmp_path)})
        data = json.loads(result)
        assert data["successful"] == 1
        assert data["total_words"] == 100
