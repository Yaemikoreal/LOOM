"""Auditor 模块测试 - 提取重试循环与急救模式。"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from opennovel.agents.auditor import Auditor, AuditorAbortError
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.yaml_storage import YAMLStorage

# ── Mock LiteLLM 响应对象（属性访问模式）──


class MockMessage:
    def __init__(self, text: str) -> None:
        self.content = text


class MockChoice:
    def __init__(self, text: str) -> None:
        self.message = MockMessage(text)


class MockUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 10
        self.total_tokens = 20


class MockLLMResponse:
    """模拟 LiteLLM 响应对象（属性访问，非字典）。"""

    def __init__(self, text: str) -> None:
        self.choices = [MockChoice(text)]
        self.usage = MockUsage()


class MockLLMBus:
    """模拟 LLMBus，可控的响应序列。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = [MockLLMResponse(r) for r in responses]
        self.call_count = 0
        self.last_messages: list[dict[str, str]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> MockLLMResponse:
        response = self.responses[self.call_count]
        self.call_count += 1
        self.last_messages = messages
        return response


# ── Fixtures ──


@pytest.fixture
def valid_json_response() -> str:
    """合法的 JSON 事件数组。"""
    return """[
        {
            "event_id": "evt_ch001_001",
            "chapter_id": "ch_001",
            "timestamp": "第3天·午后",
            "character_id": "char_001",
            "event_type": "INJURY",
            "description": "左臂被巨剑斩伤",
            "causal_pressure": 0.9
        }
    ]"""


@pytest.fixture
def invalid_json_response() -> str:
    """非法 JSON（缺少逗号）。"""
    return """[
        {
            "event_id": "evt_ch001_001"
            "chapter_id": "ch_001"
        }
    ]"""


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    """临时项目根目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    return root


# ── Auditor 提取测试 ──


class TestExtractEvents:
    """extract_events 基础功能测试。"""

    def test_successful_extraction(
        self, empty_project_root: Path, valid_json_response: str
    ) -> None:
        """测试一次成功提取。"""
        llm_bus = MockLLMBus([valid_json_response])
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )

        events = auditor.extract_events("ch_001", "正文内容")
        assert len(events) == 1
        assert events[0].event_id == "evt_ch001_001"
        assert events[0].event_type == EventType.INJURY

    def test_extraction_with_empty_array(self, empty_project_root: Path) -> None:
        """测试 LLM 返回空事件数组。"""
        llm_bus = MockLLMBus(["[]"])
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )

        events = auditor.extract_events("ch_001", "正文")
        assert events == []


class TestRetryMechanism:
    """重试机制测试。"""

    def test_retry_then_success(self, empty_project_root: Path, valid_json_response: str) -> None:
        """测试第一次失败后重试成功。"""
        llm_bus = MockLLMBus(
            [
                '{"broken": json}',  # JSONDecodeError
                valid_json_response,
            ]
        )
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )

        result = auditor.extract_events_with_retry("ch_001", "正文")
        assert result.success is True
        assert len(result.events) == 1
        assert llm_bus.call_count == 2

    @patch("rich.prompt.Prompt.ask", return_value="a")
    def test_all_retries_fail_then_abort(
        self, mock_prompt, empty_project_root: Path, invalid_json_response: str
    ) -> None:
        """测试所有重试都失败，触发 abort。"""
        llm_bus = MockLLMBus(
            [
                invalid_json_response,
                invalid_json_response,
                invalid_json_response,
            ]
        )

        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )

        with pytest.raises(AuditorAbortError):
            auditor.extract_events_with_retry("ch_001", "正文")
        assert llm_bus.call_count == 3


class TestParseEventsFromText:
    """_parse_events_from_text 解析逻辑测试。"""

    def test_parse_markdown_code_block(self, empty_project_root: Path) -> None:
        """测试解析被 markdown 代码块包裹的 JSON。"""
        text = """```json
[
  {
    "event_id": "evt_001",
    "chapter_id": "ch_001",
    "timestamp": "第1天",
    "character_id": "char_001",
    "event_type": "HEAL",
    "description": "伤口愈合",
    "causal_pressure": 0.3
  }
]
```"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        events = auditor._parse_events_from_text(text, "ch_001")
        assert len(events) == 1
        assert events[0].event_type == EventType.HEAL

    def test_parse_single_object_not_array(self, empty_project_root: Path) -> None:
        """测试 LLM 返回单个对象而非数组时的处理。"""
        text = """{
            "event_id": "evt_001",
            "chapter_id": "ch_001",
            "timestamp": "第1天",
            "character_id": "char_001",
            "event_type": "KNOWLEDGE",
            "description": "得知消息",
            "causal_pressure": 0.5
        }"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        events = auditor._parse_events_from_text(text, "ch_001")
        assert len(events) == 1

    def test_parse_invalid_json(self, empty_project_root: Path) -> None:
        """测试非法 JSON 抛出异常。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        with pytest.raises(Exception) as exc_info:
            auditor._parse_events_from_text("{broken json}", "ch_001")
        assert any(
            n in type(exc_info.value).__name__ for n in ["JSONDecodeError", "ValidationError"]
        )

    def test_parse_missing_required_field(self, empty_project_root: Path) -> None:
        """测试缺少必填字段时抛出 ValidationError。"""
        text = """[{
            "event_id": "evt_001"
        }]"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        with pytest.raises(ValidationError):
            auditor._parse_events_from_text(text, "ch_001")

    def test_parse_invalid_causal_pressure(self, empty_project_root: Path) -> None:
        """测试因果压强超范围。"""
        text = """[{
            "event_id": "evt_001",
            "chapter_id": "ch_001",
            "timestamp": "第1天",
            "character_id": "char_001",
            "event_type": "INJURY",
            "description": "test",
            "causal_pressure": 2.0
        }]"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        with pytest.raises(ValidationError):
            auditor._parse_events_from_text(text, "ch_001")


class TestRescueMode:
    """人类急救模式测试。"""

    def test_rescue_skip_writes_dirty_flag(self, empty_project_root: Path) -> None:
        """测试脏提交写入 dirty_flag。"""
        chapter_path = empty_project_root / "draft" / "ch_001.md"
        storage = YAMLStorage()
        storage.write_markdown_file(
            chapter_path,
            {"id": "ch_001", "title": "第一章"},
            "正文",
        )

        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            yaml_storage=storage,
        )

        result = auditor._rescue_skip("ch_001")
        assert result.dirty is True
        assert result.success is False
        assert result.events == []

        meta, _ = storage.read_markdown_file(chapter_path)
        assert meta.get("dirty_flag") == "extraction_failed"

    def test_rescue_skip_nonexistent_chapter(self, empty_project_root: Path) -> None:
        """测试跳过不存在的章节文件时仍返回脏结果。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._rescue_skip("nonexistent_ch")
        assert result.dirty is True
        assert result.success is False


class TestLoadPrompt:
    """_load_prompt Prompt 加载测试。"""

    def test_load_prompt_fallback_when_missing(self, empty_project_root: Path) -> None:
        """测试 Prompt 文件不存在时返回默认文本。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "nonexistent.md",
        )
        prompt = auditor._load_prompt()
        assert "叙事状态审计员" in prompt

    def test_load_prompt_from_file(self, empty_project_root: Path) -> None:
        """测试从文件加载 Prompt。"""
        prompt_path = empty_project_root / "auditor.v1.md"
        prompt_path.write_text("你是自定义审稿官。", encoding="utf-8")
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=prompt_path,
        )
        prompt = auditor._load_prompt()
        assert "自定义审稿官" in prompt


class TestEmptyResponseRetry:
    """LLM 返回空文本时的重试测试。"""

    def test_empty_then_success(self, empty_project_root: Path, valid_json_response: str) -> None:
        """测试第一次返回空文本后重试成功。"""
        llm_bus = MockLLMBus(["", valid_json_response])
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )
        result = auditor.extract_events_with_retry("ch_001", "正文")
        assert result.success is True
        assert len(result.events) == 1
        assert llm_bus.call_count == 2

    def test_all_empty_then_failed_result(self, empty_project_root: Path) -> None:
        """测试连续返回空文本后返回失败结果（不触发 rescue mode）。"""
        llm_bus = MockLLMBus(["", "", ""])
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )
        result = auditor.extract_events_with_retry("ch_001", "正文")
        # 空文本不触发 rescue mode，而是防御性返回失败结果
        assert result.success is False
        assert result.events == []
        assert llm_bus.call_count == 3


class TestGenerateDiffs:
    """generate_diffs Diff 生成测试。"""

    def test_generate_diffs_from_events(self, empty_project_root: Path) -> None:
        """测试从事件列表生成 Diff 列表。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="受伤",
                causal_pressure=0.8,
            ),
            EventCreate(
                event_id="evt_002",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.HEAL,
                description="治愈",
                causal_pressure=0.5,
            ),
        ]
        diffs = auditor.generate_diffs(events)
        assert len(diffs) == 2
        assert all(d.action == "add" for d in diffs)
        assert diffs[0].event.event_id == "evt_001"
        assert diffs[1].event.event_id == "evt_002"

    def test_generate_diffs_empty_list(self, empty_project_root: Path) -> None:
        """测试空事件列表生成空 Diff。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        diffs = auditor.generate_diffs([])
        assert diffs == []


class TestApplyConfirmedEvents:
    """apply_confirmed_events 事件写入测试。"""

    def test_apply_events_calls_state_manager(self, empty_project_root: Path) -> None:
        """测试将确认事件写入状态管理器。"""
        mock_sm = MagicMock()
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=mock_sm,
            project_root=empty_project_root,
        )
        events = [
            EventCreate(
                event_id="evt_001",
                chapter_id="ch_001",
                timestamp="第1天",
                character_id="char_001",
                event_type=EventType.INJURY,
                description="受伤",
                causal_pressure=0.8,
            ),
        ]
        event_ids = auditor.apply_confirmed_events(events, "ch_001")
        assert event_ids == ["evt_001"]
        mock_sm.apply_event.assert_called_once()

    def test_apply_empty_events(self, empty_project_root: Path) -> None:
        """测试空事件列表不调用状态管理器。"""
        mock_sm = MagicMock()
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=mock_sm,
            project_root=empty_project_root,
        )
        event_ids = auditor.apply_confirmed_events([], "ch_001")
        assert event_ids == []
        mock_sm.apply_event.assert_not_called()


class TestActiveCharactersInPrompt:
    """active_characters 信息注入测试。"""

    def test_active_characters_included_in_messages(
        self, empty_project_root: Path, valid_json_response: str
    ) -> None:
        """测试活跃角色 ID 被包含在发送给 LLM 的消息中。"""
        llm_bus = MockLLMBus([valid_json_response])
        auditor = Auditor(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "prompts" / "auditor.v1.md",
        )
        auditor.extract_events_with_retry(
            "ch_001", "正文", active_characters=["char_001", "char_002"]
        )
        # 检查发送给 LLM 的消息中包含角色信息
        user_msg = llm_bus.last_messages[1]["content"]
        assert "char_001" in user_msg
        assert "char_002" in user_msg


class TestRescueEdit:
    """_rescue_edit 手动修补测试。"""

    @patch("builtins.input", side_effect=["[]", "END"])
    def test_rescue_edit_valid_json(self, mock_input: MagicMock, empty_project_root: Path) -> None:
        """测试手动输入合法 JSON。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._rescue_edit("ch_001", "broken json")
        assert result.success is True
        assert result.events == []

    @patch("builtins.input", side_effect=["{broken json}", "END"])
    def test_rescue_edit_invalid_json(
        self, mock_input: MagicMock, empty_project_root: Path
    ) -> None:
        """测试手动输入非法 JSON。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._rescue_edit("ch_001", "broken")
        assert result.success is False

    @patch("builtins.input", side_effect=[EOFError])
    def test_rescue_edit_empty_input(self, mock_input: MagicMock, empty_project_root: Path) -> None:
        """测试空输入取消编辑。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._rescue_edit("ch_001", "broken")
        assert result.success is False
        assert "取消" in (result.error or "") or result.events == []


class TestTriggerRescueMode:
    """_trigger_rescue_mode 急救模式触发测试。"""

    @patch("rich.prompt.Prompt.ask", return_value="s")
    def test_rescue_skip_choice(self, mock_prompt: MagicMock, empty_project_root: Path) -> None:
        """测试选择 Skip 进入脏提交。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._trigger_rescue_mode("ch_001", "failed", "error")
        assert result.dirty is True
        assert result.success is False

    @patch("rich.prompt.Prompt.ask", return_value="e")
    @patch("builtins.input", side_effect=["[]", "END"])
    def test_rescue_edit_choice(
        self, mock_input: MagicMock, mock_prompt: MagicMock, empty_project_root: Path
    ) -> None:
        """测试选择 Edit 进入手动修补。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = auditor._trigger_rescue_mode("ch_001", "failed", "error")
        assert result.success is True

    @patch("rich.prompt.Prompt.ask", return_value="a")
    def test_rescue_abort_choice(self, mock_prompt: MagicMock, empty_project_root: Path) -> None:
        """测试选择 Abort 抛出异常。"""
        auditor = Auditor(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        with pytest.raises(AuditorAbortError):
            auditor._trigger_rescue_mode("ch_001", "failed", "error")
