"""llm 模块测试 - LLM 总线、响应提取。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from opennovel.core.llm import (
    LLMBus,
    extract_text_from_response,
    extract_usage_from_response,
)

# ── Mock 对象 ──


class MockMessage:
    def __init__(self, text: str) -> None:
        self.content = text


class MockChoice:
    def __init__(self, text: str) -> None:
        self.message = MockMessage(text)


class MockUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 20
        self.total_tokens = 30


class MockResponse:
    """模拟 LiteLLM 响应对象。"""

    def __init__(self, text: str = "测试响应") -> None:
        self.choices = [MockChoice(text)]
        self.usage = MockUsage()


# ── extract_text_from_response 测试 ──


class TestExtractTextFromResponse:
    """extract_text_from_response 测试。"""

    def test_extract_normal_response(self) -> None:
        """测试正常响应提取。"""
        response = MockResponse("生成的文本")
        text = extract_text_from_response(response)
        assert text == "生成的文本"

    def test_extract_empty_choices(self) -> None:
        """测试空 choices 返回空字符串。"""
        response = MagicMock()
        response.choices = []
        text = extract_text_from_response(response)
        assert text == ""

    def test_extract_none_content(self) -> None:
        """测试 content 为 None 的情况。"""
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = None
        # None 内容应返回空字符串或 None
        text = extract_text_from_response(response)
        assert text is None or text == ""


# ── extract_usage_from_response 测试 ──


class TestExtractUsageFromResponse:
    """extract_usage_from_response 测试。"""

    def test_extract_normal_usage(self) -> None:
        """测试正常用量提取。"""
        response = MockResponse()
        usage = extract_usage_from_response(response)
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 20
        assert usage["total_tokens"] == 30

    def test_extract_missing_usage(self) -> None:
        """测试 usage 缺失时返回默认值。"""
        response = MagicMock(spec=[])  # 无 usage 属性
        usage = extract_usage_from_response(response)
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0


# ── LLMBus 初始化测试 ──


class TestLLMBusInit:
    """LLMBus 初始化测试。"""

    def test_default_values(self) -> None:
        """测试默认参数值。"""
        bus = LLMBus()
        assert bus.model == "gpt-4"
        assert bus.default_max_tokens == 2000
        assert bus.default_temperature == 0.7
        assert bus.max_retries == 3

    def test_custom_values(self) -> None:
        """测试自定义参数值。"""
        bus = LLMBus(
            model="deepseek-chat",
            default_max_tokens=4000,
            default_temperature=0.3,
            max_retries=5,
        )
        assert bus.model == "deepseek-chat"
        assert bus.default_max_tokens == 4000
        assert bus.default_temperature == 0.3
        assert bus.max_retries == 5


# ── LLMBus.chat 测试 ──


class TestLLMBusChat:
    """LLMBus.chat 同步调用测试。"""

    @patch("opennovel.core.llm.completion")
    def test_chat_basic(self, mock_completion: MagicMock) -> None:
        """测试基本同步调用。"""
        mock_completion.return_value = MockResponse("AI 回复")
        bus = LLMBus(model="gpt-4")

        messages = [{"role": "user", "content": "你好"}]
        response = bus.chat(messages)

        assert response.choices[0].message.content == "AI 回复"
        mock_completion.assert_called_once_with(
            model="gpt-4",
            messages=messages,
            max_tokens=2000,
            temperature=0.7,
        )

    @patch("opennovel.core.llm.completion")
    def test_chat_with_overrides(self, mock_completion: MagicMock) -> None:
        """测试参数覆盖。"""
        mock_completion.return_value = MockResponse()
        bus = LLMBus(model="gpt-4")

        messages = [{"role": "user", "content": "测试"}]
        bus.chat(messages, model="gpt-3.5-turbo", max_tokens=100, temperature=0.1)

        mock_completion.assert_called_once_with(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=100,
            temperature=0.1,
        )

    @patch("opennovel.core.llm.completion")
    def test_chat_retry_on_failure(self, mock_completion: MagicMock) -> None:
        """测试失败重试。"""
        mock_completion.side_effect = [ConnectionError("网络错误"), MockResponse("重试成功")]
        bus = LLMBus(model="gpt-4")

        messages = [{"role": "user", "content": "测试"}]
        response = bus.chat(messages)

        assert response.choices[0].message.content == "重试成功"
        assert mock_completion.call_count == 2


# ── LLMBus.achat 测试 ──


class TestLLMBusAchat:
    """LLMBus.achat 异步调用测试。"""

    @patch("opennovel.core.llm.acompletion", new_callable=AsyncMock)
    def test_achat_basic(self, mock_acompletion: AsyncMock) -> None:
        """测试基本异步调用。"""
        mock_acompletion.return_value = MockResponse("异步回复")
        bus = LLMBus(model="gpt-4")

        messages = [{"role": "user", "content": "你好"}]
        response = asyncio.run(bus.achat(messages))

        assert response.choices[0].message.content == "异步回复"
        mock_acompletion.assert_called_once()


# ── LLMBus.achat_stream 测试 ──


class TestLLMBusAchatStream:
    """LLMBus.achat_stream 异步流式调用测试。"""

    @patch("opennovel.core.llm.acompletion", new_callable=AsyncMock)
    def test_achat_stream_yields_chunks(self, mock_acompletion: AsyncMock) -> None:
        """测试流式调用逐块返回内容。"""

        # 模拟流式 chunk 对象
        class MockDelta:
            def __init__(self, content: str | None) -> None:
                self.content = content

        class MockStreamChoice:
            def __init__(self, content: str | None) -> None:
                self.delta = MockDelta(content)

        class MockStreamChunk:
            def __init__(self, content: str | None) -> None:
                self.choices = [MockStreamChoice(content)]

        async def mock_stream():
            yield MockStreamChunk("你好")
            yield MockStreamChunk("世界")
            yield MockStreamChunk(None)  # 空 chunk 应被跳过

        mock_acompletion.return_value = mock_stream()
        bus = LLMBus(model="gpt-4")

        async def run():
            chunks = []
            async for chunk in bus.achat_stream([{"role": "user", "content": "测试"}]):
                chunks.append(chunk)
            return chunks

        result = asyncio.run(run())
        assert result == ["你好", "世界"]
        mock_acompletion.assert_called_once()

    @patch("opennovel.core.llm.acompletion", new_callable=AsyncMock)
    def test_achat_stream_passes_parameters(self, mock_acompletion: AsyncMock) -> None:
        """测试流式调用正确传递参数。"""

        async def mock_stream():
            return
            yield  # make it an async generator

        mock_acompletion.return_value = mock_stream()
        bus = LLMBus(model="gpt-4")

        async def run():
            async for _ in bus.achat_stream(
                [{"role": "user", "content": "测试"}],
                model="deepseek-chat",
                max_tokens=100,
                temperature=0.2,
            ):
                pass

        asyncio.run(run())
        mock_acompletion.assert_called_once_with(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "测试"}],
            max_tokens=100,
            temperature=0.2,
            stream=True,
        )
