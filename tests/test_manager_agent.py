"""Manager Agent 模块测试 - 状态更新提取与应用。"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from opennovel.agents.manager import Manager
from opennovel.schemas.event import EventType
from opennovel.schemas.manager_update import ManagerUpdateResult
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
def valid_manager_response() -> str:
    """合法的 ManagerUpdateResult JSON。"""
    return json.dumps(
        {
            "character_updates": [
                {
                    "character_id": "char_001",
                    "field": "emotional.grief",
                    "value": 0.8,
                    "reason": "失去了重要的人",
                }
            ],
            "events": [
                {
                    "event_id": "evt_ch001_001",
                    "character_id": "char_001",
                    "event_type": "EMOTION_SHIFT",
                    "description": "悲伤情绪上升",
                    "causal_pressure": 0.5,
                    "timestamp": "第1天·黄昏",
                }
            ],
            "chapter_summary": "主角经历了悲伤的一天",
        },
        ensure_ascii=False,
    )


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    """临时项目根目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    (root / "prompts").mkdir()
    return root


@pytest.fixture
def manager(
    empty_project_root: Path, valid_manager_response: str
) -> tuple[Manager, MockLLMBus, MagicMock]:
    """创建 Manager 实例及其 mock 依赖。"""
    llm_bus = MockLLMBus([valid_manager_response])
    mock_sm = MagicMock()
    manager = Manager(
        llm_bus=llm_bus,  # type: ignore[arg-type]
        state_manager=mock_sm,
        project_root=empty_project_root,
    )
    return manager, llm_bus, mock_sm


# ── 初始化测试 ──


class TestManagerInit:
    """Manager 初始化测试。"""

    def test_init_with_defaults(self, empty_project_root: Path) -> None:
        """测试默认参数初始化。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        assert manager.project_root == empty_project_root
        assert manager.llm_bus is not None
        assert manager.state_manager is not None

    def test_init_with_custom_prompt_path(self, empty_project_root: Path) -> None:
        """测试自定义 Prompt 路径。"""
        custom_path = empty_project_root / "prompts" / "custom.md"
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=custom_path,
        )
        assert manager.prompt_path == custom_path

    def test_init_with_yaml_storage(self, empty_project_root: Path) -> None:
        """测试注入 YAMLStorage。"""
        storage = YAMLStorage()
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            yaml_storage=storage,
        )
        assert manager.yaml_storage is storage


# ── 状态更新测试 ──


class TestManagerUpdate:
    """Manager update 状态更新测试。"""

    def test_update_returns_result(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 update 返回 ManagerUpdateResult。"""
        llm_bus = MockLLMBus([valid_manager_response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])

        assert isinstance(result, ManagerUpdateResult)
        assert len(result.character_updates) == 1
        assert result.character_updates[0].character_id == "char_001"
        assert result.character_updates[0].field == "emotional.grief"
        assert result.character_updates[0].value == 0.8
        assert len(result.events) == 1
        assert result.chapter_summary == "主角经历了悲伤的一天"

    def test_update_calls_apply_character_diff(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 update 调用 state_manager.apply_character_diff。"""
        llm_bus = MockLLMBus([valid_manager_response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        manager.update("ch_001", "章节正文", ["char_001"])

        mock_sm.apply_character_diff.assert_called_once()
        call_args = mock_sm.apply_character_diff.call_args
        assert call_args[0][0] == "char_001"
        # 验证嵌套字典结构 {"emotional": {"grief": 0.8}}
        updates = call_args[0][1]
        assert updates == {"emotional": {"grief": 0.8}}

    def test_update_calls_apply_event(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 update 调用 state_manager.apply_event。"""
        llm_bus = MockLLMBus([valid_manager_response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])

        mock_sm.apply_event.assert_called_once()
        event = mock_sm.apply_event.call_args[0][0]
        assert event.event_id == "evt_ch001_001"
        assert event.event_type == EventType.EMOTION_SHIFT
        assert event.causal_pressure == 0.5

    def test_update_with_empty_updates(self, empty_project_root: Path) -> None:
        """测试无变更时 update 正常返回。"""
        response = json.dumps(
            {
                "character_updates": [],
                "events": [],
                "chapter_summary": "平淡的一天",
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])

        assert result.character_updates == []
        assert result.events == []
        assert result.chapter_summary == "平淡的一天"
        mock_sm.apply_character_diff.assert_not_called()
        mock_sm.apply_event.assert_not_called()

    def test_update_with_multiple_character_updates(self, empty_project_root: Path) -> None:
        """测试多个角色状态更新。"""
        response = json.dumps(
            {
                "character_updates": [
                    {
                        "character_id": "char_001",
                        "field": "emotional.grief",
                        "value": 0.8,
                        "reason": "失去战友",
                    },
                    {
                        "character_id": "char_002",
                        "field": "location",
                        "value": "废弃教堂",
                        "reason": "转移阵地",
                    },
                ],
                "events": [],
                "chapter_summary": "两人转移",
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001", "char_002"])

        assert len(result.character_updates) == 2
        assert mock_sm.apply_character_diff.call_count == 2

    def test_update_with_markdown_code_block(self, empty_project_root: Path) -> None:
        """测试 LLM 返回被 markdown 代码块包裹的 JSON。"""
        response = """```json
{
  "character_updates": [],
  "events": [],
  "chapter_summary": "代码块包裹的响应"
}
```"""
        llm_bus = MockLLMBus([response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])
        assert result.chapter_summary == "代码块包裹的响应"


# ── 重试测试 ──


class TestManagerUpdateRetry:
    """Manager JSON 解析失败重试测试。"""

    def test_retry_then_success(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试第一次 JSON 解析失败后重试成功。"""
        llm_bus = MockLLMBus(
            [
                '{"broken": json}',  # JSONDecodeError
                valid_manager_response,
            ]
        )
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])

        assert isinstance(result, ManagerUpdateResult)
        assert result.chapter_summary == "主角经历了悲伤的一天"
        assert llm_bus.call_count == 2

    def test_all_retries_fail_raises(self, empty_project_root: Path) -> None:
        """测试所有重试都失败后抛出 RuntimeError。"""
        llm_bus = MockLLMBus(
            [
                '{"broken": json}',
                '{"broken": json}',
                '{"broken": json}',
            ]
        )
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )

        with pytest.raises(RuntimeError, match="Manager 更新失败"):
            manager.update("ch_001", "章节正文", ["char_001"])
        assert llm_bus.call_count == 3

    def test_retry_empty_text_then_success(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 LLM 返回空文本后重试成功。"""
        llm_bus = MockLLMBus(["", valid_manager_response])
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = manager.update("ch_001", "章节正文", ["char_001"])
        assert isinstance(result, ManagerUpdateResult)
        assert llm_bus.call_count == 2


# ── _apply_updates 方法测试 ──


class TestManagerApplyUpdates:
    """_apply_updates 内部方法测试。"""

    def test_build_nested_update_single_level(self, empty_project_root: Path) -> None:
        """测试单层字段路径转嵌套字典。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = manager._build_nested_update("location", "废弃教堂")
        assert result == {"location": "废弃教堂"}

    def test_build_nested_update_two_levels(self, empty_project_root: Path) -> None:
        """测试两层字段路径转嵌套字典。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = manager._build_nested_update("emotional.grief", 0.8)
        assert result == {"emotional": {"grief": 0.8}}

    def test_build_nested_update_three_levels(self, empty_project_root: Path) -> None:
        """测试三层字段路径转嵌套字典。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        result = manager._build_nested_update("physical.state.hp", 80)
        assert result == {"physical": {"state": {"hp": 80}}}

    def test_apply_updates_calls_methods(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 _apply_updates 调用 state_manager 的方法。"""
        mock_sm = MagicMock()
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = ManagerUpdateResult(**json.loads(valid_manager_response))
        event_ids = manager._apply_updates(result)

        # 验证 apply_character_diff 被调用
        mock_sm.apply_character_diff.assert_called_once_with(
            "char_001", {"emotional": {"grief": 0.8}}
        )

        # 验证 apply_event 被调用
        mock_sm.apply_event.assert_called_once()
        assert event_ids == ["evt_ch001_001"]

    def test_apply_updates_continues_on_character_error(self, empty_project_root: Path) -> None:
        """测试角色更新失败时不阻塞后续更新。"""
        mock_sm = MagicMock()
        mock_sm.apply_character_diff.side_effect = FileNotFoundError("角色不存在")
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=mock_sm,
            project_root=empty_project_root,
        )

        result = ManagerUpdateResult(
            character_updates=[
                {
                    "character_id": "char_999",
                    "field": "emotional.grief",
                    "value": 0.5,
                    "reason": "test",
                }
            ],
            events=[
                {
                    "event_id": "evt_001",
                    "character_id": "char_001",
                    "event_type": "EMOTION_SHIFT",
                    "description": "test",
                    "causal_pressure": 0.5,
                    "timestamp": "day1",
                }
            ],
            chapter_summary="test",
        )
        event_ids = manager._apply_updates(result)

        # 角色更新失败，但事件仍应写入
        mock_sm.apply_event.assert_called_once()
        assert event_ids == ["evt_001"]


# ── Canon 保护测试 ──


class TestManagerCanonProtection:
    """验证 Manager 不修改 Canon 设定。"""

    def test_prompt_constrains_canon_modification(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 Prompt 中包含不修改 Canon 的约束（通过检查发送给 LLM 的消息）。

        Manager 的职责是提取状态变更（STATE MEMORY 层），不应触碰 canon/ 目录。
        这个测试验证 prompt 中包含相关约束。
        """
        llm_bus = MockLLMBus([valid_manager_response])
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )

        # 写入一个自定义 prompt，包含 canon 保护约束
        prompt_path = empty_project_root / "prompts" / "manager.v1.md"
        prompt_path.write_text(
            "# Manager Agent\n\n你不能修改 canon/ 目录中的任何设定文件。\n"
            "你的职责仅限于提取角色状态变更和事件记录。",
            encoding="utf-8",
        )
        manager.prompt_path = prompt_path

        manager.update("ch_001", "章节正文", ["char_001"])

        # 验证 system prompt 包含 canon 保护约束
        system_msg = llm_bus.last_messages[0]["content"]
        assert "不能修改 canon" in system_msg

    def test_no_canon_files_modified(
        self, empty_project_root: Path, valid_manager_response: str
    ) -> None:
        """测试 Manager 运行后 canon/ 目录未被修改。"""
        canon_dir = empty_project_root / "canon"
        canon_dir.mkdir()
        canon_file = canon_dir / "world_rules.md"
        original_content = "# 世界规则\n不可修改的设定。"
        canon_file.write_text(original_content, encoding="utf-8")

        llm_bus = MockLLMBus([valid_manager_response])
        manager = Manager(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )

        manager.update("ch_001", "章节正文", ["char_001"])

        # 验证 canon 文件未被修改
        assert canon_file.read_text(encoding="utf-8") == original_content


# ── Prompt 加载测试 ──


class TestManagerLoadPrompt:
    """_load_prompt Prompt 加载测试。"""

    def test_load_prompt_fallback_when_missing(self, empty_project_root: Path) -> None:
        """测试 Prompt 文件不存在时返回默认文本。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=empty_project_root / "nonexistent.md",
        )
        prompt = manager._load_prompt()
        assert "叙事状态管理员" in prompt

    def test_load_prompt_from_file(self, empty_project_root: Path) -> None:
        """测试从文件加载 Prompt。"""
        prompt_path = empty_project_root / "manager.v1.md"
        prompt_path.write_text("你是自定义状态管理员。", encoding="utf-8")
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
            prompt_path=prompt_path,
        )
        prompt = manager._load_prompt()
        assert "自定义状态管理员" in prompt


# ── 解析测试 ──


class TestManagerParseUpdate:
    """_parse_update_from_text 解析逻辑测试。"""

    def test_parse_valid_json(self, empty_project_root: Path) -> None:
        """测试解析合法 JSON。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        text = json.dumps(
            {
                "character_updates": [],
                "events": [],
                "chapter_summary": "test",
            },
            ensure_ascii=False,
        )
        result = manager._parse_update_from_text(text)
        assert isinstance(result, ManagerUpdateResult)
        assert result.chapter_summary == "test"

    def test_parse_markdown_code_block(self, empty_project_root: Path) -> None:
        """测试解析被 markdown 代码块包裹的 JSON。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        text = """```json
{
  "character_updates": [],
  "events": [],
  "chapter_summary": "代码块测试"
}
```"""
        result = manager._parse_update_from_text(text)
        assert result.chapter_summary == "代码块测试"

    def test_parse_invalid_json_raises(self, empty_project_root: Path) -> None:
        """测试非法 JSON 抛出异常。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        with pytest.raises(json.JSONDecodeError):
            manager._parse_update_from_text("{broken json}")

    def test_parse_missing_field_raises(self, empty_project_root: Path) -> None:
        """测试缺少必填字段抛出 ValidationError。"""
        manager = Manager(
            llm_bus=MagicMock(),
            state_manager=MagicMock(),
            project_root=empty_project_root,
        )
        text = '{"character_updates": []}'  # 缺少 events 和 chapter_summary
        with pytest.raises(Exception):
            manager._parse_update_from_text(text)
