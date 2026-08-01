"""P4 Director Agent 测试。

覆盖 analyze()、配置开关、AutoRunner 集成。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opennovel.agents.director import Director
from opennovel.core.auto_runner import AutoRunner, ChapterResult
from opennovel.core.config import LoomConfig
from opennovel.schemas.director import DirectorAnalysis
from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
from opennovel.schemas.outline import ChapterOutline, SceneBreakdown


# ── 辅助工具 ──


def _make_outline() -> ChapterOutline:
    return ChapterOutline(
        chapter_id="ch_001",
        title="测试章节",
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
        manager_summary="主角在加油站遇到其他三人",
        word_count=3000,
    )


def _make_director_analysis_json() -> str:
    return """{
        "pacing_assessment": "适中",
        "tension_curve": "上升中",
        "character_arc_status": {"char_001": "成长中"},
        "strategic_guidance": "下一章应放缓节奏，安排内心独白。",
        "creative_direction_adjustment": "",
        "warnings": []
    }"""


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


# ── DirectorAnalysis Schema 测试 ──


class TestDirectorAnalysis:
    """DirectorAnalysis 模型测试。"""

    def test_valid_analysis(self) -> None:
        analysis = DirectorAnalysis(
            pacing_assessment="适中",
            tension_curve="上升中",
            character_arc_status={"char_001": "成长中"},
            strategic_guidance="放缓节奏",
        )
        assert analysis.pacing_assessment == "适中"
        assert analysis.warnings == []

    def test_optional_fields(self) -> None:
        analysis = DirectorAnalysis(
            pacing_assessment="适中",
            tension_curve="上升中",
            character_arc_status={},
            strategic_guidance="",
        )
        assert analysis.creative_direction_adjustment == ""
        assert analysis.warnings == []


# ── Director Agent 测试 ──


class TestDirectorAnalyze:
    """Director.analyze() 测试。"""

    def test_returns_valid_analysis(self, tmp_path: Path) -> None:
        """返回合法的 DirectorAnalysis。"""
        bus = MockLLMBus([_make_director_analysis_json()])
        director = Director(llm_bus=bus, project_root=tmp_path)
        results = [_make_chapter_result(85)]

        analysis = director.analyze(results, "下一章大纲")

        assert isinstance(analysis, DirectorAnalysis)
        assert analysis.pacing_assessment == "适中"
        assert "char_001" in analysis.character_arc_status

    def test_empty_results_returns_default(self, tmp_path: Path) -> None:
        """空结果返回默认分析。"""
        bus = MockLLMBus([_make_director_analysis_json()])
        director = Director(llm_bus=bus, project_root=tmp_path)

        analysis = director.analyze([], "下一章大纲")

        assert analysis.pacing_assessment == "无数据"
        assert analysis.strategic_guidance == ""

    def test_analysis_builds_correct_data(self, tmp_path: Path) -> None:
        """分析数据正确构建。"""
        bus = MockLLMBus([_make_director_analysis_json()])
        director = Director(llm_bus=bus, project_root=tmp_path)
        results = [
            _make_chapter_result(82),
            _make_chapter_result(85),
            _make_chapter_result(88),
        ]

        # 直接测试数据构建方法
        data = director._build_analysis_data(results)
        assert "82" in data
        assert "85" in data
        assert "88" in data
        assert "ch_001" in data


# ── Config 集成测试 ──


class TestDirectorConfig:
    """Director 配置测试。"""

    def test_director_enabled_default(self) -> None:
        """默认启用 Director。"""
        config = LoomConfig()
        assert config.director_enabled is True

    def test_director_disabled(self, tmp_path: Path) -> None:
        """禁用 Director 时不创建实例。"""
        config = LoomConfig(director_enabled=False)
        # 需要 mock LLMBus 因为 AutoRunner 会尝试创建
        from unittest.mock import patch

        with patch("opennovel.core.auto_runner.LLMBus"):
            runner = AutoRunner(project_root=tmp_path, config=config)
        assert runner.director is None

    def test_director_agent_config(self) -> None:
        """Director 的 AgentConfig 正确解析。"""
        config = LoomConfig()
        director_cfg = config.get_agent_llm_config("director")
        assert "model" in director_cfg
        assert "api_base" in director_cfg


class TestDirectorScheduling:
    """Director 章节调度提议测试。"""

    def test_analysis_with_scheduling_proposals(self) -> None:
        """测试 DirectorAnalysis 包含调度提议。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        analysis = DirectorAnalysis(
            pacing_assessment="过快",
            tension_curve="持续上升，需要缓冲",
            character_arc_status={"char_001": "成长中"},
            strategic_guidance="放缓节奏",
            scheduling_proposals=[
                SchedulingProposal(
                    action=SchedulingAction.INSERT,
                    target_chapter_id="ch_003",
                    rationale="需要插入一章缓冲章节调节节奏",
                    new_chapter_hint="## 缓冲章节\n\n角色内心独白。",
                ),
                SchedulingProposal(
                    action=SchedulingAction.SKIP,
                    target_chapter_id="ch_005",
                    rationale="日常内容已被覆盖",
                ),
            ],
        )
        assert len(analysis.scheduling_proposals) == 2
        assert analysis.scheduling_proposals[0].action == SchedulingAction.INSERT
        assert analysis.scheduling_proposals[0].target_chapter_id == "ch_003"
        assert "内心独白" in analysis.scheduling_proposals[0].new_chapter_hint
        assert analysis.scheduling_proposals[1].action == SchedulingAction.SKIP

    def test_analysis_scheduling_proposals_default_empty(self) -> None:
        """测试 DirectorAnalysis 默认 scheduling_proposals 为空列表。"""
        analysis = DirectorAnalysis(
            pacing_assessment="适中",
            tension_curve="正常",
            character_arc_status={},
            strategic_guidance="继续当前方向",
        )
        assert analysis.scheduling_proposals == []

    def test_invalid_scheduling_action(self) -> None:
        """测试非法调度动作值被拒绝。"""
        from pydantic import ValidationError
        from opennovel.schemas.director import SchedulingProposal

        with pytest.raises(ValidationError):
            SchedulingProposal(
                action="invalid_action",  # type: ignore[arg-type]
                target_chapter_id="ch_001",
                rationale="测试",
            )

    def test_skip_proposal_minimal(self) -> None:
        """测试 SKIP 提议不需要 new_chapter_hint。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_002",
            rationale="内容冗余，跳过",
        )
        assert proposal.new_chapter_hint == ""
        assert proposal.merge_with == ""

    def test_insert_proposal_requires_hint(self) -> None:
        """测试 INSERT 提议可以有空的 new_chapter_hint（允许只提供 rationale）。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        proposal = SchedulingProposal(
            action=SchedulingAction.INSERT,
            target_chapter_id="ch_003",
            rationale="需要补充章节",
        )
        assert proposal.action == SchedulingAction.INSERT
        assert proposal.new_chapter_hint == ""

    def test_director_analyze_with_remaining_chapters(self) -> None:
        """测试 Director.analyze 收到剩余章节信息。"""
        from unittest.mock import MagicMock, patch

        from opennovel.agents.director import Director

        llm_bus = MagicMock()
        llm_bus.chat.return_value.choices[0].message.content = (
            '{"pacing_assessment": "适中", "tension_curve": "正常", '
            '"character_arc_status": {}, "strategic_guidance": "继续", '
            '"scheduling_proposals": []}'
        )

        director = Director(llm_bus=llm_bus, project_root=Path("/tmp/test"))  # type: ignore[arg-type]

        # 模拟有剩余章节时调用
        remaining = [("ch_003", "第三章"), ("ch_004", "第四章")]
        result = director.analyze(
            results=[_make_chapter_result(85)],
            upcoming_chapter_hint="第二章",
            remaining_chapters=remaining,
        )

        assert result.pacing_assessment == "适中"
        assert result.scheduling_proposals == []
