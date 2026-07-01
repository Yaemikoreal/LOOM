"""Agent 自治引擎测试 — Mid-Write 工具调用循环。

测试覆盖（Phase 3 升级版）：
- ToolCallParser 解析逻辑（<tool_call> XML/JSON 格式）
- ToolCallParser.parse_native() 原生通道入口
- ToolCallExecutor 执行逻辑（含权限检查）
- AutonomousWriteLoop 循环控制（回退通道 + 原生通道）
- Writer.write_with_autonomy 集成
- SafetyFence 违规处理
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from opennovel.core.agent_autonomy import (
    AutonomousConfig,
    AutonomousWriteLoop,
    ToolCallExecutor,
    ToolCallParser,
    ToolCallRequest,
)
from opennovel.schemas.knowledge import KnowledgeResult, KnowledgeSource


def _make_outline(
    chapter_id: str = "ch_001",
    title: str = "测试",
    summary: str = "测试章节",
) -> Any:
    """创建完整的 ChapterOutline 实例。"""
    from opennovel.schemas.outline import ChapterOutline

    return ChapterOutline(
        chapter_id=chapter_id,
        title=title,
        summary=summary,
        scenes=[],
        character_arcs={},
        key_plot_points=[],
        narrative_rhythm="normal",
        target_words=1000,
    )


def _xml_tool_call(tool: str, query: str, reason: str = "") -> str:
    """构造 XML/JSON 工具调用块。"""
    payload = f'{{"tool": "{tool}", "args": {{"query": "{query}"}}'
    if reason:
        payload += f', "reason": "{reason}"'
    payload += "}"
    return f"<tool_call>{payload}</tool_call>"


# ═══════════════════════════════════════════════════════════════════════════
# ToolCallParser 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestToolCallParser:
    """ToolCallParser 解析测试（XML/JSON 格式）。"""

    def test_parse_query_canon(self) -> None:
        """测试解析 query_canon 工具调用。"""
        text = (
            "他走向城堡深处。\n"
            f"{_xml_tool_call('query_canon', '魔法消耗寿命规则', '需要确认魔法系统设定')}\n"
            "然后继续前进。"
        )
        request = ToolCallParser.parse(text)
        assert request is not None
        assert request.tool_name == "query_canon"
        assert request.query == "魔法消耗寿命规则"
        assert request.reason == "需要确认魔法系统设定"

    def test_parse_query_character(self) -> None:
        """测试解析 query_character 工具调用。"""
        text = f"{_xml_tool_call('query_character', 'char_001', '查看角色伤势')}\n"
        request = ToolCallParser.parse(text)
        assert request is not None
        assert request.tool_name == "query_character"
        assert request.query == "char_001"

    def test_parse_no_marker(self) -> None:
        """测试无标记时不解析。"""
        text = "这是一段普通的正文，没有工具调用标记。"
        assert ToolCallParser.parse(text) is None

    def test_parse_empty_text(self) -> None:
        """测试空文本。"""
        assert ToolCallParser.parse("") is None

    def test_parse_unknown_tool(self) -> None:
        """测试未知工具名时返回 None。"""
        text = _xml_tool_call("unknown_tool", "查询内容", "原因")
        assert ToolCallParser.parse(text) is None

    def test_parse_empty_query(self) -> None:
        """测试查询内容为空时返回 None。"""
        payload = '{"tool": "query_canon", "args": {"query": ""}, "reason": "原因"}'
        text = f"<tool_call>{payload}</tool_call>"
        assert ToolCallParser.parse(text) is None

    def test_parse_without_reason(self) -> None:
        """测试无原因字段。"""
        text = _xml_tool_call("query_canon", "魔法规则")
        request = ToolCallParser.parse(text)
        assert request is not None
        assert request.tool_name == "query_canon"
        assert request.query == "魔法规则"
        assert request.reason == ""

    def test_knowledge_source_mapping(self) -> None:
        """测试工具名到 KnowledgeSource 映射。"""
        r1 = ToolCallParser.parse(_xml_tool_call("query_canon", "规则"))
        assert r1 is not None
        assert r1.knowledge_source == KnowledgeSource.CANON

        r2 = ToolCallParser.parse(_xml_tool_call("query_character", "char_001"))
        assert r2 is not None
        assert r2.knowledge_source == KnowledgeSource.CHARACTER

        r3 = ToolCallParser.parse(_xml_tool_call("query_event", "事件"))
        assert r3 is not None
        assert r3.knowledge_source == KnowledgeSource.EVENT

        r4 = ToolCallParser.parse(_xml_tool_call("query_subconscious", "灵感"))
        assert r4 is not None
        assert r4.knowledge_source == KnowledgeSource.SUBCONSCIOUS

    def test_autonomy_prompt_suffix_fallback(self) -> None:
        """测试回退通道 Prompt 后缀包含 XML 格式说明。"""
        suffix = ToolCallParser.get_autonomy_prompt_suffix(native=False)
        assert "<tool_call>" in suffix
        assert "query_canon" in suffix
        assert "query_character" in suffix

    def test_autonomy_prompt_suffix_native(self) -> None:
        """测试原生通道 Prompt 后缀更简洁。"""
        suffix = ToolCallParser.get_autonomy_prompt_suffix(native=True)
        assert "工具函数" in suffix
        assert "<tool_call>" not in suffix

    def test_parse_malformed_json(self) -> None:
        """测试损坏的 JSON 返回 None。"""
        text = "<tool_call>{not json}</tool_call>"
        assert ToolCallParser.parse(text) is None

    def test_parse_multi_line_json(self) -> None:
        """测试多行 JSON 仍然可解析。"""
        text = """正文前
<tool_call>
{"tool": "query_canon", "args": {"query": "魔法规则"}, "reason": "测试"}
</tool_call>
正文后"""
        request = ToolCallParser.parse(text)
        assert request is not None
        assert request.tool_name == "query_canon"
        assert request.query == "魔法规则"


class TestToolCallParserFormat:
    """ToolCallParser.format_result 测试。"""

    def test_format_result_with_content(self) -> None:
        """测试格式化结果含内容。"""
        request = ToolCallRequest(
            tool_name="query_canon",
            query="魔法规则",
            reason="测试",
        )
        formatted = ToolCallParser.format_result(request, "魔法消耗寿命", source_label="canon")
        assert "魔法规则" in formatted
        assert "魔法消耗寿命" in formatted
        assert "##TOOL_RESULT##" in formatted

    def test_format_result_no_content(self) -> None:
        """测试格式化结果无内容。"""
        request = ToolCallRequest(tool_name="query_canon", query="未知规则")
        formatted = ToolCallParser.format_result(request, "", source_label="canon")
        assert "无相关结果" in formatted


class TestToolCallParserNative:
    """ToolCallParser.parse_native() 测试。"""

    def test_parse_native_valid(self) -> None:
        """测试有效的原生工具调用。"""
        request = ToolCallParser.parse_native(
            "query_canon",
            {"query": "魔法规则", "reason": "需要确认"},
        )
        assert request is not None
        assert request.tool_name == "query_canon"
        assert request.query == "魔法规则"

    def test_parse_native_unknown_tool(self) -> None:
        """测试未知工具名返回 None。"""
        assert ToolCallParser.parse_native("invalid_tool", {"query": "x"}) is None

    def test_parse_native_empty_query(self) -> None:
        """测试空查询返回 None。"""
        assert ToolCallParser.parse_native("query_canon", {"query": ""}) is None


# ═══════════════════════════════════════════════════════════════════════════
# ToolCallExecutor 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestToolCallExecutor:
    """ToolCallExecutor 执行测试。"""

    def test_execute_canon_query(self) -> None:
        """测试执行 canon 查询。"""
        mock_registry = MagicMock()
        mock_registry.execute.return_value = KnowledgeResult(
            content="魔法消耗寿命，每次施法减少 1 年寿命",
            source=KnowledgeSource.CANON,
            concept="魔法规则",
            relevance=1.0,
        )
        executor = ToolCallExecutor(mock_registry)
        request = ToolCallRequest(
            tool_name="query_canon",
            query="魔法规则",
            reason="测试",
        )
        result = executor.execute(request)
        assert result.content == "魔法消耗寿命，每次施法减少 1 年寿命"
        assert result.source == KnowledgeSource.CANON
        # 验证调用了 execute() 而非 fulfill()
        mock_registry.execute.assert_called_once()

    def test_execute_character_query(self) -> None:
        """测试执行 character 查询。"""
        mock_registry = MagicMock()
        mock_registry.execute.return_value = KnowledgeResult(
            content="角色: 林远\n伤势: 左臂骨折\n情绪: fear=0.7",
            source=KnowledgeSource.CHARACTER,
            concept="char_001",
            relevance=1.0,
        )
        executor = ToolCallExecutor(mock_registry)
        request = ToolCallRequest(
            tool_name="query_character",
            query="char_001",
            reason="查看伤势",
        )
        result = executor.execute(request)
        assert "林远" in result.content
        assert result.source == KnowledgeSource.CHARACTER

    def test_execute_unknown_tool(self) -> None:
        """测试未知工具。"""
        mock_registry = MagicMock()
        executor = ToolCallExecutor(mock_registry)
        request = ToolCallRequest(tool_name="invalid_tool", query="test")
        result = executor.execute(request)
        assert "未知工具" in result.content

    def test_format_for_llm(self) -> None:
        """测试格式化为 LLM 可读文本。"""
        mock_registry = MagicMock()
        executor = ToolCallExecutor(mock_registry)
        result = KnowledgeResult(
            content="测试内容",
            source=KnowledgeSource.CANON,
            concept="魔法规则",
        )
        formatted = executor.format_for_llm(result)
        assert "魔法规则" in formatted
        assert "测试内容" in formatted


# ═══════════════════════════════════════════════════════════════════════════
# AutonomousWriteLoop 测试
# ═══════════════════════════════════════════════════════════════════════════


class _MockResponse:
    """模拟 LLM 响应。"""

    def __init__(self, content: str) -> None:
        self.choices = [MagicMock()]
        self.choices[0].message.content = content
        self.choices[0].message.tool_calls = None
        self.usage = MagicMock()
        self.usage.prompt_tokens = 100
        self.usage.completion_tokens = 200


class _MockContextManager:
    """模拟 context manager。"""

    def __enter__(self) -> "_MockContextManager":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestAutonomousWriteLoop:
    """AutonomousWriteLoop 循环控制测试。"""

    def test_disabled_loop_calls_once(self) -> None:
        """测试禁用时只调用一次 LLM。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse("正常正文输出")

        mock_executor = MagicMock()
        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()

        config = AutonomousConfig(enabled=False)
        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence, config)
        result = loop.execute([{"role": "user", "content": "创作"}], model="test")

        assert result == "正常正文输出"
        mock_llm.chat.assert_called_once()

    def test_direct_output_no_tool_call(self) -> None:
        """测试无工具调用时直接返回。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse("这是一段正文内容")

        mock_executor = MagicMock()
        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence)
        result = loop.execute([{"role": "user", "content": "创作"}], model="test")

        assert "正文内容" in result
        mock_llm.chat.assert_called_once()

    def test_tool_call_then_output(self) -> None:
        """测试工具调用后输出正文。"""
        mock_llm = MagicMock()

        # 第一次调用：返回工具调用
        # 第二次调用：返回正文
        mock_llm.chat.side_effect = [
            _MockResponse(
                _xml_tool_call("query_canon", "魔法规则", "需要确认")
                + "\n（等待查询结果）"
            ),
            _MockResponse("根据查到的信息，魔法消耗寿命。正文继续..."),
        ]

        mock_executor = MagicMock()
        mock_executor.execute.return_value = KnowledgeResult(
            content="魔法消耗寿命",
            source=KnowledgeSource.CANON,
            concept="魔法规则",
            relevance=1.0,
        )
        mock_executor.format_for_llm.return_value = "查询结果: 魔法消耗寿命"

        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence)
        result = loop.execute([{"role": "user", "content": "创作"}], model="test")

        assert "正文继续" in result
        assert mock_llm.chat.call_count == 2

    def test_safety_fence_interrupts(self) -> None:
        """测试安全围栏中断循环。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse(
            _xml_tool_call("query_canon", "规则", "需要")
        )

        mock_executor = MagicMock()
        mock_fence = MagicMock()
        # 第一次 check_all 通过，第二次失败
        mock_fence.check_all.side_effect = [True, False]
        mock_fence.record_tokens = MagicMock()
        mock_fence.violations = [MagicMock()]
        mock_fence.violations[0].detail = "递归深度超限"
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence)
        with pytest.raises(RuntimeError, match="安全围栏阻止"):
            loop.execute([{"role": "user", "content": "创作"}], model="test")

    def test_max_tool_calls_exceeded(self) -> None:
        """测试工具调用次数超限。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse(
            _xml_tool_call("query_canon", "规则", "需要")
        )

        mock_executor = MagicMock()
        mock_executor.execute.return_value = KnowledgeResult(
            content="结果", source=KnowledgeSource.CANON, concept="规则"
        )
        mock_executor.format_for_llm.return_value = "结果"

        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        config = AutonomousConfig(max_tool_calls_per_write=1)
        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence, config)
        with pytest.raises(RuntimeError, match="超限"):
            loop.execute([{"role": "user", "content": "创作"}], model="test")


# ═══════════════════════════════════════════════════════════════════════════
# AutonomousConfig 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestAutonomousConfig:
    """AutonomousConfig 配置测试。"""

    def test_default_values(self) -> None:
        """测试默认值。"""
        config = AutonomousConfig()
        assert config.max_tool_calls_per_write == 3
        assert config.max_tool_calls_total == 10
        assert config.enabled is True
        assert config.supports_native_tool_use is False
        assert config.max_tool_call_history == 5

    def test_custom_values(self) -> None:
        """测试自定义值。"""
        config = AutonomousConfig(
            max_tool_calls_per_write=5,
            max_tool_calls_total=20,
            enabled=False,
            supports_native_tool_use=True,
        )
        assert config.max_tool_calls_per_write == 5
        assert config.max_tool_calls_total == 20
        assert config.enabled is False
        assert config.supports_native_tool_use is True


# ═══════════════════════════════════════════════════════════════════════════
# Writer.write_with_autonomy 集成测试
# ═══════════════════════════════════════════════════════════════════════════


class TestWriterAutonomyIntegration:
    """Writer.write_with_autonomy 集成测试。"""

    @patch("opennovel.agents.writer.LLMBus")
    @patch("opennovel.agents.writer.Retriever")
    def test_write_with_autonomy_requires_deps(
        self,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试未配置依赖时抛出错误。"""
        from opennovel.agents.writer import Writer

        writer = Writer(
            llm_bus=MagicMock(),
            retriever=MagicMock(),
            project_root=tmp_path,
        )
        outline = _make_outline()
        with pytest.raises(RuntimeError, match="未配置"):
            writer.write_with_autonomy("ch_001", outline)

    @patch("opennovel.agents.writer.LLMBus")
    @patch("opennovel.agents.writer.Retriever")
    def test_write_with_autonomy_success(
        self,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试自治创作成功。"""
        from opennovel.agents.writer import Writer

        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse("正常正文输出")

        mock_retriever = MagicMock()
        mock_retriever.query_canon.return_value = ""

        mock_registry = MagicMock()
        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        writer = Writer(
            llm_bus=mock_llm,
            retriever=mock_retriever,
            project_root=tmp_path,
            tool_registry=mock_registry,
            safety_fence=mock_fence,
        )

        outline = _make_outline(title="测试章节", summary="一个测试章节")
        result = writer.write_with_autonomy("ch_001", outline)
        assert "正常正文输出" in result

    @patch("opennovel.agents.writer.ToolCallParser")
    @patch("opennovel.agents.writer.LLMBus")
    @patch("opennovel.agents.writer.Retriever")
    def test_autonomy_prompt_suffix_injected(
        self,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        mock_parser: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试自治 Prompt 后缀被注入。"""
        from opennovel.agents.writer import Writer

        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse("正文")

        mock_retriever = MagicMock()
        mock_retriever.query_canon.return_value = ""

        mock_registry = MagicMock()
        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()
        mock_parser.get_autonomy_prompt_suffix.return_value = "autonomy suffix"

        writer = Writer(
            llm_bus=mock_llm,
            retriever=mock_retriever,
            project_root=tmp_path,
            tool_registry=mock_registry,
            safety_fence=mock_fence,
        )

        outline = _make_outline()
        writer.write_with_autonomy("ch_001", outline)
        # 验证 get_autonomy_prompt_suffix 被调用（带参数）
        mock_parser.get_autonomy_prompt_suffix.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# 边缘情况测试
# ═══════════════════════════════════════════════════════════════════════════


class TestAutonomousWriteLoopEdgeCases:
    """AutonomousWriteLoop 边缘情况测试。"""

    def test_remove_tool_call_clean(self) -> None:
        """测试移除完整的工具调用标记。"""
        text = f"前面正文。\n{_xml_tool_call('query_canon', '规则', '原因')}\n后面正文"
        request = ToolCallParser.parse(text)
        assert request is not None
        cleaned = AutonomousWriteLoop._remove_tool_call(text, request)
        assert "<tool_call>" not in cleaned
        assert "前面正文" in cleaned

    def test_accumulated_text_on_safety_fence(self) -> None:
        """测试安全围栏中断时返回已生成内容。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _MockResponse(
            f"已生成部分内容。\n{_xml_tool_call('query_canon', '规则', '需要')}\n"
        )

        mock_executor = MagicMock()
        mock_executor.format_for_llm.return_value = "规则: 没有武器"

        mock_fence = MagicMock()
        mock_fence.check_all.side_effect = [True, False]
        mock_fence.record_tokens = MagicMock()
        mock_fence.violations = [MagicMock()]
        mock_fence.violations[0].detail = "Token 预算超限"
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence)
        # 有已生成内容时返回内容而非异常
        result = loop.execute([{"role": "user", "content": "创作"}], model="test")
        assert "已生成部分内容" in result

    def test_executor_called_with_correct_params(self) -> None:
        """测试执行器被正确参数调用。"""
        mock_llm = MagicMock()
        # 第一次返回工具调用，第二次返回正文
        mock_llm.chat.side_effect = [
            _MockResponse(_xml_tool_call("query_canon", "魔法设定", "需要确认") + "\n"),
            _MockResponse("正文内容"),
        ]

        mock_executor = MagicMock()
        mock_executor.execute.return_value = KnowledgeResult(
            content="结果", source=KnowledgeSource.CANON, concept="魔法设定"
        )
        mock_executor.format_for_llm.return_value = "结果"

        mock_fence = MagicMock()
        mock_fence.check_all.return_value = True
        mock_fence.record_tokens = MagicMock()
        mock_fence.autonomous_call = lambda agent: _MockContextManager()

        loop = AutonomousWriteLoop(mock_llm, mock_executor, mock_fence)
        result = loop.execute([{"role": "user", "content": "创作"}], model="test")

        assert result == "正文内容"
        mock_executor.execute.assert_called_once()
        call_request = mock_executor.execute.call_args[0][0]
        assert call_request.tool_name == "query_canon"
        assert call_request.query == "魔法设定"

    def test_old_pipe_format_returns_none(self) -> None:
        """测试旧管道符格式被废弃，返回 None。"""
        text = "##TOOL_CALL## query_canon|魔法规则|原因"
        assert ToolCallParser.parse(text) is None

    def test_json_with_extra_fields(self) -> None:
        """测试 args 中包含额外字段。"""
        payload = '{"tool": "query_canon", "args": {"query": "规则", "detail": true}, "reason": "测试"}'
        text = f"<tool_call>{payload}</tool_call>"
        request = ToolCallParser.parse(text)
        assert request is not None
        assert request.tool_name == "query_canon"
        assert request.query == "规则"
