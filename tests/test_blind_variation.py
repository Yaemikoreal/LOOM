"""P3 盲目变异测试。

覆盖 think_variations、evaluate_outline、触发逻辑。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opennovel.agents.critic import Critic
from opennovel.agents.writer import Writer
from opennovel.core.auto_runner import AutoRunner, ChapterResult
from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
from opennovel.schemas.outline import ChapterOutline, SceneBreakdown
from opennovel.schemas.outline_evaluation import OutlineEvaluation


# ── 辅助工具 ──


def _make_outline(title: str = "测试章节") -> ChapterOutline:
    return ChapterOutline(
        chapter_id="ch_001",
        title=title,
        summary="测试概要",
        scenes=[
            SceneBreakdown(
                scene_id="scene_1",
                description="场景描述",
                characters_involved=["char_001"],
                emotional_tone="紧张",
                estimated_words=1000,
            )
        ],
        character_arcs={"char_001": "从恐惧到勇敢"},
        key_plot_points=["发现密室"],
        narrative_rhythm="先慢后快",
        target_words=3000,
    )


def _make_evaluation(score: int = 85) -> ChapterEvaluation:
    return ChapterEvaluation(
        total_score=score,
        dimensions=[
            DimensionScore(dimension="文笔质量", score=18, comment="ok"),
            DimensionScore(dimension="情节逻辑", score=17, comment="ok"),
            DimensionScore(dimension="角色一致性", score=17, comment="ok"),
            DimensionScore(dimension="节奏把控", score=16, comment="ok"),
            DimensionScore(dimension="情感表达", score=17, comment="ok"),
        ],
        summary="总体评价",
        issues=[],
        suggestions=[],
    )


def _make_chapter_result(score: int = 85) -> ChapterResult:
    return ChapterResult(
        chapter_id="ch_001",
        outline=_make_outline(),
        chapter_text="正文",
        evaluation=_make_evaluation(score),
        retry_count=0,
    )


class MockLLMBus:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0

    def chat(self, messages: list, **kwargs) -> MagicMock:
        resp = MagicMock()
        content = self._responses[self._call_count % len(self._responses)]
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        self._call_count += 1
        return resp


# ── OutlineEvaluation Schema 测试 ──


class TestOutlineEvaluation:
    """OutlineEvaluation 模型测试。"""

    def test_valid_evaluation(self) -> None:
        eval_ = OutlineEvaluation(
            total_score=50,
            dimensions=[
                {"dimension": "情节逻辑", "score": 17, "comment": "ok"},
                {"dimension": "角色一致性", "score": 17, "comment": "ok"},
                {"dimension": "节奏设计", "score": 16, "comment": "ok"},
            ],
            summary="ok",
            issues=[],
            suggestions=[],
        )
        assert eval_.total_score == 50
        assert eval_.is_pass  # >= 60 is pass for outline
        assert not eval_.is_excellent  # >= 80 is excellent

    def test_dimensions_must_be_3(self) -> None:
        with pytest.raises(ValueError, match="3"):
            OutlineEvaluation(
                total_score=40,
                dimensions=[
                    {"dimension": "情节逻辑", "score": 15, "comment": "ok"},
                    {"dimension": "角色一致性", "score": 15, "comment": "ok"},
                ],
                summary="ok",
                issues=[],
                suggestions=[],
            )


# ── Writer.think_variations 测试 ──


class TestThinkVariations:
    """Writer.think_variations() 测试。"""

    def test_returns_n_outlines(self, tmp_path: Path) -> None:
        """返回指定数量的大纲。"""
        outline_json = '{"chapter_id": "ch_001", "title": "t", "summary": "s", "scenes": [{"scene_id": "s1", "description": "d", "characters_involved": ["char_001"], "emotional_tone": "t", "estimated_words": 100}], "character_arcs": {}, "key_plot_points": [], "narrative_rhythm": "r", "target_words": 1000}'
        bus = MockLLMBus([outline_json, outline_json, outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(llm_bus=bus, retriever=ret, project_root=tmp_path)
        outlines = writer.think_variations("ch_001", "大纲提示", n_variants=3)

        assert len(outlines) == 3
        assert all(isinstance(o, ChapterOutline) for o in outlines)

    def test_exploratory_mode_uses_different_temperatures(self, tmp_path: Path) -> None:
        """探索型变异使用不同 temperature（通过调用次数验证）。"""
        outline_json = '{"chapter_id": "ch_001", "title": "t", "summary": "s", "scenes": [{"scene_id": "s1", "description": "d", "characters_involved": ["char_001"], "emotional_tone": "t", "estimated_words": 100}], "character_arcs": {}, "key_plot_points": [], "narrative_rhythm": "r", "target_words": 1000}'
        bus = MockLLMBus([outline_json, outline_json, outline_json])
        ret = MagicMock()
        ret.query_canon.return_value = ""
        ret.query_subconscious.return_value = ""

        writer = Writer(llm_bus=bus, retriever=ret, project_root=tmp_path)
        writer.think_variations(
            "ch_001",
            "大纲提示",
            n_variants=3,
            variation_mode="exploratory",
        )

        # 每个方案至少 1 次 LLM 调用（context assembly 不调用 LLM）
        assert bus._call_count >= 3


# ── 触发逻辑测试 ──


class TestShouldGenerateVariations:
    """_should_generate_variations() 触发逻辑测试。"""

    def test_user_multi_marker(self, tmp_path: Path) -> None:
        """用户 <!-- multi --> 标记强制触发。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        runner = AutoRunner(project_root=tmp_path, config=config)
        should, mode, feedback = runner._should_generate_variations(
            "章节描述 <!-- multi -->",
            None,
        )
        assert should is True
        assert mode == "exploratory"

    def test_low_score_triggers_corrective(self, tmp_path: Path) -> None:
        """前章评分 <80 触发纠错型变异。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        runner = AutoRunner(project_root=tmp_path, config=config)
        prev = _make_chapter_result(score=75)
        should, mode, feedback = runner._should_generate_variations(
            "正常章节描述",
            prev,
        )
        assert should is True
        assert mode == "corrective"

    def test_high_score_no_trigger(self, tmp_path: Path) -> None:
        """前章评分 >=80 不触发（除非有其他条件）。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        runner = AutoRunner(project_root=tmp_path, config=config)
        prev = _make_chapter_result(score=85)
        should, mode, feedback = runner._should_generate_variations(
            "正常章节描述",
            prev,
        )
        assert should is False

    def test_climax_keyword_triggers(self, tmp_path: Path) -> None:
        """高潮关键词触发探索型变异。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        runner = AutoRunner(project_root=tmp_path, config=config)
        should, mode, feedback = runner._should_generate_variations(
            "第三幕高潮决战",
            None,
        )
        assert should is True
        assert mode == "exploratory"
