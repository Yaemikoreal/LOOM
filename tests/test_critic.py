"""Critic Agent 测试 - 文学评判代理（mock LLM）。"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from opennovel.agents.critic import Critic
from opennovel.schemas.evaluation import ChapterEvaluation
from opennovel.schemas.outline import ChapterOutline

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


@pytest.fixture
def sample_chapter_text() -> str:
    """示例章节正文。"""
    return """# 相遇

黄昏的余晖洒在废弃加油站的屋顶上，空气中弥漫着汽油和尘土的混合气味。

李明握紧了手中的地图，目光警惕地扫视四周。远处传来了脚步声。"""


@pytest.fixture
def pass_score_json() -> str:
    """合格分数 (85分) 的评分 JSON。"""
    return json.dumps(
        {
            "total_score": 85,
            "dimensions": [
                {"dimension": "文笔质量", "score": 18, "comment": "文笔流畅"},
                {"dimension": "情节逻辑", "score": 17, "comment": "逻辑清晰"},
                {"dimension": "角色一致性", "score": 17, "comment": "角色行为合理"},
                {"dimension": "节奏把控", "score": 16, "comment": "节奏适中"},
                {"dimension": "情感表达", "score": 17, "comment": "情感到位"},
            ],
            "summary": "整体质量不错，达到出版水准。",
            "issues": ["部分对话略显生硬"],
            "suggestions": ["可以增加更多感官描写"],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def fail_score_json() -> str:
    """不合格分数 (75分) 的评分 JSON。"""
    return json.dumps(
        {
            "total_score": 75,
            "dimensions": [
                {"dimension": "文笔质量", "score": 16, "comment": "文笔尚可"},
                {"dimension": "情节逻辑", "score": 15, "comment": "逻辑有漏洞"},
                {"dimension": "角色一致性", "score": 15, "comment": "角色行为不一致"},
                {"dimension": "节奏把控", "score": 14, "comment": "节奏偏快"},
                {"dimension": "情感表达", "score": 15, "comment": "情感表达不足"},
            ],
            "summary": "质量未达标，需要修订。",
            "issues": ["情节转折突兀", "角色动机不清晰"],
            "suggestions": ["补充角色内心独白", "调整情节节奏"],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def excellent_score_json() -> str:
    """优秀分数 (92分) 的评分 JSON。"""
    return json.dumps(
        {
            "total_score": 92,
            "dimensions": [
                {"dimension": "文笔质量", "score": 19, "comment": "文笔优美"},
                {"dimension": "情节逻辑", "score": 18, "comment": "逻辑严密"},
                {"dimension": "角色一致性", "score": 19, "comment": "角色塑造出色"},
                {"dimension": "节奏把控", "score": 18, "comment": "节奏把控精准"},
                {"dimension": "情感表达", "score": 18, "comment": "情感细腻动人"},
            ],
            "summary": "优秀作品，达到出版标准。",
            "issues": [],
            "suggestions": ["可以考虑增加伏笔"],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def invalid_json_response() -> str:
    """非法 JSON（缺少逗号）。"""
    return '{"total_score": 85 "dimensions": []}'


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    """临时项目根目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    return root


# ── Critic 初始化测试 ──


class TestCriticInit:
    """Critic 初始化测试。"""

    def test_default_prompt_path(self, empty_project_root: Path) -> None:
        """测试默认 prompt 路径指向 prompts/critic.v1.md。"""
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)
        assert (
            critic.prompt_path
            == Path(__file__).parent.parent / "opennovel" / "prompts" / "critic.v1.md"
        )

    def test_custom_prompt_path(self, empty_project_root: Path) -> None:
        """测试自定义 prompt 路径。"""
        bus = MagicMock()
        custom = empty_project_root / "custom_prompt.md"
        critic = Critic(llm_bus=bus, project_root=empty_project_root, prompt_path=custom)
        assert critic.prompt_path == custom

    def test_attributes_stored(self, empty_project_root: Path) -> None:
        """测试属性正确存储。"""
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)
        assert critic.llm_bus is bus
        assert critic.project_root == empty_project_root


# ── Critic 评分测试 ──


class TestCriticEvaluate:
    """Critic.evaluate 评分测试。"""

    def test_evaluate_returns_chapter_evaluation(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        pass_score_json: str,
    ) -> None:
        """测试评分返回 ChapterEvaluation 结构。"""
        llm_bus = MockLLMBus([pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert isinstance(evaluation, ChapterEvaluation)
        assert evaluation.total_score == 85
        assert len(evaluation.dimensions) == 5
        assert evaluation.summary != ""

    def test_evaluate_passes_messages_to_llm(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        pass_score_json: str,
    ) -> None:
        """测试评分将消息列表传给 LLM。"""
        llm_bus = MockLLMBus([pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        call_args = llm_bus.last_messages
        assert isinstance(call_args, list)
        assert len(call_args) >= 2
        assert all("role" in m and "content" in m for m in call_args)

    def test_evaluate_includes_chapter_text_in_messages(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        pass_score_json: str,
    ) -> None:
        """测试评分消息中包含章节正文。"""
        llm_bus = MockLLMBus([pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        # 验证章节正文被包含在消息中
        user_messages = [m for m in llm_bus.last_messages if m["role"] == "user"]
        text_found = any("黄昏的余晖" in m["content"] for m in user_messages)
        assert text_found

    def test_evaluate_with_markdown_code_block(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
    ) -> None:
        """测试 LLM 返回被 markdown 代码块包裹的 JSON。"""
        eval_json = """```json
{
    "total_score": 80,
    "dimensions": [
        {"dimension": "文笔质量", "score": 16, "comment": "ok"},
        {"dimension": "情节逻辑", "score": 16, "comment": "ok"},
        {"dimension": "角色一致性", "score": 16, "comment": "ok"},
        {"dimension": "节奏把控", "score": 16, "comment": "ok"},
        {"dimension": "情感表达", "score": 16, "comment": "ok"}
    ],
    "summary": "合格",
    "issues": [],
    "suggestions": []
}
```"""
        llm_bus = MockLLMBus([eval_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert isinstance(evaluation, ChapterEvaluation)
        assert evaluation.total_score == 80


# ── Critic 评分重试测试 ──


class TestCriticEvaluateRetry:
    """Critic.evaluate JSON 解析失败重试测试。"""

    def test_retry_then_success(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        invalid_json_response: str,
        pass_score_json: str,
    ) -> None:
        """测试第一次 JSON 解析失败后重试成功。"""
        llm_bus = MockLLMBus([invalid_json_response, pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert isinstance(evaluation, ChapterEvaluation)
        assert evaluation.total_score == 85
        assert llm_bus.call_count == 2

    def test_all_retries_fail_raises_runtime_error(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        invalid_json_response: str,
    ) -> None:
        """测试所有重试都失败后抛出 RuntimeError。"""
        llm_bus = MockLLMBus(
            [
                invalid_json_response,
                invalid_json_response,
                invalid_json_response,
            ]
        )
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        with pytest.raises(RuntimeError, match="Critic 评分失败"):
            critic.evaluate("ch_001", sample_chapter_text, sample_outline)
        assert llm_bus.call_count == 3

    def test_empty_text_retry(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        pass_score_json: str,
    ) -> None:
        """测试 LLM 返回空文本后重试成功。"""
        llm_bus = MockLLMBus(["", pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert isinstance(evaluation, ChapterEvaluation)
        assert llm_bus.call_count == 2


# ── Critic 合格分数测试 ──


class TestCriticPassScore:
    """Critic 80 分合格测试。"""

    def test_pass_score_is_pass(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        pass_score_json: str,
    ) -> None:
        """测试 85 分为合格。"""
        llm_bus = MockLLMBus([pass_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 85
        assert evaluation.is_pass is True

    def test_exactly_80_is_pass(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
    ) -> None:
        """测试刚好 80 分为合格。"""
        eval_json = json.dumps(
            {
                "total_score": 80,
                "dimensions": [
                    {"dimension": "文笔质量", "score": 16, "comment": "ok"},
                    {"dimension": "情节逻辑", "score": 16, "comment": "ok"},
                    {"dimension": "角色一致性", "score": 16, "comment": "ok"},
                    {"dimension": "节奏把控", "score": 16, "comment": "ok"},
                    {"dimension": "情感表达", "score": 16, "comment": "ok"},
                ],
                "summary": "刚好合格",
                "issues": [],
                "suggestions": [],
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([eval_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 80
        assert evaluation.is_pass is True


# ── Critic 不合格分数测试 ──


class TestCriticFailScore:
    """Critic 79 分不合格测试。"""

    def test_fail_score_is_not_pass(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        fail_score_json: str,
    ) -> None:
        """测试 75 分为不合格。"""
        llm_bus = MockLLMBus([fail_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 75
        assert evaluation.is_pass is False

    def test_exactly_79_is_not_pass(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
    ) -> None:
        """测试刚好 79 分为不合格。"""
        eval_json = json.dumps(
            {
                "total_score": 79,
                "dimensions": [
                    {"dimension": "文笔质量", "score": 16, "comment": "ok"},
                    {"dimension": "情节逻辑", "score": 16, "comment": "ok"},
                    {"dimension": "角色一致性", "score": 16, "comment": "ok"},
                    {"dimension": "节奏把控", "score": 15, "comment": "ok"},
                    {"dimension": "情感表达", "score": 16, "comment": "ok"},
                ],
                "summary": "差一分不合格",
                "issues": ["节奏把控略有不足"],
                "suggestions": ["调整叙事节奏"],
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([eval_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 79
        assert evaluation.is_pass is False

    def test_fail_has_issues_and_suggestions(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        fail_score_json: str,
    ) -> None:
        """测试不合格评分包含问题和建议。"""
        llm_bus = MockLLMBus([fail_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert len(evaluation.issues) > 0
        assert len(evaluation.suggestions) > 0


# ── Critic 优秀分数测试 ──


class TestCriticExcellentScore:
    """Critic 90 分优秀测试。"""

    def test_excellent_score_is_excellent(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        excellent_score_json: str,
    ) -> None:
        """测试 92 分为优秀。"""
        llm_bus = MockLLMBus([excellent_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 92
        assert evaluation.is_excellent is True
        assert evaluation.is_pass is True  # 优秀必然合格

    def test_exactly_90_is_excellent(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
    ) -> None:
        """测试刚好 90 分为优秀。"""
        eval_json = json.dumps(
            {
                "total_score": 90,
                "dimensions": [
                    {"dimension": "文笔质量", "score": 18, "comment": "优秀"},
                    {"dimension": "情节逻辑", "score": 18, "comment": "优秀"},
                    {"dimension": "角色一致性", "score": 18, "comment": "优秀"},
                    {"dimension": "节奏把控", "score": 18, "comment": "优秀"},
                    {"dimension": "情感表达", "score": 18, "comment": "优秀"},
                ],
                "summary": "刚好优秀",
                "issues": [],
                "suggestions": [],
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([eval_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 90
        assert evaluation.is_excellent is True

    def test_89_is_not_excellent(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
    ) -> None:
        """测试 89 分不是优秀。"""
        eval_json = json.dumps(
            {
                "total_score": 89,
                "dimensions": [
                    {"dimension": "文笔质量", "score": 18, "comment": "good"},
                    {"dimension": "情节逻辑", "score": 18, "comment": "good"},
                    {"dimension": "角色一致性", "score": 18, "comment": "good"},
                    {"dimension": "节奏把控", "score": 17, "comment": "good"},
                    {"dimension": "情感表达", "score": 18, "comment": "good"},
                ],
                "summary": "差一分优秀",
                "issues": [],
                "suggestions": [],
            },
            ensure_ascii=False,
        )
        llm_bus = MockLLMBus([eval_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        assert evaluation.total_score == 89
        assert evaluation.is_excellent is False
        assert evaluation.is_pass is True  # 但仍然合格

    def test_excellent_dimensions_high_scores(
        self,
        empty_project_root: Path,
        sample_outline: ChapterOutline,
        sample_chapter_text: str,
        excellent_score_json: str,
    ) -> None:
        """测试优秀评分各维度分数较高。"""
        llm_bus = MockLLMBus([excellent_score_json])
        critic = Critic(
            llm_bus=llm_bus,  # type: ignore[arg-type]
            project_root=empty_project_root,
        )
        evaluation = critic.evaluate("ch_001", sample_chapter_text, sample_outline)

        for dim in evaluation.dimensions:
            assert dim.score >= 16  # 优秀作品各维度至少 16 分


# ── Critic Prompt 加载测试 ──


class TestCriticLoadPrompt:
    """Critic._load_prompt Prompt 加载测试。"""

    def test_load_prompt_fallback_when_missing(self, empty_project_root: Path) -> None:
        """测试 Prompt 文件不存在时返回默认文本。"""
        bus = MagicMock()
        critic = Critic(
            llm_bus=bus,
            project_root=empty_project_root,
            prompt_path=empty_project_root / "nonexistent.md",
        )
        prompt = critic._load_prompt()
        assert "文学评判员" in prompt

    def test_load_prompt_from_file(self, empty_project_root: Path) -> None:
        """测试从文件加载 Prompt。"""
        prompt_path = empty_project_root / "critic.v1.md"
        prompt_path.write_text("你是自定义评判员。", encoding="utf-8")
        bus = MagicMock()
        critic = Critic(
            llm_bus=bus,
            project_root=empty_project_root,
            prompt_path=prompt_path,
        )
        prompt = critic._load_prompt()
        assert "自定义评判员" in prompt


# ── Critic 解析逻辑测试 ──


class TestCriticParseEvaluation:
    """Critic._parse_evaluation_from_text 解析逻辑测试。"""

    def test_parse_valid_json(self, empty_project_root: Path) -> None:
        """测试解析合法 JSON。"""
        text = json.dumps(
            {
                "total_score": 85,
                "dimensions": [
                    {"dimension": "文笔质量", "score": 18, "comment": "ok"},
                    {"dimension": "情节逻辑", "score": 17, "comment": "ok"},
                    {"dimension": "角色一致性", "score": 17, "comment": "ok"},
                    {"dimension": "节奏把控", "score": 16, "comment": "ok"},
                    {"dimension": "情感表达", "score": 17, "comment": "ok"},
                ],
                "summary": "不错",
                "issues": [],
                "suggestions": [],
            },
            ensure_ascii=False,
        )
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)

        evaluation = critic._parse_evaluation_from_text(text)
        assert isinstance(evaluation, ChapterEvaluation)
        assert evaluation.total_score == 85

    def test_parse_json_with_markdown_block(self, empty_project_root: Path) -> None:
        """测试解析被 markdown 代码块包裹的 JSON。"""
        text = """```json
{"total_score": 80, "dimensions": [{"dimension": "文笔质量", "score": 16, "comment": "ok"}, {"dimension": "情节逻辑", "score": 16, "comment": "ok"}, {"dimension": "角色一致性", "score": 16, "comment": "ok"}, {"dimension": "节奏把控", "score": 16, "comment": "ok"}, {"dimension": "情感表达", "score": 16, "comment": "ok"}], "summary": "ok", "issues": [], "suggestions": []}
```"""
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)

        evaluation = critic._parse_evaluation_from_text(text)
        assert isinstance(evaluation, ChapterEvaluation)

    def test_parse_invalid_json_raises(self, empty_project_root: Path) -> None:
        """测试非法 JSON 抛出异常。"""
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)

        with pytest.raises(json.JSONDecodeError):
            critic._parse_evaluation_from_text("{broken json}")

    def test_parse_missing_required_field_raises(self, empty_project_root: Path) -> None:
        """测试缺少必填字段时抛出异常。"""
        text = '{"total_score": 85}'
        bus = MagicMock()
        critic = Critic(llm_bus=bus, project_root=empty_project_root)

        with pytest.raises(Exception):
            critic._parse_evaluation_from_text(text)
