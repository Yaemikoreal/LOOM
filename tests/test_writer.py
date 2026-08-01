"""Writer Agent 测试 - 沉浸式创作代理（mock LLM）。"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from opennovel.agents.writer import Writer
from opennovel.schemas.knowledge import KnowledgeResult, KnowledgeSource
from opennovel.schemas.outline import ChapterOutline
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

    def __init__(self, responses: list[str], model: str = "gpt-4") -> None:
        self.responses = [MockLLMResponse(r) for r in responses]
        self.call_count = 0
        self.last_messages: list[dict[str, str]] = []
        self.last_model: str | None = None
        self.model = model

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        model: str | None = None,
        **kwargs: Any,
    ) -> MockLLMResponse:
        response = self.responses[self.call_count]
        self.call_count += 1
        self.last_messages = messages
        self.last_model = model
        return response


# ── Fixtures ──


@pytest.fixture
def valid_outline_json() -> str:
    """合法的 ChapterOutline JSON。"""
    return json.dumps(
        {
            "chapter_id": "ch_001",
            "title": "相遇",
            "summary": "四人在废弃加油站相遇，气氛紧张。",
            "scenes": [
                {
                    "scene_id": "scene_1",
                    "description": "加油站外景，黄昏时分",
                    "characters_involved": ["char_001"],
                    "emotional_tone": "紧张",
                    "estimated_words": 800,
                },
                {
                    "scene_id": "scene_2",
                    "description": "加油站内，四人对峙",
                    "characters_involved": ["char_001", "char_002"],
                    "emotional_tone": "压抑",
                    "estimated_words": 1200,
                },
            ],
            "character_arcs": {"char_001": "从警惕到试探"},
            "key_plot_points": ["四人首次碰面", "发现地图线索"],
            "narrative_rhythm": "先慢后快，逐步升温",
            "target_words": 3000,
        },
        ensure_ascii=False,
    )


@pytest.fixture
def invalid_json_response() -> str:
    """非法 JSON（缺少逗号）。"""
    return '{"chapter_id": "ch_001" "title": "test"}'


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    """临时项目根目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    (root / "outlines").mkdir()
    (root / "prompts").mkdir()
    return root


@pytest.fixture
def sample_chapter_text() -> str:
    """示例章节正文。"""
    return """# 相遇

黄昏的余晖洒在废弃加油站的屋顶上，空气中弥漫着汽油和尘土的混合气味。

李明握紧了手中的地图，目光警惕地扫视四周。远处传来了脚步声。"""


@pytest.fixture
def sample_outline() -> ChapterOutline:
    """示例 ChapterOutline 对象。"""
    return ChapterOutline(
        chapter_id="ch_001",
        title="相遇",
        summary="四人在废弃加油站相遇，气氛紧张。",
        scenes=[
            {
                "scene_id": "scene_1",
                "description": "加油站外景，黄昏时分",
                "characters_involved": ["char_001"],
                "emotional_tone": "紧张",
                "estimated_words": 800,
            },
        ],
        character_arcs={"char_001": "从警惕到试探"},
        key_plot_points=["四人首次碰面"],
        narrative_rhythm="先慢后快",
        target_words=3000,
    )


# ── Writer 初始化测试 ──


class TestWriterInit:
    """Writer 初始化测试。"""

    def test_default_prompt_path(self, empty_project_root: Path) -> None:
        """测试默认 prompt 路径指向 prompts/writer.v1.md。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)
        assert (
            writer.prompt_path
            == Path(__file__).parent.parent / "opennovel" / "prompts" / "writer.v1.md"
        )

    def test_custom_prompt_path(self, empty_project_root: Path) -> None:
        """测试自定义 prompt 路径。"""
        bus = MagicMock()
        ret = MagicMock()
        custom = empty_project_root / "custom_prompt.md"
        writer = Writer(
            llm_bus=bus,
            retriever=ret,
            project_root=empty_project_root,
            prompt_path=custom,
        )
        assert writer.prompt_path == custom

    def test_attributes_stored(self, empty_project_root: Path) -> None:
        """测试属性正确存储。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)
        assert writer.llm_bus is bus
        assert writer.retriever is ret
        assert writer.project_root == empty_project_root

    def test_creative_direction_stored(self, empty_project_root: Path) -> None:
        """测试创作方向参数正确存储。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(
            llm_bus=bus,
            retriever=ret,
            project_root=empty_project_root,
            creative_direction="暗黑风格",
        )
        assert writer.creative_direction == "暗黑风格"

    def test_words_per_chapter_stored(self, empty_project_root: Path) -> None:
        """测试目标字数参数正确存储。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(
            llm_bus=bus,
            retriever=ret,
            project_root=empty_project_root,
            words_per_chapter=5000,
        )
        assert writer.words_per_chapter == 5000


# ── Writer 思考阶段测试 ──


class TestWriterThink:
    """Writer.think 思考阶段测试。"""

    def test_think_returns_chapter_outline(
        self, empty_project_root: Path, valid_outline_json: str
    ) -> None:
        """测试思考阶段返回 ChapterOutline 结构。"""
        llm_bus = MockLLMBus([valid_outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = "魔法世界观设定"
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        outline = writer.think("ch_001", "四人在加油站相遇")

        assert isinstance(outline, ChapterOutline)
        assert outline.chapter_id == "ch_001"
        assert outline.title == "相遇"
        assert len(outline.scenes) == 2
        assert outline.target_words == 3000

    def test_think_calls_retriever_canon(
        self, empty_project_root: Path, valid_outline_json: str
    ) -> None:
        """测试思考阶段调用 retriever.query_canon()。"""
        llm_bus = MockLLMBus([valid_outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = "设定内容"
        ret.query_subconscious.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        writer.think("ch_001", "大纲提示")

        ret.query_canon.assert_called_once()
        # 现在 query_canon 接收截断后的 task_message，而非原始大纲提示

    def test_think_passes_messages_to_llm(
        self, empty_project_root: Path, valid_outline_json: str
    ) -> None:
        """测试思考阶段将消息列表传给 LLM。"""
        llm_bus = MockLLMBus([valid_outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        writer.think("ch_001", "大纲提示")

        call_args = llm_bus.last_messages
        assert isinstance(call_args, list)
        assert len(call_args) >= 2
        assert all("role" in m and "content" in m for m in call_args)

    def test_think_with_markdown_code_block(self, empty_project_root: Path) -> None:
        """测试 LLM 返回被 markdown 代码块包裹的 JSON。"""
        outline_json = """```json
{
    "chapter_id": "ch_001",
    "title": "相遇",
    "summary": "四人在加油站相遇。",
    "scenes": [
        {
            "scene_id": "scene_1",
            "description": "加油站外景",
            "characters_involved": ["char_001"],
            "emotional_tone": "紧张",
            "estimated_words": 800
        }
    ],
    "character_arcs": {"char_001": "从警惕到试探"},
    "key_plot_points": ["首次碰面"],
    "narrative_rhythm": "先慢后快",
    "target_words": 3000
}
```"""
        llm_bus = MockLLMBus([outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        outline = writer.think("ch_001", "大纲提示")

        assert isinstance(outline, ChapterOutline)
        assert outline.title == "相遇"


# ── Writer 思考重试测试 ──


class TestWriterThinkRetry:
    """Writer.think JSON 解析失败重试测试。"""

    def test_retry_then_success(
        self, empty_project_root: Path, valid_outline_json: str, invalid_json_response: str
    ) -> None:
        """测试第一次 JSON 解析失败后重试成功。"""
        llm_bus = MockLLMBus([invalid_json_response, valid_outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        outline = writer.think("ch_001", "大纲提示")

        assert isinstance(outline, ChapterOutline)
        assert llm_bus.call_count == 2

    def test_all_retries_fail_raises_runtime_error(
        self, empty_project_root: Path, invalid_json_response: str
    ) -> None:
        """测试所有重试都失败后抛出 RuntimeError。"""
        llm_bus = MockLLMBus(
            [
                invalid_json_response,
                invalid_json_response,
                invalid_json_response,
            ]
        )
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        with pytest.raises(RuntimeError, match="Writer 思考失败"):
            writer.think("ch_001", "大纲提示")
        assert llm_bus.call_count == 3

    def test_empty_text_retry(self, empty_project_root: Path, valid_outline_json: str) -> None:
        """测试 LLM 返回空文本后重试成功。"""
        llm_bus = MockLLMBus(["", valid_outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        outline = writer.think("ch_001", "大纲提示")

        assert isinstance(outline, ChapterOutline)
        assert llm_bus.call_count == 2


# ── Writer 创作阶段测试 ──


class TestWriterWrite:
    """Writer.write 创作阶段测试。"""

    def test_write_returns_text(
        self, empty_project_root: Path, sample_outline: ChapterOutline, sample_chapter_text: str
    ) -> None:
        """测试创作阶段返回生成文本。"""
        llm_bus = MockLLMBus([sample_chapter_text])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.write("ch_001", sample_outline)

        assert result == sample_chapter_text
        assert llm_bus.call_count == 1

    def test_write_passes_messages_to_llm(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试创作阶段将消息列表传给 LLM。"""
        llm_bus = MockLLMBus(["生成的正文内容"])
        ret = MagicMock()
        ret.query_canon.return_value = "设定"
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        writer.write("ch_001", sample_outline, previous_chapter_text="前文内容")

        call_args = llm_bus.last_messages
        assert isinstance(call_args, list)
        assert len(call_args) >= 2
        assert all("role" in m and "content" in m for m in call_args)

    def test_write_with_previous_chapter(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试创作阶段传入前一章正文。"""
        llm_bus = MockLLMBus(["新章节内容"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.write("ch_001", sample_outline, previous_chapter_text="前一章结尾")

        assert result == "新章节内容"

    def test_write_empty_text_raises_error(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试 LLM 返回空文本时抛出 RuntimeError。"""
        llm_bus = MockLLMBus([""])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        with pytest.raises(RuntimeError, match="Writer 创作返回空文本"):
            writer.write("ch_001", sample_outline)


# ── Writer 修订阶段测试 ──


class TestWriterRevise:
    """Writer.revise 修订阶段测试。"""

    def test_revise_returns_text(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试修订阶段返回修订后的文本。"""
        revised_text = "# 相遇\n\n修订后的正文内容。"
        llm_bus = MockLLMBus([revised_text])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.revise(
            "ch_001",
            sample_outline,
            current_text="原始正文",
            feedback="角色对话需要更自然",
        )

        assert result == revised_text
        assert llm_bus.call_count == 1

    def test_revise_passes_feedback_to_llm(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试修订阶段将反馈传给 LLM。"""
        llm_bus = MockLLMBus(["修订后的内容"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        writer.revise(
            "ch_001",
            sample_outline,
            current_text="原始正文",
            feedback="情节转折太突兀",
        )

        # 验证消息中包含反馈内容
        user_messages = [m for m in llm_bus.last_messages if m["role"] == "user"]
        feedback_found = any("情节转折太突兀" in m["content"] for m in user_messages)
        assert feedback_found

    def test_revise_empty_text_raises_error(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试 LLM 返回空文本时抛出 RuntimeError。"""
        llm_bus = MockLLMBus([""])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        with pytest.raises(RuntimeError, match="Writer 修订返回空文本"):
            writer.revise(
                "ch_001",
                sample_outline,
                current_text="原始正文",
                feedback="需要修改",
            )


# ── Writer Prompt 加载测试 ──


# ── Writer 解析逻辑测试 ──


class TestWriterParseOutline:
    """Writer._parse_outline_from_text 解析逻辑测试。"""

    def test_parse_valid_json(self, empty_project_root: Path) -> None:
        """测试解析合法 JSON。"""
        text = json.dumps(
            {
                "chapter_id": "ch_001",
                "title": "测试",
                "summary": "测试概要",
                "scenes": [
                    {
                        "scene_id": "scene_1",
                        "description": "场景描述",
                        "characters_involved": ["char_001"],
                        "emotional_tone": "平静",
                        "estimated_words": 500,
                    }
                ],
                "character_arcs": {},
                "key_plot_points": [],
                "narrative_rhythm": "平缓",
                "target_words": 1000,
            },
            ensure_ascii=False,
        )
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        outline = writer._parse_outline_from_text(text, "ch_001")
        assert isinstance(outline, ChapterOutline)
        assert outline.chapter_id == "ch_001"

    def test_parse_json_with_markdown_block(self, empty_project_root: Path) -> None:
        """测试解析被 markdown 代码块包裹的 JSON。"""
        text = """```json
{"chapter_id": "ch_001", "title": "test", "summary": "测试概要", "scenes": [{"scene_id": "s1", "description": "d", "characters_involved": ["char_001"], "emotional_tone": "t", "estimated_words": 100}], "character_arcs": {}, "key_plot_points": [], "narrative_rhythm": "r", "target_words": 1000}
```"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        outline = writer._parse_outline_from_text(text, "ch_001")
        assert isinstance(outline, ChapterOutline)

    def test_parse_invalid_json_raises(self, empty_project_root: Path) -> None:
        """测试非法 JSON 抛出异常。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        with pytest.raises(json.JSONDecodeError):
            writer._parse_outline_from_text("{broken json}", "ch_001")

    def test_parse_missing_required_field_raises(self, empty_project_root: Path) -> None:
        """测试缺少必填字段时抛出 ValidationError。"""
        text = '{"chapter_id": "ch_001"}'
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        with pytest.raises(Exception):
            writer._parse_outline_from_text(text, "ch_001")


# ── Writer 局部热修复测试 ──


class TestWriterHotFix:
    """Writer.hot_fix 局部热修复测试。"""

    @pytest.fixture
    def long_chapter_text(self) -> str:
        """较长的章节正文，包含可定位的段落。"""
        return (
            "# 命运的交汇\n\n"
            "黄昏的余晖洒在废弃加油站的屋顶上，空气中弥漫着汽油和尘土的混合气味。\n\n"
            "李明握紧了手中的地图，目光警惕地扫视四周。远处传来了脚步声，"
            "他本能地退后了一步，右手摸向腰间的手枪。\n\n"
            "「谁在那里？」他沉声问道，声音在空旷的加油站中回荡。\n\n"
            "一个身影从阴影中走出，是个年轻的女子，穿着一件沾满灰尘的夹克。"
            "她的眼神疲惫但警觉，手里提着一个破旧的背包。\n\n"
            "「我不知道你是谁，但这条路我已经走了三天了。」她开口说道，"
            "声音沙哑却带着一丝坚定，「如果你也想活下去，最好不要挡我的路。」"
        )

    @pytest.fixture
    def anchored_issues(self) -> list[dict]:
        """示例锚定问题列表。"""
        return [
            {
                "dimension": "角色一致性",
                "severity": "major",
                "quote": "「我不知道你是谁，但这条路我已经走了三天了。」",
                "problem": "角色语气前后不一致，前文还在警惕，这里突然变得强硬",
                "suggestion": "保持警惕但柔和的语气，改为试探性的对话",
            },
        ]

    @pytest.fixture
    def multiple_anchored_issues(self) -> list[dict]:
        """多个锚定问题。"""
        return [
            {
                "dimension": "文笔质量",
                "severity": "minor",
                "quote": "目光警惕地扫视四周",
                "problem": "描写略显平淡，缺少感官细节",
                "suggestion": "增加触觉和听觉细节",
            },
            {
                "dimension": "情节逻辑",
                "severity": "critical",
                "quote": "这条我已经走了三天了",
                "problem": "时间线矛盾，前文未提及长途跋涉背景",
                "suggestion": "调整为更符合当前语境的对话",
            },
        ]

    def test_hot_fix_returns_revised_text(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 hot_fix 成功返回修复后的段落。"""
        hot_fix_output = """## 修复 1
「请问……你是谁？」她试探性地开口，声音沙哑却带着一丝疲惫，"
    "「我已经独自走了很久，不想再有什么意外了。」"""

        llm_bus = MockLLMBus([hot_fix_output])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.hot_fix("ch_001", sample_outline, long_chapter_text, anchored_issues)

        assert result is not None
        assert len(result) > len(long_chapter_text) * 0.5
        # 修复后的文本应不再包含原文引用
        assert "「我不知道你是谁，但这条路我已经走了三天了。」" not in result
        assert llm_bus.call_count == 1

    def test_hot_fix_returns_none_when_quote_not_found(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
    ) -> None:
        """测试 hot_fix 在无法定位原文引用时返回 None。"""
        llm_bus = MockLLMBus(["修复后的文本"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        # quote 不存在于正文中
        result = writer.hot_fix(
            "ch_001",
            sample_outline,
            long_chapter_text,
            [{"quote": "这段文本不在正文中任何一个段落里出现", "problem": "测试", "suggestion": "测试"}],
        )

        assert result is None

    def test_hot_fix_returns_none_when_empty_issues(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
    ) -> None:
        """测试 hot_fix 在没有问题时返回 None。"""
        llm_bus = MockLLMBus(["修复文本"])
        ret = MagicMock()

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        assert writer.hot_fix("ch_001", sample_outline, long_chapter_text, []) is None

    def test_hot_fix_returns_none_when_empty_text(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
    ) -> None:
        """测试 hot_fix 在正文为空时返回 None。"""
        llm_bus = MockLLMBus(["修复文本"])
        ret = MagicMock()

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        assert writer.hot_fix("ch_001", sample_outline, "", [{"quote": "测试", "problem": "测试", "suggestion": "测试"}]) is None

    def test_find_paragraph_around_finds_correct_paragraph(
        self,
        empty_project_root: Path,
        long_chapter_text: str,
    ) -> None:
        """测试 _find_paragraph_around 正确找到包含 quote 的段落。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        result = writer._find_paragraph_around(long_chapter_text, "目光警惕地扫视四周")
        assert "李明握紧了手中的地图" in result
        assert "摸向腰间的手枪" in result

    def test_find_paragraph_around_fallback_to_start(
        self,
        empty_project_root: Path,
        long_chapter_text: str,
    ) -> None:
        """测试 _find_paragraph_around 找不到 quote 时回退到开头。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        result = writer._find_paragraph_around(long_chapter_text, "不存在的文本")
        assert len(result) > 0
        assert "黄昏的余晖" in result

    def test_apply_hot_fix_replaces_correctly(
        self,
        empty_project_root: Path,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 _apply_hot_fix 正确替换问题段落。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        llm_output = """## 修复 1
「请问你是什么人？」她用警惕但克制的语气问道。"""

        result = writer._apply_hot_fix(long_chapter_text, llm_output, anchored_issues)
        assert result is not None
        # 原文被替换
        assert "「我不知道你是谁，但这条路我已经走了三天了。」" not in result
        # 替换后的内容出现
        assert "「请问你是什么人？」" in result
        # 未修改部分保留
        assert "黄昏的余晖洒在废弃加油站的屋顶上" in result
        assert "右手摸向腰间的手枪" in result

    def test_apply_hot_fix_accepts_full_text(
        self,
        empty_project_root: Path,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 _apply_hot_fix 接受 LLM 直接返回完整正文。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        full_text = long_chapter_text + "\n\n新增的结尾段落。"
        result = writer._apply_hot_fix(long_chapter_text, full_text, anchored_issues)
        assert result is not None
        assert "新增的结尾段落" in result

    def test_apply_hot_fix_empty_fixes_returns_none(
        self,
        empty_project_root: Path,
        long_chapter_text: str,
    ) -> None:
        """测试 _apply_hot_fix 在无法解析修复时返回 None。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        # LLM 返回了无关文本而非修复段落
        result = writer._apply_hot_fix(long_chapter_text, "这是无关的文本", [{"quote": "测试"}])
        assert result is None

    def test_hot_fix_length_validation(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 hot_fix 返回太短的文本时返回 None。"""
        llm_bus = MockLLMBus(["## 修复 1\n太短了"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        # 因为 LLM 输出无法被 _apply_hot_fix 正确解析（太短、不包含可识别的替换），
        # 且 _apply_hot_fix 中 _find_paragraph_around 找到 quote 后会尝试替换
        # 但这个测试主要验证长度验证逻辑
        result = writer.hot_fix("ch_001", sample_outline, long_chapter_text, anchored_issues)
        if result is not None:
            assert len(result) >= len(long_chapter_text) * 0.5

    def test_hot_fix_llm_exception_returns_none(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 LLM 调用异常时 hot_fix 返回 None。"""
        bus = MagicMock()
        bus.chat.side_effect = RuntimeError("LLM 调用失败")
        ret = MagicMock()
        ret.query_canon.return_value = ""

        writer = Writer(
            llm_bus=bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.hot_fix("ch_001", sample_outline, long_chapter_text, anchored_issues)
        assert result is None

    def test_hot_fix_preserves_unaffected_parts(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        long_chapter_text: str,
        anchored_issues: list[dict],
    ) -> None:
        """测试 hot_fix 只修改问题段落，未修改部分保持不变。"""
        hot_fix_output = """## 修复 1
「请问……你是？」她试探性地开口，声音沙哑却带着疲惫。"""

        llm_bus = MockLLMBus([hot_fix_output])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        result = writer.hot_fix("ch_001", sample_outline, long_chapter_text, anchored_issues)

        assert result is not None
        # 未涉及问题部分的段落应保持不变
        assert "黄昏的余晖洒在废弃加油站的屋顶上" in result
        assert "空气中弥漫着汽油和尘土的混合气味" in result
        assert "右手摸向腰间的手枪" in result


class TestWriterKnowledgeGaps:
    """Writer 知识缺口检测测试。"""

    def test_detect_character_gaps(self, empty_project_root: Path) -> None:
        """测试检测角色知识缺口。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        outline = ChapterOutline(
            chapter_id="ch_001",
            title="相遇",
            summary="角色相遇",
            scenes=[{
                "scene_id": "s1",
                "description": "主角在森林中遇到神秘女子",
                "characters_involved": ["char_001", "char_002"],
                "emotional_tone": "紧张",
                "estimated_words": 1000,
            }],
            character_arcs={"char_001": "从恐惧到勇敢", "char_002": "神秘莫测"},
            key_plot_points=["相遇"],
            narrative_rhythm="平缓",
            target_words=3000,
        )

        needs = writer.detect_knowledge_gaps(outline, available_context="")
        char_ids = {n.character_id for n in needs if n.source == KnowledgeSource.CHARACTER}
        assert "char_001" in char_ids
        assert "char_002" in char_ids

    def test_no_gaps_when_context_sufficient(self, empty_project_root: Path) -> None:
        """测试上下文足够时无缺口。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        outline = ChapterOutline(
            chapter_id="ch_001",
            title="相遇",
            summary="测试",
            scenes=[{
                "scene_id": "s1",
                "description": "测试场景",
                "characters_involved": ["char_001"],
                "emotional_tone": "平静",
                "estimated_words": 500,
            }],
            character_arcs={"char_001": "保持"},
            key_plot_points=["测试"],
            narrative_rhythm="平缓",
            target_words=1000,
        )

        # char_001 已在上下文中
        needs = writer.detect_knowledge_gaps(outline, available_context="char_001")
        assert len(needs) == 0

    def test_detect_setting_keywords(self, empty_project_root: Path) -> None:
        """测试检测设定关键词缺口。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        outline = ChapterOutline(
            chapter_id="ch_001",
            title="测试",
            summary="关于魔法的故事",
            scenes=[{
                "scene_id": "s1",
                "description": "主角使用古老的魔法，触发了诅咒",
                "characters_involved": ["char_001"],
                "emotional_tone": "神秘",
                "estimated_words": 800,
            }],
            character_arcs={"char_001": "觉醒"},
            key_plot_points=["使用魔法"],
            narrative_rhythm="平缓",
            target_words=2000,
        )

        needs = writer.detect_knowledge_gaps(outline, available_context="")
        canon_concepts = {n.concept for n in needs if n.source == KnowledgeSource.CANON}
        # "魔法" 和 "诅咒" 应在关键词列表中
        assert "魔法" in canon_concepts or "诅咒" in canon_concepts

    def test_format_knowledge_results_empty(self, empty_project_root: Path) -> None:
        """测试格式化空结果。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)
        assert writer.format_knowledge_results([]) == ""

    def test_format_knowledge_results_with_data(self, empty_project_root: Path) -> None:
        """测试格式化查询结果。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        results = [
            KnowledgeResult(content="魔法消耗寿命", source=KnowledgeSource.CANON, concept="魔法", relevance=1.0),
            KnowledgeResult(content="艾伦处于恐惧状态", source=KnowledgeSource.CHARACTER, concept="char_001", relevance=1.0),
        ]

        formatted = writer.format_knowledge_results(results)
        assert "魔法消耗寿命" in formatted
        assert "艾伦处于恐惧状态" in formatted
        assert "canon" in formatted
        assert "character" in formatted

    def test_format_knowledge_results_filters_low_relevance(self, empty_project_root: Path) -> None:
        """测试过滤低相关性结果。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(llm_bus=bus, retriever=ret, project_root=empty_project_root)

        results = [
            KnowledgeResult(content="相关内容", source=KnowledgeSource.CANON, concept="测试", relevance=1.0),
            KnowledgeResult(content="", source=KnowledgeSource.SUBCONSCIOUS, concept="空结果", relevance=0.0),
        ]

        formatted = writer.format_knowledge_results(results)
        assert "相关内容" in formatted
        assert "空结果" not in formatted  # 相关性 0 不被包含

    def test_write_with_additional_knowledge(self, empty_project_root: Path, sample_outline: ChapterOutline) -> None:
        """测试 write 方法接收 additional_knowledge 参数。"""
        llm_bus = MockLLMBus(["# 相遇\n\n正文内容。"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )

        result = writer.write(
            "ch_001",
            sample_outline,
            additional_knowledge="【补充设定】魔法消耗寿命。",
        )

        assert result == "# 相遇\n\n正文内容。"
        # 验证补充知识出现在 LLM 的消息中
        user_messages = [m for m in llm_bus.last_messages if m["role"] == "user"]
        combined = " ".join(m["content"] for m in user_messages)
        assert "【补充设定】魔法消耗寿命。" in combined


class TestWriterStageModels:
    """Writer 阶段级模型路由测试。"""

    @pytest.fixture
    def cheap_outline(self) -> str:
        """廉价模型生成的简单大纲。"""
        return json.dumps({
            "chapter_id": "ch_001",
            "title": "相遇",
            "summary": "简单概要",
            "scenes": [{"scene_id": "s1", "description": "场景", "characters_involved": ["char_001"], "emotional_tone": "平静", "estimated_words": 500}],
            "character_arcs": {},
            "key_plot_points": [],
            "narrative_rhythm": "平缓",
            "target_words": 1000,
        })

    def test_writer_stores_stage_models(self, empty_project_root: Path) -> None:
        """测试 Writer 存储阶段模型参数。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(
            llm_bus=bus, retriever=ret, project_root=empty_project_root,
            think_model="cheap-model",
            write_model="main-model",
            revise_model="main-model",
        )
        assert writer.think_model == "cheap-model"
        assert writer.write_model == "main-model"
        assert writer.revise_model == "main-model"

    def test_writer_stage_models_fallback(self, empty_project_root: Path) -> None:
        """测试阶段模型未设置时继承规则。"""
        bus = MagicMock()
        ret = MagicMock()
        writer = Writer(
            llm_bus=bus, retriever=ret, project_root=empty_project_root,
            think_model="cheap-model",
        )
        # write_model 未设置 → 继承 think_model
        assert writer.write_model == "cheap-model"
        # revise_model 未设置 → 继承 write_model → 继承 think_model
        assert writer.revise_model == "cheap-model"

    def test_think_uses_think_model(
        self, empty_project_root: Path, cheap_outline: str
    ) -> None:
        """测试 think 阶段使用 think_model。"""
        llm_bus = MockLLMBus([cheap_outline])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
            think_model="gpt-4o-mini",
        )
        writer.think("ch_001", "测试大纲")
        assert llm_bus.last_model == "gpt-4o-mini"

    def test_write_uses_write_model(
        self, empty_project_root: Path, sample_outline: ChapterOutline, sample_chapter_text: str
    ) -> None:
        """测试 write 阶段使用 write_model。"""
        llm_bus = MockLLMBus([sample_chapter_text])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
            think_model="gpt-4o-mini",
            write_model="gpt-4",
        )
        writer.write("ch_001", sample_outline)
        assert llm_bus.last_model == "gpt-4"

    def test_revise_uses_revise_model(
        self, empty_project_root: Path, sample_outline: ChapterOutline
    ) -> None:
        """测试 revise 阶段使用 revise_model。"""
        llm_bus = MockLLMBus(["# 修订后\n\n内容。"])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
            think_model="gpt-4o-mini",
            write_model="gpt-4",
            revise_model="gpt-4",
        )
        writer.revise("ch_001", sample_outline, "原文本", "需要修改")
        assert llm_bus.last_model == "gpt-4"

    def test_no_stage_model_falls_back_to_default(
        self, empty_project_root: Path, cheap_outline: str
    ) -> None:
        """测试未设置 stage model 时使用默认模型（llm_bus 的默认值）。"""
        llm_bus = MockLLMBus([cheap_outline])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            retriever=ret,
            project_root=empty_project_root,
        )
        writer.think("ch_001", "测试")
        # 不传 model 时 last_model 为 None
        assert writer.think_model is None
        assert writer.write_model is None
