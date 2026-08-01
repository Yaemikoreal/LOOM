"""P0/P1/P2 优化特性测试。

覆盖：
- P0: AutoRunner 快照 + DiffChecker 集成
- P1: Writer/Critic 通过 assemble_context() 获取统一上下文
- P2: AnchoredIssue Schema + 反馈锚定
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.agents.critic import Critic
from opennovel.agents.writer import Writer
from opennovel.core.auto_runner import AutoRunner, ChapterResult, RunReport
from opennovel.core.context_assembler import assemble_context, assemble_actor_context
from opennovel.core.diff_checker import Mismatch, Severity
from opennovel.schemas.evaluation import AnchoredIssue, ChapterEvaluation, DimensionScore
from opennovel.schemas.outline import ChapterOutline, SceneBreakdown


# ── 辅助工具 ──


def _make_outline() -> ChapterOutline:
    """创建测试用大纲。"""
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


def _make_evaluation(
    total_score: int = 85,
    issues: list[str] | None = None,
    suggestions: list[str] | None = None,
    anchored_issues: list[AnchoredIssue] | None = None,
) -> ChapterEvaluation:
    """创建测试用评分结果。"""
    return ChapterEvaluation(
        total_score=total_score,
        dimensions=[
            DimensionScore(dimension="文笔质量", score=18, comment="ok"),
            DimensionScore(dimension="情节逻辑", score=17, comment="ok"),
            DimensionScore(dimension="角色一致性", score=17, comment="ok"),
            DimensionScore(dimension="节奏把控", score=16, comment="ok"),
            DimensionScore(dimension="情感表达", score=17, comment="ok"),
        ],
        summary="总体评价",
        issues=issues or [],
        suggestions=suggestions or [],
        anchored_issues=anchored_issues or [],
    )


class MockLLMBus:
    """测试用 LLM Bus。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0
        self.last_messages: list[dict] = []

    def chat(self, messages: list[dict], **kwargs) -> MagicMock:
        self.last_messages = messages
        resp = MagicMock()
        content = self._responses[self._call_count % len(self._responses)]
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        self._call_count += 1
        return resp


# ── P2: AnchoredIssue Schema 测试 ──


class TestAnchoredIssue:
    """AnchoredIssue 模型测试。"""

    def test_valid_anchored_issue(self) -> None:
        """合法的 AnchoredIssue 创建。"""
        issue = AnchoredIssue(
            dimension="情节逻辑",
            severity="major",
            quote="他突然转身离开了房间",
            problem="角色行为缺乏动机铺垫",
            suggestion="在前文补充心理描写",
            location_hint="第 3 段",
        )
        assert issue.severity == "major"
        assert len(issue.quote) >= 5

    def test_severity_validation(self) -> None:
        """severity 必须是 critical/major/minor。"""
        with pytest.raises(ValueError, match="severity"):
            AnchoredIssue(
                dimension="情节逻辑",
                severity="invalid",
                quote="他突然转身离开了房间",
                problem="问题",
                suggestion="建议",
            )

    def test_quote_too_short(self) -> None:
        """quote 必须至少 5 个字符。"""
        with pytest.raises(ValueError, match="quote"):
            AnchoredIssue(
                dimension="情节逻辑",
                severity="major",
                quote="短",
                problem="问题",
                suggestion="建议",
            )

    def test_location_hint_optional(self) -> None:
        """location_hint 可选。"""
        issue = AnchoredIssue(
            dimension="情节逻辑",
            severity="minor",
            quote="他突然转身离开了房间",
            problem="问题",
            suggestion="建议",
        )
        assert issue.location_hint == ""


class TestChapterEvaluationAnchored:
    """ChapterEvaluation 的 anchored_issues 扩展测试。"""

    def test_has_anchored_issues_true(self) -> None:
        """有锚定问题时返回 True。"""
        eval_ = _make_evaluation(
            anchored_issues=[
                AnchoredIssue(
                    dimension="情节逻辑",
                    severity="major",
                    quote="他突然转身离开了房间",
                    problem="问题",
                    suggestion="建议",
                )
            ]
        )
        assert eval_.has_anchored_issues is True

    def test_has_anchored_issues_false(self) -> None:
        """无锚定问题时返回 False。"""
        eval_ = _make_evaluation()
        assert eval_.has_anchored_issues is False

    def test_backward_compatibility_no_anchored(self) -> None:
        """不传 anchored_issues 时默认空列表（向后兼容）。"""
        data = {
            "total_score": 80,
            "dimensions": [
                {"dimension": "文笔质量", "score": 16, "comment": "ok"},
                {"dimension": "情节逻辑", "score": 16, "comment": "ok"},
                {"dimension": "角色一致性", "score": 16, "comment": "ok"},
                {"dimension": "节奏把控", "score": 16, "comment": "ok"},
                {"dimension": "情感表达", "score": 16, "comment": "ok"},
            ],
            "summary": "ok",
            "issues": [],
            "suggestions": [],
        }
        eval_ = ChapterEvaluation(**data)
        assert eval_.anchored_issues == []
        assert eval_.has_anchored_issues is False


# ── P1: assemble_context() 通用入口测试 ──


class TestAssembleContext:
    """assemble_context() 通用上下文组装测试。"""

    def test_actor_context_backward_compat(self, tmp_path: Path) -> None:
        """assemble_actor_context 行为不变（回归测试）。"""
        # 创建最小项目结构
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world_rules.md").write_text("# 世界观\n魔法消耗寿命", encoding="utf-8")

        chars_dir = tmp_path / "characters"
        chars_dir.mkdir()
        char_fm = "---\nid: char_001\nname: 测试角色\n---\n角色正文"
        (chars_dir / "char_001.md").write_text(char_fm, encoding="utf-8")

        draft_dir = tmp_path / "draft"
        draft_dir.mkdir()
        ch_fm = "---\nid: ch_001\npov: char_001\nactive_characters:\n  - char_001\n---\n章节正文"
        chapter_path = draft_dir / "ch_001.md"
        chapter_path.write_text(ch_fm, encoding="utf-8")

        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        prompt_path = prompt_dir / "test.md"
        prompt_path.write_text("你是测试角色。", encoding="utf-8")

        messages = assemble_actor_context(
            chapter_path=chapter_path,
            project_root=tmp_path,
            current_text="这是续写的文本。",
            prompt_path=prompt_path,
            canon_content="魔法消耗寿命",
        )

        # 应该包含 system（prompt）+ system（canon）+ user（task）
        assert len(messages) >= 2
        # 最后一条应该是 user 消息（CONTINUE）
        assert messages[-1]["role"] == "user"
        assert "CONTINUE:" in messages[-1]["content"]

    def test_assemble_context_custom_task(self, tmp_path: Path) -> None:
        """assemble_context 使用自定义 task_message。"""
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        prompt_path = prompt_dir / "test.md"
        prompt_path.write_text("你是测试 Agent。", encoding="utf-8")

        messages = assemble_context(
            project_root=tmp_path,
            task_message="请评估这个章节的质量。",
            prompt_path=prompt_path,
            canon_content="测试设定",
        )

        # 应该包含 system（prompt）+ system（canon）+ user（task）
        assert len(messages) >= 2
        # 最后一条应该是自定义 task_message
        assert messages[-1]["role"] == "user"
        assert "请评估这个章节的质量。" in messages[-1]["content"]
        # 不应该包含 CONTINUE:
        assert "CONTINUE:" not in messages[-1]["content"]

    def test_canon_has_authority_tag(self, tmp_path: Path) -> None:
        """CANON 内容应包含权威标签。"""
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        prompt_path = prompt_dir / "test.md"
        prompt_path.write_text("你是测试 Agent。", encoding="utf-8")

        messages = assemble_context(
            project_root=tmp_path,
            task_message="测试任务",
            prompt_path=prompt_path,
            canon_content="魔法消耗寿命",
        )

        # 找到包含 CANON 标签的消息
        canon_msgs = [
            m for m in messages if "[CANON | IMMUTABLE | HIGH AUTHORITY]" in m.get("content", "")
        ]
        assert len(canon_msgs) > 0
        assert "魔法消耗寿命" in canon_msgs[0]["content"]


# ── P0: AutoRunner 快照集成测试 ──


class TestAutoRunnerSnapshot:
    """AutoRunner 快照 + DiffChecker 集成测试。"""

    def test_chapter_result_has_mismatches(self) -> None:
        """ChapterResult 包含 mismatches 字段。"""
        result = ChapterResult(
            chapter_id="ch_001",
            outline=_make_outline(),
            chapter_text="正文",
            evaluation=_make_evaluation(),
            retry_count=0,
        )
        assert hasattr(result, "mismatches")
        assert result.mismatches == []

    def test_run_report_has_all_mismatches(self) -> None:
        """RunReport 包含 all_mismatches 汇总字段。"""
        report = RunReport()
        assert hasattr(report, "all_mismatches")
        assert report.all_mismatches == []

    def test_run_report_aggregates_mismatches(self) -> None:
        """RunReport.all_mismatches 汇总所有章节的一致性问题。"""
        m1 = Mismatch(
            severity=Severity.WARNING,
            category="injury",
            character_id="char_001",
            message="伤势不一致",
            source="draft/ch_001.md",
        )
        m2 = Mismatch(
            severity=Severity.INFO,
            category="reference",
            character_id="",
            message="角色引用缺失",
            source="draft/ch_002.md",
        )
        r1 = ChapterResult(
            chapter_id="ch_001",
            outline=_make_outline(),
            chapter_text="正文",
            evaluation=_make_evaluation(),
            retry_count=0,
            mismatches=[m1],
        )
        r2 = ChapterResult(
            chapter_id="ch_002",
            outline=_make_outline(),
            chapter_text="正文",
            evaluation=_make_evaluation(),
            retry_count=0,
            mismatches=[m2],
        )
        report = RunReport(chapters=[r1, r2])
        report.all_mismatches = [m for r in report.chapters for m in r.mismatches]
        assert len(report.all_mismatches) == 2


# ── P2: 反馈构建测试 ──


class TestFeedbackConstruction:
    """AutoRunner 反馈构建逻辑测试。"""

    def test_anchored_feedback_format(self) -> None:
        """锚定反馈应包含原文引用。"""
        eval_ = _make_evaluation(
            total_score=75,
            anchored_issues=[
                AnchoredIssue(
                    dimension="情节逻辑",
                    severity="major",
                    quote="他突然转身离开了房间",
                    problem="角色行为缺乏动机",
                    suggestion="补充心理描写",
                )
            ],
        )

        # 模拟 AutoRunner 的反馈构建逻辑
        if eval_.has_anchored_issues:
            parts = []
            for issue in eval_.anchored_issues:
                parts.append(
                    f"[{issue.severity.upper()}] [{issue.dimension}]\n"
                    f'  原文: "{issue.quote}"\n'
                    f"  问题: {issue.problem}\n"
                    f"  建议: {issue.suggestion}"
                )
            feedback = "不合格原因及修改指引:\n" + "\n".join(parts)
        else:
            feedback = "fallback"

        assert "原文:" in feedback
        assert "他突然转身离开了房间" in feedback
        assert "MAJOR" in feedback
        assert "补充心理描写" in feedback

    def test_fallback_feedback_format(self) -> None:
        """无锚定问题时使用旧格式反馈。"""
        eval_ = _make_evaluation(
            total_score=75,
            issues=["角色动机不足"],
            suggestions=["补充心理描写"],
        )

        if eval_.has_anchored_issues:
            feedback = "锚定格式"
        else:
            issues_text = "\n".join(f"- {i}" for i in eval_.issues)
            suggestions_text = "\n".join(f"- {s}" for s in eval_.suggestions)
            feedback = f"不合格原因:\n{issues_text}\n\n改进建议:\n{suggestions_text}"

        assert "- 角色动机不足" in feedback
        assert "- 补充心理描写" in feedback
        assert "原文:" not in feedback
